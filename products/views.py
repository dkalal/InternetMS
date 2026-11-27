from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse, reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.contrib import messages
from django.db.models import F, Q
from custom_fields.mixins import CustomFieldPageContextMixin
from .models import Product, ProductCategory
from .forms import ProductForm, ProductUnitSuccessorForm
from .services import ProductUnitTransitionError, ProductUnitTransitionService
from custom_fields.services import CustomFieldService
from users.permissions import PermissionCode, has_tenant_permission, require_permission
from inventory.services import audit
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context, positive_decimal
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse, Http404
from uuid import uuid4
from audit.models import AuditLog

class ProductListView(LoginRequiredMixin, ListView):
    model = Product
    template_name = 'products/product_list.html'
    context_object_name = 'products'
    paginate_by = 25
    sort_options = {
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "category": ("catalog_category__name", "category", "name"),
        "-category": ("-catalog_category__name", "-category", "name"),
        "stock": ("quantity", "name"),
        "-stock": ("-quantity", "name"),
        "retail": ("selling_price", "name"),
        "-retail": ("-selling_price", "name"),
        "wholesale": ("wholesale_price", "name"),
        "-wholesale": ("-wholesale_price", "name"),
    }

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_VIEW)
        queryset = super().get_queryset().filter(organization=organization).select_related(
            "catalog_category", "inventory_balance"
        )
        search = (self.request.GET.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(sku__icontains=search)
                | Q(brand__icontains=search)
                | Q(model_number__icontains=search)
                | Q(stock_units__serial_number__icontains=search)
            ).distinct()
        catalog_category = self.request.GET.get("catalog_category")
        if catalog_category and catalog_category.isdigit():
            queryset = queryset.filter(catalog_category_id=catalog_category)
        legacy_category = self.request.GET.get("category")
        if legacy_category in dict(Product.CATEGORY_CHOICES):
            queryset = queryset.filter(category=legacy_category)
        item_type = self.request.GET.get("item_type")
        if item_type in Product.ItemType.values:
            queryset = queryset.filter(item_type=item_type)
        is_active = self.request.GET.get('is_active')
        if is_active in {"0", "1"}:
            queryset = queryset.filter(is_active=bool(int(is_active)))
        stock_state = self.request.GET.get("stock_state")
        if stock_state == "out":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL, track_stock=True, quantity__lte=0
            )
        elif stock_state == "low":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL,
                track_stock=True,
                quantity__gt=0,
            ).filter(
                Q(quantity__lte=F("reorder_threshold"))
                | Q(reorder_threshold=0, quantity__lte=5)
            )
        elif stock_state == "available":
            queryset = queryset.filter(
                item_type=Product.ItemType.PHYSICAL, track_stock=True, quantity__gt=0
            )
        serialized = self.request.GET.get("serialized")
        if serialized in {"0", "1"}:
            queryset = queryset.filter(is_serialized=bool(int(serialized)))
        min_price = positive_decimal(self.request.GET.get("min_price"))
        max_price = positive_decimal(self.request.GET.get("max_price"))
        if min_price is not None:
            queryset = queryset.filter(selling_price__gte=min_price)
        if max_price is not None:
            queryset = queryset.filter(selling_price__lte=max_price)
        queryset, self.active_sort = apply_sort(queryset, self.request.GET.get("sort"), self.sort_options, "name")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "name"))
        context["category_choices"] = Product.CATEGORY_CHOICES
        context["catalog_categories"] = ProductCategory.objects.filter(
            tenant=require_organization(self.request), is_active=True
        ).order_by("name")
        context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

class ProductDetailView(CustomFieldPageContextMixin, LoginRequiredMixin, DetailView):
    model = Product
    template_name = 'products/product_detail.html'
    context_object_name = 'product'
    custom_field_target_model = "product"

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_VIEW)
        return super().get_queryset().filter(organization=organization)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["custom_fields"] = CustomFieldService.get_custom_field_values(self.object)
        context["has_unit_history"] = self.object.has_unit_history()
        context.update(self.get_custom_field_modal_context(target_model="product"))
        return context


