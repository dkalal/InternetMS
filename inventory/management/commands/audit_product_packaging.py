"""Read-only, explicitly tenant-scoped packaging reconciliation preflight."""
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, Sum
from products.models import Product, ProductPackaging
from products.packaging_snapshots import validate_snapshot
from django.core.exceptions import ValidationError
from users.models import Organization
from inventory.models import ContainerAllocation, ContainerStockEvent, InventoryBalance, StockContainer


class Command(BaseCommand):
    help = 'Read-only base/container and posted allocation audit for one explicit tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Exact tenant slug; never inferred from a browser session')
        parser.add_argument('--fail-on-drift', action='store_true')

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options['tenant']).first()
        if organization is None:
            raise CommandError('Tenant not found.')
        checked = 0
        failures = []
        products = Product.objects.unscoped().filter(tenant=organization).filter(
            Q(packaging_enabled=True) | Q(packaging_definitions__isnull=False) | Q(stock_containers__isnull=False)).distinct().order_by('pk')
        for product in products.iterator(chunk_size=500):
            checked += 1
            # Authorized-product dependency probes intentionally detect corrupt foreign owners.
            if StockContainer.objects.unscoped().filter(product=product).exclude(tenant=organization, organization=organization).exists():
                failures.append((product.pk, 'foreign_container_owner'))
            if ProductPackaging.objects.unscoped().filter(product=product).exclude(tenant=organization, unit__tenant=organization).exists():
                failures.append((product.pk, 'foreign_packaging_owner'))
            if not product.packaging_enabled:
                continue
            balance = InventoryBalance.objects.unscoped().filter(product=product, tenant=organization, organization=organization).first()
            physical = StockContainer.objects.unscoped().filter(tenant=organization, organization=organization, product=product).aggregate(qty=Sum('remaining_quantity'))['qty'] or Decimal('0')
            quantity = balance.quantity if balance else Decimal(product.quantity)
            average = Decimal(balance.average_cost) if balance else Decimal(product.buying_price)
            if not average.is_finite() or average < 0:
                failures.append((product.pk, 'invalid_inventory_valuation'))
            if quantity != physical or quantity != product.quantity or quantity != Decimal(product.stock):
                failures.append((product.pk, 'base_container_catalog_quantity_difference'))
        allocations = ContainerAllocation.objects.unscoped().filter(tenant=organization).select_related(
            'container', 'billing_line__product', 'billing_line__packaging', 'movement').order_by('pk')
        movement_totals = {}
        for allocation in allocations.iterator(chunk_size=500):
            line = allocation.billing_line
            try:
                validate_snapshot(line)
                if any(obj.tenant_id != organization.pk for obj in (allocation.container, line, allocation.movement)):
                    raise ValidationError('Foreign allocation relationship.')
                if any(obj.product_id != allocation.product_id for obj in (allocation.container, line, allocation.movement)):
                    raise ValidationError('Foreign allocation product.')
                if allocation.movement.billing_line_id != line.pk or allocation.movement.quantity >= 0:
                    raise ValidationError('Invalid stock-out movement.')
            except ValidationError:
                failures.append((allocation.product_id, 'invalid_posted_allocation'))
            entry = movement_totals.setdefault(allocation.movement_id, [Decimal('0'), Decimal('0'), allocation.movement, line])
            entry[0] += allocation.base_quantity
            entry[1] += allocation.cost_total
        for quantity, cost, movement, line in movement_totals.values():
            if quantity != -movement.quantity or quantity != line.base_quantity or cost != getattr(getattr(line, 'inventory_sale_line', None), 'cost_total', None):
                failures.append((line.product_id, 'posted_allocation_quantity_or_cost_difference'))
        for allocation in allocations.filter(return_events__isnull=False).distinct().iterator(chunk_size=500):
            totals = ContainerStockEvent.objects.unscoped().filter(tenant=organization, source_allocation=allocation, kind='return').aggregate(qty=Sum('base_quantity'), cost=Sum('cost_total'))
            if totals['qty'] > allocation.base_quantity or totals['cost'] > allocation.cost_total:
                failures.append((allocation.product_id, 'over_returned_allocation'))
        self.stdout.write(f'products_checked={checked} packaging_differences={len(failures)}')
        for product_id, reason in failures:
            self.stdout.write(f'product={product_id} reason={reason}')
        if failures and options['fail_on_drift']:
            raise CommandError('Packaging differences require investigation; no records were changed.')
