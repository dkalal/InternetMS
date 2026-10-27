from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditLog
from billing.models import BillingDocument, BillingLineItem
from customers.models import Customer
from products.models import Product

from .models import (
    Cart,
    CartLine,
    CartSerialSelection,
    DocumentSerialSelection,
    InventoryBalance,
    InventorySale,
    InventorySaleLine,
    InventorySettings,
    Purchase,
    PurchaseLine,
    StockAdjustment,
    StockMovement,
    StockUnit,
)


MONEY = Decimal('0.01')
COST = Decimal('0.000001')
QUANTITY = Decimal('0.000001')


class InventoryError(Exception):
    pass


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(MONEY, rounding=ROUND_HALF_UP)


def cost(value) -> Decimal:
    return Decimal(value or 0).quantize(COST, rounding=ROUND_HALF_UP)


def stock_quantity(value) -> Decimal:
    return Decimal(value or 0).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def audit(*, organization, actor, action, obj, old_value=None, new_value=None, metadata=None):
    return AuditLog.objects.create(
        organization=organization,
        tenant=organization,
        actor=actor,
        performed_by=actor,
        action=action,
        action_type=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        document_id=str(obj.pk),
        old_value=old_value or {},
        new_value=new_value or {},
        metadata=metadata or {},
        performed_at=timezone.now(),
    )


