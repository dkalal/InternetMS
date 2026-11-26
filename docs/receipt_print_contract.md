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
VAT is never recalculated at print time. The item table always shows a Discount
column between Qty / Unit and Amount, including 0 for undiscounted lines. A
positive stored line discount is displayed with a minus sign; it is the full
line discount, not a per-unit discount. Description retains the saved unit
price but does not repeat the item discount. The summary Discount still includes
both item and document discounts. Existing currency formatting applies.

For invoices linked to a subscription period, the receipt form may capture an
edited description for the subscription package line. That receipt-specific text
is stored on the receipt line and shown on its print/PDF output; it never updates
the invoice line. Invoice amounts, quantity, unit, discounts and coverage remain
the source of financial truth. This field is unavailable to cart/POS invoices
and to invoices without a linked subscription package line.

Zero discount and zero VAT remain visible. Fully paid receipts show **TOTAL** once;
the duplicate **TOTAL AMOUNT RECEIVED** section is omitted when both amounts match.
If this receipt's amount differs from
the sale total, the summary explicitly says **INVOICE TOTAL** and explains that
**AMOUNT RECEIVED** refers only to this payment. Later installments do not alter
that receipt's printed financial breakdown. No live account balances, later
credits, or later receipts are presented as historical cash paid by this receipt.

Cash receipts also preserve `cash_received` on the receipt when the tender is
known. The cash checkout passes the entered tender to the billing service,
which validates it against the amount applied and stores it atomically with the
receipt. A cash receipt created without an explicit tender records its applied
amount as an exact tender, so change is zero. The compact, low-emphasis rows
below TOTAL show **Cash received** and **Change** only for cash receipts that
have a persisted tender. Non-cash receipts never show these rows. Legacy
receipts keep `cash_received = NULL`; their unknown tender/change is not
backfilled or inferred.

Migration `billing.0034_billingdocument_cash_received` is additive and nullable.
It preserves all existing documents without rewriting their payment history.
Rolling application code back remains compatible with the added nullable field;
rolling the migration back drops recorded tender/change values but leaves receipt
amounts and inventory untouched. Reapply the migration before redeploying code
that reads or writes the field.

## Security and compatibility

Existing login, canonical billing-read permissions, tenant and owner visibility
remain enforced at both HTTP routes. Collection access follows the architecture
contract: authorized Sales can read Manager-issued invoices and their receipts
within their tenant, but cannot read another Sales member's unassigned documents
or any foreign tenant's receipt. Both allowed and denied paths have route tests.
The source link must have the same tenant,
organization and currency as the receipt and must refer to an invoice. A damaged
cross-tenant source link fails closed before its items are read. Source lines are
filtered by both tenant and organization. Template autoescaping remains enabled;
private costs and staff notes are not added to customer printouts.

Receipt display remains read-only. Capturing cash tender is part of the existing
atomic checkout and receipt creation transaction; it adds no payment, stock
mutation, permission grant or dependency. The existing thermal template and
PDF/HTML fallback remain shared. Regression coverage is
`billing.tests_receipt_totals` plus the existing billing and checkout suites.

## Research references

- [Financial terms](financial_terms_guide.md): billing net subtotal, item/document
  discounts, tax, receipts, installments and credit-note distinctions.
- [Django 5.2 templates](https://docs.djangoproject.com/en/5.2/ref/templates/language/):
  keep arithmetic in Python and preserve automatic HTML escaping.
- [WeasyPrint web integration and print CSS](https://doc.courtbouillon.org/weasyprint/stable/common_use_cases.html):
  share server-rendered HTML/CSS for print/PDF; do not introduce a second frontend
  calculation path or a Tailwind CDN dependency into receipt printing.
- [Oracle Retail Xstore receipt example](https://docs.oracle.com/cd/E62106_01/xpos/pdf/16001/xstore-16001-03-ug.pdf):
  printed cash receipts identify the tender amount and change due.
- [Shopify POS cash payments](https://help.shopify.com/en/manual/sell-in-person/shopify-pos/payment-management/cash-payments):
  compute change from the amount of cash received during checkout.
