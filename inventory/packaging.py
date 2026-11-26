"""Audited activation reconciles physical counts without posting stock or value."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from products import packaging as policy
from products.models import Product, ProductPackaging
from .models import (CartLine, InventoryBalance, PackagingActivation, StockContainer)


def _open_documents(product):
    from billing.models import BillingLineItem
    if CartLine.objects.unscoped().filter(product=product, cart__status='draft').exists():
        raise policy.PackagingError('Resolve open carts before changing packaging activation.')
    if BillingLineItem.objects.unscoped().filter(product=product).filter(
        document__document_type__in=['invoice', 'quotation'],
        document__status__in=['draft', 'sent', 'issued', 'partially_paid', 'accepted', 'approved'],
    ).exists():
        raise policy.PackagingError('Resolve active quotations and unpaid invoices before changing packaging activation.')


def _balance(product):
    balance = InventoryBalance.objects.unscoped().select_for_update().filter(product=product).first()
    if balance and (balance.tenant_id != product.tenant_id or balance.organization_id != product.tenant_id):
        raise PermissionDenied('Inventory balance belongs to another tenant.')
    quantity = balance.quantity if balance else Decimal(product.quantity)
    average = balance.average_cost if balance else Decimal(product.buying_price)
    policy.whole_quantity(quantity, allow_zero=True)
    if balance is None and quantity:
        raise policy.PackagingError('Initialize and audit the existing aggregate inventory balance before mapping physical containers.')
    if Decimal(product.quantity) != quantity or Decimal(product.stock) != quantity:
        raise policy.PackagingError('Reconcile inconsistent catalog and aggregate stock before activation.')
    return balance, quantity, average


class PackagingActivationService:
    @staticmethod
    @transaction.atomic
    def change(*, organization, actor, product_id, enabled, request_key, reason,
               expected_quantity, expected_average_cost, expected_version, containers, support_session=None):
        policy.authorize(organization=organization, actor=actor, stock=True, support_session=support_session)
        type(organization).objects.select_for_update(no_key=True).get(pk=organization.pk)
        product = policy.locked_product(organization=organization, product_id=product_id)
        if type(enabled) is not bool or not isinstance(containers, list) or len(containers) > 2000:
            raise policy.PackagingError('Supply a boolean activation state and at most 2,000 counted containers.')
        reason = str(reason or '').strip()
        if not reason or len(reason) > 500:
            raise policy.PackagingError('A reconciliation reason of at most 500 characters is required.')
        try:
            request_key = UUID(str(request_key))
            expected_average_cost = Decimal(str(expected_average_cost))
            if not expected_average_cost.is_finite() or expected_average_cost < 0 or expected_average_cost >= Decimal("10000000000") or expected_average_cost.as_tuple().exponent < -6:
                raise InvalidOperation
        except (ValueError, TypeError, InvalidOperation):
            raise policy.PackagingError('Supply a valid retry key and six-decimal acquisition cost.')
        expected_quantity = policy.whole_quantity(expected_quantity, allow_zero=True)
        expected_average_cost = expected_average_cost.quantize(Decimal('0.000001'))
        expected_version = policy.identifier(expected_version, label='Configuration version')
        try:
            canonical = json.dumps({'product': product.pk, 'enabled': enabled, 'reason': reason, 'containers': containers, 'version': expected_version,
                                    'quantity': str(expected_quantity), 'cost': str(expected_average_cost)}, sort_keys=True)
        except (ValueError, TypeError):
            raise policy.PackagingError('Container counts must contain plain JSON-compatible values.')
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        prior = PackagingActivation.objects.unscoped().filter(tenant=organization, request_key=request_key).first()
        if prior:
            if prior.fingerprint != fingerprint:
                raise policy.PackagingError('This retry key was already used for different reconciliation content.')
            return prior
        if product.packaging_version != expected_version:
            raise policy.PackagingError('Packaging configuration changed. Review the count with the current version.')
        if not policy.workflows_ready():
            raise policy.PackagingError('Packaging activation is unavailable until receiving and sales phases are verified.')
        if product.packaging_enabled == enabled:
            raise policy.PackagingError('The product already has the requested packaging activation state.')
        _open_documents(product)
        balance, quantity, average = _balance(product)
        if quantity != expected_quantity or average != expected_average_cost:
            raise policy.PackagingError('Stock or valuation changed. Review the current count and retry with a new key.')
        if not enabled:
            if quantity or containers or StockContainer.objects.unscoped().filter(product=product, remaining_quantity__gt=0).exists():
                raise policy.PackagingError('Packaging can be disabled only with zero stock, no remaining containers and no open documents.')
            rows = []
        else:
            if StockContainer.objects.unscoped().filter(product=product, remaining_quantity__gt=0).exists():
                raise policy.PackagingError('Existing positive container balances require explicit review before activation.')
            rows = []
            total = Decimal('0')
            for row in containers:
                if not isinstance(row, dict) or set(row) - {'quantity', 'packaging_id', 'sealed', 'supplier_identifier', 'expiry_date'}:
                    raise policy.PackagingError('Use quantity, packaging_id, sealed and optional supplier identifier for each count.')
                amount = policy.whole_quantity(row.get('quantity'))
                sealed = row.get('sealed', False)
                if type(sealed) is not bool:
                    raise policy.PackagingError('A container sealed state must be a boolean.')
                package = None
                if row.get('packaging_id') is not None:
                    package = ProductPackaging.objects.unscoped().filter(
                        pk=policy.identifier(row['packaging_id'], label='Counted packaging'), tenant=organization, product=product, is_active=True).first()
                    if package is None:
                        raise PermissionDenied('Counted packaging is unavailable for this tenant/product.')
                if sealed and (package is None or amount != package.factor):
                    raise policy.PackagingError('A sealed container must match its packaging capacity exactly.')
                capacity = package.factor if package else amount
                if amount > capacity:
                    raise policy.PackagingError('Counted contents cannot exceed container capacity.')
                identifier = str(row.get('supplier_identifier') or '').strip()
                if len(identifier) > 100:
                    raise policy.PackagingError('Supplier identifier must be at most 100 characters.')
                from datetime import date
                try:
                    expiry = date.fromisoformat(row['expiry_date']) if row.get('expiry_date') else None
                except (ValueError, TypeError):
                    raise policy.PackagingError('Container expiry must be an ISO date.')
                if product.track_expiry and expiry is None:
                    raise policy.PackagingError('Supply the counted expiry date for every expiry-tracked container.')
                rows.append((package, amount, capacity, sealed, identifier, expiry))
                total += amount
            if total != quantity:
                raise policy.PackagingError('Counted container contents must equal the existing base stock exactly; no stock conversion is performed.')
            if not ProductPackaging.objects.unscoped().filter(tenant=organization, product=product, is_active=True).exists():
                raise policy.PackagingError('Configure at least one active packaging definition before activation.')
        from .services import audit, money
        event = PackagingActivation.objects.create(organization=organization, tenant=organization, product=product,
            request_key=request_key, fingerprint=fingerprint, enabled=enabled, version=product.packaging_version + 1,
            reason=reason, base_quantity=quantity, average_cost=average, inventory_value=money(quantity * average), created_by=actor)
        now = timezone.now()
        for package, amount, capacity, sealed, identifier, expiry in rows:
            StockContainer.objects.create(organization=organization, tenant=organization, product=product,
                packaging=package, activation=event, capacity=capacity, original_quantity=amount, remaining_quantity=amount,
                state=StockContainer.State.SEALED if sealed else StockContainer.State.OPENED,
                received_at=now, opened_at=None if sealed else now, supplier_identifier=identifier, expiry_date=expiry)
        product.packaging_enabled = enabled
        product.packaging_version = event.version
        product._packaging_service_update = True
        product.save(update_fields=['packaging_enabled', 'packaging_version'])
        audit(organization=organization, actor=actor, action='inventory.packaging.activation_changed', obj=event,
              new_value={'product_id': product.pk, 'enabled': enabled, 'base_quantity': str(quantity), 'container_count': len(rows)},
              metadata={'request_key': str(request_key), 'reason': reason, 'quantity_changed': False, 'value_changed': False})
        return event
