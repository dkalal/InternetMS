# Receipt amount breakdown — research, implementation and verification

Branch/base: `feature/tenant-sku-sequence` / `d56b6a0`.

## Finding and solution

A read-only inspection of the explicitly requested receipt 366 confirmed:

| Persisted field | Receipt 366 | Source invoice 365 |
|---|---:|---:|
| Subtotal | 7 | 10 |
| Document discount | 0 | 3 |
| VAT | 0 | 0 |
| Total | 7 | 7 |

This is intentional storage: a receipt records cash received, while the source
invoice preserves the sale breakdown. The defect was presentation: the thermal
template printed source item values and only the receipt amount, omitting sale
discount and VAT. Both print and PDF already share the same context/template.

The fix presents historical sale subtotal before discounts, the combined item and
document discount, stored VAT, stored total, and this receipt's amount received.
Saved unit price and any item discount appear beneath each description. Item
discounts are added back to the billing model's net subtotal for the gross printed
subtotal, then subtracted exactly once in the displayed combined discount. No tax,
stock, pricing or payment mutation occurs. Zero values remain visible. Partial
payments clearly distinguish invoice total from the payment being acknowledged.

Research and the precise formulas/security contract are documented in
[receipt print contract](receipt_print_contract.md), including primary
[Django template](https://docs.djangoproject.com/en/5.2/ref/templates/language/)
and [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/common_use_cases.html)
references and the existing financial terms guide. Arithmetic remains in Python;
the shared print HTML retains autoescaping and has no Tailwind/CDN dependency.

## Files changed for this task

- `billing/receipt_presentation.py`: read-only Decimal amount summary.
- `billing/views.py`: shared summary context and fail-closed source validation;
  preserves the user's existing payment-method edits.
- `templates/billing/receipt_print_tra.html`: unit price/item discount and totals.
- `billing/tests_receipt_totals.py`: 11 regression tests.
- `docs/receipt_print_contract.md`: display, historical and security contract.
- `docs/financial_terms_guide.md`: explicit gross printed subtotal explanation.
- `docs/receipt_totals_handoff_2026-10-30.md`: this record.

All unrelated tracked and untracked work was preserved. No migration, dependency,
RBAC grant, database backfill, commit, push, live checkout or deployment was made.
Existing browser print controls, receipt identity, thermal styling and PDF fallback
behavior remain supported.

## Verification

Docker prefix (current source, isolated temporary static assets):

```powershell
docker compose run --rm --no-deps --user root --volume "${PWD}:/app" --volume /app/staticfiles web
```

After `python manage.py collectstatic --noinput`, commands/results:

- `python manage.py check`: no issues, exit 0.
- `python manage.py makemigrations --check --dry-run`: no changes detected, exit 0.
- `python manage.py test billing inventory.tests_cart_checkout --keepdb --noinput`:
  **122 passed in 35.853 seconds**, exit 0. Log `.receipt-totals-focused-final.log`.
- Full suite runs with `test --keepdb --noinput --parallel 4` using the ignored
  runner `.backups/receipt-totals/run_gate.py`, which only sets the test DB name to
  `test_receipt_totals_gate_20261030`. This avoids stale parallel clones from other
  repository test runs. Command:
  `PYTHONPATH=/app python /app/.backups/receipt-totals/run_gate.py`.
  **779 tests passed in 184.255 seconds**, exit 0. Log `.receipt-totals-full.log`.
- `git diff --check`: passed.

Coverage includes the exact 10 - 3 = 7 case; simultaneous item/document discounts;
VAT and zero VAT/discount; actual PDF text and attachment response; HTML fallback;
historical receipts without an invoice; installments and unchanged reprints after
later payments; catalog/tax setting changes; cross-tenant and foreign-owner denial;
corrupt source-link rejection; and escaped untrusted descriptions.

A read-only snapshot of receipt 366 was rendered with current source and inspected
in Edge at a 72mm content width. All totals were visible without horizontal
overflow. Review artifacts (ignored, contain the user's requested receipt data):

- `.backups/receipt-totals/receipt-366.html`
- `.backups/receipt-totals/receipt-366.png`
- `.backups/receipt-totals/receipt-366.pdf`

## Remaining delivery scope

The running Docker/remote service must consume a reviewed new build to display
source changes. No live records need correction. This task does not remove the
invoice model, change tax calculation, alter financial/stock services, or change
the previously discussed PDF-backend availability behavior. Suggested commit:
one cohesive receipt-display fix with the seven task files/hunks above, excluding
pre-existing billing payment-method and unrelated changes.
