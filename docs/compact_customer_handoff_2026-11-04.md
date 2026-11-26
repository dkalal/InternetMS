# Compact POS customer fields — 2026-11-04

Branch `feature/tenant-sku-sequence`; base `ba89500`. Unrelated work preserved.

Customer, Walk in name and Customer category share three equal columns when the
customer panel has at least 360px of content width. Narrow panels stack the
fields. Container-based layout accounts for the split POS desktop sidebar as
well as tablet/mobile widths. Labels stay on one line, fields retain readable
text and 40px minimum height, helper text/errors remain visible. Save customer
uses a compact button. Existing fields, submitted values, AJAX and permissions
remain in use.

Files: `templates/inventory/cart_detail.html`,
`inventory/static/inventory/css/jims-ui.css`,
`inventory/browser_cart_workspace.py` (existing optional browser check extended).

Verification in the configured disposable Docker Compose web runtime:

- `python manage.py check`: passed.
- `python manage.py makemigrations --check --dry-run`: passed; no changes.
- `python manage.py test inventory.tests_cart_workspace inventory.tests_cart_checkout --keepdb --noinput`: 31 passed in 17.881s.
- Full suite (`python manage.py test --keepdb --noinput --parallel 4`, isolated database): 797 passed in 205.622s.
- Isolated Edge check of customer save, three-column/stacked layout, no horizontal
  overflow at 1440/800/390px: passed; screenshots under `.backups/pos-workspace/compact-customer-*.png`.
- `git diff --check`: passed, existing CRLF warnings only.

No migrations, financial mutations, security/tenant scope changes, dependency
changes, commit, push or deployment. No deferred work. Logs:
`.compact-customer-focused.log`, `.compact-customer-full.log`,
`.compact-customer-browser.log`. Review only this task's hunks in shared dirty
files before any future commit.