@login_required
def product_unit_successor(request, pk):
    organization = require_organization(request)
    require_permission(request, PermissionCode.PRODUCT_MANAGE)
    require_permission(request, PermissionCode.COST_REPORT_VIEW)
    source = get_object_or_404(
        Product.objects.unscoped().select_related('sales_unit', 'catalog_category'),
        pk=pk,
        tenant=organization,
    )
    if not source.has_unit_history():
        messages.info(request, 'This product has no transaction history. Edit its unit directly instead.')
        return redirect('product-update', pk=source.pk)

    form = ProductUnitSuccessorForm(
        request.POST or None,
        organization=organization,
        source_product=source,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            successor, created = ProductUnitTransitionService.create_successor(
                organization=organization,
                source_product_id=source.pk,
                actor=request.user,
                transition_key=form.cleaned_data['transition_key'],
                values=form.cleaned_data,
            )
        except ProductUnitTransitionError as exc:
            form.add_error(None, str(exc))
        else:
            if created:
                messages.success(
                    request,
                    f'{successor.name} was created in {successor.get_measure_unit_display()}. '
                    f'{source.name} was made inactive; its history and stock were not moved.',
                )
            else:
                messages.info(request, 'This unit transition was already completed; no duplicate product was created.')
            return redirect('product-detail', pk=successor.pk)

    open_document_count = source.billinglineitem_set.filter(
        document__tenant=organization,
        document__document_type='invoice',
        document__status__in=('draft', 'sent', 'issued', 'partially_paid'),
    ).values('document_id').distinct().count()
    draft_cart_count = source.cart_lines.filter(
        cart__tenant=organization, cart__status='draft',
    ).values('cart_id').distinct().count()
    return render(request, 'products/product_unit_successor.html', {
        'source': source,
        'form': form,
        'open_document_count': open_document_count,
        'draft_cart_count': draft_cart_count,
    })

class ProductCreateView(CustomFieldPageContextMixin, LoginRequiredMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')
    custom_field_target_model = "product"
    custom_field_inline_use = True

    def post(self, request, *args, **kwargs):
        token = request.POST.get('submission_token')
        if not token:
            # Compatibility for pre-token integrations and legacy form clients.
            return super().post(request, *args, **kwargs)
        organization = require_organization(request)
        try:
            key = signing.loads(token, salt='product-single', max_age=86400)
            if key['tenant'] != organization.pk or key['actor'] != request.user.pk:
                raise signing.BadSignature('Submission belongs to another actor.')
        except (signing.BadSignature, KeyError):
            return HttpResponse('Invalid or expired product submission.', status=400)
        from .workspace import submission_fingerprint
        payload = {k: v for k, v in request.POST.lists() if k not in ('csrfmiddlewaretoken', 'submission_token')}
        fingerprint = submission_fingerprint(payload, request.FILES)
        with transaction.atomic():
            type(organization).objects.select_for_update().get(pk=organization.pk)
            prior = AuditLog.objects.unscoped().filter(tenant=organization, actor=request.user,
                action='inventory.product.submission', metadata__key=key['key']).first()
            if prior:
                if prior.metadata.get('fingerprint') != fingerprint:
                    return HttpResponse('Submission key was already used with different content.', status=409)
                self.object = get_object_or_404(Product.objects.unscoped(), tenant=organization, pk=prior.object_id)
                if request.GET.get('purchase_modal') == '1':
                    return self.modal_created_response()
                return redirect(self.success_url)
            response = super().post(request, *args, **kwargs)
            if self.object and self.object.pk:
                audit(organization=organization, actor=request.user, action='inventory.product.submission', obj=self.object,
                      metadata={'key': key['key'], 'fingerprint': fingerprint})
            return response

    def modal_created_response(self):
        return render(self.request, 'inventory/includes/purchase_modal_created.html', {
            'created_record': {'kind': 'product', 'id': self.object.pk, 'text': str(self.object),
                'name': self.object.name, 'sku': self.object.sku, 'unit': self.object.sales_unit.label,
                'serialized': self.object.is_serialized, 'expiry': self.object.track_expiry,
                **({'buying_price': str(self.object.buying_price)} if has_tenant_permission(
                    self.request.user, self.request.organization, PermissionCode.COST_REPORT_VIEW,
                    membership=self.request.membership,
                ) else {})}})

    def dispatch(self, request, *args, **kwargs):
        require_organization(request)
        require_permission(request, PermissionCode.PRODUCTS_CREATE)
        if request.GET.get('purchase_modal') != '1' and (request.method == 'GET' or 'rows' in request.POST):
            from .workspace import product_workspace
            return product_workspace(request)
        response = super().dispatch(request, *args, **kwargs)
        if request.GET.get('purchase_modal') == '1':
            response['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    def form_valid(self, form):
        try:
            with transaction.atomic():
                return self._save_valid_form(form)
        except ValidationError as exc:
            self.object = None
            form.instance.pk = None
            form.instance._state.adding = True
            form.add_error(None, exc)
            return self.form_invalid(form)

    def _save_valid_form(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCTS_CREATE)
        form.instance.organization = organization
        form.instance.tenant = organization
        form.instance.quantity = 0
        form.instance.stock = 0
        with transaction.atomic():
            response = super().form_valid(form)
            CustomFieldService.save_custom_field_values(self.object, form.cleaned_data, user=self.request.user)
            audit(
                organization=organization,
                actor=self.request.user,
                action='inventory.product.created',
                obj=self.object,
                new_value={
                    'sku': self.object.sku,
                    'name': self.object.name,
                    'item_type': self.object.item_type,
                    'has_image': bool(self.object.image),
                },
            )
            legacy_quantity = None if self.request.GET.get('purchase_modal') == '1' else self.request.POST.get('quantity')
            if legacy_quantity and self.object.track_stock:
                from decimal import Decimal
                from inventory.services import InventoryService

                if Decimal(legacy_quantity) > 0:
                    InventoryService.adjust_stock(
                        organization=organization,
                        product_id=self.object.pk,
                        quantity_delta=Decimal(legacy_quantity),
                        reason='opening_balance',
                        notes='Opening balance supplied by the legacy product form.',
                        actor=self.request.user,
                    )
        if self.request.GET.get('purchase_modal') == '1':
            return self.modal_created_response()
        if (
            self.request.POST.get('next') == 'add_serials'
            and self.object.is_serialized
            and has_tenant_permission(
                self.request.user,
                organization,
                PermissionCode.STOCK_ADJUST,
                membership=getattr(self.request, 'membership', None),
            )
        ):
            messages.success(
                self.request,
                f'{self.object.name} was created. Enter one serial number for each unit being added.',
            )
            return redirect(f'{reverse("inventory:stock_adjust")}?product={self.object.pk}')
        return response

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.request.GET.get('purchase_modal') == '1':
            for name, value in (
                ('item_type', Product.ItemType.PHYSICAL), ('track_stock', True), ('is_active', True),
            ):
                form.fields[name].initial = value
                form.fields[name].disabled = True
        return form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        kwargs['actor'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['embedded_create'] = self.request.GET.get('purchase_modal') == '1'
        context['submission_token'] = self.request.POST.get('submission_token') or signing.dumps(
            {'tenant': require_organization(self.request).pk, 'actor': self.request.user.pk, 'key': str(uuid4())}, salt='product-single')
        context['quick_category_units'] = context['form'].fields['sales_unit'].queryset
        context.update(self.get_custom_field_modal_context(target_model="product"))
        context["has_movement_history"] = getattr(context["form"], "has_movement_history", False)
        categories = ProductCategory.objects.filter(tenant=require_organization(self.request)).prefetch_related('allowed_units')
        context['unit_rules'] = {
            str(category.pk): {
                'allowed': [unit.pk for unit in category.allowed_units.all()],
                'default': category.default_unit_id or '',
            }
            for category in categories
        }
        return context

class ProductUpdateView(CustomFieldPageContextMixin, LoginRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'products/product_form.html'
    success_url = reverse_lazy('product-list')
    custom_field_target_model = "product"
    custom_field_inline_use = True

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        return super().get_queryset().filter(organization=organization)

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        previous = Product.objects.unscoped().get(pk=self.object.pk)
        old_value = {
            'sku': previous.sku,
            'name': previous.name,
            'selling_price': str(previous.selling_price),
            'image': previous.image.name if previous.image else '',
            'track_stock': previous.track_stock,
            'is_serialized': previous.is_serialized,
            'is_active': previous.is_active,
        }
        form.instance.organization = organization
        form.instance.tenant = organization
        with transaction.atomic():
            response = super().form_valid(form)
            CustomFieldService.save_custom_field_values(self.object, form.cleaned_data, user=self.request.user)
            audit(
                organization=organization,
                actor=self.request.user,
                action='inventory.product.updated',
                obj=self.object,
                old_value=old_value,
                new_value={
                    'sku': self.object.sku,
                    'name': self.object.name,
                    'selling_price': str(self.object.selling_price),
                    'image': self.object.image.name if self.object.image else '',
                    'track_stock': self.object.track_stock,
                    'is_serialized': self.object.is_serialized,
                    'is_active': self.object.is_active,
                },
            )
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        kwargs['actor'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_custom_field_modal_context(target_model="product"))
        context["has_movement_history"] = getattr(context["form"], "has_movement_history", False)
        categories = ProductCategory.objects.filter(tenant=require_organization(self.request)).prefetch_related('allowed_units')
        context['unit_rules'] = {
            str(category.pk): {
                'allowed': [unit.pk for unit in category.allowed_units.all()],
                'default': category.default_unit_id or '',
            }
            for category in categories
        }
        return context

class ProductDeleteView(LoginRequiredMixin, DeleteView):
    model = Product
    template_name = 'products/product_confirm_delete.html'
    success_url = reverse_lazy('product-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.PRODUCT_MANAGE)
        return super().get_queryset().filter(organization=organization)

    def form_valid(self, form):
        if self.object.quantity > 0 or self.object.stock > 0:
            messages.error(
                self.request,
                'This product still has stock and cannot be deleted. Reconcile its stock '
                'through the inventory workflow before considering removal.',
            )
            return redirect('product-detail', pk=self.object.pk)
        try:
            with transaction.atomic():
                return super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                'This product cannot be deleted because purchase, stock, sale, or other records '
                'refer to it. Its history is preserved. If it should no longer be used, '
                'edit the product and mark it inactive.',
            )
            return redirect('product-detail', pk=self.object.pk)


@login_required
def product_packaging(request, pk):
    from . import packaging as policy
    from inventory.models import InventoryBalance
    from inventory.packaging import PackagingActivationService, prepare_existing_balance
    from inventory.packaging_forms import PackageConfigurationForm, PackagePriceForm, ActivationForm, CountedContainers
    organization = require_organization(request)
    support = getattr(request, 'support_access', None)
    policy.authorize(organization=organization, actor=request.user, support_session=support)
    product = get_object_or_404(Product.objects.filter(tenant=organization, organization=organization).select_related('sales_unit'), pk=pk)
    if product.is_serialized or product.item_type != Product.ItemType.PHYSICAL or not product.track_stock:
        raise Http404('Packaging supports non-serialized physical stock products only.')
    action = request.POST.get('action') if request.method == 'POST' else None
    configuration = PackageConfigurationForm(request.POST if action == 'configure' else None, organization=organization, prefix='configuration')
    balance = InventoryBalance.objects.filter(tenant=organization, product=product).first()
    quantity = balance.quantity if balance else product.quantity
    average = balance.average_cost if balance else product.buying_price
    activation = ActivationForm(request.POST if request.method == 'POST' and action != 'configure' else None, prefix='activation',
        initial={'expected_quantity': quantity, 'expected_average_cost': average, 'expected_version': product.packaging_version})
    from inventory.packaging_transport import expand_fields
    transport_error = None
    counted_data = request.POST if action == 'activate' else None
    try:
        counted_data = expand_fields(counted_data, 'counted_payload')
    except ValidationError as exc:
        transport_error = str(exc)
    packages = list(product.packaging_definitions.filter(tenant=organization).select_related('unit').order_by('-version'))
    counted = CountedContainers(counted_data, prefix='counted', form_kwargs={'organization': organization, 'product': product, 'packages': packages})
    if transport_error:
        activation.is_valid()
        activation.add_error(None, transport_error)
    price_forms = []
    target = None
    for package in packages:
        bound = action in {'prices', 'retire'} and request.POST.get('packaging_id') == str(package.pk)
        form = PackagePriceForm(request.POST if bound and action == 'prices' else None, prefix=f'price_{package.pk}',
            initial={name: getattr(package, name) for name in PackagePriceForm.base_fields})
        price_forms.append((package, form))
        if bound:
            target = (package, form)
    if request.method == 'POST':
        error_form = configuration if action == 'configure' else target[1] if target else activation
        try:
            changed = False
            if action == 'configure' and configuration.is_valid():
                policy.PackagingService.configure(organization=organization, actor=request.user, product_id=product.pk,
                    unit_id=configuration.cleaned_data['unit'].pk, factor=configuration.cleaned_data['factor'],
                    prices={name: configuration.cleaned_data[name] for name in PackagePriceForm.base_fields}, support_session=support)
                changed = True
            elif action == 'prices' and target and target[1].is_valid():
                policy.PackagingService.update_prices(organization=organization, actor=request.user, product_id=product.pk,
                    packaging_id=target[0].pk, prices=target[1].cleaned_data, support_session=support)
                changed = True
            elif action == 'retire' and target:
                policy.PackagingService.retire(organization=organization, actor=request.user, product_id=product.pk,
                    packaging_id=target[0].pk, support_session=support)
                changed = True
            elif action in {'activate', 'disable', 'prepare'} and activation.is_valid():
                values = activation.cleaned_data
                if action == 'prepare':
                    prepare_existing_balance(organization=organization, actor=request.user, product_id=product.pk,
                        expected_quantity=values['expected_quantity'], expected_average_cost=values['expected_average_cost'],
                        reason=values['reason'], support_session=support)
                    changed = True
                elif action == 'disable' or counted.is_valid():
                    rows = []
                    if action == 'activate':
                        for row in counted.cleaned_data:
                            if row and not row.get('DELETE'):
                                rows.append(dict(quantity=str(row['quantity']), packaging_id=row.get('packaging'),
                                    sealed=row.get('sealed', False), supplier_identifier=row.get('supplier_identifier', ''),
                                    expiry_date=row['expiry_date'].isoformat() if row.get('expiry_date') else ''))
                    PackagingActivationService.change(organization=organization, actor=request.user, product_id=product.pk,
                        enabled=action == 'activate', containers=rows, support_session=support, **values)
                    changed = True
            elif action not in {'configure', 'prices', 'retire', 'activate', 'disable', 'prepare'} or action in {'prices', 'retire'} and not target:
                raise policy.PackagingError('Choose a valid packaging action for this product.')
            if changed:
                messages.success(request, 'Packaging action recorded. Existing transaction history and base unit are preserved.')
                return redirect('product-packaging', pk=product.pk)
        except ValidationError as exc:
            error_form.is_valid()
            error_form.add_error(None, str(exc))
    return render(request, 'products/product_packaging.html', {'product': product, 'configuration': configuration,
        'activation': activation, 'counted': counted, 'price_forms': price_forms, 'base_quantity': quantity,
        'average_cost': average, 'workflow_ready': policy.workflows_ready(), 'needs_balance_preparation': balance is None and quantity > 0})
