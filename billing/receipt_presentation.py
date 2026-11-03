"""Read-only receipt amounts derived from persisted billing snapshots."""
from decimal import Decimal


def receipt_amount_summary(*, receipt, source, items):
    # Billing subtotal is already net of item discounts. Add those discounts
    # back for a customer-facing gross subtotal, then show every discount once.
    # Never recalculate VAT from today's product tax eligibility or prices.
    item_discount = sum((item.discount_amount for item in items), Decimal('0.00'))
    return {
        'subtotal': source.subtotal + item_discount,
        'discount': source.discount_amount + item_discount,
        'tax_rate': source.tax_rate,
        'tax': source.tax_amount,
        'total': source.total,
        'amount_received': receipt.total,
        'payment_differs_from_total': receipt.total != source.total,
    }
