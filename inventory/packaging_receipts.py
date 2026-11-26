"""Commercial purchase entry and physical receipt provenance; no legacy conversion fields."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied
from django.core.validators import DecimalValidator
from django.utils import timezone
from products.models import Product, ProductPackaging
from products.packaging import PackagingError, identifier, whole_quantity
from .models import StockContainer


def purchase_snapshot(*, organization, product, packaging_id, quantity, unit_cost, version):
    if product.tenant_id != organization.pk or product.organization_id != organization.pk:
        raise PermissionDenied('Product unavailable in the active tenant.')
    if not product.packaging_enabled or product.is_serialized or not product.track_stock or product.item_type != 'physical':
        raise PackagingError('Commercial receiving requires an activated non-serialized stock product.')
    if not product.sales_unit_id or product.sales_unit.tenant_id != organization.pk or not product.sales_unit.is_active:
        raise PackagingError('The base unit is unavailable in this tenant.')
    if str(version) != str(product.packaging_version):
        raise PackagingError('Packaging configuration changed. Review the entered unit and cost.')
    package = None
    if packaging_id not in (None, ''):
        package = ProductPackaging.objects.unscoped().select_related('unit').filter(
            pk=identifier(packaging_id), tenant=organization, product=product, is_active=True, unit__is_active=True, unit__tenant=organization).first()
        if package is None:
            raise PermissionDenied('Purchase packaging unavailable in this tenant/product.')
    factor = package.factor if package else Decimal('1')
    entered = whole_quantity(quantity)
    base = whole_quantity(entered * factor, label='Base quantity')
    try:
        entered_cost = Decimal(str(unit_cost))
        if not entered_cost.is_finite() or entered_cost < 0 or entered_cost.as_tuple().exponent < -6:
            raise InvalidOperation
        DecimalValidator(16, 6)(entered_cost)
    except (InvalidOperation, ValueError, TypeError):
        raise PackagingError('Enter a nonnegative cost with at most six decimal places.')
    total = (entered * entered_cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    DecimalValidator(14, 2)(total)
    return dict(packaging=package, packaging_version=product.packaging_version, unit_factor=factor,
        entered_quantity=entered, entered_unit_cost=entered_cost, commercial_total=total,
        selected_unit_snapshot=package.unit.label if package else product.get_measure_unit_display(),
        quantity=base, unit_cost=(entered_cost / factor).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP))


def validate_receipt_line(*, organization, line, product):
    expected = purchase_snapshot(organization=organization, product=product, packaging_id=line.packaging_id,
        quantity=line.entered_quantity, unit_cost=line.entered_unit_cost, version=line.packaging_version)
    for name, value in expected.items():
        if name == 'packaging':
            continue
        if getattr(line, name) != value:
            raise PackagingError('Purchase unit/cost snapshot changed. Edit and review the draft before receiving.')
    count = int(line.entered_quantity) if line.packaging_id else 1
    if count > 2000:
        raise PackagingError('Receive at most 2,000 physical containers in one purchase.')
    return count


def create_receipt_containers(*, organization, line, product):
    now = timezone.now()
    count = int(line.entered_quantity) if line.packaging_id else 1
    amount = line.unit_factor if line.packaging_id else line.quantity
    containers = [StockContainer(tenant=organization, organization=organization, product=product,
        packaging_id=line.packaging_id, purchase_line=line, capacity=amount, original_quantity=amount,
        remaining_quantity=amount, state='sealed' if line.packaging_id else 'opened', received_at=now,
        opened_at=None if line.packaging_id else now, expiry_date=line.expiry_date) for _ in range(count)]
    StockContainer.objects.bulk_create(containers)


def packaging_options(product):
    if not product.packaging_enabled:
        return []
    packages = getattr(product, 'available_purchase_packages', None)
    if packages is None:
        packages = product.packaging_definitions.filter(tenant_id=product.tenant_id, is_active=True,
            unit__is_active=True, unit__tenant_id=product.tenant_id).select_related('unit').order_by('pk')
    return [{'id': package.pk, 'label': f'{package.unit.label} ({format(package.factor.normalize(), 'f')} {product.get_measure_unit_display()})',
             'factor': str(package.factor)} for package in packages]
