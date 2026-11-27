# Receipt column alignment fix — 2026-11-03

Branch `feature/tenant-sku-sequence`, base `ba89500`. Preserved unrelated edits.

Cause: the description `<td>` was commented out while the four table headers
remained. Browsers placed quantity in the description column, discount in the
quantity column, and line amount in the discount column. This was a display
alignment defect, not evidence of a stored discount of 100,000.

Restored the description cell in `templates/billing/receipt_print_tra.html`.
`billing/tests_receipt_totals.py` now verifies the complete four-cell row,
including description/unit price, quantity, zero discount and line amount in
that order. The existing discounted-item and real-PDF checks remain.

Verification in disposable Docker Compose web with current source mounted:

- `python manage.py check`: passed, no issues.
- `python manage.py makemigrations --check --dry-run`: passed, no changes.
- `python manage.py test billing.tests_receipt_totals --keepdb --noinput`: 11 passed in 3.771s.
- Full suite (`python manage.py test --keepdb --noinput --parallel 4`, isolated test database): 797 passed in 186.453s.
- Edge receipt preview: correct discount cells and no horizontal overflow passed.
- `git diff --check`: passed (existing CRLF warnings only).

Logs: `.receipt-alignment-focused.log`, `.receipt-alignment-full.log`.
No migration, stored amount recalculation, record update, permission change,
commit, push or deployment. Physical printing not tested. No deferred work.
Recommended commit scope is these two changed files and this report; review
pre-existing edits in the shared files before staging.
