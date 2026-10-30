# Receipt print and PDF amount contract

Receipt preview (`receipt_print`) and PDF download (`document_pdf` for receipts)
share `_receipt_print_context` and `billing/receipt_print_tra.html`. The summary
is prepared by the read-only `billing.receipt_presentation.receipt_amount_summary`.

## Historical amounts

Receipts linked to invoices record a cash payment, not another sale. For example,
receipt 366 records 7 while its source invoice stores subtotal 10, sale discount 3,
VAT 0 and total 7. Receipt subtotal/discount/tax fields must not be rewritten to
duplicate invoice amounts. Standalone legacy receipts fall back to their own
stored amounts; new standalone receipt creation remains prohibited.

Printed amounts come from the immutable source document and its stored line
discounts, never current catalog prices, tax eligibility or customer defaults:

```text
Printed subtotal (before discounts) = stored source subtotal + stored item discounts
Printed discount = stored item discounts + stored document discount
Printed VAT = stored source tax amount
Printed total = stored source total
Amount received = this receipt's stored total
```

This gross printed subtotal intentionally differs from the billing model's net
subtotal when item discounts exist. Each item retains its saved unit price,
quantity, unit, item discount and net line amount. No discount is deducted twice;
VAT is never recalculated at print time. Existing currency formatting applies.

Zero discount and zero VAT remain visible. If this receipt's amount differs from
the sale total, the summary explicitly says **INVOICE TOTAL** and explains that
**AMOUNT RECEIVED** refers only to this payment. Later installments do not alter
that receipt's printed financial breakdown. No live account balances, later
credits, or later receipts are presented as historical cash paid by this receipt.

## Security and compatibility

Existing login, canonical billing-read permissions, tenant and owner visibility
remain enforced at both HTTP routes. The source link must have the same tenant,
organization and currency as the receipt and must refer to an invoice. A damaged
cross-tenant source link fails closed before its items are read. Source lines are
filtered by both tenant and organization. Template autoescaping remains enabled;
private costs and staff notes are not added to customer printouts.

This is a read-only display change: no migration, record backfill, reissue, payment,
stock mutation, permission grant or dependency change is needed. It preserves the
existing thermal template and PDF/HTML fallback behavior. Regression coverage is
`billing.tests_receipt_totals` plus the existing billing and checkout suites.

## Research references

- [Financial terms](financial_terms_guide.md): billing net subtotal, item/document
  discounts, tax, receipts, installments and credit-note distinctions.
- [Django 5.2 templates](https://docs.djangoproject.com/en/5.2/ref/templates/language/):
  keep arithmetic in Python and preserve automatic HTML escaping.
- [WeasyPrint web integration and print CSS](https://doc.courtbouillon.org/weasyprint/stable/common_use_cases.html):
  share server-rendered HTML/CSS for print/PDF; do not introduce a second frontend
  calculation path or a Tailwind CDN dependency into receipt printing.