class InventoryService:
    @classmethod
    def _locked_product_and_balance(cls, *, organization, product_id):
        product = Product.objects.unscoped().select_for_update().filter(pk=product_id).first()
        if product is None:
            raise InventoryError('Invalid product.')
        if product.tenant_id != organization.id:
            raise PermissionDenied('Cross-tenant inventory access denied.')
        legacy_quantity = max(Decimal(str(product.quantity or 0)), Decimal(str(product.stock or 0)), Decimal('0.00'))
        balance, created = InventoryBalance.objects.unscoped().get_or_create(
            product=product,
            defaults={
                'organization': organization,
                'tenant': organization,
                'quantity': legacy_quantity,
                'average_cost': cost(product.buying_price),
            },
        )
        if created and legacy_quantity > 0:
            StockMovement.objects.create(
                organization=organization,
                tenant=organization,
                product=product,
                movement_type=StockMovement.MovementType.OPENING,
                quantity=legacy_quantity,
                balance_after=legacy_quantity,
                unit_cost=cost(product.buying_price),
                created_by=None,
            )
        balance = InventoryBalance.objects.unscoped().select_for_update().get(pk=balance.pk)
        return product, balance

    @staticmethod
    def _sync_legacy_stock(product, balance):
        whole_stock = max(int(balance.quantity), 0)
        Product.objects.unscoped().filter(pk=product.pk).update(quantity=balance.quantity, stock=whole_stock)

    @classmethod
    @transaction.atomic
    def confirm_purchase(cls, *, organization, purchase_id: int, actor):
        purchase = (
            Purchase.objects.unscoped()
            .select_for_update()
            .select_related('supplier')
            .filter(pk=purchase_id)
            .first()
        )
        if purchase is None or purchase.tenant_id != organization.id:
            raise PermissionDenied('Purchase is not available in the active tenant.')
        if purchase.status == Purchase.Status.CONFIRMED:
            return purchase
        if purchase.status != Purchase.Status.DRAFT:
            raise InventoryError('Only draft purchases can be confirmed.')

        from .purchase_batch import apply_purchase_batch
        from .purchase_import import ImportInputError
        from users.permissions import membership_for
        proposals = list(purchase.product_proposals.select_for_update().filter(tenant=organization))
        if proposals:
            # Serialize tenant SKU allocation and revalidation with other imports.
            type(organization).objects.select_for_update().get(pk=organization.pk)
            try:
                apply_purchase_batch(
                    purchase=purchase, rows=[row for proposal in proposals for row in proposal.rows],
                    organization=organization, actor=actor,
                    membership=membership_for(actor, organization), receiving=True,
                )
            except (ImportInputError, ValidationError) as exc:
                raise InventoryError(f'Proposed products need review before receiving: {exc}') from exc
            purchase.product_proposals.filter(tenant=organization).delete()

        lines = list(purchase.lines.select_related('product').order_by('product_id', 'id'))
        if not lines:
            raise InventoryError('Add at least one purchase line before confirmation.')

        normalized_serials: dict[int, list[str]] = {}
        seen_serials: set[str] = set()
        for line in lines:
            product = line.product
            if product.tenant_id != organization.id:
                raise PermissionDenied('Cross-tenant purchase product denied.')
            if product.item_type == Product.ItemType.SERVICE or not product.track_stock:
                raise InventoryError(f'{product.name} is a service/non-stock item and cannot be received.')
            if line.quantity <= 0 or line.unit_cost < 0:
                raise InventoryError('Purchase quantities must be positive and costs cannot be negative.')
            serials = [serial.strip().upper() for serial in line.parsed_serial_numbers()]
            if product.is_serialized:
                if line.quantity != line.quantity.to_integral_value():
                    raise InventoryError(f'{product.name} requires a whole-number quantity.')
                if len(serials) != int(line.quantity):
                    raise InventoryError(f'{product.name} requires exactly {int(line.quantity)} serial numbers.')
                if len(set(serials)) != len(serials):
                    raise InventoryError(f'Duplicate serial numbers were entered for {product.name}.')
                duplicates_across_lines = seen_serials.intersection(serials)
                if duplicates_across_lines:
                    raise InventoryError('Duplicate serial numbers were entered across purchase lines.')
                seen_serials.update(serials)
            elif serials:
                raise InventoryError(f'{product.name} is quantity-based and must not have serial numbers.')
            if product.track_expiry and not line.expiry_date:
                raise InventoryError(f'{product.name} requires an expiry date.')
            normalized_serials[line.pk] = serials

        if seen_serials and StockUnit.objects.unscoped().filter(tenant=organization, serial_number__in=seen_serials).exists():
            raise InventoryError('One or more serial numbers have already been received.')

        total_cost = Decimal('0.00')
        for line in lines:
            product, balance = cls._locked_product_and_balance(organization=organization, product_id=line.product_id)
            old_quantity = balance.quantity
            incoming_cost = cost(line.unit_cost)
            new_quantity = stock_quantity(old_quantity + line.quantity)
            if new_quantity > 0:
                balance.average_cost = cost(
                    ((old_quantity * balance.average_cost) + line.line_total) / new_quantity
                )
            balance.quantity = new_quantity
            balance.save(update_fields=['quantity', 'average_cost', 'updated_at'])
            cls._sync_legacy_stock(product, balance)
            StockMovement.objects.create(
                organization=organization,
                tenant=organization,
                product=product,
                movement_type=StockMovement.MovementType.PURCHASE_IN,
                quantity=line.quantity,
                balance_after=balance.quantity,
                unit_cost=incoming_cost,
                purchase_line=line,
                batch_reference=line.batch_reference,
                expiry_date=line.expiry_date,
                created_by=actor,
            )
            for serial in normalized_serials[line.pk]:
                StockUnit.objects.create(
                    organization=organization,
                    tenant=organization,
                    product=product,
                    serial_number=serial,
                    status=StockUnit.Status.AVAILABLE,
                    unit_cost=incoming_cost,
                    batch_reference=line.batch_reference,
                    expiry_date=line.expiry_date,
                    received_purchase_line=line,
                )
            total_cost += line.line_total

        purchase.status = Purchase.Status.CONFIRMED
        purchase.total_cost = money(total_cost)
        purchase.confirmed_by = actor
        purchase.confirmed_at = timezone.now()
        purchase._service_update = True
        purchase.save(update_fields=['status', 'total_cost', 'confirmed_by', 'confirmed_at', 'updated_at'])
        audit(
            organization=organization,
            actor=actor,
            action='inventory.purchase.confirmed',
            obj=purchase,
            new_value={'status': purchase.status, 'total_cost': str(purchase.total_cost)},
            metadata={'line_count': len(lines)},
        )
        return purchase

    @classmethod
    @transaction.atomic
    def cancel_purchase(cls, *, organization, purchase_id: int, actor):
        """Cancel an unposted supplier receipt without ever touching stock."""
        purchase = Purchase.objects.unscoped().select_for_update().filter(pk=purchase_id).first()
        if purchase is None or purchase.tenant_id != organization.id:
            raise PermissionDenied('Purchase is not available in the active tenant.')
        if purchase.status == Purchase.Status.CANCELLED:
            return purchase
        if purchase.status != Purchase.Status.DRAFT:
            raise InventoryError('Only draft purchases can be cancelled. Confirmed stock receipts are immutable.')

        purchase.status = Purchase.Status.CANCELLED
        purchase.save(update_fields=['status', 'updated_at'])
        audit(
            organization=organization, actor=actor, action='inventory.purchase.cancelled', obj=purchase,
            old_value={'status': Purchase.Status.DRAFT}, new_value={'status': Purchase.Status.CANCELLED},
            metadata={'line_count': purchase.lines.count()},
        )
        return purchase

    @classmethod
    @transaction.atomic
    def adjust_stock(
        cls,
        *,
        organization,
        product_id: int,
        quantity_delta: Decimal,
        reason: str,
        actor,
        notes: str = '',
        serial_numbers: list[str] | None = None,
    ):
        quantity_delta = money(quantity_delta)
        if quantity_delta == 0:
            raise InventoryError('Adjustment quantity cannot be zero.')
        if reason not in StockAdjustment.Reason.values:
            raise InventoryError('A valid adjustment reason is required.')
        product, balance = cls._locked_product_and_balance(organization=organization, product_id=product_id)
        if product.item_type == Product.ItemType.SERVICE or not product.track_stock:
            raise InventoryError('Service/non-stock items cannot be adjusted.')
        new_quantity = stock_quantity(balance.quantity + quantity_delta)
        if new_quantity < 0:
            raise InventoryError('Adjustment would make stock negative.')

        serials = [value.strip().upper() for value in (serial_numbers or []) if value.strip()]
        locked_units = []
        if product.is_serialized:
            if abs(quantity_delta) != abs(quantity_delta).to_integral_value():
                raise InventoryError('Serialized adjustments require whole units.')
            if len(serials) != int(abs(quantity_delta)) or len(serials) != len(set(serials)):
                raise InventoryError('Provide one unique serial number for each adjusted serialized unit.')
            if quantity_delta > 0:
                if StockUnit.objects.unscoped().filter(tenant=organization, serial_number__in=serials).exists():
                    raise InventoryError('A serial number has already been received.')
            else:
                locked_units = list(
                    StockUnit.objects.unscoped()
                    .select_for_update()
                    .filter(tenant=organization, product=product, serial_number__in=serials, status=StockUnit.Status.AVAILABLE)
                )
                if len(locked_units) != len(serials):
                    raise InventoryError('Every removed serial must be available for this product.')

        adjustment = StockAdjustment.objects.create(
            organization=organization,
            tenant=organization,
            product=product,
            quantity_delta=quantity_delta,
            reason=reason,
            notes=notes,
            serial_numbers=serials,
            created_by=actor,
        )
        balance.quantity = new_quantity
        if quantity_delta > 0 and balance.quantity == quantity_delta:
            balance.average_cost = cost(product.buying_price)
        balance.save(update_fields=['quantity', 'average_cost', 'updated_at'])
        cls._sync_legacy_stock(product, balance)
        movement_type = (
            StockMovement.MovementType.OPENING
            if reason == StockAdjustment.Reason.OPENING
            else StockMovement.MovementType.ADJUSTMENT_IN if quantity_delta > 0 else StockMovement.MovementType.ADJUSTMENT_OUT
        )
        StockMovement.objects.create(
            organization=organization,
            tenant=organization,
            product=product,
            movement_type=movement_type,
            quantity=quantity_delta,
            balance_after=balance.quantity,
            unit_cost=balance.average_cost,
            adjustment=adjustment,
            created_by=actor,
        )
        if product.is_serialized and quantity_delta > 0:
            for serial in serials:
                StockUnit.objects.create(
                    organization=organization,
                    tenant=organization,
                    product=product,
                    serial_number=serial,
                    status=StockUnit.Status.AVAILABLE,
                    unit_cost=balance.average_cost,
                )
        elif product.is_serialized:
            StockUnit.objects.unscoped().filter(pk__in=[unit.pk for unit in locked_units]).update(status=StockUnit.Status.REMOVED)
        audit(
            organization=organization,
            actor=actor,
            action='inventory.stock.adjusted',
            obj=adjustment,
            new_value={'quantity_delta': str(quantity_delta), 'balance_after': str(balance.quantity), 'reason': reason},
        )
        return adjustment

    @classmethod
    def requires_full_payment(cls, *, organization, invoice: BillingDocument) -> bool:
        if InventorySale.objects.unscoped().filter(tenant=organization, invoice=invoice).exists():
            return True
        return invoice.items.filter(product__isnull=False).exclude(product__sku='').exists()

    @classmethod
    def ensure_invoice_sale(cls, *, organization, invoice: BillingDocument, cart: Cart | None = None):
        if invoice.document_type != BillingDocument.DocumentType.INVOICE:
            return None
        if not invoice.items.filter(product__isnull=False).exclude(product__sku='').exists() and cart is None:
            return None
        sale, _ = InventorySale.objects.unscoped().get_or_create(
            invoice=invoice,
            defaults={'organization': organization, 'tenant': organization, 'cart': cart},
        )
        if cart and sale.cart_id is None:
            sale.cart = cart
            sale.save(update_fields=['cart'])
        return sale

    @classmethod
    def complete_invoice_sale(cls, *, organization, invoice: BillingDocument, receipt: BillingDocument, actor):
        sale = cls.ensure_invoice_sale(organization=organization, invoice=invoice)
        if sale is None:
            return None
        sale = InventorySale.objects.unscoped().select_for_update().get(pk=sale.pk)
        if sale.stock_deducted:
            return sale

        billing_lines = list(invoice.items.select_related('product').order_by('product_id', 'id'))
        product_lines = [line for line in billing_lines if line.product_id]
        for line in product_lines:
            historical_unit = (line.unit_snapshot or '').strip()
            current_unit = (line.product.get_measure_unit_display() or '').strip()
            if historical_unit.casefold() != current_unit.casefold():
                raise InventoryError(
                    f'Invoice line for {line.product.name} uses the historical unit '
                    f'{historical_unit or "(missing)"}, but its current inventory unit is '
                    f'{current_unit or "(missing)"}. Do not change the payment amount. '
                    'Reconcile the product unit and cost, then correct the invoice through '
                    'the approved financial workflow.'
                )
        # Recheck immediately before stock leaves: a newer receipt may have raised
        # weighted-average cost after this invoice was drafted or issued.
        from billing.services import BillingService
        BillingService._validate_product_cost_floors(
            organization=organization,
            actor=actor,
            line_items=product_lines,
            document_discount=getattr(invoice, 'discount_amount', Decimal('0.00')),
        )
        subtotal = sum((line.line_total for line in product_lines), Decimal('0.00'))
        document_discount = money(getattr(invoice, 'discount_amount', Decimal('0.00')))
        processed_discount = Decimal('0.00')

        for index, line in enumerate(product_lines):
            product = line.product
            if product.tenant_id != organization.id:
                raise PermissionDenied('Cross-tenant invoice product denied.')
            if index == len(product_lines) - 1:
                allocated_discount = document_discount - processed_discount
            else:
                allocated_discount = money(document_discount * line.line_total / subtotal) if subtotal else Decimal('0.00')
                processed_discount += allocated_discount
            net_revenue = money(line.line_total - allocated_discount)
            cost_total = Decimal('0.00')

            if product.item_type == Product.ItemType.PHYSICAL and product.track_stock:
                locked_product, balance = cls._locked_product_and_balance(organization=organization, product_id=product.pk)
                quantity = stock_quantity(line.quantity)
                if quantity <= 0 or balance.quantity < quantity:
                    raise InventoryError(f'Insufficient stock for {product.name}. Available: {balance.quantity}.')
                if product.is_serialized:
                    if quantity != quantity.to_integral_value():
                        raise InventoryError(f'{product.name} must be sold in whole serialized units.')
                    selections = list(
                        DocumentSerialSelection.objects.unscoped()
                        .select_for_update()
                        .select_related('stock_unit')
                        .filter(tenant=organization, billing_line=line)
                    )
                    if len(selections) != int(quantity):
                        raise InventoryError(f'Select exactly {int(quantity)} available serial numbers for {product.name}.')
                    unit_ids = [selection.stock_unit_id for selection in selections]
                    units = list(
                        StockUnit.objects.unscoped()
                        .select_for_update()
                        .filter(pk__in=unit_ids, tenant=organization, product=product, status=StockUnit.Status.AVAILABLE)
                    )
                    if len(units) != len(unit_ids):
                        raise InventoryError(f'A selected serial number for {product.name} is no longer available.')
                    cost_total = money(sum((unit.unit_cost for unit in units), Decimal('0.00')))
                    now = timezone.now()
                    StockUnit.objects.unscoped().filter(pk__in=unit_ids).update(
                        status=StockUnit.Status.SOLD, sold_billing_line=line, sold_at=now
                    )
                    DocumentSerialSelection.objects.unscoped().filter(pk__in=[s.pk for s in selections]).update(sold_at=now)
                    movement_unit_cost = money(cost_total / quantity)
                else:
                    movement_unit_cost = balance.average_cost
                    cost_total = money(quantity * movement_unit_cost)

                # Compare the actual locked acquisition cost, including selected
                # serial costs. A stale pre-lock average cannot authorize a loss.
                # Raising here rolls back receipt/payment, serials and stock.
                if net_revenue / quantity <= balance.average_cost or net_revenue < cost_total:
                    raise InventoryError(
                        f'The net selling amount for {product.name} does not meet the current stock cost requirements. '
                        'Ask an authorized manager to review pricing before payment.'
                    )

                balance.quantity = stock_quantity(balance.quantity - quantity)
                balance.save(update_fields=['quantity', 'updated_at'])
                cls._sync_legacy_stock(locked_product, balance)
                StockMovement.objects.create(
                    organization=organization,
                    tenant=organization,
                    product=product,
                    movement_type=StockMovement.MovementType.SALE_OUT,
                    quantity=-quantity,
                    balance_after=balance.quantity,
                    unit_cost=movement_unit_cost,
                    billing_line=line,
                    created_by=actor,
                )

            InventorySaleLine.objects.create(
                sale=sale,
                billing_line=line,
                cost_total=cost_total,
                net_revenue=net_revenue,
            )

        sale.stock_deducted = True
        sale.completed_at = timezone.now()
        sale.receipt = receipt
        sale.save(update_fields=['stock_deducted', 'completed_at', 'receipt'])
        audit(
            organization=organization,
            actor=actor,
            action='inventory.sale.completed',
            obj=sale,
            new_value={'invoice_id': invoice.pk, 'receipt_id': receipt.pk, 'stock_deducted': True},
        )
        return sale


