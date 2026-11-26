from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError

from users.permissions import PermissionCode, has_tenant_permission


COST_PRECISION = Decimal('0.000001')


class BelowCostError(ValueError):
    pass


def cost_floor_for(product) -> Decimal:
    """Return the authoritative same-sales-unit cost without mutating inventory."""
    return cost_floor_details(product)[0]


def cost_floor_details(product) -> tuple[Decimal, bool]:
    """Return the cost floor and whether positive movement-backed stock sets it."""
    from inventory.models import InventoryBalance, StockMovement

    balance = InventoryBalance.objects.unscoped().filter(
        tenant_id=product.tenant_id, product_id=product.pk, quantity__gt=0,
    ).first() if product.pk else None
    if balance is not None and StockMovement.objects.unscoped().filter(
        tenant_id=product.tenant_id, product_id=product.pk,
    ).exists():
        return Decimal(balance.average_cost).quantize(COST_PRECISION), True
    return Decimal(product.buying_price or 0).quantize(COST_PRECISION), False


def can_view_cost(*, actor, organization) -> bool:
    return bool(actor and has_tenant_permission(actor, organization, PermissionCode.COST_REPORT_VIEW))


def below_cost_message(*, product, net_unit_price: Decimal, actor=None, organization=None) -> str:
    if organization is not None and can_view_cost(actor=actor, organization=organization):
        unit = product.get_measure_unit_display()
        return (
            f'Net selling price TZS {net_unit_price:,.2f} per {unit} must be greater than '
            f'the current cost of TZS {cost_floor_for(product):,.2f} per {unit}.'
        )
    return 'This price is below the minimum allowed selling price. Ask an authorized manager to review the product pricing.'


def validate_net_unit_price(*, product, quantity, unit_price, line_discount=Decimal('0'),
                            allocated_document_discount=Decimal('0'), actor=None, organization=None):
    quantity = Decimal(quantity or 0)
    if quantity <= 0:
        raise BelowCostError('Sale quantity must be greater than zero.')
    net = (
        quantity * Decimal(unit_price or 0)
        - Decimal(line_discount or 0)
        - Decimal(allocated_document_discount or 0)
    ) / quantity
    floor = cost_floor_for(product)
    if net <= floor:
        raise BelowCostError(below_cost_message(
            product=product, net_unit_price=net, actor=actor, organization=organization,
        ))
    return net


def underpriced_categories(product) -> list[str]:
    floor = cost_floor_for(product)
    prices = [('Standard', product.selling_price), ('Technician', product.effective_technician_price)]
    if product.allow_wholesale and product.wholesale_price is not None:
        prices.append(('Wholesale', product.wholesale_price))
    return [label for label, price in prices if price is not None and Decimal(price) <= floor]


@transaction.atomic
def update_standard_selling_price(*, product_id, organization, price, actor, membership=None) -> Decimal:
    """Update only a tenant product's current standard price, preserving purchase history."""
    from .models import Product

    if not has_tenant_permission(actor, organization, PermissionCode.PRODUCTS_UPDATE, membership=membership):
        raise ValidationError('You do not have permission to change product prices.')
    product = Product.objects.select_for_update().filter(tenant=organization).get(pk=product_id)
    price = Decimal(price).quantize(Decimal('0.01'))
    floor = cost_floor_for(product)
    if price <= floor:
        raise ValidationError('Standard selling price must be greater than the current cost per sales unit.')
    previous = product.selling_price
    if previous == price:
        return price
    product.selling_price = price
    product.save(update_fields=['selling_price'])
    from inventory.services import audit
    audit(
        organization=organization, actor=actor, action='inventory.product.standard_price_updated',
        obj=product, old_value={'selling_price': str(previous)},
        new_value={'selling_price': str(price)},
    )
    return price
