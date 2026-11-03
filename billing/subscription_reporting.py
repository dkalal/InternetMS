"""Tenant-scoped, read-only subscription billing reporting.

Cash and debt use posted document totals. Forecasts use the persisted pre-tax
period charge or captured agreement fee; no tax is invented for future months.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .models import BillingDocument, CustomerSubscription, SubscriptionPeriod


ZERO = Decimal("0.00")
CENT = Decimal("0.01")
OPEN_STATUSES = (
    BillingDocument.Status.SENT, BillingDocument.Status.ISSUED,
    BillingDocument.Status.PARTIALLY_PAID, BillingDocument.Status.APPROVED,
)
ELIGIBLE_STATUSES = OPEN_STATUSES + (BillingDocument.Status.PAID,)


def next_month(month):
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def month_starts(first, last):
    month = first.replace(day=1)
    while month <= last:
        yield month
        month = next_month(month)


def allocate(amount, weights):
    """Proportional cent allocation; the final share absorbs rounding residue."""
    amount = Decimal(amount).quantize(CENT)
    total = sum(weights, ZERO)
    if not weights or total <= 0:
        return [ZERO for _ in weights]
    shares = [(amount * weight / total).quantize(CENT, rounding=ROUND_HALF_UP) for weight in weights[:-1]]
    return shares + [amount - sum(shares, ZERO)]


def subscription_snapshot(organization, selected_month):
    """Return account balances, cash receipts, and service-month forecasts.

    An invoice is selected by its period linkage; each invoice and receipt is
    counted once. Mixed-invoice cash and debt are attributed in proportion to
    subscription line totals. This is reporting attribution only.
    """
    year_start = date(selected_month.year, 1, 1)
    year_end = date(selected_month.year, 12, 31)
    periods = list(SubscriptionPeriod.objects.filter(
        organization=organization,
    ).exclude(status=SubscriptionPeriod.Status.CANCELLED).select_related("subscription", "invoice"))
    invoices = {period.invoice_id: period.invoice for period in periods if period.invoice_id}
    invoice_ids = set(invoices)
    period_charge_by_invoice = defaultdict(lambda: ZERO)
    for period in periods:
        if period.invoice_id:
            period_charge_by_invoice[period.invoice_id] += period.final_amount
    lines = list(BillingDocument.objects.filter(
        organization=organization, id__in=invoice_ids,
    ).prefetch_related("items"))
    invoices = {invoice.id: invoice for invoice in lines}
    ratios = {}
    for invoice in lines:
        subscription_total = sum((item.line_total for item in invoice.items.all() if item.subscription_id), ZERO)
        line_total = sum((item.line_total for item in invoice.items.all()), ZERO)
        if subscription_total > 0 and line_total > 0:
            ratios[invoice.id] = min(subscription_total / line_total, Decimal(1))
        else:
            # Legacy renewal lines have no subscription FK; the linked period
            # retains their historical subscription charge.
            ratios[invoice.id] = min(period_charge_by_invoice[invoice.id] / invoice.total, Decimal(1)) if invoice.total > 0 else ZERO
    receipts = list(BillingDocument.objects.filter(
        organization=organization, document_type=BillingDocument.DocumentType.RECEIPT,
        invoice_id__in=invoice_ids,
    ).values("invoice_id", "payment_date", "total"))
    credits = list(BillingDocument.objects.filter(
        organization=organization, document_type=BillingDocument.DocumentType.CREDIT_NOTE,
        corrected_invoice_id__in=invoice_ids, status=BillingDocument.Status.ISSUED,
    ).values("corrected_invoice_id", "total"))
    paid_by_invoice = defaultdict(lambda: ZERO)
    credit_by_invoice = defaultdict(lambda: ZERO)
    cash = ZERO
    month_end = next_month(selected_month)
    for receipt in receipts:
        invoice = invoices.get(receipt["invoice_id"])
        if invoice is None or invoice.status not in ELIGIBLE_STATUSES:
            continue
        paid_by_invoice[invoice.id] += receipt["total"]
        if receipt["payment_date"] and selected_month <= receipt["payment_date"] < month_end:
            cash += (receipt["total"] * ratios[invoice.id]).quantize(CENT)
    for credit in credits:
        credit_by_invoice[credit["corrected_invoice_id"]] += max(-credit["total"], ZERO)
    balances = {}
    outstanding = ZERO
    for invoice in lines:
        if invoice.status not in ELIGIBLE_STATUSES:
            continue
        balance = max(invoice.total - paid_by_invoice[invoice.id] - credit_by_invoice[invoice.id], ZERO)
        attributed = (balance * ratios[invoice.id]).quantize(CENT)
        balances[invoice.id] = attributed
        outstanding += attributed

    charges = defaultdict(lambda: ZERO)
    covered = defaultdict(set)
    for period in periods:
        if period.period_end < year_start or period.period_start > year_end:
            continue
        months = list(month_starts(period.period_start, period.period_end))
        charged = min(period.months, len(months))
        weights = [Decimal(1) if index < charged else ZERO for index in range(len(months))]
        shares = allocate(period.final_amount, weights)
        for month, share in zip(months, shares):
            covered[period.subscription_id].add(month)
            if month.year == selected_month.year:
                charges[month] += share
    subscriptions = CustomerSubscription.objects.filter(
        organization=organization, start_date__lte=year_end,
        status=CustomerSubscription.Status.ACTIVE,
    ).select_related("promotion")
    # Cancelled agreements without an effective end are excluded from forecast;
    # their persisted historical periods remain included above.
    for subscription in subscriptions:
        if subscription.status == CustomerSubscription.Status.PAUSED:
            continue  # No persisted effective pause dates exist for a defensible forecast.
        end = min(subscription.end_date or year_end, year_end)
        for month in month_starts(max(subscription.start_date, year_start), end):
            if month not in covered[subscription.id]:
                forecast = subscription.monthly_fee_at_signup
                if subscription.promotion_id:
                    from .services import SubscriptionBillingService
                    amount = SubscriptionBillingService.calculate_period_amount(
                        subscription=subscription, months=1,
                        promotion=subscription.promotion, when=month,
                    )
                    if not amount["free_months"]:
                        forecast = amount["final"]
                charges[month] += forecast
    return {
        "cash": cash, "outstanding": outstanding,
        "monthly_expected": charges[selected_month],
        "yearly_expected": sum((charges[date(selected_month.year, m, 1)] for m in range(1, 13)), ZERO),
        "balances": balances,
    }
