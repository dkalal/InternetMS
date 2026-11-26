"""Shared commercial/base unit contract used by owning transaction models."""
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Floor


def sales_constraints(prefix):
    absent = Q(packaging__isnull=True, packaging_version__isnull=True, unit_factor__isnull=True, base_quantity__isnull=True)
    present = Q(packaging_version__gt=0, packaging_version__isnull=False, unit_factor__gt=0, unit_factor__isnull=False,
                base_quantity__gt=0, base_quantity__lt=10000000000, unit_factor__lt=10000000000, base_quantity__isnull=False, quantity__gt=0,
                quantity=Floor('quantity'), unit_factor=Floor('unit_factor'), base_quantity=F('quantity') * F('unit_factor'))
    if prefix == 'cart':
        absent &= Q(selected_unit_snapshot__isnull=True)
        present &= Q(selected_unit_snapshot__isnull=False) & ~Q(selected_unit_snapshot='')
    return [models.CheckConstraint(condition=absent | present, name=prefix + '_commercial_snapshot'),
            models.CheckConstraint(condition=Q(unit_factor__isnull=True) | Q(packaging__isnull=False) | Q(unit_factor=1), name=prefix + '_base_unit_factor')]


def purchase_constraints():
    names = ('packaging_version', 'unit_factor', 'entered_quantity', 'entered_unit_cost', 'commercial_total', 'selected_unit_snapshot')
    absent = Q(packaging__isnull=True, **{n + '__isnull': True for n in names})
    present = Q(**{n + '__isnull': False for n in names}) & Q(
        packaging_version__gt=0, unit_factor__gt=0, unit_factor__lt=10000000000, entered_quantity__gt=0, quantity__lt=10000000000,
        entered_unit_cost__gte=0, entered_unit_cost__lt=10000000000, commercial_total__gte=0, commercial_total__lt=100000000000000,
        entered_quantity=Floor('entered_quantity'), unit_factor=Floor('unit_factor'), quantity=F('entered_quantity') * F('unit_factor')) & ~Q(selected_unit_snapshot='')
    return [models.CheckConstraint(condition=absent | present, name='purchase_commercial_snapshot'),
            models.CheckConstraint(condition=Q(unit_factor__isnull=True) | Q(packaging__isnull=False) | Q(unit_factor=1), name='purchase_base_unit_factor')]


def validate_snapshot(line, *, purchase=False):
    fields = ['packaging_version', 'unit_factor'] + (['entered_quantity', 'entered_unit_cost', 'commercial_total', 'selected_unit_snapshot']
                                                   if purchase else ['base_quantity'])
    values = [getattr(line, name) for name in fields]
    if line.product_id and line.product.packaging_enabled:
        from .packaging import workflows_ready
        if not workflows_ready():
            raise ValidationError('Packaging transactions are unavailable until receiving and sales phases are verified.')
    if all(value is None for value in values) and not line.packaging_id:
        if line.product_id and line.product.packaging_enabled:
            raise ValidationError('Activated products require an explicit commercial unit snapshot.')
        return
    if any(value is None for value in values) or not line.product_id:
        raise ValidationError('A commercial unit snapshot must be complete and refer to a product.')
    from .packaging import whole_quantity
    factor = whole_quantity(line.unit_factor, label='Factor')
    entered = whole_quantity(line.entered_quantity if purchase else line.quantity)
    base = entered * factor
    if base >= Decimal('10000000000') or base != (line.quantity if purchase else line.base_quantity):
        raise ValidationError('Commercial quantity and factor must equal the snapshotted base quantity.')
    if line.product.tenant_id != line.tenant_id:
        raise ValidationError('Snapshot product belongs to another tenant.')
    if line.packaging_id:
        package = line.packaging
        if package.tenant_id != line.tenant_id or package.product_id != line.product_id or package.factor != factor:
            raise ValidationError('Packaging must match the snapshot product, tenant and factor.')
    elif factor != 1:
        raise ValidationError('A base-unit snapshot must have factor one.')
    label = getattr(line, 'selected_unit_snapshot', None) if purchase or hasattr(line, 'selected_unit_snapshot') else line.unit_snapshot
    if not (label or '').strip():
        raise ValidationError('The selected commercial unit label is required.')
    if purchase:
        if line.entered_unit_cost < 0:
            raise ValidationError('Purchase costs cannot be negative.')
        total = (entered * line.entered_unit_cost).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if total != line.commercial_total:
            raise ValidationError('Commercial purchase total does not match entered quantity and cost.')
