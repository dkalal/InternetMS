# Checkout opens receipt printing

Branch/base: `feature/tenant-sku-sequence` / `ba89500`.

Successful cart payment now redirects to `billing:receipt_print` at
`/billing/receipt/<receipt_id>/print/`, using the receipt actually returned by
checkout. Successful retries open that same receipt print URL. The destination
provides the existing Print Receipt button. No automatic print-dialog behavior
or receipt layout changes are introduced.

Task hunks/files:

- `inventory/views.py`: change the checkout success redirect.
- `inventory/tests_cart_checkout.py`: assert print destination and retry URL;
  all amount/date/stock and duplicate-payment assertions remain.
- `inventory/browser_cart_checkout.py`, `inventory/browser_cart_workspace.py`:
  update optional browser expectations to the print URL and Print Receipt button.
- `docs/cart_lifecycle.md`: document the checkout destination.
- `docs/checkout_print_redirect_handoff_2026-11-02.md`: this record.

Pre-existing workspace, receipt and other changes are preserved. No payment,
inventory, tenant/RBAC, migration or dependency behavior changes. The print route's
existing authentication and scoped receipt access still apply. No commit, push,
deployment or live transaction was performed.

Verification uses a disposable Compose `web` container with current source mounted
at `/app` and a temporary static-assets volume. After collecting static assets:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test inventory.tests_cart_checkout billing.tests_receipt_totals --keepdb --noinput
python manage.py test --keepdb --noinput --parallel 4
```

Full suite uses isolated test DB `test_pos_print_redirect_20261102` and four clones
through ignored runner `.backups/pos-workspace/run_print_redirect_gate.py`:
`PYTHONPATH=/app python /app/.backups/pos-workspace/run_print_redirect_gate.py`.
Checks: no issues, exit 0. Migration drift: no changes detected, exit 0.
Focused tests: **24 passed in 9.983 seconds**; `.pos-print-redirect-focused.log`.
Full suite: **797 passed in 195.455 seconds**, exit 0;
`.pos-print-redirect-full.log`. `git diff --check` passed.

The running Docker/remote service needs the normal reviewed rebuild/release to
consume source changes. No other work is deferred. Suggested commit: one checkout
navigation change containing only the task hunks above.