class CartService:
    @classmethod
    @transaction.atomic
    def save_line(cls, *, organization, cart_id, data, actor, membership=None, line_id=None):
        """Add quantities/serials to one product line; edits replace its values."""
        from .cart_lifecycle import visible_carts
        from .forms import CartLineForm
        from users.permissions import PermissionCode, has_tenant_permission

        if not has_tenant_permission(actor, organization, PermissionCode.CART_MANAGE, membership=membership):
            raise PermissionDenied('Cannot manage cart items.')
        cart = visible_carts(organization=organization, actor=actor, membership=membership).select_for_update().filter(
            pk=cart_id, status=Cart.Status.DRAFT,
        ).first()
        if cart is None:
            raise PermissionDenied('Draft cart unavailable.')
        line = cart.lines.filter(tenant=organization, pk=line_id).first() if line_id else None
        if line_id and line is None:
            raise PermissionDenied('Cart item unavailable.')
        incoming = CartLineForm(data, instance=line, organization=organization, cart=cart,
                                sale_pricing_category=cart.sale_pricing_category, membership=membership)
        if not incoming.is_valid():
            raise ValidationError(dict(incoming.errors))
        product = incoming.cleaned_data['product']
        if not line_id:
            line = cart.lines.filter(tenant=organization, product=product).first()
        if line and not line_id:
            selected = list(incoming.cleaned_data['serial_units'])
            saved_ids = list(line.serial_selections.values_list('stock_unit_id', flat=True))
            if set(saved_ids).intersection(unit.pk for unit in selected):
                raise ValidationError({'serial_units': 'A selected serial is already in this cart. Choose a different serial or edit the existing item.'})
            merged = data.copy()
            merged['quantity'] = str(line.quantity + incoming.cleaned_data['quantity'])
            merged['discount_amount'] = str(line.discount_amount + incoming.cleaned_data['discount_amount'])
            merged.setlist('serial_units', [str(pk) for pk in saved_ids] + [str(unit.pk) for unit in selected])
            incoming = CartLineForm(merged, instance=line, organization=organization, cart=cart,
                                    sale_pricing_category=cart.sale_pricing_category, membership=membership)
            if not incoming.is_valid():
                raise ValidationError(dict(incoming.errors))
        obj = incoming.save(commit=False)
        obj.cart = cart
        obj.unit_price, _ = cls.line_pricing(product=product, quantity=obj.quantity, customer=cart.customer,
                                            sale_pricing_category=cart.sale_pricing_category)
        obj.save()
        obj.serial_selections.all().delete()
        CartSerialSelection.objects.bulk_create([
            CartSerialSelection(tenant=organization, cart_line=obj, stock_unit=unit)
            for unit in incoming.cleaned_data['serial_units']
        ])
        cls.validate_discount_authority(cart=cart, actor=actor, membership=membership)
        audit(organization=organization, actor=actor, action='inventory.cart_line.saved', obj=obj,
              new_value={'quantity': str(obj.quantity), 'serial_ids': list(obj.serial_selections.values_list('stock_unit_id', flat=True))},
              metadata={'stock_changed': False})
        return obj

    @classmethod
    @transaction.atomic
    def start(cls, *, organization, actor, membership=None):
        from django.contrib.auth import get_user_model
        from .cart_lifecycle import genuinely_empty, visible_carts
        from users.permissions import PermissionCode, has_tenant_permission
        if not has_tenant_permission(actor, organization, PermissionCode.SALES_DOCUMENTS_CREATE, membership=membership):
            raise PermissionDenied('Cannot start a sale.')
        # A stable row exists even when there are no carts yet. Locking only an
        # empty queryset cannot serialize simultaneous first requests.
        # NO KEY UPDATE allows concurrent foreign-key references to the actor
        # (for example checkout), while still serializing Start sale requests.
        get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
        candidates = visible_carts(organization=organization, actor=actor, membership=membership).select_for_update(of=('self',)).filter(
            created_by=actor, status=Cart.Status.DRAFT,
            updated_at__gt=timezone.now() - timedelta(hours=24),
            customer__isnull=True, quotation__isnull=True, invoice__isnull=True,
            lines__isnull=True,
        ).order_by('-updated_at', '-pk')
        for cart in candidates:
            if genuinely_empty(cart):
                return cart
        cart = Cart.objects.create(organization=organization, tenant=organization, created_by=actor)
        audit(organization=organization, actor=actor, action='inventory.cart.created', obj=cart)
        return cart

    @classmethod
    @transaction.atomic
    def restore(cls, *, organization, cart_id, actor, membership=None):
        from .cart_lifecycle import business_linked, visible_carts
        from users.permissions import PermissionCode, has_tenant_permission
        if not has_tenant_permission(actor, organization, PermissionCode.FINANCE_SALES_VIEW_ALL, membership=membership):
            raise PermissionDenied('Only authorized sales managers can restore carts.')
        cart = visible_carts(organization=organization, actor=actor, membership=membership).select_for_update().filter(pk=cart_id).first()
        if cart is None:
            raise PermissionDenied('Cart unavailable.')
        if cart.status != Cart.Status.ABANDONED or business_linked(cart) or cart.converted_at:
            raise InventoryError('Only abandoned carts without business links can be restored.')
        cart.status = Cart.Status.DRAFT
        cart.restored_at = timezone.now()
        cart.restored_by = actor
        cart.save(update_fields=['status', 'restored_at', 'restored_by', 'updated_at'])
        audit(organization=organization, actor=actor, action='inventory.cart.restored', obj=cart,
              old_value={'status': Cart.Status.ABANDONED}, new_value={'status': cart.status},
              metadata={'stock_changed': False})
        return cart

    @classmethod
    def validate_restored(cls, *, cart):
        if not cart.restored_at:
            return
        for line in cart.lines.select_related('product', 'product__inventory_balance').prefetch_related('serial_selections__stock_unit'):
            product = line.product
            if not product.is_active or product.tenant_id != cart.tenant_id:
                raise InventoryError(f'Item #{line.pk} is unavailable. Remove or replace it before conversion.')
            price, _ = cls.line_pricing(product=product, quantity=line.quantity, customer=cart.customer,
                                       sale_pricing_category=cart.sale_pricing_category)
            if money(line.unit_price) != price:
                raise InventoryError(f'Pricing for {product.name} has changed. Edit and review the item before conversion.')
            if product.track_stock and product.item_type == Product.ItemType.PHYSICAL and line.quantity > product.available_stock:
                raise InventoryError(f'Availability for {product.name} has changed. Review its quantity before conversion.')
            if product.is_serialized and any(selection.stock_unit.status != StockUnit.Status.AVAILABLE for selection in line.serial_selections.all()):
                raise InventoryError(f'Serial availability for {product.name} has changed. Review serial selections before conversion.')

    @classmethod
    def set_line_discount(cls, *, organization, cart_id, line_id, discount_amount, actor, membership=None):
        """Apply one draft-line discount under the same checkout and grant rules."""
        from products.pricing import BelowCostError, validate_net_unit_price
        from users.permissions import PermissionCode, has_tenant_permission

        if not has_tenant_permission(actor, organization, PermissionCode.CART_DISCOUNT_APPLY, membership=membership):
            raise InventoryError('You do not have permission to apply discounts.')
        with transaction.atomic():
            cart = Cart.objects.select_for_update().filter(
                tenant=organization, pk=cart_id, status=Cart.Status.DRAFT,
            ).first()
            if cart is None:
                raise InventoryError('This draft sale is unavailable.')
            from .cart_lifecycle import visible_carts
            if not visible_carts(organization=organization, actor=actor, membership=membership).filter(pk=cart.pk).exists():
                raise PermissionDenied('Cart unavailable.')
            line = CartLine.objects.select_for_update().select_related('product').filter(
                tenant=organization, cart=cart, pk=line_id,
            ).first()
            if line is None:
                raise InventoryError('This item is unavailable in the draft sale.')
            cls.refresh_cart_prices(cart=cart)
            line.refresh_from_db()
            amount = money(discount_amount)
            if amount < 0 or amount > line.quantity * line.unit_price:
                raise InventoryError('Item discount cannot exceed the line amount.')
            line.discount_amount = amount
            line.save(update_fields=['discount_amount', 'updated_at'])
            cls.validate_discount_authority(cart=cart, actor=actor, membership=membership)
            lines = list(cart.lines.select_related('product').order_by('id'))
            subtotal = sum((item.line_total for item in lines), Decimal('0.00'))
            if cart.discount_amount > subtotal:
                raise InventoryError('Cart discount cannot exceed the subtotal after item discounts.')
            allocated = Decimal('0.00')
            for index, item in enumerate(lines):
                if index == len(lines) - 1:
                    share = cart.discount_amount - allocated
                else:
                    share = cart.discount_amount * item.line_total / subtotal if subtotal else Decimal('0.00')
                    allocated += share
                try:
                    validate_net_unit_price(
                        product=item.product, quantity=item.quantity, unit_price=item.unit_price,
                        line_discount=item.discount_amount, allocated_document_discount=share,
                        actor=actor, organization=organization,
                    )
                except BelowCostError as exc:
                    raise InventoryError(str(exc)) from exc
            return cart

    @classmethod
    def validate_discount_authority(cls, *, cart, actor, membership=None):
        """Enforce the actor's current cap over all line and cart discounts."""
        from users.permissions import discount_authorization_error, membership_for

        membership = membership or membership_for(actor, cart.tenant)
        lines = list(cart.lines.all())
        gross_subtotal = sum((line.quantity * line.unit_price for line in lines), Decimal('0.00'))
        total_discount = cart.discount_amount + sum(
            (line.discount_amount for line in lines), Decimal('0.00')
        )
        error = discount_authorization_error(
            membership,
            gross_subtotal=gross_subtotal,
            total_discount=total_discount,
        )
        if error:
            raise InventoryError(error)

    WHOLESALE_TIERS = {
        Customer.PricingTier.WHOLESALE,
    }

    @classmethod
    def line_pricing(
        cls, *, product: Product, quantity: Decimal, customer: Customer | None = None,
        sale_pricing_category: str = Cart.SalePricingCategory.LEGACY_RETAIL,
    ):
        """Return the authoritative explicit-category POS price and audit mode.

        The legacy/standard POS quantity break remains for backward compatibility;
        Technician pricing is never inferred from a logged-in user's role.
        """
        category = sale_pricing_category
        if category == Cart.SalePricingCategory.CUSTOMER_TIER:
            category = customer.default_sale_pricing_category if customer else Product.PricingMode.STANDARD
        if category == Cart.SalePricingCategory.LEGACY_RETAIL:
            qualifies_for_wholesale = (
                product.allow_wholesale and product.wholesale_price is not None
                and quantity >= product.wholesale_min_quantity
            )
            if qualifies_for_wholesale:
                category = Product.PricingMode.WHOLESALE
            else:
                category = Product.PricingMode.RETAIL
        unit_price = money(product.price_for_sale_category(sale_pricing_category=category, quantity=quantity))
        if category == Product.PricingMode.WHOLESALE and product.wholesale_price is not None and unit_price == money(product.wholesale_price):
            mode = BillingLineItem.PricingMode.WHOLESALE
        elif category == Product.PricingMode.WHOLESALE:
            mode = BillingLineItem.PricingMode.STANDARD
        elif category == Product.PricingMode.TECHNICIAN:
            mode = BillingLineItem.PricingMode.TECHNICIAN
        elif category == Product.PricingMode.STANDARD:
            mode = BillingLineItem.PricingMode.STANDARD
        else:
            mode = BillingLineItem.PricingMode.RETAIL
        return unit_price, mode

    @classmethod
    def refresh_cart_prices(cls, *, cart: Cart):
        """Keep the draft preview aligned with the price that checkout will issue."""
        for line in cart.lines.select_related('product').all():
            unit_price, _ = cls.line_pricing(
                product=line.product, quantity=line.quantity, customer=cart.customer,
                sale_pricing_category=cart.sale_pricing_category,
            )
            if money(line.unit_price) != unit_price:
                line.unit_price = unit_price
                line.save(update_fields=['unit_price', 'updated_at'])

    @classmethod
    @transaction.atomic
    def abandon(cls, *, organization, cart_id: int, actor, reason="", membership=None) -> tuple[Cart, bool]:
        """Soft-discard an editable sale without mutating stock or financial records."""
        cart = Cart.objects.unscoped().select_for_update().filter(pk=cart_id).first()
        if cart is None:
            raise InventoryError('Sale not found.')
        if cart.tenant_id != organization.id:
            raise PermissionDenied('Cross-tenant cart access denied.')
        from .cart_lifecycle import visible_carts, business_linked
        if not visible_carts(organization=organization, actor=actor, membership=membership).filter(pk=cart.pk).exists():
            raise PermissionDenied('Cart unavailable.')
        if cart.status == Cart.Status.ABANDONED:
            return cart, False
        if cart.status != Cart.Status.DRAFT or business_linked(cart) or cart.converted_at:
            raise InventoryError('Only an unconverted draft sale can be discarded.')

        line_count = cart.lines.count()
        subtotal = money(cart.subtotal)
        old_value = {'status': cart.status, 'line_count': line_count, 'subtotal': str(subtotal)}
        cart.status = Cart.Status.ABANDONED
        cart.abandoned_at = timezone.now()
        cart.abandoned_by = actor
        cart.abandonment_reason = reason.strip()[:500]
        cart.save(update_fields=['status', 'abandoned_at', 'abandoned_by', 'abandonment_reason'])
        audit(
            organization=organization,
            actor=actor,
            action='inventory.cart.abandoned',
            obj=cart,
            old_value=old_value,
            new_value={'status': cart.status, 'line_count': line_count, 'subtotal': str(subtotal),
                       'reason': cart.abandonment_reason, 'abandoned_at': cart.abandoned_at.isoformat()},
            metadata={'stock_changed': False, 'financial_document_created': False},
        )
        return cart, True

    @classmethod
    def _walk_in_customer(cls, *, organization, label=''):
        settings_obj, _ = InventorySettings.objects.unscoped().get_or_create(
            tenant=organization,
            defaults={'organization': organization, 'walk_in_customer_label': 'Walk-in Customer'},
        )
        # Billing requires a customer FK. This internal, tenant-owned account
        # is never the identity of a shopper; every sale owns its own snapshot.
        customer, _ = Customer.all_objects.get_or_create(
            tenant=organization, is_pos_placeholder=True,
            defaults=dict(
                organization=organization,
                name=settings_obj.walk_in_customer_label.strip() or 'Walk-in Customer',
                customer_type='random',
                status=Customer.Status.ACTIVE,
                location='Walk-in',
            ),
        )
        return customer

    @classmethod
    @transaction.atomic
    def convert(cls, *, organization, cart_id: int, target: str, actor, membership=None):
        from billing.services import BillingService, LineItemInput

        cart = Cart.objects.unscoped().select_for_update().filter(pk=cart_id, tenant=organization).first()
        if cart is None:
            raise PermissionDenied('Cart is not available in the active tenant.')
        from users.permissions import PermissionCode, has_tenant_permission
        # This domain service retains its existing trusted-caller contract; HTTP
        # callers require canonical permissions before entering the service.
        if cart.created_by_id != actor.pk and not has_tenant_permission(actor, organization, PermissionCode.FINANCE_SALES_VIEW_ALL, membership=membership):
            raise PermissionDenied('Cart unavailable.')
        if cart.status != Cart.Status.DRAFT:
            if target == BillingDocument.DocumentType.INVOICE and cart.invoice_id:
                return cart.invoice
            if target == BillingDocument.DocumentType.QUOTATION and cart.quotation_id:
                return cart.quotation
            raise InventoryError('Only draft carts can be converted.')
        from .cart_lifecycle import business_linked
        if business_linked(cart) or cart.converted_at:
            raise InventoryError('This draft has unexpected business links. Ask a manager to review it.')
        if target not in {BillingDocument.DocumentType.QUOTATION, BillingDocument.DocumentType.INVOICE}:
            raise InventoryError('Cart can only become a quotation or invoice.')
        if cart.customer_id and cart.shipping_address.strip():
            raise InventoryError('A walk-in shipping address cannot be used with a registered customer.')
        cls.validate_restored(cart=cart)
        lines = list(cart.lines.select_related('product').prefetch_related('serial_selections__stock_unit').order_by('id'))
        if not lines:
            raise InventoryError('Add at least one cart item before conversion.')
        customer = cart.customer or cls._walk_in_customer(organization=organization, label=cart.walk_in_name)
        inputs = []
        for line in lines:
            if line.product.tenant_id != organization.id or not line.product.is_active:
                raise InventoryError('A cart product is unavailable.')
            fixed_price, pricing_mode = cls.line_pricing(
                product=line.product, quantity=line.quantity, customer=customer,
                sale_pricing_category=cart.sale_pricing_category,
            )
            if money(line.unit_price) != fixed_price:
                line.unit_price = fixed_price
                line.save(update_fields=['unit_price', 'updated_at'])
            if line.discount_amount < 0 or line.discount_amount > line.quantity * fixed_price:
                raise InventoryError('Item discount cannot exceed the line amount.')
            if line.product.is_serialized:
                selections = list(line.serial_selections.all())
                if len(selections) != int(line.quantity):
                    raise InventoryError(f'Select exactly {int(line.quantity)} serial numbers for {line.product.name}.')
            inputs.append(
                LineItemInput(
                    product_id=line.product_id,
                    description=line.product.name,
                    quantity=line.quantity,
                    unit_price=fixed_price,
                    discount_amount=line.discount_amount,
                    pricing_mode=pricing_mode,
                )
            )
        # This execution-time gate catches stale drafts, revoked/reduced grants,
        # and any write path that did not pass through the browser forms.
        cls.validate_discount_authority(cart=cart, actor=actor, membership=membership)
        document = BillingService.create_document(
            organization=organization,
            created_by=actor,
            document_type=target,
            customer_id=customer.pk,
            issue_date=date.today(),
            status=(BillingDocument.Status.ISSUED if target == BillingDocument.DocumentType.INVOICE else BillingDocument.Status.DRAFT),
            currency='TZS',
            tax_rate=cart.tax_rate,
            discount_amount=cart.discount_amount,
            notes=cart.notes,
            items=inputs,
            sale_pricing_category=cart.sale_pricing_category,
            walk_in_name=(cart.walk_in_name.strip() or 'Walk-in Customer') if cart.customer_id is None else None,
            shipping_address=cart.shipping_address if cart.customer_id is None else '',
        )
        document_lines = list(document.items.order_by('id'))
        for cart_line, document_line in zip(lines, document_lines):
            for selection in cart_line.serial_selections.all():
                DocumentSerialSelection.objects.create(
                    organization=organization,
                    tenant=organization,
                    billing_line=document_line,
                    stock_unit=selection.stock_unit,
                )
        cart.status = Cart.Status.CONVERTED
        cart.converted_at = timezone.now()
        if target == BillingDocument.DocumentType.QUOTATION:
            cart.quotation = document
            update_fields = ['status', 'converted_at', 'quotation', 'updated_at']
        else:
            cart.invoice = document
            update_fields = ['status', 'converted_at', 'invoice', 'updated_at']
            InventoryService.ensure_invoice_sale(organization=organization, invoice=document, cart=cart)
        cart.save(update_fields=update_fields)
        audit(
            organization=organization,
            actor=actor,
            action=f'inventory.cart.converted_to_{target}',
            obj=cart,
            new_value={'document_id': document.pk, 'document_type': target},
        )
        return document

    @classmethod
    def copy_document_serials(cls, *, organization, source: BillingDocument, target: BillingDocument):
        source_lines = list(source.items.order_by('id'))
        target_lines = list(target.items.order_by('id'))
        for source_line, target_line in zip(source_lines, target_lines):
            for selection in DocumentSerialSelection.objects.unscoped().filter(tenant=organization, billing_line=source_line):
                DocumentSerialSelection.objects.get_or_create(
                    organization=organization,
                    tenant=organization,
                    billing_line=target_line,
                    stock_unit=selection.stock_unit,
                )

    @classmethod
    def copy_quotation_context(cls, *, organization, quotation: BillingDocument, invoice: BillingDocument):
        cart = Cart.objects.unscoped().filter(tenant=organization, quotation=quotation).first()
        cls.copy_document_serials(organization=organization, source=quotation, target=invoice)
        if cart:
            Cart.objects.unscoped().filter(pk=cart.pk).update(invoice=invoice)
        InventoryService.ensure_invoice_sale(organization=organization, invoice=invoice, cart=cart)
