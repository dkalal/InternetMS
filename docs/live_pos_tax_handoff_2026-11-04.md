# Live POS Tax/VAT preview — 2026-11-04

Branch `feature/tenant-sku-sequence`, base `ba89500`; unrelated edits preserved.

Cause: the existing input-driven totals preview explicitly excluded `[data-pos]`.
Enabled the shared preview for POS, updated both the payment summary and modal
total, and recomputed after Cancel restores the saved modal value. The shared
function uses a function-scoped variable so the modal close handler can invoke
it under strict JavaScript semantics. Existing taxable-item eligibility,
proportional cart discount and preview rounding remain in use. Server-side
services still validate and calculate persisted financial amounts.

Files: `inventory/static/inventory/js/inventory-ui.js`,
`inventory/browser_cart_checkout.py`. Extended the existing browser smoke check
with input preview, cancellation rollback, Save tax, cart refresh and final receipt
amount checks. Verified subtotal 200 + 18% VAT 36 = total 236.

Verification:

- Configured Docker Compose `python manage.py check`: passed.
- `python manage.py makemigrations --check --dry-run`: no changes.
- `python manage.py test inventory.tests_cart_checkout billing.tests_receipt_totals --keepdb --noinput`: 24 passed in 9.859s.
- Isolated Edge `inventory.browser_cart_checkout`: 1 passed in 10.367s.
- Full suite (`python manage.py test --keepdb --noinput --parallel 4`, isolated database): 797 passed in 184.248s.
- `git diff --check`: passed; existing CRLF warnings only.

No migration, historical record change, permission/tenant scope change, commit,
push or deployment. No deferred work. Logs: `.live-tax-focused.log`,
`.live-tax-browser.log`, `.live-tax-full.log`. Review only this task's hunks in
shared dirty files before staging any future commit.
