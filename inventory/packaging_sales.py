"""Commercial sale snapshots and physical selections; selections never reserve stock."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from products.packaging import PackagingError, identifier, whole_quantity
from products.packaging_models import ProductPackaging
from .models import ContainerAllocation, ProvisionalContainerAllocation, StockContainer


def sale_snapshot(*, organization, product, packaging_id, quantity, version):
    from products.packaging import workflows_ready
    if not workflows_ready():
        raise PackagingError('Packaging sales are unavailable until all workflows are verified.')
    if (product.tenant_id != organization.pk or product.organization_id != organization.pk
            or not product.packaging_enabled or not product.is_active or product.is_serialized
            or not product.track_stock or product.item_type != 'physical'):
        raise PackagingError('Choose an active packaging-enabled stock product in this tenant.')
    if not product.sales_unit_id or not product.sales_unit.is_active or product.sales_unit.tenant_id != organization.pk:
        raise PackagingError('The base unit is unavailable.')
    if identifier(version) != product.packaging_version:
        raise PackagingError('Product configuration changed. Review the selected unit and physical pieces again.')
    package = None
    if packaging_id not in (None, ''):
        package = ProductPackaging.objects.unscoped().select_related('unit', 'product').filter(
            tenant=organization, product=product, pk=identifier(packaging_id), is_active=True,
            unit__is_active=True, unit__tenant=organization,
        ).first()
        if package is None:
            raise PackagingError('Choose an active package belonging to this product and tenant.')
    quantity = whole_quantity(quantity)
    factor = package.factor if package else Decimal('1')
    base = whole_quantity(quantity * factor, label='Base quantity')
    return dict(packaging=package, packaging_version=product.packaging_version, unit_factor=factor,
                base_quantity=base, selected_unit_snapshot=package.unit.label if package else product.sales_unit.label)


def validate_sale_line(*, organization, line):
    values = sale_snapshot(organization=organization, product=line.product, packaging_id=line.packaging_id,
                           quantity=line.quantity, version=line.packaging_version)
    label = getattr(line, 'selected_unit_snapshot', None) or getattr(line, 'unit_snapshot', '')
    for name in ('unit_factor', 'base_quantity', 'packaging_version'):
        if getattr(line, name) != values[name]:
            raise PackagingError('Commercial sale snapshot is inconsistent. Review the document.')
    if label != values['selected_unit_snapshot']:
        raise PackagingError('Selected unit changed. Review the document.')
    return values


def eligible_containers(*, organization, line):
    qs = StockContainer.objects.unscoped().filter(
        tenant=organization, organization=organization, product_id=line.product_id, remaining_quantity__gt=0,
    ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=date.today()))
    if line.packaging_id:
        qs = qs.filter(state='sealed', packaging_id=line.packaging_id, capacity=line.unit_factor,
                       remaining_quantity=line.unit_factor, opened_at__isnull=True, returned_allocation__isnull=True)
    else:
        qs = qs.filter(state__in=['opened', 'returned_piece', 'returned_full', 'sealed'])
    return qs.annotate(piece_order=Case(When(state='sealed', then=Value(1)), default=Value(0), output_field=IntegerField())).order_by('piece_order', 'received_at', 'pk')


def suggest(*, organization, line, used=None):
    used = used or {}
    remaining = line.base_quantity
    result = []
    # Bounded physical review; do not enumerate a growing stock catalog.
    for container in eligible_containers(organization=organization, line=line)[:2000]:
        available = container.remaining_quantity - used.get(container.pk, Decimal('0'))
        if available <= 0 or (line.packaging_id and available != line.unit_factor):
            continue
        quantity = min(remaining, available)
        result.append((container.pk, quantity))
        remaining -= quantity
        if remaining == 0:
            return result
    raise PackagingError('Insufficient eligible physical pieces. Review stock and container selections.')


def validate_selection(*, organization, line, selections, used=None, locked=None):
    validate_sale_line(organization=organization, line=line)
    used = used or {}
    if not selections or len(selections) > 2000:
        raise PackagingError('Review and select physical pieces before payment; at most 2,000 pieces per line.')
    eligible = {c.pk: c for c in eligible_containers(organization=organization, line=line).filter(pk__in=[pk for pk, _ in selections])}
    seen = set()
    total = Decimal('0')
    result = []
    for raw_id, raw_qty in selections:
        pk = identifier(raw_id)
        quantity = whole_quantity(raw_qty)
        container = (locked or eligible).get(pk)
        if pk in seen or pk not in eligible or container is None:
            raise PackagingError('A selected physical piece is unavailable. Review selections before payment.')
        if quantity + used.get(pk, Decimal('0')) > container.remaining_quantity:
            raise PackagingError('A selected piece no longer has the required quantity. Review selections.')
        if line.packaging_id and quantity != line.unit_factor:
            raise PackagingError('A whole package requires one intact sealed container; separate pieces cannot form a package.')
        seen.add(pk)
        total += quantity
        result.append((container, quantity))
    if total != line.base_quantity:
        raise PackagingError('Selected pieces must equal the exact base quantity on the line.')
    return result


def replace_selections(*, organization, line, selections, used=None):
    pairs = validate_selection(organization=organization, line=line, selections=selections, used=used)
    parent = {'cart_line': line} if hasattr(line, 'cart_id') else {'billing_line': line}
    # Caller holds the parent lock and has checked canonical object access.
    ProvisionalContainerAllocation.objects.unscoped().filter(tenant=organization, **parent).delete()
    for container, quantity in pairs:
        ProvisionalContainerAllocation.objects.create(tenant=organization, organization=organization,
            container=container, base_quantity=quantity, **parent)
    return pairs


def selections_for(line):
    return list(line.container_selections.order_by('container_id').values_list('container_id', 'base_quantity'))


def post_allocations(*, organization, line, movement, pairs, cost_total, unit_cost):
    allocated_cost = Decimal('0')
    allocated_quantity = Decimal('0')
    for index, (container, quantity) in enumerate(pairs):
        allocated_quantity += quantity
        cumulative_cost = (cost_total if index == len(pairs) - 1 else
            (cost_total * allocated_quantity / line.base_quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        share = cumulative_cost - allocated_cost
        allocated_cost += share
        container.remaining_quantity -= quantity
        if not container.remaining_quantity:
            container.state = StockContainer.State.DEPLETED
        elif container.state == StockContainer.State.SEALED:
            container.state = StockContainer.State.OPENED
            container.opened_at = timezone.now()
        container._container_service_update = True
        container.save(update_fields=['remaining_quantity', 'state', 'opened_at'])
        ContainerAllocation.objects.create(tenant=organization, organization=organization, product=line.product,
            container=container, billing_line=line, movement=movement, base_quantity=quantity,
            unit_cost=unit_cost, cost_total=share)


class PackagingSelectionService:
    @staticmethod
    def review_document(*, organization, actor, document_id, selections, membership=None, issue=False):
        from django.db import transaction
        from django.core.exceptions import PermissionDenied
        from billing.models import BillingDocument
        from users.permissions import PermissionCode, has_tenant_permission, can_view_sales_document
        from .services import audit
        with transaction.atomic():
            document = BillingDocument.objects.unscoped().select_for_update().filter(
                tenant=organization, organization=organization, pk=document_id, document_type='invoice',
                status__in=['draft', 'issued']).first()
            if document is None or not has_tenant_permission(actor, organization, PermissionCode.BILLING_CREATE, membership=membership) or not can_view_sales_document(actor, document, membership=membership):
                raise PermissionDenied('Invoice allocation review is unavailable.')
            lines = list(document.items.filter(unit_factor__isnull=False).select_related('product').order_by('product_id', 'pk'))
            if set(selections) != {line.pk for line in lines}:
                raise PackagingError('Review every commercial line on this invoice.')
            from products.models import Product
            products = {p.pk: p for p in Product.objects.unscoped().select_for_update().filter(
                tenant=organization, pk__in=[line.product_id for line in lines]).order_by('pk')}
            used = {}
            for line in lines:
                line.product = products[line.product_id]
                pairs = replace_selections(organization=organization, line=line, selections=selections[line.pk], used=used)
                for container, quantity in pairs:
                    used[container.pk] = used.get(container.pk, Decimal('0')) + quantity
            if issue:
                from billing.services import BillingService
                BillingService._issue_reviewed_packaging_invoice(organization=organization, actor=actor, invoice=document)
            audit(organization=organization, actor=actor, action='inventory.invoice.containers_reviewed', obj=document,
                metadata={'stock_changed': False, 'reservation': False})
            return document
