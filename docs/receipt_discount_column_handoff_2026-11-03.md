# Receipt item discount column — 2026-11-03

Branch: `feature/tenant-sku-sequence`; base HEAD: `ba89500`. Existing unrelated working-tree edits preserved.

## Result

Receipt browser print and PDF share four columns: Description, Qty / Unit,
Discount, Amount. Item discount no longer appears under description. The column
always renders, with 0 for an undiscounted item and a minus sign for a discount.
Saved unit price remains under the description. The summary discount continues
to combine stored item and document discounts without deducting them twice.
Receipt-specific column widths fit the existing 72mm printable area.

## Files

- `templates/billing/receipt_print_tra.html`: discount cell/header, four-column empty state.
- `templates/billing/base_print_tra.html`: receipt-scoped print column sizing/wrapping.
- `billing/tests_receipt_totals.py`: discounted/zero column assertions and real PDF coverage.
- `docs/receipt_print_contract.md`: presentation contract.

## Verification

Configured disposable Docker Compose web runtime, mounted current source:

- `python manage.py check`: passed, no issues.
- `python manage.py makemigrations --check --dry-run`: passed, no changes detected.
- `python manage.py test billing.tests_receipt_totals --keepdb --noinput`: 11 passed in 4.398s.
- Full suite (`python manage.py test --keepdb --noinput --parallel 4`, isolated test database): 797 passed in 185.774s.
- `git diff --check`: passed (existing CRLF normalization warnings only).
- Edge preview at 390px: discounted/zero cells and no horizontal overflow passed;
  screenshot inspected at `.backups/receipt-totals/discount-column-preview.png`.
- Logs: `.receipt-discount-column-focused.log`, `.receipt-discount-column-full.log`.

## Preservation and delivery

No migration, financial calculation, historical record, tenant scope or permission
change. No new dependencies. No commit, push or deployment. No deferred feature.
Physical printer output was not tested; browser layout and actual PDF generation
were verified. Recommended commit scope is the five files in this task only,
with pre-existing changes in shared files reviewed separately before staging.
