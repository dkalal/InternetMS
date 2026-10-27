"""Bounded, atomic new-product batches. No inventory mutation occurs here."""
import json
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from audit.models import AuditLog
from custom_fields.services import CustomFieldService
from custom_fields.mixins import CustomFieldPageContextMixin
from inventory.services import audit
from users.models import Organization
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization
from .creation import save_purchase_product
from .forms import ProductForm
from .models import ProductCategory
from .pricing import can_view_cost

MAX_ROWS = 200
MAX_IMAGES = 24 * 1024 * 1024
MAX_IMAGE_COUNT = 50
GRID = [('name', 'Name / brand'), ('model_number', 'Model'), ('sku', 'SKU'),
        ('catalog_category', 'Category'), ('sales_unit', 'Sales unit'),
        ('buying_price', 'Buying price (TZS)'), ('selling_price', 'Standard price (TZS)'),
        ('technician_price', 'Technician price (TZS)'), ('description', 'Description'), ('image', 'Picture')]


class BatchRowError(Exception):
    def __init__(self, row_id, errors):
        self.row_id = row_id
        self.errors = errors
        super().__init__('A product row could not be saved.')


def meaningful_row(values, baseline):
    """Compare typed defaults: compact decimal widgets render 0.00 as 0."""
    for name, field in baseline.fields.items():
        if name == 'image' or name not in values:
            continue
        try:
            if field.to_python(values[name]) != field.to_python(baseline[name].value()):
                return True
        except ValidationError:
            return True
    return False


def workspace_form(organization, actor, *args, **kwargs):
    form = ProductForm(*args, organization=organization, actor=actor, **kwargs)
    form.fields['category'].disabled = True
    if not can_view_cost(actor=actor, organization=organization):
        form.fields['buying_price'].disabled = True
        form.fields['buying_price'].initial = 0
    return form


