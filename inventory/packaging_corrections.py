"""Audited physical returns and wastage; never creates a monetary refund."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from products import packaging as policy
from .models import ContainerAllocation, ContainerStockEvent, StockAdjustment, StockContainer, StockMovement
from .services import InventoryService, audit, money, stock_quantity


def request_identity(*, request_key, reason, payload):
    try:
        key = UUID(str(request_key))
    except (ValueError, TypeError):
        raise policy.PackagingError('Supply a valid physical correction retry key.')
    reason = str(reason or '').strip()
    if not reason or len(reason) > 500:
        raise policy.PackagingError('A reason of at most 500 characters is required.')
    fingerprint = hashlib.sha256(json.dumps(dict(payload, reason=reason), sort_keys=True).encode()).hexdigest()
    return key, reason, fingerprint


def existing_event(*, organization, key, fingerprint):
    event = ContainerStockEvent.objects.unscoped().filter(tenant=organization, request_key=key).first()
    if event is not None and event.fingerprint != fingerprint:
        raise policy.PackagingError('This retry key was used for a different physical correction.')
    return event


def locked_stock(*, organization, actor, product_id, support_session):
    policy.authorize(organization=organization, actor=actor, stock=True, support_session=support_session)
    if not policy.workflows_ready():
        raise policy.PackagingError('Physical packaging corrections are unavailable until release gates pass.')
    product = policy.locked_product(organization=organization, product_id=product_id)
    _, balance = InventoryService._locked_product_and_balance(organization=organization,
        product_id=product.pk, packaging_receipt=True)
    return product, balance


def create_event(**values):
    try:
        return ContainerStockEvent.objects.create(**values)
    except IntegrityError as exc:
        # The owning outer transaction rolls back all balance/container writes.
        raise policy.PackagingError('This physical correction conflicts with another request. Review the retry key and quantities.') from exc


class ContainerCorrectionService:
    @staticmethod
    @transaction.atomic
    def return_stock(*, organization, actor, allocation_id, quantity, request_key, reason, support_session=None):
        allocation_id = policy.identifier(allocation_id)
        source = ContainerAllocation.objects.unscoped().filter(tenant=organization, organization=organization, pk=allocation_id).first()
        if source is None:
            raise policy.PackagingError('Sold allocation unavailable in this tenant.')
        product, balance = locked_stock(organization=organization, actor=actor, product_id=source.product_id, support_session=support_session)
        from users.permissions import can_view_sales_document
        membership = policy.authorize(organization=organization, actor=actor, stock=True, support_session=support_session)
        if not can_view_sales_document(actor, source.billing_line.document, membership=membership):
            raise PermissionDenied('Original invoice unavailable for physical return.')
        quantity = policy.whole_quantity(quantity)
        key, reason, fingerprint = request_identity(request_key=request_key, reason=reason,
            payload={'kind': 'return', 'allocation': source.pk, 'quantity': str(quantity)})
        previous = existing_event(organization=organization, key=key, fingerprint=fingerprint)
        if previous is not None:
            return previous
        if not product.packaging_enabled:
            raise policy.PackagingError('Choose an activated packaging stock product.')
        source = ContainerAllocation.objects.unscoped().select_for_update(of=('self',)).select_related('container', 'billing_line').get(pk=source.pk, tenant=organization)
        if (any(obj.tenant_id != organization.pk or obj.organization_id != organization.pk for obj in
                (source, source.container, source.billing_line, source.billing_line.document, source.movement))
                or source.container.tenant_id != organization.pk or source.billing_line.tenant_id != organization.pk
                or source.billing_line.document.tenant_id != organization.pk or source.movement.tenant_id != organization.pk
                or source.container.product_id != product.pk or source.billing_line.product_id != product.pk
                or source.movement.product_id != product.pk):
            raise policy.PackagingError('Sold allocation provenance is inconsistent.')
        totals = ContainerStockEvent.objects.unscoped().filter(tenant=organization, source_allocation=source, kind='return').aggregate(
            quantity=Sum('base_quantity'), cost=Sum('cost_total'))
        returned_quantity = totals['quantity'] or Decimal('0')
        returned_cost = totals['cost'] or Decimal('0')
        if returned_quantity + quantity > source.base_quantity:
            raise policy.PackagingError('Physical return exceeds the original sold quantity after previous returns.')
        cumulative_cost = money(source.cost_total * (returned_quantity + quantity) / source.base_quantity)
        restored_cost = cumulative_cost - returned_cost
        if restored_cost < 0 or cumulative_cost > source.cost_total:
            raise policy.PackagingError('Physical return cost exceeds the original allocation.')
        new_quantity = policy.whole_quantity(balance.quantity + quantity)
        new_average = ((balance.quantity * balance.average_cost + restored_cost) / new_quantity).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        balance.quantity = stock_quantity(new_quantity)
        balance.average_cost = new_average
        balance.save(update_fields=['quantity', 'average_cost', 'updated_at'])
        InventoryService._sync_legacy_stock(product, balance)
        original = source.container
        full = bool(source.billing_line.packaging_id and source.base_quantity == original.capacity
                    and quantity == source.base_quantity and not returned_quantity and original.opened_at is None
                    and original.returned_allocation_id is None)
        now = timezone.now()
        container = StockContainer.objects.create(tenant=organization, organization=organization, product=product,
            packaging=original.packaging if full else None, returned_allocation=source,
            capacity=quantity, original_quantity=quantity, remaining_quantity=quantity,
            state='returned_full' if full else 'returned_piece', received_at=now, opened_at=now,
            expiry_date=original.expiry_date)
        movement = StockMovement.objects.create(tenant=organization, organization=organization, product=product,
            movement_type='adjustment_in', quantity=quantity, balance_after=balance.quantity,
            unit_cost=(restored_cost / quantity).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP),
            billing_line=source.billing_line, created_by=actor)
        event = create_event(tenant=organization, organization=organization, product=product,
            kind='return', request_key=key, fingerprint=fingerprint, source_allocation=source, container=container,
            movement=movement, base_quantity=quantity, cost_total=restored_cost, reason=reason, created_by=actor)
        audit(organization=organization, actor=actor, action='inventory.container.returned', obj=event,
            new_value={'allocation_id': source.pk, 'container_id': container.pk, 'base_quantity': str(quantity)},
            metadata={'request_key': str(key), 'reason': reason, 'monetary_refund_created': False})
        return event

    @staticmethod
    @transaction.atomic
    def waste(*, organization, actor, container_id, quantity, request_key, reason, category='damaged', support_session=None):
        container_id = policy.identifier(container_id)
        source = StockContainer.objects.unscoped().filter(tenant=organization, organization=organization, pk=container_id).first()
        if source is None:
            raise policy.PackagingError('Physical container unavailable in this tenant.')
        product, balance = locked_stock(organization=organization, actor=actor, product_id=source.product_id, support_session=support_session)
        quantity = policy.whole_quantity(quantity)
        if category not in {'damaged', 'expired', 'lost'}:
            raise policy.PackagingError('Choose damaged, expired or lost stock.')
        key, reason, fingerprint = request_identity(request_key=request_key, reason=reason,
            payload={'kind': 'waste', 'container': source.pk, 'quantity': str(quantity), 'category': category})
        previous = existing_event(organization=organization, key=key, fingerprint=fingerprint)
        if previous is not None:
            return previous
        if not product.packaging_enabled:
            raise policy.PackagingError('Choose an activated packaging stock product.')
        container = StockContainer.objects.unscoped().select_for_update().get(pk=source.pk, tenant=organization)
        if quantity > container.remaining_quantity or quantity > balance.quantity:
            raise policy.PackagingError('Wastage exceeds the remaining physical quantity.')
        cost = money(balance.quantity * balance.average_cost) - money((balance.quantity - quantity) * balance.average_cost)
        container.remaining_quantity -= quantity
        if not container.remaining_quantity:
            container.state = 'written_off'
        elif container.state == 'sealed':
            container.state = 'opened'
            container.opened_at = timezone.now()
        container._container_service_update = True
        container.save(update_fields=['remaining_quantity', 'state', 'opened_at'])
        balance.quantity = stock_quantity(balance.quantity - quantity)
        balance.save(update_fields=['quantity', 'updated_at'])
        InventoryService._sync_legacy_stock(product, balance)
        adjustment = StockAdjustment.objects.create(tenant=organization, organization=organization, product=product,
            quantity_delta=-quantity, reason=category, notes=reason, created_by=actor)
        movement = StockMovement.objects.create(tenant=organization, organization=organization, product=product,
            movement_type='adjustment_out', quantity=-quantity, balance_after=balance.quantity,
            unit_cost=balance.average_cost, adjustment=adjustment, created_by=actor)
        event = create_event(tenant=organization, organization=organization, product=product,
            kind='waste', request_key=key, fingerprint=fingerprint, container=container, movement=movement,
            base_quantity=quantity, cost_total=cost, reason=reason, created_by=actor)
        audit(organization=organization, actor=actor, action='inventory.container.wasted', obj=event,
            new_value={'container_id': container.pk, 'base_quantity': str(quantity)},
            metadata={'request_key': str(key), 'reason': reason, 'category': category})
        return event
