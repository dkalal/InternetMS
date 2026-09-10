from __future__ import annotations

from decimal import Decimal

from users.permissions import PermissionCode, has_tenant_permission


COST_PRECISION = Decimal('0.000001')


class BelowCostError(ValueError):
    pass


def cost_floor_for(product) -> Decimal:
    """Return the authoritative base-unit sale cost without mutating inventory."""
    from inventory.models import InventoryBalance, StockMovement

    balance = InventoryBalance.objects.unscoped().filter(
        tenant_id=product.tenant_id, product_id=product.pk, quantity__gt=0,
    ).first() if product.pk else None
    if balance is not None and StockMovement.objects.unscoped().filter(
        tenant_id=product.tenant_id, product_id=product.pk,
    ).exists():
        return Decimal(balance.average_cost).quantize(COST_PRECISION)
    return Decimal(product.buying_price or 0).quantize(COST_PRECISION)


def can_view_cost(*, actor, organization) -> bool:
    return bool(actor and has_tenant_permission(actor, organization, PermissionCode.COST_REPORT_VIEW))


def below_cost_message(*, product, net_unit_price: Decimal, actor=None, organization=None) -> str:
    if organization is not None and can_view_cost(actor=actor, organization=organization):
        unit = product.get_measure_unit_display()
        return (
            f'Net selling price TZS {net_unit_price:,.6f} per {unit} must be greater than '
            f'the current cost of TZS {cost_floor_for(product):,.6f} per {unit}.'
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
