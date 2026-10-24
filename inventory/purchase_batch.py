"""Atomic purchase-workspace batch application."""

from decimal import Decimal
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Upper

from products.creation import save_purchase_product
from products.models import Product
from users.permissions import PermissionCode, has_tenant_permission

from .forms import QuickPurchaseProductForm
from .models import PurchaseLine, StockUnit, PurchaseProductProposal
from .purchase_import import HEADERS, ImportInputError, validate_rows
from .services import audit


def proposal_rows(purchase):
    if not purchase.pk:
        return []
    return [row for proposal in purchase.product_proposals.filter(tenant_id=purchase.tenant_id)
            for row in proposal.rows]


def proposal_lines(purchase):
    """Transient review objects; never persist catalog or stock during display."""
    from types import SimpleNamespace
    references = {}
    lines = []
    for row in proposal_rows(purchase):
        reference = row['staging_reference']
        key = references.setdefault(reference, -(len(references) + 1))
        product = Product(name=row['product_name'], sku=row['sku'] or 'Allocated on receiving',
                          measure_unit=row['unit'], quantity=Decimal('0'),
                          selling_price=Decimal(row['selling_price']), buying_price=Decimal('0'),
                          is_serialized=row.get('serialized_value', False),
                          track_expiry=row.get('track_expiry_value', False))
        quantity, unit_cost = Decimal(row['quantity']), Decimal(row['unit_cost'])
        lines.append(SimpleNamespace(product=product, product_id=key, quantity=quantity,
            unit_cost=unit_cost, line_total=(quantity * unit_cost).quantize(Decimal('0.01')),
            batch_reference=row['batch_reference'], expiry_date=(
                date.fromisoformat(row['expiry_date']) if row['expiry_date'] else None),
            serial_numbers=row['serial_numbers'], is_proposal=True))
    return lines


def apply_purchase_batch(*, purchase, rows, organization, actor, membership, receiving=False):
    """Called inside the purchase save transaction after formset persistence."""
    if membership is None or membership.user_id != actor.pk or membership.tenant_id != organization.pk:
        raise ImportInputError('Active tenant membership is required for this purchase import.')
    ids = {int(str(row['product_id'])) for row in rows
           if str(row.get('product_id', '')).isdigit() and len(str(row['product_id'])) <= 18}
    skus = {str(row.get('sku', '')).strip().upper() for row in rows if row.get('sku')}
    list(Product.objects.select_for_update().filter(tenant=organization).annotate(normalized_sku=Upper('sku')).filter(
        Q(pk__in=ids) | Q(normalized_sku__in=skus)
    ))
    checked = validate_rows(rows, organization=organization, for_save=True)
    if receiving:
        for source, current in zip(rows, checked):
            if any(str(source.get(key)) != str(current.get(key)) for key in ('category_id', 'unit_id')):
                raise ImportInputError('A proposed category or unit changed. Edit and review the draft before receiving.')
    invalid = [row for row in checked if not row['valid']]
    if invalid:
        raise ImportInputError(f"Import row {invalid[0]['row']}: {' '.join(invalid[0]['errors'])}")
    included = [row for row in checked if not row['excluded']]
    names = {}
    for row in included:
        if row['status'] == 'new':
            name = row['product_name'].strip().casefold()
            if name in names and names[name] != row['staging_reference']:
                raise ImportInputError('An active product with this name already exists in the proposals. Confirm a shared staging reference or use distinct names.')
            names[name] = row['staging_reference']
    if not included:
        raise ImportInputError('Include at least one import row.')
    if any(row['status'] == 'new' for row in included) and not has_tenant_permission(
        actor, organization, PermissionCode.PRODUCTS_CREATE, membership=membership
    ):
        raise ImportInputError('Creating products requires the product creation permission.')
    imported_serials = {serial.upper() for row in included for serial in row['serial_numbers'].splitlines() if serial}
    if imported_serials and StockUnit.objects.filter(tenant=organization, serial_number__in=imported_serials).exists():
        raise ImportInputError('An imported serial number already exists in stock.')
    manual_serials = [serial.strip().upper() for line in purchase.lines.all()
                      for serial in line.serial_numbers.replace(',', '\n').splitlines() if serial.strip()]
    if any(serial in imported_serials for serial in manual_serials):
        raise ImportInputError('An imported serial number also appears in a manual purchase line.')
    created = {}
    proposals = []
    for row in included:
        if row['status'] == 'new':
            reference = row['staging_reference']
            if reference not in created:
                form = QuickPurchaseProductForm({
                    'name': row['product_name'], 'sku': row['sku'],
                    'catalog_category': row['category_id'], 'sales_unit': row['unit_id'],
                    'selling_price': row['selling_price'], 'brand': row['brand'],
                    'model_number': row['model_number'], 'is_serialized': row['serialized_value'],
                    'track_expiry': row['track_expiry_value'],
                }, organization=organization, allow_extra_fields=True)
                if not form.is_valid():
                    raise ImportInputError(f"Import row {row['row']}: {form.errors.as_text()}")
                if not receiving:
                    proposals.append({**{key: str(row.get(key, '')) for key in HEADERS},
                                      'resolution': 'new', 'row': row['row'],
                                      'category_id': row['category_id'], 'unit_id': row['unit_id'],
                                      'serialized_value': row['serialized_value'],
                                      'track_expiry_value': row['track_expiry_value']})
                    continue
                product = form.save(commit=False)
                product = save_purchase_product(product, organization=organization, supplied_sku=row['sku'])
                audit(organization=organization, actor=actor, action='inventory.product.created_purchase_import',
                      obj=product, new_value={'sku': product.sku, 'staging_reference': reference})
                created[reference] = product
            product = created[reference]
        else:
            product = Product.objects.select_for_update().get(tenant=organization, pk=row['product_id'])
            if not (product.is_active and product.track_stock and product.item_type == Product.ItemType.PHYSICAL):
                raise ImportInputError(f"Import row {row['row']}: catalog product changed. Review the batch again.")
        line = PurchaseLine(tenant=organization, purchase=purchase, product=product,
                            quantity=Decimal(row['quantity']), unit_cost=Decimal(row['unit_cost']),
                            batch_reference=row['batch_reference'], expiry_date=row['expiry_date'] or None,
                            serial_numbers=row['serial_numbers'])
        DecimalValidator(16, 6)(line.quantity)
        DecimalValidator(16, 6)(line.unit_cost)
        line.save()
    if proposals:
        PurchaseProductProposal.objects.create(
            organization=organization, tenant=organization, purchase=purchase, rows=proposals,
        )
    audit(organization=organization, actor=actor, action='inventory.purchase.import_applied', obj=purchase,
          new_value={'rows': len(included), 'new_products': len(created)})
    return len(included)
