# Remove separate Save customer action — 2026-11-04

Branch `feature/tenant-sku-sequence`, base `ba89500`; unrelated edits preserved.

Removed Save customer from the POS customer panel and its unused CSS rule.
Customer fields remain in the existing cart-details form submitted by payment
and quotation actions. This does not introduce automatic background saving;
customer details are persisted when the sale form is submitted.

Files: `templates/inventory/cart_detail.html`,
`inventory/static/inventory/css/jims-ui.css`,
`inventory/browser_cart_workspace.py`. The optional browser check now verifies
that no Save customer button appears, compact responsive layout still works,
and entering a walk-in name then saving a quotation persists the name.

Verification:

- Docker Compose `python manage.py check`: passed.
- Docker Compose `python manage.py makemigrations --check --dry-run`: no changes.
- Docker Compose `python manage.py test inventory.tests_cart_workspace inventory.tests_cart_checkout --keepdb --noinput`: 31 passed in 17.115s.
- Edge isolated browser test: 1 passed in 7.889s, including quotation persistence.
- Full suite (`python manage.py test --keepdb --noinput --parallel 4`, isolated database): 797 passed in 179.296s.
- `git diff --check`: passed, existing CRLF warnings only.

No schema, permission, tenant scope or financial-service changes. No commit,
push or deployment. No deferred work. Logs: `.remove-customer-save-focused.log`,
`.remove-customer-save-full.log`, `.remove-customer-save-browser.log`.
Review only task-specific hunks before any future commit of these shared files.
