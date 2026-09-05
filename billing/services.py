from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import calendar

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from audit.models import AuditLog
from customers.models import Customer, CustomerSite, InternetService
from products.models import Product
from services.models import Package
from users.models import Organization, TenantMembership

from .models import BillingDocument, BillingItem, BillingLineItem, BillingSheet, CustomerSubscription, Promotion, SubscriptionPeriod
from .numbering import DocumentNumberService


ISSUED_INVOICE_EDIT_ERROR = (
    "This invoice has already been issued. To modify it, create a credit note or void and reissue."
)
STANDARD_TAX_RATE = Decimal("18.00")
ZERO_TAX_RATE = Decimal("0.00")


@dataclass(frozen=True)
class LineItemInput:
    product_id: int | None = None
    package_id: int | None = None
    internet_service_id: int | None = None
    subscription_id: int | None = None
    description: str = ""
    quantity: Decimal = Decimal("1.00")
    unit_price: Decimal = Decimal("0.00")
    base_unit_price: Decimal | None = None
    discount_amount: Decimal = Decimal("0.00")
    discount_percent: Decimal = Decimal("0.00")
    discount_reason: str = ""
    pricing_mode: str = BillingLineItem.PricingMode.RETAIL
    billing_behavior: str = BillingLineItem.BillingBehavior.ONE_TIME
    promotion_id: int | None = None
    preserve_unit_price: bool = False
    unit_snapshot: str = ""


@dataclass(frozen=True)
class PaidCoverageWindow:
    """A continuous, financially settled service-coverage interval."""

    start: date
    end: date
    period_count: int


def _merge_paid_coverage_ranges(ranges) -> tuple[PaidCoverageWindow, ...]:
    """Merge settled ranges only when their effective coverage is continuous."""
    paid_ranges = sorted(ranges, key=lambda item: (item[0], item[1], item[2]))
    windows: list[PaidCoverageWindow] = []
    for coverage_start, coverage_end, _period_id in paid_ranges:
        if coverage_start > coverage_end:
            continue
        if windows and coverage_start <= windows[-1].end + timedelta(days=1):
            previous = windows[-1]
            windows[-1] = PaidCoverageWindow(
                start=previous.start,
                end=max(previous.end, coverage_end),
                period_count=previous.period_count + 1,
            )
        else:
            windows.append(PaidCoverageWindow(coverage_start, coverage_end, 1))
    return tuple(windows)


def _paid_ranges_for_subscription(subscription: CustomerSubscription):
    for period in subscription.periods.all():
        if period.status != SubscriptionPeriod.Status.PAID:
            continue
        coverage_start = max(period.period_start, subscription.start_date)
        coverage_end = period.period_end
        if subscription.end_date is not None:
            coverage_end = min(coverage_end, subscription.end_date)
        yield coverage_start, coverage_end, period.id


def paid_service_coverage(subscription: CustomerSubscription) -> tuple[PaidCoverageWindow, ...]:
    """Return one subscription's confirmed, continuous paid-coverage windows.

    `SubscriptionPeriod` is the source of truth: an invoice or receipt date does
    not itself grant service coverage, and partial payments remain excluded until
    the linked invoice is fully settled and the period moves to PAID.
    """
    return _merge_paid_coverage_ranges(_paid_ranges_for_subscription(subscription))


def paid_service_coverage_for_subscriptions(subscriptions) -> tuple[PaidCoverageWindow, ...]:
    """Return service-level coverage across immutable package-agreement history."""
    return _merge_paid_coverage_ranges(
        paid_range
        for subscription in subscriptions
        for paid_range in _paid_ranges_for_subscription(subscription)
    )


class BillingServiceError(Exception):
    code = "billing_error"


