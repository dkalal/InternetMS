# Live serial validation: research, implementation and review

Branch: `feature/tenant-sku-sequence`; starting HEAD: `d56b6a0`. Existing unrelated tracked changes and untracked files are preserved. No commit, push or deployment is included.

## Findings before implementation

The repository already normalizes serials by trimming and uppercasing them. Inventory receiving rejects duplicate serials within/across purchase lines and existing tenant stock under the transaction boundary. `StockUnit` has tenant-level uniqueness, including sold and removed units. Draft purchases do not reserve serials or change stock. The purchase grid previously showed only a generic same-line duplicate message; import preview checked repeated input, but an existing stock serial could reach draft-save validation. Stock adjustment had no live duplicate feedback. POS cart services already rejected repeated selection, but the picker still offered serials already selected in that cart. Invoice selection needed an explicit cross-line duplicate check before replacing selections.

The implemented approach uses local feedback immediately and a debounced, bounded server lookup for existing identities. It preserves typed data and identifies affected lines instead of deleting duplicates or stopping individual keystrokes. Browser validation is supplementary: [MDN's Constraint Validation guide](https://developer.mozilla.org/en-US/docs/Web/HTML/Guides/Constraint_validation) requires custom validity to be cleared after correction and explains why server validation remains necessary. [MDN's setCustomValidity reference](https://developer.mozilla.org/en-US/docs/Web/API/HTMLInputElement/setCustomValidity) documents preventing submission while a custom error is present. The shared component adds associated inline errors, `aria-invalid`, a polite live summary, and focus recovery for the purchase tracking drawer.

## Coverage

| Entry point | Behavior |
| --- | --- |
| Purchase manual lines | Immediately finds normalized duplicates within a field and across active rows, including fields moved into the tracking drawer. Deleted rows are ignored; labels follow visible row order. Checks existing stock serials after 350 ms. Invalid submission opens the affected drawer. |
| Purchase import/paste | Raw source is parsed through the existing explicit column mapping. Preview flags duplicate/existing serials. The contextual serial editor checks every included review row plus current manual lines while typing. Excluded rows do not conflict. Continue/Apply rechecks; applied rows are checked with manual rows before saving. Unapplied hidden editor changes do not invalidate the outer form. |
| Stock increase | Checks local duplicates and existing tenant serials across all stock lifecycle states. |
| Stock decrease | Checks local duplicates and requires currently available serials of the selected tenant product. Existing stock identity is expected in this context. |
| Opening-stock XLSX import | After parsing the upload, preview reports repeated serials within/across rows and serials already in tenant stock. Preview creates only the existing import job, not stock. No browser guessing of spreadsheet columns is introduced. |
| POS cart serial picker | Serials already on another/current saved cart line are disabled when adding. Editing the owning line retains its existing selections. Existing `CartService` checks remain authoritative. |
| Invoice serial picker | Live checkbox feedback blocks choosing the same unit on multiple lines. Server validation rejects repeated IDs within/across lines, preserves submitted selections on failure, and leaves saved selections unchanged on error. |
| APIs and final posting | Existing authenticated server validation, receipt atomicity, row locks and database uniqueness remain in force. APIs have no browser entry to enhance. |

Other catalog screens configure whether a product is serialized; they do not enter serial identities. Customer/integration asset records are not an additional writable serial-entry screen in this workflow.

## Endpoint and failure behavior

`POST /inventory/serials/check/` requires authentication, CSRF and the applicable `PURCHASES_CREATE` or `STOCK_ADJUST` permission. Tenant context comes from membership. Adjustment product lookup is tenant-scoped. The response contains only conflicts among supplied values and a reason, without product, customer, financial or other-tenant information. Responses are non-cacheable and the endpoint performs no writes or reservations.

Requests are limited to 1,000 serials, 100,000 input characters, and 160 normalized characters per serial. Malformed payloads and invalid contexts fail safely. Local duplicates are checked immediately; remote requests wait 350 ms, abort when input changes, ignore stale responses, and time out after eight seconds. A failed live request shows an unavailable-check notice rather than claiming success; normal authoritative validation remains available. Any other transaction can receive a serial after live feedback, so final posting must always recheck. Drafts elsewhere are not reservations and are not queried or disclosed.

No migration or historical data update is needed. Persisted serials, package preservation fields, costs and stock balances are not changed by feedback.

## Code review

- New `inventory/serial_validation.py` owns the bounded tenant-stock lookup; the view coordinates HTTP and authorization.
- New `inventory/static/inventory/js/serial-validation.js` shares parsing, local duplicate analysis, asynchronous checking and accessible feedback. Messages use `textContent`, not HTML interpolation.
- `inventory/views.py` and `inventory/urls.py` expose the protected lookup and reject repeated invoice selections. Malformed/out-of-range invoice unit IDs become validation errors before querying or replacing selections. `inventory/forms.py` supplies the cart's already-selected IDs.
- `inventory/purchase_import.py` and `inventory/imports.py` add earlier preview errors without moving stock mutations into previews.
- `inventory/static/inventory/js/inventory-ui.js` reveals the owning purchase row on invalid submission and refreshes feedback after deletion. `purchase-import.js` supplies current review rows and validates before review/apply transitions.
- `templates/base.html`, purchase/adjustment forms and cart/invoice pickers connect the shared behavior. The user's unrelated `templates/inventory/includes/pos_cart_lines.html` changes are preserved.
- New tests cover permission denial, CSRF, cross-tenant isolation, limits, normalization, lifecycle states, purchase/opening-stock previews, cart editing, invoice duplicates, asynchronous stale responses and network failure. Optional Edge checks cover the drawer, cross-row correction/deletion, draft save after correction, import editor, stock directions and invoice checkboxes.

The new static asset must be collected during the ordinary image build/redeploy. Verification builds its manifest under `/tmp/jims-serial-static` inside the test container, preserving the workspace's existing staticfiles directory. Browser checks use the local `hidden` utility to avoid depending on the external Tailwind CDN; they are behavior checks rather than full visual acceptance.

## Verification

Commands and final results are recorded below after the required gates finish. Focused development run: 75 tests passed; subsequent serial/invoice regressions: 9 passed. JavaScript checks: 8 passed, including the previous purchase-price regressions. No tests were skipped or weakened to hide failures.

Final commands run from the repository root:

```powershell
node --check inventory/static/inventory/js/serial-validation.js
node --check inventory/static/inventory/js/inventory-ui.js
node --check inventory/static/inventory/js/purchase-import.js
node --test inventory/js_tests/serial-validation.test.cjs inventory/js_tests/purchase-cost-default.test.cjs
git diff --check
```

All syntax and diff checks passed; all 8 Node tests passed. The Edge behavior command ran `inventory.browser_serial_validation --keepdb` through Django's test runner with `DJANGO_SETTINGS_MODULE=internetservices.settings` and `DATABASE_URL=sqlite:///:memory:`. All 4 browser checks passed in 37.844 seconds, including a real draft save after correcting a duplicate. Output: `.serial-live-browser.log`.

The configured Docker web service has no source bind mount, so current-source verification used `docker compose run --rm --no-deps -v "${PWD}:/app" web python -c "..."` with the following Python program:

```python
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'internetservices.settings')
django.setup()
from django.conf import settings
settings.STATIC_ROOT = '/tmp/jims-serial-static'
settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = 'test_live_serials_release_20261029'
from django.core.management import call_command
call_command('collectstatic', interactive=False, verbosity=0)
call_command('check')
call_command('makemigrations', check=True, dry_run=True)
call_command('test', 'inventory.tests_serial_validation', 'inventory.tests_purchase_mixed_import',
             'inventory.tests_purchase_paste_rows', 'inventory.tests_cart_line_merge',
             'inventory.tests_purchase_proposals', keepdb=True)
call_command('test', keepdb=True, parallel=4)
```

The final focused run passed all 76 tests in 29.504 seconds. System check found no issues; migration check found no changes. Output: `.serial-live-gates.log`. An early focused run exposed a missing static manifest entry for the new asset; collecting the asset into a container-local directory resolved the build prerequisite without altering the workspace staticfiles directory or weakening a test.

A repeated full run using `--keepdb` on existing parallel clones exposed an older schema left by migration tests (`work_reports_technicianpaymentbatch` missing); the parallel runner then could not serialize that traceback without `tblib`. No application dependency or test was changed to hide this. Final full-suite evidence uses the same temporary-static setup with a fresh test database name `test_live_serials_final_20261029`, running `check`, `makemigrations --check --dry-run` and `test --keepdb --parallel 4`. Output: `.serial-live-final-full.log`. Reusable parallel-clone restoration in the pre-existing migration tests is a separate test-infrastructure follow-up; it does not affect the live application database.

Final clean full-suite result: **755 tests passed in 208.335 seconds**, no failures/errors. All final release gates passed. Focused regression tests: **76 passed**; JavaScript tests: **8 passed**; Edge behavior tests: **4 passed**. No migration was added and no scoped blocking code-review issue remains. Known limits are advisory live checks (no reservation; final stock mutations must revalidate), unavailable-network fallback, and the documented reusable-clone test-infrastructure issue. Full visual acceptance and deployment were not performed. Recommended delivery is one cohesive serial-feedback commit selecting only this task's hunks from already-dirty files; no commit or push is authorized or performed.

Docker's recorded exit event confirms final container js-internetservices-web-run-0852b8edbb89 exited with code 0. The PowerShell native-stderr redirection ErrorRecord is not a failed Django/container gate.