def product_workspace(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.PRODUCTS_CREATE)
    if request.method == 'GET':
        form = workspace_form(organization, request.user)
        columns = [(name, label) for name, label in GRID
                   if name != 'buying_price' or can_view_cost(actor=request.user, organization=organization)]
        for name, field in form.fields.items():
            field.widget.attrs['data-field'] = name
            # Native selects keep each cell's keyboard/label contract intact.
            field.widget.attrs.pop('data-searchable-select', None)
        rules = {str(c.pk): {'allowed': [unit.pk for unit in c.allowed_units.all()],
                            'default': c.default_unit_id}
                 for c in ProductCategory.objects.filter(tenant=organization, is_active=True).prefetch_related('allowed_units')}
        modal_context = CustomFieldPageContextMixin()
        modal_context.request = request
        context = modal_context.get_custom_field_modal_context(target_model='product')
        from users.permissions import has_tenant_permission
        context.update({
            'can_manage_custom_fields': has_tenant_permission(request.user, organization, PermissionCode.CUSTOM_FIELD_MANAGE),
            'custom_field_inline_use': True,
            'custom_field_grid': True,
            'columns': [{'name': n, 'label': label, 'field': form[n]} for n, label in columns],
            'detail_fields': [form[n] for n in ('item_type', 'is_active', 'tax_eligible',
                'track_stock', 'is_serialized', 'track_expiry', 'reorder_threshold')],
            'wholesale_fields': [form[n] for n in ('allow_wholesale', 'wholesale_price', 'wholesale_min_quantity')],
            'additional_fields': [form[n] for n in form.custom_field_names],
            'required_details': form.has_required_custom_fields,
            'unit_rules': rules,
            'batch_token': signing.dumps({'tenant': organization.pk, 'actor': request.user.pk, 'key': str(uuid4())}, salt='product-batch'),
        })
        return render(request, 'products/product_workspace.html', context)
    try:
        token = signing.loads(request.POST.get('batch_token', ''), salt='product-batch', max_age=86400)
        if token['tenant'] != organization.pk or token['actor'] != request.user.pk:
            raise ValueError('Invalid submission token.')
        if AuditLog.objects.unscoped().filter(tenant=organization, actor=request.user,
                action='inventory.products.batch_created', metadata__batch_key=token['key']).exists():
            return JsonResponse({'redirect': reverse('product-list'), 'replayed': True})
        rows = json.loads(request.POST.get('rows', 'null'))
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ValueError('Enter between 1 and 200 rows.')
        if len(request.FILES) > MAX_IMAGE_COUNT or sum(f.size for f in request.FILES.values()) > MAX_IMAGES:
            raise ValueError('Use at most 50 pictures, totaling 24 MB or smaller.')
        ids = [r['id'] for r in rows]
        if any(not isinstance(i, str) or not i.isalnum() or len(i) > 64 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Invalid row identifiers.')
        forms_to_save, errors, explicit_skus = [], {}, set()
        baseline = workspace_form(organization, request.user)
        for row in rows:
            values = row['values']
            if not isinstance(values, dict) or any(isinstance(v, (dict, list)) for v in values.values()):
                raise ValueError('Invalid cell values.')
            form = workspace_form(organization, request.user, values,
                                  {'image': request.FILES[row['id']]} if row['id'] in request.FILES else {})
            meaningful = meaningful_row(values, baseline) or row['id'] in request.FILES
            if not meaningful:
                continue
            valid = form.is_valid()
            sku = str(values.get('sku', '')).strip().upper()
            if sku and sku in explicit_skus:
                form.add_error('sku', 'Duplicate SKU in this batch.')
                valid = False
            explicit_skus.add(sku)
            if not valid:
                errors[row['id']] = form.errors.get_json_data()
            forms_to_save.append((row, form))
        if errors:
            return JsonResponse({'errors': errors}, status=400)
        if not forms_to_save:
            raise ValueError('Enter at least one product.')
        created = save_product_batch(organization=organization, actor=request.user, forms_to_save=forms_to_save, batch_key=token['key'])
        if created:
            messages.success(request, f'{len(forms_to_save)} products created. Stock remains unchanged.')
        return JsonResponse({'redirect': reverse('product-list'), 'count': len(forms_to_save), 'replayed': not created})
    except BatchRowError as exc:
        return JsonResponse({'errors': {exc.row_id: exc.errors}}, status=400)
    except (ValueError, KeyError, TypeError, signing.BadSignature, ValidationError) as exc:
        return JsonResponse({'message': '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)}, status=400)


def save_product_batch(*, organization, actor, forms_to_save, batch_key):
    """Persist validated product forms, audit and custom fields as one aggregate."""
    from django.core.exceptions import PermissionDenied
    from users.permissions import has_tenant_permission
    if not has_tenant_permission(actor, organization, PermissionCode.PRODUCTS_CREATE):
        raise PermissionDenied("Insufficient permissions.")
    if not 1 <= len(forms_to_save) <= MAX_ROWS:
        raise ValidationError('Enter between 1 and 200 products.')
    if any(form.organization != organization or form.actor != actor for _, form in forms_to_save):
        raise PermissionDenied('Batch forms must belong to the active business and actor.')
    stored = []
    try:
        with transaction.atomic():
            # A tenant row lock serializes both SKU allocation and token consumption.
            Organization.objects.select_for_update().get(pk=organization.pk)
            prior = AuditLog.objects.unscoped().filter(tenant=organization, actor=actor,
                action='inventory.products.batch_created', metadata__batch_key=batch_key).exists()
            if prior:
                return False
            # Explicit SKUs reserve their names before automatic allocation.
            for row, form in sorted(forms_to_save, key=lambda pair: not bool(str(pair[0]['values'].get('sku', '')).strip())):
                product = form.save(commit=False)
                product.quantity = product.stock = 0
                try:
                    save_purchase_product(product, organization=organization, supplied_sku=str(row['values'].get('sku', '')), max_attempts=1000)
                except ValidationError as exc:
                    form.add_error(None, exc)
                    raise BatchRowError(row['id'], form.errors.get_json_data()) from exc
                finally:
                    if product.image and product.image._committed:
                        stored.append((product.image.storage, product.image.name))
                CustomFieldService.save_custom_field_values(product, form.cleaned_data, user=actor)
                audit(organization=organization, actor=actor, action='inventory.product.created', obj=product,
                      new_value={'sku': product.sku, 'name': product.name, 'item_type': product.item_type, 'has_image': bool(product.image)})
            audit(organization=organization, actor=actor, action='inventory.products.batch_created', obj=organization,
                  metadata={'batch_key': batch_key, 'count': len(forms_to_save)})
    except Exception:
        for storage, name in stored:
            storage.delete(name)
        raise
    return True
