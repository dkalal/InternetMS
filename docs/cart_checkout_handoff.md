# Direct cart checkout — 2026-10-29

Branch/base: `feature/tenant-sku-sequence` / `d56b6a0`. Existing unrelated tracked
and untracked work was preserved. No commit, push, deployment, schema change or
historical data rewrite was performed.

## Delivered behavior

The cart collects payment method and optional payment reference only. The server
records the invoice's complete total and current Tanzania local payment date;
submitted payment amounts/dates cannot override these values. Confirmation opens
the receipt directly. The invoice remains an internal billing/stock link. Existing
invoice payment routes and quotation conversion remain available. Delivery controls
and user-commented template content remain as found.

## Files in this change

- `inventory/forms.py`: two-field cart payment form and external form association.
- `inventory/services.py`: atomic, permission-checked, row-locked checkout service.
- `inventory/views.py`: checkout POST, bound-form errors, payment permission in AJAX updates.
- `inventory/urls.py`: named cart checkout endpoint.
- `templates/inventory/cart_detail.html`: payment inputs outside replaced AJAX fragments.
- `templates/inventory/includes/pos_checkout.html`: direct receipt action and confirmation.
- `inventory/static/inventory/css/jims-ui.css`: full-width checkout action.
- `inventory/tests_cart_checkout.py`: totals/date, retry, concurrency, rollback,
  reference collision, serial, scope, permission, CSRF and quotation contracts.
- `inventory/tests.py`: existing AJAX pricing test now asserts the new action/label;
  existing price and total assertions are preserved.
- `inventory/browser_cart_checkout.py`: optional isolated Edge desktop/mobile smoke test.
- `docs/cart_lifecycle.md`: updated checkout contract.
- `docs/cart_checkout_handoff.md`: this verification/handoff record.

## Integrity and security

The existing cart access policy and canonical sales/payment permissions remain
server-enforced. A cart lock serializes simultaneous checkout requests. Invoice,
receipt, stock, serial and audit writes share one transaction. A failed payment
rolls back pending cart details and the new invoice. Successful retries, including
cash payments without references, open the same receipt without updating history.
Existing billing/inventory services retain pricing, discount, stock and serial checks.

## Verification

Docker checks use the current source mounted into a disposable Compose `web` run
container. Static assets are collected into a separate temporary container volume
because the running image's manifest predates current workspace assets. The live
service and production database are not used for synthetic test transactions.

Command prefix:

```powershell
docker compose run --rm --no-deps --user root --volume "${PWD}:/app" --volume /app/staticfiles web
```

Commands inside that container:

```text
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test inventory.tests_cart_checkout inventory.tests_financial_controls inventory.tests_cart_readiness inventory.tests_cart_lifecycle inventory.tests_serial_validation inventory.tests_sale_cost_guard billing --keepdb --noinput
python manage.py test --keepdb --noinput
python manage.py test --keepdb --noinput --parallel 4
```

Checks: no issues. Migration drift: no changes detected. Focused tests: 172 passed.
The initial full run completed 768 tests with one failure: an existing AJAX pricing
test still expected the removed "Continue to payment" label. Its replacement
asserts the new label and checkout URL while preserving all pricing assertions.
The first parallel attempt could not use existing old test clones: their schema
lacked `inventory_cart.abandoned_at`; Django then failed to pickle that traceback.
The final parallel run uses isolated database `test_cart_checkout_gate_v1` and its
four clones. Its ignored runner `.backups/cart-checkout/run_full_gate.py` configures
only `DATABASES['default']['TEST']['NAME']`, then executes
`test --keepdb --noinput --parallel 4`. The container command is
`PYTHONPATH=/app python /app/.backups/cart-checkout/run_full_gate.py`.
Final full-suite result: **768 tests passed in 173.241 seconds**, exit code 0.
Log: `.cart-checkout-full-isolated.log`. Focused gate log:
`.cart-checkout-focused-final.log` (172 passed in 80.057 seconds).
Browser log: `.cart-checkout-browser-final.log` (one passed in 8.492 seconds).

Optional Edge check: `inventory.browser_cart_checkout` passed (one test) using the
local virtualenv with an isolated `test_cart_checkout_browser` PostgreSQL database.
It exercised AJAX quantity changes, retained payment selections, desktop/mobile
layout, confirmation, receipt navigation and actual stock deduction. The app's
Tailwind CDN script was cached under ignored `.backups/cart-checkout/` for this
test environment. Screenshots are `desktop.png` and `mobile.png` in that directory.
`git diff --check` passed.

## Delivery limits

Docker hosts need the normal reviewed rebuild/release to consume changed source;
the current running service was not rebuilt with unrelated dirty workspace changes.
The localhost/remote PDF fallback difference was investigated earlier but is
intentionally outside this checkout change. No financial history was removed and
no invoice schema was retired. Suggested commit split: one cohesive checkout commit
containing only the hunks and new files listed above, excluding pre-existing work.
