# POS workspace and attribution delivery

Branch/base: `feature/tenant-sku-sequence` / `ba89500`.

## Delivered behavior

- Save as quotation and payment actions share one compact row on desktop and mobile.
- Start sale opens an unsaved workspace. Reload/search, opening the customer tab,
  or opening/cancelling the first serial picker creates no Cart record.
- The first valid item/customer/details save atomically creates the draft, that
  mutation, creation/activity audit and workspace-to-cart event. Invalid/no-op
  empty requests leave no Cart or creation audit. No stock is reserved/deducted.
- An actor row lock and append-only, indexed workspace event serialize retries and
  simultaneous first writes. The same token opens the same canonical cart without
  increasing quantity twice. Different tokens represent distinct sales.
- Workspace tokens are signed, expire after 24 hours, and bind actor and tenant;
  every request still requires authenticated membership/permissions. Tenant/user
  switches, tampering, expiry and CSRF failures cannot materialize a draft.
- Quantity adjustments, item/serial edits, removals, discounts and detail/repricing
  changes use CartService with actor/membership and before/after audit values.
- Lists distinguish original Created by, latest known Last updated by, and the
  completed sale's receipt cashier as Completed by. Unknown historical editors
  remain Unknown; no historical ownership inference/backfill is performed.

## Historical attribution preservation

The original office investigation is preserved privately. Historical creator and
audit records were not changed. Missing editor events cannot establish who made
a historical change; existing drafts were not deleted, hidden or reassigned.

## Task files

- `inventory/cart_workspace.py`: signed workspace and atomic first-write service.
- `inventory/services.py`: audited details, quantity, removal and line changes.
- `inventory/forms.py`: safe unsaved-cart/serial validation and invalid product IDs.
- `inventory/views.py`: workspace coordinator, shared builder, service calls and
  bounded audit/completion list annotations.
- `inventory/urls.py`: workspace route.
- `inventory/static/inventory/js/inventory-ui.js`: first-details-save redirect.
- `inventory/static/inventory/css/jims-ui.css`: compact, adjacent checkout buttons.
- `templates/inventory/cart_detail.html`: unsaved workspace controls and first-item URLs.
- `templates/inventory/cart_line_form.html`: first serialized item/cancel support.
- `templates/inventory/cart_list.html`: creator/editor/cashier labels.
- `inventory/tests_cart_workspace.py`: 18 first-write, isolation, rollback,
  concurrency and attribution regression tests.
- `inventory/tests.py`, `inventory/tests_cart_lifecycle.py`: replacement HTTP Start
  sale/support contracts; existing stock, CSRF, support audit and lifecycle checks
  remain. Trusted internal `CartService.start` compatibility tests remain intact.
- `inventory/browser_cart_workspace.py`: isolated Edge desktop/mobile smoke checks.
- `docs/cart_lifecycle.md`: updated operational/idempotency/attribution contract.
- `docs/pos_workspace_handoff_2026-11-02.md`: this handoff.

Pre-existing receipt changes and unrelated user files were preserved. No migration,
dependency, role grant, commit, push or deployment was introduced. The durable
workspace lookup reuses the existing audit organization/object-type/object-ID index;
append-only materialization events must remain preserved like other audit history.

## Verification

Docker commands use the Compose `web` image, the current source mounted at `/app`,
and a separate temporary `/app/staticfiles` volume. The running live web service
was not modified or rebuilt. Synthetic data uses test databases only.

```text
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test inventory.tests_cart_workspace inventory.tests_cart_lifecycle inventory.tests_cart_checkout inventory.tests_cart_line_merge inventory.tests_financial_controls inventory.tests_cart_readiness inventory.tests --keepdb --noinput
python manage.py test inventory.tests_cart_workspace --keepdb --noinput
python manage.py test --keepdb --noinput --parallel 4
```

- Checks: no issues. Migration drift: no changes detected; both exit 0.
- Broad focused gate: **160 passed in 79.039 seconds**; `.pos-workspace-focused-final.log`.
- Final workspace/audit contracts after extra before/after and tenant-switch review:
  **18 passed in 9.031 seconds**; `.pos-workspace-contracts-final2.log`.
- Edge: **3 passed in 28.265 seconds**; `.pos-workspace-browser-final.log`.
  First-item checkout reached a real receipt, first customer save followed the AJAX
  redirect, and serial picker cancellation left no draft. Desktop/mobile buttons
  had matching vertical position/height; neither viewport overflowed horizontally.
  Screenshots: `.backups/pos-workspace/{empty-workspace,desktop,mobile}.png`.
- Full suite uses isolated `test_pos_lazy_workspace_20261102` and four clones via
  ignored runner `.backups/pos-workspace/run_full_gate.py`:
  `PYTHONPATH=/app python /app/.backups/pos-workspace/run_full_gate.py`.
  **797 passed in 167.961 seconds**, exit 0; `.pos-workspace-full.log`.
- `git diff --check`: passed.

## Delivery limits

Historical missing editor events cannot identify who physically changed a cart.
The new audit coverage applies to subsequent operational writes. A stale initial
workspace POST after another tab already saved it opens the existing sale for
review rather than reapplying its payload. Expired workspaces must be restarted;
saved cart URLs remain valid under normal access rules. Existing empty drafts and
maintenance cleanup policy remain untouched. Docker/remote hosts need the normal
reviewed build/release to consume the new source; no deployment was performed.
Suggested commit: one cohesive POS workspace/audit/UI commit containing only the
task files/hunks above, excluding the pre-existing receipt and handover changes.