class BillingService:
    NON_EDITABLE_INVOICE_STATUSES = {
        BillingDocument.Status.SENT,
        BillingDocument.Status.ISSUED,
        BillingDocument.Status.PARTIALLY_PAID,
        BillingDocument.Status.PAID,
        BillingDocument.Status.VOID,
        BillingDocument.Status.SUPERSEDED,
    }

    LEGACY_TERMINAL_INVOICE_STATUSES = {
        BillingDocument.Status.CANCELLED,
        BillingDocument.Status.REISSUED,
    }

    TERMINAL_INVOICE_STATUSES = {
        BillingDocument.Status.PAID,
        BillingDocument.Status.VOID,
        BillingDocument.Status.SUPERSEDED,
        *LEGACY_TERMINAL_INVOICE_STATUSES,
    }

    ACTIVE_QUOTATION_STATUSES = {
        BillingDocument.Status.DRAFT,
        BillingDocument.Status.SENT,
        BillingDocument.Status.ACCEPTED,
    }

    CLOSED_QUOTATION_STATUSES = {
        BillingDocument.Status.REJECTED,
        BillingDocument.Status.EXPIRED,
        BillingDocument.Status.CONVERTED,
    }

    @classmethod
    def _raise_cross_tenant(cls):
        raise PermissionDenied("Cross-tenant object access denied.")

    @classmethod
    def _require_same_tenant(cls, organization: Organization, obj, *, attr: str = "organization_id"):
        if obj is None:
            return
        if getattr(obj, attr, None) != organization.id or getattr(obj, "tenant_id", organization.id) != organization.id:
            cls._raise_cross_tenant()

    @classmethod
    def compute_totals(
        cls,
        *,
        tax_rate: Decimal,
        line_items: list,
        discount_amount: Decimal = Decimal('0.00'),
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Calculate document totals while respecting a product's tax treatment.

        Tax-exempt products are excluded from VAT. Document-level discounts are
        allocated proportionally before VAT so mixed taxable/exempt sales remain
        mathematically consistent in both invoices and the POS preview.
        """
        subtotal = sum((li.line_total for li in line_items), Decimal("0.00"))
        if tax_rate < Decimal('0.00'):
            raise BillingServiceError('Tax rate cannot be negative.')
        discount_amount = (discount_amount or Decimal('0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if subtotal < Decimal('0.00') and discount_amount != Decimal('0.00'):
            raise BillingServiceError('Credit-note documents cannot use a document discount.')
        if subtotal >= Decimal('0.00') and (discount_amount < Decimal('0.00') or discount_amount > subtotal):
            raise BillingServiceError('Document discount cannot be negative or exceed the subtotal.')
        taxable_before_discount = sum(
            (
                line.line_total
                for line in line_items
                if not getattr(line, 'product_id', None)
                or getattr(line.product, 'tax_eligible', True)
            ),
            Decimal('0.00'),
        )
        taxable_discount = (
            discount_amount * taxable_before_discount / subtotal
            if subtotal > Decimal('0.00') and taxable_before_discount > Decimal('0.00')
            else Decimal('0.00')
        )
        taxable_subtotal = taxable_before_discount - taxable_discount
        tax_amount = (taxable_subtotal * (tax_rate / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = (subtotal - discount_amount + tax_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return subtotal.quantize(Decimal("0.01")), tax_amount, total

    _compute_totals = compute_totals

    @classmethod
    def default_tax_rate_for_customer(cls, customer: Customer) -> Decimal:
        if (customer.vrn_number or "").strip():
            return STANDARD_TAX_RATE
        return ZERO_TAX_RATE

    @classmethod
    def is_accounting_period_open(cls, *, organization: Organization, on_date: date) -> bool:
        return True

    @classmethod
    def _resolve_invoice(cls, *, organization: Organization, invoice_id: int) -> BillingDocument:
        invoice = (
            BillingDocument.objects.unscoped()
            .select_related("customer")
            .prefetch_related("items")
            .filter(pk=invoice_id, document_type=BillingDocument.DocumentType.INVOICE)
            .first()
        )
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        return invoice

    @classmethod
    def _resolve_quotation(cls, *, organization: Organization, quotation_id: int) -> BillingDocument:
        quotation = (
            BillingDocument.objects.unscoped()
            .select_related("customer", "converted_invoice")
            .prefetch_related("items")
            .filter(pk=quotation_id, document_type=BillingDocument.DocumentType.QUOTATION)
            .first()
        )
        if quotation is None:
            raise BillingServiceError("Quotation not found.")
        cls._require_same_tenant(organization, quotation)
        if quotation.organization_id != organization.id:
            cls._raise_cross_tenant()
        return quotation

    @classmethod
    def _validate_document_status(cls, *, document_type: str, status: str) -> None:
        allowed_by_type = {
            BillingDocument.DocumentType.INVOICE: {
                BillingDocument.Status.DRAFT,
                BillingDocument.Status.ISSUED,
            },
            BillingDocument.DocumentType.QUOTATION: {
                BillingDocument.Status.DRAFT,
                BillingDocument.Status.SENT,
                BillingDocument.Status.ACCEPTED,
                BillingDocument.Status.REJECTED,
                BillingDocument.Status.EXPIRED,
            },
            BillingDocument.DocumentType.CREDIT_NOTE: {
                BillingDocument.Status.ISSUED,
            },
            BillingDocument.DocumentType.RECEIPT: {
                BillingDocument.Status.PAID,
            },
        }
        allowed = allowed_by_type.get(document_type, set())
        if status not in allowed:
            raise BillingServiceError("This status is not allowed for that document type.")

    @classmethod
    def invoice_paid_total(cls, *, organization: Organization, invoice: BillingDocument) -> Decimal:
        paid = (
            BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice=invoice,
            ).aggregate(total=Sum("total"))["total"]
            or Decimal("0.00")
        )
        return paid.quantize(Decimal("0.01"))

    @classmethod
    def invoice_credited_total(cls, *, organization: Organization, invoice: BillingDocument) -> Decimal:
        credited = (
            BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.CREDIT_NOTE,
                corrected_invoice=invoice,
                status=BillingDocument.Status.ISSUED,
            ).aggregate(total=Sum("total"))["total"]
            or Decimal("0.00")
        )
        return max(-credited, Decimal("0.00")).quantize(Decimal("0.01"))

    @classmethod
    def invoice_credit_capacity(cls, *, organization: Organization, invoice: BillingDocument) -> Decimal:
        # JBMS does not yet model post-payment refunds or customer credit
        # balances. A credit note therefore may only reduce the amount that is
        # currently unpaid on this invoice; it must never over-settle it.
        return cls.invoice_remaining_balance(organization=organization, invoice=invoice)

    @classmethod
    def invoice_remaining_balance(cls, *, organization: Organization, invoice: BillingDocument) -> Decimal:
        paid = cls.invoice_paid_total(organization=organization, invoice=invoice)
        credited = cls.invoice_credited_total(organization=organization, invoice=invoice)
        return max(invoice.total - paid - credited, Decimal("0.00")).quantize(Decimal("0.01"))

    @classmethod
    def invoice_payment_policy(cls, *, organization: Organization, invoice: BillingDocument) -> dict:
        """Return the authoritative payment rule for an invoice.

        Cart/inventory invoices are atomic sales: payment and stock completion
        happen together, so they require the entire remaining balance. Service
        and subscription invoices may accumulate multiple allocated receipts.
        """
        cls._require_same_tenant(organization, invoice)
        if invoice.document_type != BillingDocument.DocumentType.INVOICE:
            raise BillingServiceError('Payment policy is only available for invoices.')
        from inventory.services import InventoryService

        requires_full_payment = InventoryService.requires_full_payment(
            organization=organization,
            invoice=invoice,
        )
        remaining_balance = cls.invoice_remaining_balance(organization=organization, invoice=invoice)
        return {
            'requires_full_payment': requires_full_payment,
            'allows_partial_payment': not requires_full_payment,
            'remaining_balance': remaining_balance,
            'policy_code': 'inventory_full_payment' if requires_full_payment else 'service_partial_payment',
        }

    @classmethod
    def customer_open_invoice_balance(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        exclude_invoice_id: int | None = None,
        lock: bool = False,
    ) -> Decimal:
        """Return open receivables without copying them into a new tax invoice.

        Carry-forward is an account-balance disclosure, not another taxable
        line item.  The original invoice remains the source of truth for its
        VAT and payment allocation, preventing the same debt from being
        counted twice.
        """
        invoices = BillingDocument.objects.filter(
            organization=organization,
            customer=customer,
            document_type=BillingDocument.DocumentType.INVOICE,
            status__in=[
                BillingDocument.Status.SENT,
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PARTIALLY_PAID,
                BillingDocument.Status.APPROVED,
            ],
        ).order_by("issue_date", "created_at", "id")
        if exclude_invoice_id is not None:
            invoices = invoices.exclude(pk=exclude_invoice_id)
        if lock:
            invoices = invoices.select_for_update()
        return sum(
            (cls.invoice_remaining_balance(organization=organization, invoice=invoice) for invoice in invoices),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    @classmethod
    def get_invoice_action_state(cls, *, organization: Organization, invoice: BillingDocument) -> dict:
        paid_total = cls.invoice_paid_total(organization=organization, invoice=invoice)
        credited_total = cls.invoice_credited_total(organization=organization, invoice=invoice)
        remaining_balance = max(invoice.total - paid_total - credited_total, Decimal("0.00")).quantize(Decimal("0.01"))
        credit_capacity = remaining_balance
        is_open_period = cls.is_accounting_period_open(organization=organization, on_date=invoice.issue_date)
        status = invoice.status
        active_issued_states = {
            BillingDocument.Status.ISSUED,
            BillingDocument.Status.SENT,
            BillingDocument.Status.APPROVED,
            BillingDocument.Status.REJECTED,
        }
        can_void = (
            status in active_issued_states
            and paid_total == Decimal("0.00")
            and credited_total == Decimal("0.00")
            and is_open_period
        )
        can_reissue = status in active_issued_states and paid_total == Decimal("0.00") and credited_total == Decimal("0.00")
        can_register_payment = (
            status not in {BillingDocument.Status.DRAFT, *cls.TERMINAL_INVOICE_STATUSES}
            and remaining_balance > Decimal("0.00")
        )
        can_create_credit_note = (
            status not in {
                BillingDocument.Status.DRAFT,
                BillingDocument.Status.VOID,
                BillingDocument.Status.SUPERSEDED,
                *cls.LEGACY_TERMINAL_INVOICE_STATUSES,
            }
            and credit_capacity > Decimal("0.00")
        )
        return {
            "paid_total": paid_total,
            "credited_total": credited_total,
            "remaining_balance": remaining_balance,
            "credit_capacity": credit_capacity,
            "is_open_period": is_open_period,
            "can_void": can_void,
            "can_reissue": can_reissue,
            "can_register_payment": can_register_payment,
            "can_create_credit_note": can_create_credit_note,
            "is_locked": status != BillingDocument.Status.DRAFT,
        }

    @classmethod
    def invoice_account_summary(cls, *, organization: Organization, invoice: BillingDocument) -> dict:
        """Return one accounting-safe summary for invoice screens and PDFs."""
        cls._require_same_tenant(organization, invoice)
        state = cls.get_invoice_action_state(organization=organization, invoice=invoice)
        previous_outstanding = invoice.balance_brought_forward.quantize(Decimal('0.01'))
        current_total = invoice.total.quantize(Decimal('0.01'))
        payment_received = state['paid_total']
        total_amount_due = (previous_outstanding + current_total).quantize(Decimal('0.01'))
        open_account_balance = cls.customer_open_invoice_balance(
            organization=organization,
            customer=invoice.customer,
        )
        if invoice.status == BillingDocument.Status.DRAFT:
            open_account_balance = (open_account_balance + state['remaining_balance']).quantize(Decimal('0.01'))
        return {
            'previous_outstanding_balance': previous_outstanding,
            'current_invoice_total': current_total,
            'total_amount_due': total_amount_due,
            'payment_received': payment_received,
            'current_invoice_balance': state['remaining_balance'],
            'outstanding_account_balance': open_account_balance,
            'has_previous_outstanding': previous_outstanding > Decimal('0.00'),
            'has_payment': payment_received > Decimal('0.00'),
        }

    @classmethod
    def get_invoice_supersession_details(
        cls,
        *,
        organization: Organization,
        invoice: BillingDocument,
    ) -> dict | None:
        if invoice.document_type != BillingDocument.DocumentType.INVOICE:
            return None
        cls._require_same_tenant(organization, invoice)
        supersession_log = (
            AuditLog.objects.filter(
                organization=organization,
                object_type="BillingDocument",
                object_id=str(invoice.id),
                action_type="invoice_superseded",
            )
            .order_by("-performed_at", "-id")
            .first()
        )
        superseding_invoice = invoice.superseded_by
        reason = ""
        superseded_at = None
        if supersession_log is not None:
            reason = (supersession_log.metadata or {}).get("reason", "")
            superseded_at = supersession_log.performed_at
        if supersession_log is None and superseding_invoice is None and not reason:
            return None
        return {
            "reason": reason,
            "superseded_at": superseded_at,
            "superseding_invoice": superseding_invoice,
            "superseding_invoice_number": superseding_invoice.number if superseding_invoice is not None else "",
        }

    @classmethod
    def get_quotation_action_state(cls, *, organization: Organization, quotation: BillingDocument) -> dict:
        cls._require_same_tenant(organization, quotation)
        status = quotation.status
        is_current = quotation.is_current_version
        can_edit = is_current and status == BillingDocument.Status.DRAFT
        can_send = is_current and status == BillingDocument.Status.DRAFT
        can_accept = is_current and status == BillingDocument.Status.SENT
        can_reject = is_current and status in {
            BillingDocument.Status.DRAFT,
            BillingDocument.Status.SENT,
        }
        can_expire = is_current and status == BillingDocument.Status.SENT
        can_convert = is_current and status in cls.ACTIVE_QUOTATION_STATUSES
        return {
            "is_current_version": is_current,
            "is_locked": status != BillingDocument.Status.DRAFT,
            "can_edit": can_edit,
            "can_send": can_send,
            "can_accept": can_accept,
            "can_reject": can_reject,
            "can_expire": can_expire,
            "can_convert": can_convert,
            "converted_invoice": quotation.converted_invoice,
        }

    @classmethod
    def _credit_note_subtotal_for_total(cls, *, total_amount: Decimal, tax_rate: Decimal) -> Decimal:
        total_amount = total_amount.quantize(Decimal("0.01"))
        if tax_rate == Decimal("0.00"):
            return total_amount

        rate_fraction = (tax_rate / Decimal("100.00"))
        base = (total_amount / (Decimal("1.00") + rate_fraction)).quantize(Decimal("0.01"))
        candidates = [base + (Decimal(step) / Decimal("100.00")) for step in range(-5, 6)]

        def computed_total(subtotal: Decimal) -> Decimal:
            tax = (subtotal * rate_fraction).quantize(Decimal("0.01"))
            return (subtotal + tax).quantize(Decimal("0.01"))

        exact = next((candidate for candidate in candidates if candidate > Decimal("0.00") and computed_total(candidate) == total_amount), None)
        if exact is not None:
            return exact

        best = min(
            (candidate for candidate in candidates if candidate > Decimal("0.00")),
            key=lambda candidate: abs(computed_total(candidate) - total_amount),
        )
        return best.quantize(Decimal("0.01"))

    @classmethod
    def _validate_editable_items(cls, *, items: list[LineItemInput]) -> None:
        if not items:
            raise BillingServiceError("At least one line item is required.")

        for item in items:
            quantity = item.quantity or Decimal("0.00")
            unit_price = item.unit_price or Decimal("0.00")
            if quantity <= Decimal("0.00"):
                raise BillingServiceError("Line item quantity must be greater than 0.")
            if unit_price < Decimal("0.00"):
                raise BillingServiceError("Line item unit price cannot be negative.")
            if item.discount_amount < Decimal("0.00"):
                raise BillingServiceError("Line item discount cannot be negative.")

    @classmethod
    def _validate_manual_recurring_invoice_items(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        site: CustomerSite | None,
        items: list[LineItemInput],
    ) -> None:
        """Keep active subscription charges inside the renewal workflow.

        A free-form invoice has no service-period identity. Allowing it to bill
        an already assigned package as recurring creates duplicate months and
        leaves the invoice disconnected from paid coverage.
        """
        package_ids = {
            item.package_id
            for item in items
            if item.package_id
            and item.subscription_id is None
            and item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY
        }
        if not package_ids:
            return

        subscriptions = CustomerSubscription.objects.unscoped().filter(
            organization=organization,
            customer=customer,
            package_id__in=package_ids,
            status=CustomerSubscription.Status.ACTIVE,
        )
        if site is not None:
            subscriptions = subscriptions.filter(site=site)
        conflict = subscriptions.select_related("package", "site").order_by("id").first()
        if conflict is None:
            return

        site_label = conflict.site.name if conflict.site_id else "Main Office"
        raise BillingServiceError(
            f"{conflict.package.name} is already an active subscription at {site_label}. "
            "Use Renew from the customer account so the invoice is linked to exact coverage dates. "
            "For a genuine one-time charge, change Billing behavior to One time."
        )

    @classmethod
    def _promotion_discount(
        cls,
        *,
        promotion: Promotion,
        product: Product | None,
        package: Package | None,
        quantity: Decimal,
        unit_price: Decimal,
        gross: Decimal,
    ) -> tuple[Decimal, Decimal, str]:
        today = timezone.localdate()
        if not promotion.is_valid_for(when=today):
            raise BillingServiceError("Selected promotion is not active for today.")
        if promotion.minimum_quantity and quantity < promotion.minimum_quantity:
            raise BillingServiceError("Selected promotion requires a higher quantity.")
        if promotion.minimum_amount and gross < promotion.minimum_amount:
            raise BillingServiceError("Selected promotion requires a higher line amount.")
        if promotion.minimum_months and package is not None and quantity < Decimal(promotion.minimum_months):
            raise BillingServiceError("Selected promotion requires more subscription months.")

        if promotion.applies_to == Promotion.AppliesTo.PRODUCT and (
            product is None or promotion.product_id != product.id
        ):
            raise BillingServiceError("Selected promotion does not apply to this product.")
        if promotion.applies_to == Promotion.AppliesTo.PACKAGE and (
            package is None or promotion.package_id != package.id
        ):
            raise BillingServiceError("Selected promotion does not apply to this package.")

        discount = Decimal("0.00")
        price = unit_price
        if promotion.reward_type == Promotion.RewardType.PERCENT:
            discount = (gross * (promotion.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.FIXED:
            discount = min(promotion.reward_value, gross).quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.WHOLESALE_PRICE:
            if product is None or not product.allow_wholesale or product.wholesale_price is None:
                raise BillingServiceError("Selected wholesale promotion is not available for this product.")
            price = product.wholesale_price.quantize(Decimal("0.01"))
        elif promotion.reward_type == Promotion.RewardType.FREE_MONTHS:
            discount = Decimal("0.00")
        return price, discount, promotion.name

    @classmethod
    def _serialize_items(cls, line_items) -> list[dict]:
        return [
            {
                "product_id": item.product_id,
                "package_id": item.package_id,
                "description": item.description,
                "quantity": str(item.quantity),
                "base_unit_price": str(item.base_unit_price),
                "unit_price": str(item.unit_price),
                "discount_amount": str(item.discount_amount),
                "pricing_mode": item.pricing_mode,
                "billing_behavior": item.billing_behavior,
                "promotion_id": item.promotion_id,
                "line_total": str(item.line_total),
            }
            for item in line_items
        ]

    @classmethod
    def _document_snapshot(cls, document: BillingDocument) -> dict:
        items = list(document.items.all().order_by("id"))
        return {
            "id": document.id,
            "document_type": document.document_type,
            "number": document.number,
            "status": document.status,
            "customer_id": document.customer_id,
            "issue_date": document.issue_date.isoformat() if document.issue_date else None,
            "issued_at": document.issued_at.isoformat() if document.issued_at else None,
            "sent_at": document.sent_at.isoformat() if document.sent_at else None,
            "accepted_at": document.accepted_at.isoformat() if document.accepted_at else None,
            "rejected_at": document.rejected_at.isoformat() if document.rejected_at else None,
            "expired_at": document.expired_at.isoformat() if document.expired_at else None,
            "converted_at": document.converted_at.isoformat() if document.converted_at else None,
            "voided_at": document.voided_at.isoformat() if document.voided_at else None,
            "due_date": document.due_date.isoformat() if document.due_date else None,
            "currency": document.currency,
            "sale_pricing_category": document.sale_pricing_category,
            "tax_rate": str(document.tax_rate),
            "subtotal": str(document.subtotal),
            "discount_amount": str(document.discount_amount),
            "tax_amount": str(document.tax_amount),
            "total": str(document.total),
            "notes": document.notes,
            "version_number": document.version_number,
            "parent_quotation_id": document.parent_quotation_id,
            "root_quotation_id": document.root_quotation_id,
            "is_current_version": document.is_current_version,
            "original_invoice_id": document.original_invoice_id,
            "superseded_by_id": document.superseded_by_id,
            "corrected_invoice_id": document.corrected_invoice_id,
            "source_quotation_id": document.source_quotation_id,
            "converted_invoice_id": document.converted_invoice_id,
            "items": cls._serialize_items(items),
        }

    @classmethod
    def _log_action(
        cls,
        *,
        organization: Organization,
        performed_by,
        action_type: str,
        document: BillingDocument,
        old_value: dict | None = None,
        new_value: dict | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        return AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=performed_by,
            performed_by=performed_by,
            action=action_type,
            action_type=action_type,
            object_type="BillingDocument",
            object_id=str(document.id),
            document_id=str(document.id),
            old_value=old_value or {},
            new_value=new_value or {},
            metadata=metadata or {},
            performed_at=timezone.now(),
        )

    @classmethod
    def _resolve_customer(cls, *, organization: Organization, customer_id: int) -> Customer:
        customer = Customer.all_objects.filter(id=customer_id).first()
        if customer is None:
            raise BillingServiceError("Invalid customer.")
        cls._require_same_tenant(organization, customer)
        if customer.status != Customer.Status.ACTIVE:
            raise BillingServiceError("Customer is not Active. Billing is not allowed.")
        return customer

    @classmethod
    def _resolve_line_item_refs(cls, *, organization: Organization, item: LineItemInput) -> tuple[Product | None, Package | None]:
        product = None
        package = None
        if item.product_id:
            product = Product.objects.unscoped().filter(id=item.product_id).first()
            if product is None:
                raise BillingServiceError("Invalid product.")
            cls._require_same_tenant(organization, product)
            if product.organization_id != organization.id:
                cls._raise_cross_tenant()
        if item.package_id:
            package = Package.objects.unscoped().filter(id=item.package_id).first()
            if package is None:
                raise BillingServiceError("Invalid package.")
            cls._require_same_tenant(organization, package)
            if package.organization_id != organization.id:
                cls._raise_cross_tenant()
        if product and package:
            raise BillingServiceError("Line item cannot reference both product and package.")
        if not product and not package and not item.description:
            raise BillingServiceError("Line item requires product, package, or description.")
        return product, package

    @classmethod
    def _build_line_items(
        cls,
        *,
        organization: Organization,
        document: BillingDocument,
        items: list[LineItemInput],
    ) -> list[BillingLineItem]:
        created_items: list[BillingLineItem] = []
        for item in items:
            product, package = cls._resolve_line_item_refs(organization=organization, item=item)
            internet_service = None
            subscription = None
            if item.internet_service_id:
                internet_service = InternetService.objects.unscoped().filter(
                    id=item.internet_service_id,
                    tenant=organization,
                ).first()
                if internet_service is None or internet_service.customer_id != document.customer_id:
                    raise BillingServiceError("Invalid Internet service context.")
                if document.site_id and internet_service.site_id != document.site_id:
                    raise BillingServiceError("Internet service and document site context do not match.")
            if item.subscription_id:
                subscription = CustomerSubscription.objects.unscoped().filter(
                    id=item.subscription_id,
                    tenant=organization,
                ).first()
                if subscription is None or subscription.customer_id != document.customer_id:
                    raise BillingServiceError("Invalid subscription context.")
                if document.site_id and subscription.site_id != document.site_id:
                    raise BillingServiceError("Subscription and document site context do not match.")
                if package is not None and subscription.package_id != package.id:
                    raise BillingServiceError("Subscription and package context do not match.")
                if internet_service is not None and subscription.internet_service_id != internet_service.id:
                    raise BillingServiceError("Subscription and Internet service context do not match.")
                if internet_service is None and subscription.internet_service_id:
                    internet_service = subscription.internet_service
            promotion = None
            if item.promotion_id:
                promotion = Promotion.objects.unscoped().filter(id=item.promotion_id, organization=organization).first()
                if promotion is None:
                    raise BillingServiceError("Invalid promotion.")
            qty = (item.quantity or Decimal("0.00")).quantize(Decimal("0.01"))
            pricing_mode = item.pricing_mode
            unit = (item.unit_price or Decimal("0.00")).quantize(Decimal("0.01"))
            # Catalog products have fixed prices. MANUAL remains available for
            # legacy free-text/package billing, but never overrides an inventory
            # catalog item's configured selling price.
            if not item.preserve_unit_price and (pricing_mode != BillingLineItem.PricingMode.MANUAL or (product is not None and product.sku)):
                if product is not None:
                    category = document.sale_pricing_category
                    if category == BillingDocument.SalePricingCategory.CUSTOMER_TIER:
                        category = document.customer.default_sale_pricing_category
                    if category == BillingDocument.SalePricingCategory.LEGACY_RETAIL:
                        wants_wholesale = (
                            pricing_mode == BillingLineItem.PricingMode.WHOLESALE
                            or document.customer.pricing_tier == Customer.PricingTier.WHOLESALE
                        )
                        category = Product.PricingMode.WHOLESALE if wants_wholesale else Product.PricingMode.RETAIL
                    elif category == BillingDocument.SalePricingCategory.STANDARD and pricing_mode == BillingLineItem.PricingMode.WHOLESALE:
                        category = Product.PricingMode.WHOLESALE
                    unit = product.price_for_sale_category(
                        sale_pricing_category=category, quantity=qty,
                    ).quantize(Decimal("0.01"))
                    if category == Product.PricingMode.WHOLESALE and product.wholesale_price is not None and unit == product.wholesale_price:
                        pricing_mode = BillingLineItem.PricingMode.WHOLESALE
                    elif category == Product.PricingMode.WHOLESALE:
                        pricing_mode = BillingLineItem.PricingMode.STANDARD
                    elif category == Product.PricingMode.TECHNICIAN:
                        pricing_mode = BillingLineItem.PricingMode.TECHNICIAN
                    elif category == Product.PricingMode.STANDARD:
                        pricing_mode = BillingLineItem.PricingMode.STANDARD
                    else:
                        pricing_mode = BillingLineItem.PricingMode.RETAIL
                elif package is not None:
                    if item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY:
                        unit = package.monthly_fee.quantize(Decimal("0.01"))
                    else:
                        unit = package.price.quantize(Decimal("0.01"))
            base_unit = (item.base_unit_price if item.base_unit_price is not None else unit).quantize(Decimal("0.01"))
            discount_amount = (item.discount_amount or Decimal("0.00")).quantize(Decimal("0.01"))
            discount_reason = item.discount_reason
            if promotion is not None and pricing_mode != BillingLineItem.PricingMode.MANUAL and not item.preserve_unit_price:
                gross = (qty * unit).quantize(Decimal("0.01"))
                unit, promotion_discount, promotion_reason = cls._promotion_discount(
                    promotion=promotion,
                    product=product,
                    package=package,
                    quantity=qty,
                    unit_price=unit,
                    gross=gross,
                )
                discount_amount = promotion_discount
                discount_reason = discount_reason or promotion_reason
                pricing_mode = BillingLineItem.PricingMode.PROMOTION
            line_total = ((qty * unit) - discount_amount).quantize(Decimal("0.01"))
            if line_total < Decimal("0.00") and document.document_type != BillingDocument.DocumentType.CREDIT_NOTE:
                raise BillingServiceError("Line item discount cannot exceed line total.")
            created_items.append(
                BillingLineItem(
                    organization=organization,
                    tenant=organization,
                    document=document,
                    product=product,
                    package=package,
                    internet_service=internet_service,
                    subscription=subscription,
                    description=item.description,
                    quantity=qty,
                    unit_snapshot=cls._resolve_line_unit(
                        item=item,
                        product=product,
                        package=package,
                    ),
                    base_unit_price=base_unit,
                    unit_price=unit,
                    discount_amount=discount_amount,
                    discount_percent=item.discount_percent or Decimal("0.00"),
                    discount_reason=discount_reason,
                    pricing_mode=pricing_mode,
                    billing_behavior=item.billing_behavior,
                    promotion=promotion,
                    line_total=line_total,
                )
            )
        return created_items

    @classmethod
    def _resolve_line_unit(cls, *, item: LineItemInput, product: Product | None, package: Package | None) -> str:
        requested = (item.unit_snapshot or '').strip()
        if requested and item.preserve_unit_price:
            return requested
        if product is not None:
            configured = product.get_measure_unit_display()
            if requested:
                if product.catalog_category_id:
                    allowed = {
                        unit.label
                        for unit in product.catalog_category.allowed_units.filter(is_active=True)
                    }
                    if requested not in allowed:
                        raise BillingServiceError('Select a unit allowed by the product category.')
                elif requested != configured:
                    raise BillingServiceError('Product unit must match its configured sales unit.')
                return requested
            return configured
        if package is not None:
            configured = (
                'Month'
                if item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY
                else 'Installation'
            )
            if requested and requested != configured:
                raise BillingServiceError('Package unit is determined by its billing behavior.')
            return configured
        return requested or 'Unit'

    @classmethod
    def _store_document(
        cls,
        *,
        organization: Organization,
        created_by,
        document_type: str,
        customer: Customer,
        site: CustomerSite | None = None,
        issue_date: date,
        due_date: date | None,
        status: str,
        currency: str,
        tax_rate: Decimal,
        notes: str,
        items: list[LineItemInput],
        sale_pricing_category: str = BillingDocument.SalePricingCategory.CUSTOMER_TIER,
        discount_amount: Decimal = Decimal('0.00'),
        invoice: BillingDocument | None = None,
        original_invoice: BillingDocument | None = None,
        corrected_invoice: BillingDocument | None = None,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        balance_brought_forward: Decimal = Decimal("0.00"),
        number: str | None = None,
        version_number: int = 1,
        parent_quotation: BillingDocument | None = None,
        root_quotation: BillingDocument | None = None,
        is_current_version: bool = True,
        superseded_by: BillingDocument | None = None,
        source_quotation: BillingDocument | None = None,
        converted_invoice: BillingDocument | None = None,
    ) -> BillingDocument:
        if number is None:
            document_date = (
                payment_date
                if document_type == BillingDocument.DocumentType.RECEIPT and payment_date is not None
                else issue_date
            )
            number = DocumentNumberService.next_number(
                organization=organization,
                document_type=document_type,
                issue_date=document_date,
            ).value
        now = timezone.now()
        actor_membership = TenantMembership.objects.filter(
            tenant=organization, user=created_by, is_active=True
        ).first()
        responsible_membership = (
            getattr(source_quotation, "responsible_membership", None)
            or getattr(invoice, "responsible_membership", None)
            or actor_membership
        )
        timestamp_fields = {
            "issued_at": now,
            "sent_at": now if status == BillingDocument.Status.SENT else None,
            "accepted_at": now if status in {BillingDocument.Status.ACCEPTED, BillingDocument.Status.APPROVED} else None,
            "rejected_at": now if status == BillingDocument.Status.REJECTED else None,
            "expired_at": now if status == BillingDocument.Status.EXPIRED else None,
            "converted_at": now if status == BillingDocument.Status.CONVERTED else None,
            "voided_at": now if status in {BillingDocument.Status.VOID, BillingDocument.Status.CANCELLED} else None,
        }
        document = BillingDocument.objects.create(
            organization=organization,
            tenant=organization,
            document_type=document_type,
            number=number,
            customer=customer,
            site=site,
            issue_date=issue_date,
            due_date=due_date,
            status=status,
            currency=currency,
            sale_pricing_category=sale_pricing_category,
            tax_rate=tax_rate,
            discount_amount=discount_amount,
            balance_brought_forward=balance_brought_forward,
            notes=notes,
            created_by=created_by,
            created_by_membership=actor_membership,
            responsible_membership=responsible_membership,
            issued_by_membership=actor_membership if status != BillingDocument.Status.DRAFT else None,
            payment_recorded_by_membership=actor_membership if document_type == BillingDocument.DocumentType.RECEIPT else None,
            last_modified_by_membership=actor_membership,
            invoice=invoice,
            original_invoice=original_invoice,
            corrected_invoice=corrected_invoice,
            payment_date=payment_date,
            payment_method=payment_method,
            payment_reference=payment_reference,
            version_number=version_number,
            parent_quotation=parent_quotation,
            root_quotation=root_quotation,
            is_current_version=is_current_version,
            superseded_by=superseded_by,
            source_quotation=source_quotation,
            converted_invoice=converted_invoice,
            **timestamp_fields,
        )

        created_items = cls._build_line_items(organization=organization, document=document, items=items)
        if created_items:
            BillingLineItem.objects.bulk_create(created_items)

        subtotal, tax_amount, total = cls._compute_totals(
            tax_rate=tax_rate, line_items=created_items, discount_amount=discount_amount
        )
        BillingDocument.objects.filter(id=document.id).update(subtotal=subtotal, tax_amount=tax_amount, total=total)
        document.refresh_from_db()
        return document

    @classmethod
    def create_document(
        cls,
        *,
        organization: Organization,
        created_by,
        document_type: str,
        customer_id: int,
        site_id: int | None = None,
        issue_date: date | None = None,
        due_date: date | None = None,
        status: str = BillingDocument.Status.DRAFT,
        currency: str = "TZS",
        tax_rate: Decimal | None = None,
        discount_amount: Decimal = Decimal('0.00'),
        notes: str = "",
        invoice_id: int | None = None,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        items: list[LineItemInput] | None = None,
        sale_pricing_category: str = BillingDocument.SalePricingCategory.CUSTOMER_TIER,
    ) -> BillingDocument:
        if issue_date is None:
            issue_date = timezone.localdate()
        if items is None:
            items = []
        cls._validate_document_status(document_type=document_type, status=status)

        customer = cls._resolve_customer(organization=organization, customer_id=customer_id)
        site = None
        if site_id is not None:
            site = CustomerSite.objects.filter(
                id=site_id,
                organization=organization,
                customer=customer,
            ).first()
            if site is None:
                raise BillingServiceError("Invalid customer site context.")
        if tax_rate is None:
            tax_rate = cls.default_tax_rate_for_customer(customer)
        invoice = None
        if document_type == BillingDocument.DocumentType.RECEIPT:
            if invoice_id is None:
                raise BillingServiceError("Receipt requires invoice.")
            invoice = BillingDocument.objects.unscoped().select_related("customer").prefetch_related("items").filter(
                id=invoice_id,
                document_type=BillingDocument.DocumentType.INVOICE,
            ).first()
            if invoice is None:
                raise BillingServiceError("Invalid invoice.")
            cls._require_same_tenant(organization, invoice)
            if invoice.organization_id != organization.id:
                cls._raise_cross_tenant()
            if invoice.customer_id != customer.id:
                raise BillingServiceError("Receipt customer must match its invoice customer.")
            if site is None:
                site = invoice.site
            elif invoice.site_id and site.id != invoice.site_id:
                raise BillingServiceError("Receipt site must match its invoice site.")
        cls._validate_editable_items(items=items)
        if document_type == BillingDocument.DocumentType.INVOICE:
            cls._validate_manual_recurring_invoice_items(
                organization=organization,
                customer=customer,
                site=site,
                items=items,
            )

        with transaction.atomic():
            balance_brought_forward = Decimal("0.00")
            if document_type == BillingDocument.DocumentType.INVOICE:
                balance_brought_forward = cls.customer_open_invoice_balance(
                    organization=organization,
                    customer=customer,
                    lock=True,
                )
            document = cls._store_document(
                organization=organization,
                created_by=created_by,
                document_type=document_type,
                customer=customer,
                site=site,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
                currency=currency,
                tax_rate=tax_rate,
                discount_amount=discount_amount,
                notes=notes,
                items=items,
                sale_pricing_category=sale_pricing_category,
                invoice=invoice,
                payment_date=payment_date,
                payment_method=payment_method,
                payment_reference=payment_reference,
                balance_brought_forward=balance_brought_forward,
            )

            action_type = {
                BillingDocument.DocumentType.QUOTATION: "quotation_created",
                BillingDocument.DocumentType.INVOICE: "invoice_created",
                BillingDocument.DocumentType.CREDIT_NOTE: "credit_note_created",
            }.get(document_type)
            if action_type is not None:
                cls._log_action(
                    organization=organization,
                    performed_by=created_by,
                    action_type=action_type,
                    document=document,
                    new_value=cls._document_snapshot(document),
                )
            if document_type == BillingDocument.DocumentType.INVOICE:
                from inventory.services import InventoryService

                InventoryService.ensure_invoice_sale(organization=organization, invoice=document)
            return document

    @classmethod
    def get_quotation_history(cls, *, organization: Organization, quotation_id: int):
        quotation = BillingDocument.objects.unscoped().filter(
            pk=quotation_id,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).first()
        if quotation is None:
            raise BillingServiceError("Quotation not found.")
        cls._require_same_tenant(organization, quotation)
        if quotation.organization_id != organization.id:
            cls._raise_cross_tenant()
        root_id = quotation.root_quotation_id or quotation.id
        return BillingDocument.objects.filter(
            organization=organization,
            document_type=BillingDocument.DocumentType.QUOTATION,
        ).filter(
            Q(root_quotation_id=root_id) | Q(id=root_id)
        )

    @classmethod
    def compare_quotation_versions(cls, *, organization: Organization, from_quotation_id: int, to_quotation_id: int) -> dict:
        versions = {
            item.id: item
            for item in BillingDocument.objects.unscoped().filter(
                document_type=BillingDocument.DocumentType.QUOTATION,
                id__in=[from_quotation_id, to_quotation_id],
            ).prefetch_related("items")
        }
        if len(versions) != 2:
            raise BillingServiceError("Quotation version not found.")
        left = versions[from_quotation_id]
        right = versions[to_quotation_id]
        cls._require_same_tenant(organization, left)
        cls._require_same_tenant(organization, right)
        left_snapshot = cls._document_snapshot(left)
        right_snapshot = cls._document_snapshot(right)
        diff = {}
        for key in ("issue_date", "due_date", "tax_rate", "subtotal", "tax_amount", "total", "notes"):
            if left_snapshot[key] != right_snapshot[key]:
                diff[key] = {"from": left_snapshot[key], "to": right_snapshot[key]}
        if left_snapshot["items"] != right_snapshot["items"]:
            diff["items"] = {"from": left_snapshot["items"], "to": right_snapshot["items"]}
        return {"from": left_snapshot, "to": right_snapshot, "changes": diff}

    @classmethod
    def create_quotation_version(
        cls,
        *,
        organization: Organization,
        created_by,
        quotation_id: int,
        customer_id: int,
        site_id: int | None = None,
        issue_date: date | None = None,
        due_date: date | None = None,
        status: str = BillingDocument.Status.DRAFT,
        currency: str = "TZS",
        tax_rate: Decimal = Decimal("18.00"),
        notes: str = "",
        items: list[LineItemInput] | None = None,
        sale_pricing_category: str | None = None,
    ) -> BillingDocument:
        if issue_date is None:
            issue_date = timezone.localdate()
        if items is None:
            items = []
        cls._validate_document_status(document_type=BillingDocument.DocumentType.QUOTATION, status=status)
        previous = cls._resolve_quotation(organization=organization, quotation_id=quotation_id)
        customer = cls._resolve_customer(organization=organization, customer_id=customer_id)
        site = None
        if site_id is not None:
            site = CustomerSite.objects.filter(
                id=site_id, organization=organization, customer=customer,
            ).first()
            if site is None:
                raise BillingServiceError("Invalid customer site context.")
        if sale_pricing_category is None:
            sale_pricing_category = previous.sale_pricing_category
        cls._validate_editable_items(items=items)
        root = previous.root_quotation or previous
        with transaction.atomic():
            BillingDocument.objects.filter(pk=previous.pk).update(is_current_version=False)
            version = cls._store_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer=customer,
                site=site,
                issue_date=issue_date,
                due_date=due_date,
                status=status,
                currency=currency,
                tax_rate=tax_rate,
                discount_amount=previous.discount_amount,
                notes=notes,
                items=items,
                number=previous.number,
                version_number=previous.version_number + 1,
                parent_quotation=previous,
                root_quotation=root,
                is_current_version=True,
                sale_pricing_category=sale_pricing_category,
            )
            from inventory.services import CartService

            CartService.copy_document_serials(organization=organization, source=previous, target=version)
            cls._log_action(
                organization=organization,
                performed_by=created_by,
                action_type="quotation_version_created",
                document=version,
                old_value=cls._document_snapshot(previous),
                new_value=cls._document_snapshot(version),
                metadata={"parent_quotation_id": previous.id, "root_quotation_id": root.id},
            )
            return version

    @classmethod
    def update_draft_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        tax_rate: Decimal,
        status: str = BillingDocument.Status.DRAFT,
        items: list[LineItemInput],
    ) -> BillingDocument:
        invoice = BillingDocument.objects.unscoped().filter(
            pk=invoice_id,
            document_type=BillingDocument.DocumentType.INVOICE,
        ).prefetch_related("items").first()
        if invoice is None:
            raise BillingServiceError("Invoice not found.")
        cls._require_same_tenant(organization, invoice)
        if invoice.organization_id != organization.id:
            cls._raise_cross_tenant()
        if invoice.status != BillingDocument.Status.DRAFT:
            raise BillingServiceError(ISSUED_INVOICE_EDIT_ERROR)
        if status not in {BillingDocument.Status.DRAFT, BillingDocument.Status.ISSUED}:
            raise BillingServiceError("Draft invoices can only be saved as Draft or Issued.")
        cls._validate_editable_items(items=items)
        old_snapshot = cls._document_snapshot(invoice)
        with transaction.atomic():
            BillingLineItem.objects.filter(document=invoice).delete()
            created_items = cls._build_line_items(organization=organization, document=invoice, items=items)
            if created_items:
                BillingLineItem.objects.bulk_create(created_items)
            linked_periods = list(
                SubscriptionPeriod.objects.filter(organization=organization, invoice=invoice).select_related("subscription")
            )
            for period in linked_periods:
                period_item = cls._subscription_invoice_item_for_period(period=period, line_items=created_items)
                if period_item is None:
                    continue
                months = cls._infer_subscription_invoice_months(period=period, item=period_item)
                if months < 1:
                    continue
                original_amount = (period.subscription.monthly_fee_at_signup * Decimal(months)).quantize(Decimal("0.01"))
                period.months = months
                period.period_end = last_day_of_month(add_months(period.period_start, months + period.free_months - 1))
                period.original_amount = original_amount
                period.final_amount = period_item.line_total.quantize(Decimal("0.01"))
                period.save(update_fields=["months", "period_end", "original_amount", "final_amount"])
                if period_item.billing_behavior == BillingLineItem.BillingBehavior.RECURRING_MONTHLY:
                    description = SubscriptionBillingService.format_invoice_description(
                        subscription=period.subscription,
                        period=period,
                    )
                    if period.free_months:
                        description += (
                            f" (includes {period.free_months} complimentary month"
                            f"{'s' if period.free_months != 1 else ''})"
                        )
                    BillingLineItem.objects.filter(pk=period_item.pk).update(description=description)
            subtotal, tax_amount, total = cls._compute_totals(
                tax_rate=tax_rate,
                line_items=created_items,
                discount_amount=invoice.discount_amount,
            )
            updates = {
                "tax_rate": tax_rate,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total": total,
                "status": status,
            }
            if status == BillingDocument.Status.ISSUED:
                updates["issued_at"] = timezone.now()
            BillingDocument.objects.filter(pk=invoice.pk).update(**updates)
            invoice.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_edited",
                document=invoice,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(invoice),
            )
            return invoice

    @classmethod
    def _subscription_invoice_item_for_period(
        cls,
        *,
        period: SubscriptionPeriod,
        line_items: list[LineItemInput],
    ) -> LineItemInput | None:
        package_items_by_package = {
            item.package_id: item
            for item in line_items
            if item.package_id is not None
        }
        if period.subscription.package_id in package_items_by_package:
            return package_items_by_package[period.subscription.package_id]
        return next(iter(package_items_by_package.values()), None)

    @classmethod
    def _infer_subscription_invoice_months(cls, *, period: SubscriptionPeriod, item: LineItemInput) -> int:
        months = int(item.quantity)
        if months > 1:
            return months

        if item.billing_behavior != BillingLineItem.BillingBehavior.RECURRING_MONTHLY:
            return max(months, 1)

        monthly_fee = period.subscription.monthly_fee_at_signup.quantize(Decimal("0.01"))
        if monthly_fee <= Decimal("0.00"):
            return max(months, 1)

        line_total = (item.quantity * item.unit_price - item.discount_amount).quantize(Decimal("0.01"))
        if line_total <= Decimal("0.00"):
            return max(months, 1)

        inferred = (line_total / monthly_fee).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        inferred_months = int(inferred)
        if inferred_months < 1:
            return max(months, 1)

        expected_total = (monthly_fee * Decimal(inferred_months)).quantize(Decimal("0.01"))
        if expected_total != line_total:
            return max(months, 1)
        return inferred_months

    @classmethod
    def transition_quotation_status(
        cls,
        *,
        organization: Organization,
        performed_by,
        quotation_id: int,
        to_status: str,
        reason: str = "",
    ) -> BillingDocument:
        quotation = cls._resolve_quotation(organization=organization, quotation_id=quotation_id)
        state = cls.get_quotation_action_state(organization=organization, quotation=quotation)
        allowed = {
            BillingDocument.Status.SENT: state["can_send"],
            BillingDocument.Status.ACCEPTED: state["can_accept"],
            BillingDocument.Status.REJECTED: state["can_reject"],
            BillingDocument.Status.EXPIRED: state["can_expire"],
        }
        if not allowed.get(to_status, False):
            raise BillingServiceError("This quotation cannot move to that status from its current state.")

        reason = (reason or "").strip()
        old_snapshot = cls._document_snapshot(quotation)
        now = timezone.now()
        updates = {"status": to_status}
        if to_status == BillingDocument.Status.SENT:
            updates["sent_at"] = now
        elif to_status == BillingDocument.Status.ACCEPTED:
            updates["accepted_at"] = now
        elif to_status == BillingDocument.Status.REJECTED:
            updates["rejected_at"] = now
        elif to_status == BillingDocument.Status.EXPIRED:
            updates["expired_at"] = now

        with transaction.atomic():
            BillingDocument.objects.filter(pk=quotation.pk).update(**updates)
            quotation.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type=f"quotation_{to_status}",
                document=quotation,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(quotation),
                metadata={"reason": reason},
            )
            return quotation

    @classmethod
    def create_invoice_from_quotation(cls, *, organization: Organization, created_by, quotation_id: int) -> BillingDocument:
        quotation = cls._resolve_quotation(organization=organization, quotation_id=quotation_id)
        if not quotation.is_current_version:
            raise BillingServiceError("Only the latest quotation version can be converted to an invoice.")
        if quotation.converted_invoice_id:
            return quotation.converted_invoice
        if quotation.status in {BillingDocument.Status.REJECTED, BillingDocument.Status.EXPIRED, BillingDocument.Status.CONVERTED}:
            raise BillingServiceError("This quotation cannot be converted in its current state.")

        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                internet_service_id=item.internet_service_id,
                subscription_id=item.subscription_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
                preserve_unit_price=True,
                unit_snapshot=item.unit_snapshot,
            )
            for item in quotation.items.all()
        ]
        with transaction.atomic():
            old_quotation_snapshot = cls._document_snapshot(quotation)
            invoice = cls.create_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=quotation.customer_id,
                site_id=quotation.site_id,
                issue_date=timezone.localdate(),
                due_date=quotation.due_date,
                status=BillingDocument.Status.DRAFT,
                currency=quotation.currency,
                tax_rate=quotation.tax_rate,
                discount_amount=quotation.discount_amount,
                notes=quotation.notes,
                items=items,
                sale_pricing_category=quotation.sale_pricing_category,
            )
            BillingDocument.objects.filter(pk=invoice.pk).update(source_quotation=quotation)
            BillingDocument.objects.filter(pk=quotation.pk).update(
                status=BillingDocument.Status.CONVERTED,
                converted_at=timezone.now(),
                converted_invoice=invoice,
            )
            invoice.refresh_from_db()
            quotation.refresh_from_db()
            from inventory.services import CartService

            CartService.copy_quotation_context(organization=organization, quotation=quotation, invoice=invoice)
            cls._log_action(
                organization=organization,
                performed_by=created_by,
                action_type="quotation_converted_to_invoice",
                document=invoice,
                old_value=old_quotation_snapshot,
                new_value=cls._document_snapshot(invoice),
                metadata={
                    "quotation_id": quotation.id,
                    "quotation_version": quotation.version_number,
                    "converted_from_status": old_quotation_snapshot["status"],
                },
            )
            return invoice

    @classmethod
    def void_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        reason: str,
    ) -> BillingDocument:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BillingServiceError("A short reason is required to void this invoice.")

        invoice = cls._resolve_invoice(organization=organization, invoice_id=invoice_id)
        state = cls.get_invoice_action_state(organization=organization, invoice=invoice)
        if not state["can_void"]:
            raise BillingServiceError("Only unpaid issued invoices without receipts or credit notes can be voided.")

        old_snapshot = cls._document_snapshot(invoice)
        with transaction.atomic():
            BillingDocument.objects.filter(pk=invoice.pk).update(
                status=BillingDocument.Status.VOID,
                voided_at=timezone.now(),
            )
            invoice.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_voided",
                document=invoice,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(invoice),
                metadata={"reason": reason, "is_open_period": state["is_open_period"]},
            )
            return invoice

    @classmethod
    def cancel_invoice(cls, *, organization: Organization, performed_by, invoice_id: int) -> BillingDocument:
        return cls.void_invoice(
            organization=organization,
            performed_by=performed_by,
            invoice_id=invoice_id,
            reason="Legacy cancel action converted to void.",
        )

    @classmethod
    def void_subscription_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        period_id: int,
        reason: str,
    ) -> SubscriptionPeriod:
        reason = (reason or "").strip()
        if not reason:
            raise BillingServiceError("A reason is required to void a subscription invoice.")

        period = (
            SubscriptionPeriod.objects.unscoped()
            .select_related("subscription", "subscription__customer", "invoice", "receipt")
            .filter(pk=period_id, organization=organization)
            .first()
        )
        if period is None:
            raise BillingServiceError("Subscription period not found.")
        if period.invoice_id is None:
            raise BillingServiceError("This subscription period does not have an invoice to void.")
        if period.receipt_id is not None or period.status == SubscriptionPeriod.Status.PAID:
            raise BillingServiceError("Paid subscription periods need a credit note or payment reversal.")
        if period.status not in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}:
            raise BillingServiceError("Only unpaid subscription invoices can be voided.")

        invoice = period.invoice
        cls._require_same_tenant(organization, invoice)
        if invoice.document_type != BillingDocument.DocumentType.INVOICE:
            raise BillingServiceError("Only invoices can be voided through this workflow.")

        old_period = {
            "id": period.id,
            "status": period.status,
            "invoice_id": period.invoice_id,
            "receipt_id": period.receipt_id,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        }

        with transaction.atomic():
            cls.void_invoice(
                organization=organization,
                performed_by=performed_by,
                invoice_id=invoice.id,
                reason=reason,
            )
            SubscriptionPeriod.objects.filter(pk=period.pk).update(status=SubscriptionPeriod.Status.CANCELLED)
            period.refresh_from_db()
            invoice.refresh_from_db()
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="subscription.invoice_voided",
                document=invoice,
                old_value={"subscription_period": old_period},
                new_value={
                    "subscription_period": {
                        "id": period.id,
                        "status": period.status,
                        "invoice_id": period.invoice_id,
                        "receipt_id": period.receipt_id,
                    }
                },
                metadata={
                    "reason": reason,
                    "subscription_period_id": period.id,
                    "subscription_id": period.subscription_id,
                    "customer_id": period.subscription.customer_id,
                },
            )
            return period

    @classmethod
    def reissue_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        tax_rate: Decimal | None = None,
        reason: str = "",
    ) -> BillingDocument:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BillingServiceError("A short reason is required to reissue this invoice.")

        invoice = cls._resolve_invoice(organization=organization, invoice_id=invoice_id)
        state = cls.get_invoice_action_state(organization=organization, invoice=invoice)
        if not state["can_reissue"]:
            raise BillingServiceError("Only unpaid issued invoices without receipts or credit notes can be reissued.")
        if tax_rate is None:
            tax_rate = cls.default_tax_rate_for_customer(invoice.customer)
        items = [
            LineItemInput(
                product_id=item.product_id,
                package_id=item.package_id,
                internet_service_id=item.internet_service_id,
                subscription_id=item.subscription_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                base_unit_price=item.base_unit_price,
                discount_amount=item.discount_amount,
                discount_percent=item.discount_percent,
                discount_reason=item.discount_reason,
                pricing_mode=item.pricing_mode,
                billing_behavior=item.billing_behavior,
                promotion_id=item.promotion_id,
                preserve_unit_price=True,
                unit_snapshot=item.unit_snapshot,
            )
            for item in invoice.items.all()
        ]
        with transaction.atomic():
            original_snapshot = cls._document_snapshot(invoice)
            reissued = cls._store_document(
                organization=organization,
                created_by=performed_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer=invoice.customer,
                site=invoice.site,
                issue_date=timezone.localdate(),
                due_date=invoice.due_date,
                status=BillingDocument.Status.DRAFT,
                currency=invoice.currency,
                tax_rate=tax_rate,
                discount_amount=invoice.discount_amount,
                balance_brought_forward=invoice.balance_brought_forward,
                notes=invoice.notes,
                items=items,
                sale_pricing_category=invoice.sale_pricing_category,
                original_invoice=invoice,
            )
            from inventory.services import CartService, InventoryService

            CartService.copy_document_serials(organization=organization, source=invoice, target=reissued)
            InventoryService.ensure_invoice_sale(organization=organization, invoice=reissued)
            BillingDocument.objects.filter(pk=invoice.pk).update(
                status=BillingDocument.Status.SUPERSEDED,
                superseded_by=reissued,
            )
            invoice.refresh_from_db()
            SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).update(
                invoice=reissued,
                status=SubscriptionPeriod.Status.INVOICED,
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_superseded",
                document=invoice,
                old_value=original_snapshot,
                new_value=cls._document_snapshot(invoice),
                metadata={"reason": reason},
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="invoice_reissued",
                document=reissued,
                old_value=original_snapshot,
                new_value=cls._document_snapshot(reissued),
                metadata={"original_invoice_id": invoice.id, "reason": reason},
            )
            return reissued

    @classmethod
    def create_credit_note(
        cls,
        *,
        organization: Organization,
        performed_by,
        invoice_id: int,
        amount: Decimal,
        reason: str,
        issue_date: date | None = None,
        notes: str = "",
    ) -> BillingDocument:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BillingServiceError("A short reason is required to create a credit note.")
        if issue_date is None:
            issue_date = timezone.localdate()
        amount = amount.quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            raise BillingServiceError("Credit note amount must be greater than zero.")
        with transaction.atomic():
            invoice = (
                BillingDocument.objects.unscoped()
                .select_for_update()
                .select_related("customer")
                .filter(
                    id=invoice_id,
                    document_type=BillingDocument.DocumentType.INVOICE,
                    organization=organization,
                )
                .first()
            )
            if invoice is None:
                raise BillingServiceError("Invoice not found.")
            cls._require_same_tenant(organization, invoice)
            state = cls.get_invoice_action_state(organization=organization, invoice=invoice)
            if not state["can_create_credit_note"]:
                raise BillingServiceError("This invoice cannot receive a credit note.")
            if amount > state["credit_capacity"]:
                raise BillingServiceError(
                    f"Credit amount ({amount:,.2f}) cannot exceed the unpaid invoice balance "
                    f"({state['credit_capacity']:,.2f})."
                )

            subtotal_amount = cls._credit_note_subtotal_for_total(total_amount=amount, tax_rate=invoice.tax_rate)
            item = LineItemInput(
                description=f"Credit for invoice {invoice.number}: {reason}",
                quantity=Decimal("-1.00"),
                unit_price=subtotal_amount,
                pricing_mode=BillingLineItem.PricingMode.MANUAL,
            )
            credit_note = cls._store_document(
                organization=organization,
                created_by=performed_by,
                document_type=BillingDocument.DocumentType.CREDIT_NOTE,
                customer=invoice.customer,
                site=invoice.site,
                issue_date=issue_date,
                due_date=None,
                status=BillingDocument.Status.ISSUED,
                currency=invoice.currency,
                tax_rate=invoice.tax_rate,
                notes=notes or reason,
                items=[item],
                sale_pricing_category=invoice.sale_pricing_category,
                corrected_invoice=invoice,
            )
            cls._sync_invoice_after_credit_change(
                organization=organization,
                invoice=invoice,
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="credit_note_created",
                document=credit_note,
                old_value=cls._document_snapshot(invoice),
                new_value=cls._document_snapshot(credit_note),
                metadata={
                    "corrected_invoice_id": invoice.id,
                    "reason": reason,
                    "amount": str(amount),
                },
            )
            return credit_note

    @classmethod
    def _sync_invoice_after_credit_change(
        cls,
        *,
        organization: Organization,
        invoice: BillingDocument,
    ) -> None:
        """Reconcile invoice and linked coverage after an auditable credit change."""
        paid_total = cls.invoice_paid_total(organization=organization, invoice=invoice)
        remaining = cls.invoice_remaining_balance(organization=organization, invoice=invoice)
        if remaining == Decimal("0.00"):
            target_status = BillingDocument.Status.PAID
        elif paid_total > Decimal("0.00"):
            target_status = BillingDocument.Status.PARTIALLY_PAID
        else:
            target_status = BillingDocument.Status.ISSUED
        BillingDocument.objects.filter(pk=invoice.pk, organization=organization).update(status=target_status)
        invoice.status = target_status

        periods = list(
            SubscriptionPeriod.objects.filter(
                organization=organization,
                invoice=invoice,
            ).select_related("subscription")
        )
        if not periods:
            return

        if remaining == Decimal("0.00"):
            receipt = (
                BillingDocument.objects.filter(
                    organization=organization,
                    document_type=BillingDocument.DocumentType.RECEIPT,
                    invoice=invoice,
                )
                .order_by("payment_date", "created_at", "id")
                .last()
            )
            SubscriptionPeriod.objects.filter(
                organization=organization,
                invoice=invoice,
            ).update(
                status=SubscriptionPeriod.Status.PAID,
                receipt=receipt,
                paid_at=timezone.now(),
            )
        else:
            SubscriptionPeriod.objects.filter(
                organization=organization,
                invoice=invoice,
                status=SubscriptionPeriod.Status.PAID,
            ).update(
                status=SubscriptionPeriod.Status.INVOICED,
                receipt=None,
                paid_at=None,
            )

        for subscription_id in {period.subscription_id for period in periods}:
            paid_through = (
                SubscriptionPeriod.objects.filter(
                    organization=organization,
                    subscription_id=subscription_id,
                    status=SubscriptionPeriod.Status.PAID,
                ).aggregate(value=Max("period_end"))["value"]
            )
            CustomerSubscription.objects.filter(
                organization=organization,
                pk=subscription_id,
            ).update(paid_through_date=paid_through)

    @classmethod
    def void_credit_note(
        cls,
        *,
        organization: Organization,
        performed_by,
        credit_note_id: int,
        reason: str,
    ) -> BillingDocument:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BillingServiceError("A short reason is required to void a credit note.")

        with transaction.atomic():
            credit_note = (
                BillingDocument.objects.unscoped()
                .select_for_update()
                .select_related("customer")
                .filter(
                    id=credit_note_id,
                    organization=organization,
                    document_type=BillingDocument.DocumentType.CREDIT_NOTE,
                )
                .first()
            )
            if credit_note is None:
                raise BillingServiceError("Credit note not found.")
            cls._require_same_tenant(organization, credit_note)
            if credit_note.status != BillingDocument.Status.ISSUED:
                raise BillingServiceError("Only an issued credit note can be voided.")
            if credit_note.corrected_invoice_id is None:
                raise BillingServiceError("This credit note is not linked to an invoice.")

            invoice = BillingDocument.objects.unscoped().select_for_update().get(
                pk=credit_note.corrected_invoice_id,
                organization=organization,
            )
            if invoice.status in {BillingDocument.Status.VOID, BillingDocument.Status.SUPERSEDED}:
                raise BillingServiceError("A credit note cannot be voided after its invoice is closed by reversal.")

            old_snapshot = cls._document_snapshot(credit_note)
            BillingDocument.objects.filter(pk=credit_note.pk, organization=organization).update(
                status=BillingDocument.Status.VOID,
                voided_at=timezone.now(),
            )
            credit_note.refresh_from_db()
            cls._sync_invoice_after_credit_change(
                organization=organization,
                invoice=invoice,
            )
            cls._log_action(
                organization=organization,
                performed_by=performed_by,
                action_type="credit_note_voided",
                document=credit_note,
                old_value=old_snapshot,
                new_value=cls._document_snapshot(credit_note),
                metadata={
                    "corrected_invoice_id": invoice.id,
                    "reason": reason,
                },
            )
            return credit_note

    @classmethod
    def create_receipt_from_invoice(
        cls,
        *,
        organization: Organization,
        created_by,
        invoice_id: int,
        amount_paid: Decimal,
        payment_date: date | None = None,
        payment_method: str = "",
        payment_reference: str = "",
        notes: str = "",
    ) -> BillingDocument:
        with transaction.atomic():
            invoice = (
                BillingDocument.objects.unscoped()
                .select_for_update()
                .select_related('customer')
                .filter(pk=invoice_id, document_type=BillingDocument.DocumentType.INVOICE)
                .first()
            )
            if invoice is None:
                raise BillingServiceError('Invalid invoice.')
            cls._require_same_tenant(organization, invoice)

            amount_paid = amount_paid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if amount_paid <= Decimal("0.00"):
                raise BillingServiceError("Amount paid must be greater than zero.")

            payment_reference = (payment_reference or "").strip()
            if payment_reference:
                reference_owner = BillingDocument.objects.unscoped().filter(
                    organization=organization,
                    payment_reference=payment_reference,
                ).first()
                if reference_owner is not None:
                    if (
                        reference_owner.document_type == BillingDocument.DocumentType.RECEIPT
                        and reference_owner.invoice_id == invoice.id
                    ):
                        if reference_owner.total != amount_paid:
                            raise BillingServiceError(
                                "This payment reference was already used for a different payment amount."
                            )
                        return reference_owner
                    raise BillingServiceError("This payment reference has already been used for another document.")

            state = cls.get_invoice_action_state(organization=organization, invoice=invoice)
            if not state["can_register_payment"]:
                raise BillingServiceError("This invoice cannot accept a payment in its current state.")
            policy = cls.invoice_payment_policy(organization=organization, invoice=invoice)
            remaining_balance = state["remaining_balance"]
            if amount_paid > remaining_balance:
                raise BillingServiceError(
                    f"Payment cannot exceed the remaining balance ({remaining_balance:,.2f})."
                )

            from inventory.services import InventoryError, InventoryService

            inventory_sale = policy['requires_full_payment']
            if inventory_sale and amount_paid != remaining_balance:
                raise BillingServiceError('Inventory sales require complete payment of the full remaining balance.')
            payment_method = (payment_method or '').strip().lower()
            if inventory_sale and payment_method not in {'cash', 'bank', 'mobile_money', 'card', 'other'}:
                raise BillingServiceError('Select Cash, Bank, Mobile money, Credit/Debit card, or Other.')
            if payment_date is None:
                payment_date = timezone.localdate()
            remaining_after_payment = (remaining_balance - amount_paid).quantize(Decimal('0.01'))
            is_partial = remaining_after_payment > Decimal('0.00')
            receipt_item = LineItemInput(
                description=f"Payment for invoice {invoice.number}",
                quantity=Decimal("1.00"),
                unit_price=amount_paid,
                pricing_mode=BillingLineItem.PricingMode.MANUAL,
            )
            try:
                receipt = cls._store_document(
                    organization=organization,
                    created_by=created_by,
                    document_type=BillingDocument.DocumentType.RECEIPT,
                    customer=invoice.customer,
                    site=invoice.site,
                    issue_date=timezone.localdate(),
                    due_date=None,
                    status=BillingDocument.Status.PAID,
                    currency=invoice.currency,
                    tax_rate=Decimal("0.00"),
                    notes=notes,
                    items=[receipt_item],
                    sale_pricing_category=invoice.sale_pricing_category,
                    invoice=invoice,
                    payment_date=payment_date,
                    payment_method=payment_method,
                    payment_reference=payment_reference,
                )
            except IntegrityError as exc:
                if payment_reference:
                    raise BillingServiceError("This payment reference has already been used.") from exc
                raise

            if inventory_sale:
                try:
                    InventoryService.complete_invoice_sale(
                        organization=organization,
                        invoice=invoice,
                        receipt=receipt,
                        actor=created_by,
                    )
                except InventoryError as exc:
                    raise BillingServiceError(str(exc)) from exc

            new_invoice_status = BillingDocument.Status.PARTIALLY_PAID if is_partial else BillingDocument.Status.PAID
            BillingDocument.objects.filter(id=invoice.id).update(status=new_invoice_status)
            invoice.refresh_from_db()

            if not is_partial:
                linked_periods = list(
                    SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).select_related("subscription")
                )
                SubscriptionPeriod.objects.filter(invoice=invoice, organization=organization).update(
                    status=SubscriptionPeriod.Status.PAID,
                    receipt=receipt,
                    paid_at=timezone.now(),
                )
                for period in linked_periods:
                    subscription = CustomerSubscription.objects.select_for_update().get(
                        id=period.subscription_id,
                        organization=organization,
                    )
                    if (
                        subscription.paid_through_date is None
                        or period.period_end > subscription.paid_through_date
                    ):
                        CustomerSubscription.objects.filter(id=subscription.id).update(
                            paid_through_date=period.period_end
                        )

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=created_by,
                performed_by=created_by,
                action="billing.payment.registered",
                action_type="billing.payment.registered",
                object_type="BillingDocument",
                object_id=str(receipt.id),
                document_id=str(receipt.id),
                old_value={},
                new_value={
                    "invoice_id": invoice.id,
                    "amount_paid": str(amount_paid),
                    "invoice_total": str(invoice.total),
                    "credited_total": str(state["credited_total"]),
                    "is_partial": is_partial,
                    "payment_reference": payment_reference,
                    "remaining_balance_after": str(remaining_after_payment),
                },
                metadata={
                    "invoice_id": invoice.id,
                    "amount_paid": str(amount_paid),
                    "credited_total": str(state["credited_total"]),
                    "is_partial": is_partial,
                    "payment_reference": payment_reference,
                    "remaining_balance_after": str(remaining_after_payment),
                },
                performed_at=timezone.now(),
            )

            return receipt


class InvoiceLifecycleService:
    @classmethod
    def get_action_state(cls, *, organization: Organization, invoice: BillingDocument) -> dict:
        return BillingService.get_invoice_action_state(organization=organization, invoice=invoice)


class QuotationLifecycleService:
    @classmethod
    def get_action_state(cls, *, organization: Organization, quotation: BillingDocument) -> dict:
        return BillingService.get_quotation_action_state(organization=organization, quotation=quotation)

    @classmethod
    def send(cls, *, organization: Organization, performed_by, quotation_id: int, reason: str = "") -> BillingDocument:
        return BillingService.transition_quotation_status(
            organization=organization,
            performed_by=performed_by,
            quotation_id=quotation_id,
            to_status=BillingDocument.Status.SENT,
            reason=reason,
        )

    @classmethod
    def accept(cls, *, organization: Organization, performed_by, quotation_id: int, reason: str = "") -> BillingDocument:
        return BillingService.transition_quotation_status(
            organization=organization,
            performed_by=performed_by,
            quotation_id=quotation_id,
            to_status=BillingDocument.Status.ACCEPTED,
            reason=reason,
        )

    @classmethod
    def reject(cls, *, organization: Organization, performed_by, quotation_id: int, reason: str = "") -> BillingDocument:
        return BillingService.transition_quotation_status(
            organization=organization,
            performed_by=performed_by,
            quotation_id=quotation_id,
            to_status=BillingDocument.Status.REJECTED,
            reason=reason,
        )

    @classmethod
    def expire(cls, *, organization: Organization, performed_by, quotation_id: int, reason: str = "") -> BillingDocument:
        return BillingService.transition_quotation_status(
            organization=organization,
            performed_by=performed_by,
            quotation_id=quotation_id,
            to_status=BillingDocument.Status.EXPIRED,
            reason=reason,
        )


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def first_day_of_month(value: date) -> date:
    return date(value.year, value.month, 1)


def last_day_of_month(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


class SubscriptionBillingService:
    @classmethod
    def format_invoice_description(cls, *, subscription: CustomerSubscription, period: SubscriptionPeriod) -> str:
        """Describe the exact inclusive service period without exposing office topology."""
        package = subscription.package
        speed = (package.speed or "Speed not specified").strip()
        start = period.period_start.strftime("%d %b %Y")
        end = period.period_end.strftime("%d %b %Y")
        return f"{package.name} ({speed}) subscription - service period: {start} - {end}"

    @classmethod
    def _raise_cross_tenant(cls):
        raise PermissionDenied("Cross-tenant object access denied.")

    @classmethod
    def get_or_create_subscription(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        package: Package,
        site: CustomerSite | None = None,
        internet_service: InternetService | None = None,
        start_date: date | None = None,
        promotion: Promotion | None = None,
    ) -> CustomerSubscription:
        if start_date is None:
            start_date = timezone.localdate()
        if customer.organization_id != organization.id or package.organization_id != organization.id:
            cls._raise_cross_tenant()
        if site is None:
            from customers.services import CustomerService

            site = CustomerService.ensure_primary_site(organization=organization, customer=customer)
        elif site.organization_id != organization.id or site.customer_id != customer.id:
            cls._raise_cross_tenant()
        if internet_service is not None:
            if (
                internet_service.organization_id != organization.id
                or internet_service.customer_id != customer.id
                or internet_service.site_id != site.id
            ):
                cls._raise_cross_tenant()
            current = CustomerSubscription.objects.filter(
                organization=organization,
                internet_service=internet_service,
                status=CustomerSubscription.Status.ACTIVE,
            ).first()
            if current is not None:
                if current.package_id != package.id:
                    raise BillingServiceError("This Internet service already has an active subscription.")
                return current
        subscription, _ = CustomerSubscription.objects.get_or_create(
            organization=organization,
            tenant=organization,
            customer=customer,
            site=site,
            internet_service=internet_service,
            package=package,
            status=CustomerSubscription.Status.ACTIVE,
            defaults={
                "start_date": start_date,
                "billing_day": start_date.day,
                "monthly_fee_at_signup": package.monthly_fee,
                "promotion": promotion,
            },
        )
        return subscription

    @classmethod
    def sync_customer_package_subscriptions(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        site: CustomerSite | None = None,
        packages=None,
        start_date: date | None = None,
    ) -> list[CustomerSubscription]:
        if site is None:
            from customers.services import CustomerService

            site = CustomerService.ensure_primary_site(organization=organization, customer=customer)
        subscriptions = []
        package_iterable = packages if packages is not None else customer.packages.filter(organization=organization, is_active=True)
        active_package_ids = {p.id for p in package_iterable if p.organization_id == organization.id and p.is_active}
        for package in package_iterable:
            if package.organization_id != organization.id or not package.is_active:
                continue
            subscriptions.append(
                cls.get_or_create_subscription(
                    organization=organization,
                    customer=customer,
                    package=package,
                    site=site,
                    start_date=start_date,
                )
            )
        # Cancel active subscriptions for packages no longer assigned
        cls._cancel_removed_subscriptions(
            organization=organization,
            site=site,
            active_package_ids=active_package_ids,
        )
        return subscriptions

    @classmethod
    def _cancel_removed_subscriptions(
        cls,
        *,
        organization: Organization,
        site: CustomerSite,
        active_package_ids: set[int],
    ) -> None:
        """Cancel any active subscriptions on a site whose package is no longer assigned."""
        stale = CustomerSubscription.objects.filter(
            organization=organization,
            site=site,
            status=CustomerSubscription.Status.ACTIVE,
        ).exclude(package_id__in=active_package_ids)
        for sub in stale:
            has_open_invoice = SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription=sub,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            ).exists()
            if not has_open_invoice:
                CustomerSubscription.objects.filter(pk=sub.pk).update(
                    status=CustomerSubscription.Status.CANCELLED
                )

    @classmethod
    def sync_customer_site_package_subscriptions(
        cls,
        *,
        organization: Organization,
        customer: Customer,
        site: CustomerSite,
        packages=None,
        start_date: date | None = None,
    ) -> list[CustomerSubscription]:
        subscriptions = []
        package_iterable = packages if packages is not None else site.packages.filter(organization=organization, is_active=True)
        active_package_ids = {p.id for p in package_iterable if p.organization_id == organization.id and p.is_active}
        for package in package_iterable:
            if package.organization_id != organization.id or not package.is_active:
                continue
            subscriptions.append(
                cls.get_or_create_subscription(
                    organization=organization,
                    customer=customer,
                    package=package,
                    site=site,
                    start_date=start_date,
                )
            )
        # Cancel active subscriptions for packages no longer assigned
        cls._cancel_removed_subscriptions(
            organization=organization,
            site=site,
            active_package_ids=active_package_ids,
        )
        return subscriptions

    @classmethod
    def best_package_promotion(
        cls,
        *,
        organization: Organization,
        package: Package,
        months: int,
        amount: Decimal,
        when: date,
    ) -> Promotion | None:
        candidates = Promotion.objects.filter(
            organization=organization,
            is_active=True,
            applies_to=Promotion.AppliesTo.PACKAGE,
        ).filter(Q(package=package) | Q(package__isnull=True))
        best = None
        best_value = Decimal("0.00")
        for promo in candidates:
            if not promo.is_valid_for(when=when):
                continue
            if months < promo.minimum_months or amount < promo.minimum_amount:
                continue
            value = Decimal("0.00")
            if promo.reward_type == Promotion.RewardType.PERCENT:
                value = (amount * (promo.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            elif promo.reward_type == Promotion.RewardType.FIXED:
                value = promo.reward_value
            elif promo.reward_type == Promotion.RewardType.FREE_MONTHS:
                value = package.monthly_fee * promo.reward_value
            if value > best_value:
                best = promo
                best_value = value
        return best

    @classmethod
    def calculate_period_amount(
        cls,
        *,
        subscription: CustomerSubscription,
        months: int,
        promotion: Promotion | None,
        when: date,
    ) -> dict:
        original = (subscription.monthly_fee_at_signup * Decimal(months)).quantize(Decimal("0.01"))
        discount = Decimal("0.00")
        free_months = 0
        if promotion and promotion.is_valid_for(when=when):
            if promotion.reward_type == Promotion.RewardType.PERCENT:
                discount = (original * (promotion.reward_value / Decimal("100.00"))).quantize(Decimal("0.01"))
            elif promotion.reward_type == Promotion.RewardType.FIXED:
                discount = min(promotion.reward_value, original).quantize(Decimal("0.01"))
            elif promotion.reward_type == Promotion.RewardType.FREE_MONTHS:
                free_months = int(promotion.reward_value)
        final = max(original - discount, Decimal("0.00")).quantize(Decimal("0.01"))
        return {"original": original, "discount": discount, "final": final, "free_months": free_months}

    @classmethod
    def create_period(
        cls,
        *,
        organization: Organization,
        subscription: CustomerSubscription,
        period_start: date,
        months: int = 1,
        promotion: Promotion | None = None,
    ) -> SubscriptionPeriod:
        if subscription.organization_id != organization.id:
            cls._raise_cross_tenant()
        period_start = first_day_of_month(period_start)
        amount = cls.calculate_period_amount(
            subscription=subscription,
            months=months,
            promotion=promotion,
            when=period_start,
        )
        paid_until_month = add_months(period_start, months + amount["free_months"] - 1)
        period_end = last_day_of_month(paid_until_month)
        with transaction.atomic():
            CustomerSubscription.objects.unscoped().select_for_update().get(
                pk=subscription.pk,
                organization=organization,
            )
            existing = SubscriptionPeriod.objects.unscoped().filter(
                organization=organization,
                subscription=subscription,
                period_start=period_start,
            ).first()
            if existing is not None:
                if existing.status == SubscriptionPeriod.Status.CANCELLED:
                    replaced_invoice_id = existing.invoice_id
                    if replaced_invoice_id is not None:
                        replaced_status = (
                            BillingDocument.objects.unscoped()
                            .filter(
                                pk=replaced_invoice_id,
                                organization=organization,
                                document_type=BillingDocument.DocumentType.INVOICE,
                            )
                            .values_list("status", flat=True)
                            .first()
                        )
                        if replaced_status != BillingDocument.Status.VOID:
                            raise BillingServiceError(
                                "This cancelled billing period is linked to an invoice that is not void. "
                                "Resolve the invoice state before retrying the renewal."
                            )

                    # A void invoice is immutable, but the intended coverage period can be
                    # retried. Re-open the same period so its unique business key remains
                    # stable and the replacement invoice can take over the active link.
                    SubscriptionPeriod.objects.unscoped().filter(pk=existing.pk).update(
                        period_end=period_end,
                        months=months,
                        free_months=amount["free_months"],
                        original_amount=amount["original"],
                        discount_amount=amount["discount"],
                        final_amount=amount["final"],
                        status=SubscriptionPeriod.Status.PENDING,
                        invoice=None,
                        receipt=None,
                        promotion=promotion,
                        paid_at=None,
                    )
                    existing.refresh_from_db()
                    existing._replaced_void_invoice_id = replaced_invoice_id
                return existing
            overlap = (
                SubscriptionPeriod.objects.unscoped()
                .filter(
                    organization=organization,
                    subscription=subscription,
                    period_start__lte=period_end,
                    period_end__gte=period_start,
                )
                .exclude(status=SubscriptionPeriod.Status.CANCELLED)
                .order_by("period_start", "id")
                .first()
            )
            if overlap is not None:
                raise BillingServiceError(
                    "Coverage overlaps an existing subscription period "
                    f"({overlap.period_start:%d %b %Y} – {overlap.period_end:%d %b %Y})."
                )
            return SubscriptionPeriod.objects.unscoped().create(
                organization=organization,
                tenant=organization,
                subscription=subscription,
                period_start=period_start,
                period_end=period_end,
                months=months,
                free_months=amount["free_months"],
                original_amount=amount["original"],
                discount_amount=amount["discount"],
                final_amount=amount["final"],
                promotion=promotion,
            )

    @classmethod
    def create_invoice_for_period(
        cls,
        *,
        organization: Organization,
        created_by,
        period: SubscriptionPeriod,
        due_date: date | None = None,
    ) -> BillingDocument:
        if period.organization_id != organization.id:
            cls._raise_cross_tenant()
        if period.invoice_id:
            return period.invoice
        subscription = period.subscription
        description = cls.format_invoice_description(subscription=subscription, period=period)
        if period.free_months:
            description += f" (includes {period.free_months} complimentary month{'s' if period.free_months != 1 else ''})"
        item = LineItemInput(
            package_id=subscription.package_id,
            internet_service_id=subscription.internet_service_id,
            subscription_id=subscription.id,
            description=description,
            quantity=Decimal(period.months),
            base_unit_price=subscription.monthly_fee_at_signup,
            unit_price=subscription.monthly_fee_at_signup,
            discount_amount=period.discount_amount,
            discount_reason=period.promotion.name if period.promotion_id else "",
            pricing_mode=BillingLineItem.PricingMode.PROMOTION if period.promotion_id else BillingLineItem.PricingMode.RETAIL,
            billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
            promotion_id=period.promotion_id,
        )
        with transaction.atomic():
            invoice = BillingService.create_document(
                organization=organization,
                created_by=created_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=subscription.customer_id,
                site_id=subscription.site_id,
                issue_date=timezone.localdate(),
                due_date=due_date,
                status=BillingDocument.Status.ISSUED,
                notes=(
                    "Subscription renewal for service period "
                    f"{period.period_start:%d %b %Y} - {period.period_end:%d %b %Y}."
                ),
                items=[item],
            )
            SubscriptionPeriod.objects.filter(id=period.id).update(
                invoice=invoice,
                status=SubscriptionPeriod.Status.INVOICED,
            )
            period.refresh_from_db()
            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=created_by,
                performed_by=created_by,
                action="subscription.invoice_created",
                action_type="subscription.invoice_created",
                object_type="SubscriptionPeriod",
                object_id=str(period.id),
                document_id=str(invoice.id),
                metadata={"subscription_id": subscription.id, "invoice_id": invoice.id},
                performed_at=timezone.now(),
            )
            return invoice

    @classmethod
    def cancel_subscription(
        cls,
        *,
        organization: Organization,
        performed_by,
        subscription_id: int,
        reason: str,
    ) -> CustomerSubscription:
        from billing.models import SubscriptionPeriod

        reason = (reason or "").strip()
        if not reason:
            raise BillingServiceError("A reason is required to cancel a subscription.")

        subscription = (
            CustomerSubscription.objects.unscoped()
            .select_related("customer", "package")
            .filter(id=subscription_id, organization=organization)
            .first()
        )
        if subscription is None:
            raise BillingServiceError("Subscription not found.")
        if subscription.organization_id != organization.id:
            cls._raise_cross_tenant()
        if subscription.status == CustomerSubscription.Status.CANCELLED:
            raise BillingServiceError("Subscription is already cancelled.")

        unpaid = SubscriptionPeriod.objects.filter(
            organization=organization,
            subscription=subscription,
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        ).exists()
        if unpaid:
            raise BillingServiceError(
                "This subscription has open invoices. Void or settle them first using 'Resolve issue' on each period."
            )

        with transaction.atomic():
            CustomerSubscription.objects.filter(pk=subscription.pk).update(
                status=CustomerSubscription.Status.CANCELLED
            )
            subscription.refresh_from_db()
            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=performed_by,
                performed_by=performed_by,
                action="subscription.cancelled",
                action_type="subscription.cancelled",
                object_type="CustomerSubscription",
                object_id=str(subscription.id),
                old_value={"status": CustomerSubscription.Status.ACTIVE},
                new_value={"status": CustomerSubscription.Status.CANCELLED},
                metadata={
                    "reason": reason,
                    "customer_id": subscription.customer_id,
                    "package_id": subscription.package_id,
                },
                performed_at=timezone.now(),
            )
            return subscription

    @classmethod
    @transaction.atomic
    def renew(
        cls,
        *,
        organization: Organization,
        created_by,
        subscription_id: int,
        period_start: date,
        months: int = 1,
        promotion_id: int | None = None,
        due_date: date | None = None,
        issue_invoice: bool = True,
    ) -> SubscriptionPeriod:
        subscription = CustomerSubscription.objects.unscoped().select_related("customer", "package").filter(
            id=subscription_id,
            organization=organization,
            status=CustomerSubscription.Status.ACTIVE,
        ).first()
        if subscription is None:
            raise BillingServiceError("Subscription not found.")
        if subscription.customer.status != Customer.Status.ACTIVE:
            raise BillingServiceError("Customer is not Active. Renewal is not allowed.")
        promotion = None
        if promotion_id:
            promotion = Promotion.objects.unscoped().filter(id=promotion_id, organization=organization).first()
            if promotion is None:
                raise BillingServiceError("Promotion not found.")
        if promotion is None:
            promotion = cls.best_package_promotion(
                organization=organization,
                package=subscription.package,
                months=months,
                amount=subscription.monthly_fee_at_signup * Decimal(months),
                when=period_start,
            )
        period = cls.create_period(
            organization=organization,
            subscription=subscription,
            period_start=period_start,
            months=months,
            promotion=promotion,
        )
        if issue_invoice:
            replacement_invoice = cls.create_invoice_for_period(
                organization=organization,
                created_by=created_by,
                period=period,
                due_date=due_date,
            )
            replaced_invoice_id = getattr(period, "_replaced_void_invoice_id", None)
            if replaced_invoice_id:
                BillingDocument.objects.unscoped().filter(
                    pk=replacement_invoice.pk,
                    organization=organization,
                ).update(original_invoice_id=replaced_invoice_id)
                AuditLog.objects.create(
                    organization=organization,
                    tenant=organization,
                    actor=created_by,
                    performed_by=created_by,
                    action="subscription.period_reopened",
                    action_type="subscription.period_reopened",
                    object_type="SubscriptionPeriod",
                    object_id=str(period.id),
                    document_id=str(replacement_invoice.id),
                    old_value={
                        "status": SubscriptionPeriod.Status.CANCELLED,
                        "invoice_id": replaced_invoice_id,
                    },
                    new_value={
                        "status": SubscriptionPeriod.Status.INVOICED,
                        "invoice_id": replacement_invoice.id,
                    },
                    metadata={
                        "subscription_id": subscription.id,
                        "replaced_void_invoice_id": replaced_invoice_id,
                        "replacement_invoice_id": replacement_invoice.id,
                    },
                    performed_at=timezone.now(),
                )
            period.refresh_from_db()
        return period


class BillingSheetService:
    @classmethod
    def _next_reference(cls, *, organization: Organization) -> str:
        from django.db.models import Max
        from django.utils import timezone as tz

        today = tz.localdate()
        prefix = f"BS-{today:%Y%m%d}-"
        result = BillingSheet.objects.unscoped().filter(
            organization=organization,
            reference_number__startswith=prefix,
        ).aggregate(max_ref=Max("reference_number"))
        max_ref = result.get("max_ref") or ""
        try:
            last_seq = int(max_ref.split("-")[-1])
        except (ValueError, AttributeError):
            last_seq = 0
        return f"{prefix}{last_seq + 1:04d}"

    @classmethod
    def create_sheet(
        cls,
        *,
        organization: Organization,
        created_by,
        customer_id: int,
        title: str,
        notes: str = "",
    ) -> "BillingSheet":
        customer = BillingService._resolve_customer(organization=organization, customer_id=customer_id)
        reference = cls._next_reference(organization=organization)
        return BillingSheet.objects.create(
            organization=organization,
            tenant=organization,
            customer=customer,
            reference_number=reference,
            title=title,
            notes=notes,
            status=BillingSheet.Status.OPEN,
            created_by=created_by,
        )

    @classmethod
    def generate_invoice(
        cls,
        *,
        organization: Organization,
        performed_by,
        sheet_id: int,
        due_date: date | None = None,
    ) -> BillingDocument:
        from django.db import transaction

        with transaction.atomic():
            sheet = (
                BillingSheet.objects.unscoped()
                .select_for_update()
                .select_related("customer")
                .prefetch_related("items")
                .filter(pk=sheet_id, organization=organization)
                .first()
            )
            if sheet is None:
                raise BillingServiceError("Billing sheet not found.")
            if sheet.organization_id != organization.id:
                raise BillingServiceError("Cross-tenant access denied.")
            if sheet.status != BillingSheet.Status.OPEN:
                raise BillingServiceError("Only OPEN billing sheets can be converted to an invoice.")

            items = list(sheet.items.all())
            if not items:
                raise BillingServiceError("Add at least one billing item before generating an invoice.")

            line_inputs = [
                LineItemInput(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
                for item in items
            ]

            invoice = BillingService.create_document(
                organization=organization,
                created_by=performed_by,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=sheet.customer_id,
                issue_date=timezone.localdate(),
                due_date=due_date,
                status=BillingDocument.Status.DRAFT,
                notes=f"Generated from billing sheet {sheet.reference_number}: {sheet.title}",
                items=line_inputs,
            )

            BillingSheet.objects.filter(pk=sheet.pk).update(
                status=BillingSheet.Status.INVOICED,
                invoice=invoice,
            )

            AuditLog.objects.create(
                organization=organization,
                tenant=organization,
                actor=performed_by,
                performed_by=performed_by,
                action="billing_sheet.invoice_generated",
                action_type="billing_sheet.invoice_generated",
                object_type="BillingSheet",
                object_id=str(sheet.id),
                document_id=str(invoice.id),
                new_value={"invoice_id": invoice.id, "sheet_reference": sheet.reference_number},
                performed_at=timezone.now(),
            )

            return invoice
