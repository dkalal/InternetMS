# Purchase buying-price defaults review (2026-10-28)

Branch: `feature/tenant-sku-sequence`; starting HEAD: `d56b6a0`.
The working tree already contained unrelated tracked/untracked work, which is preserved. No commit or push is authorized.

## Behavior and analysis

Selecting an eligible product in the purchase combobox or bulk picker copies its catalog buying price into the editable same-unit unit-cost field. Zero and six-decimal precision are preserved. An informational buying-price reference remains visible while the buyer edits the cost. Newly created products returned from the full catalog dialog supply the same permission-protected default.

Draft initialization, quantity changes and validation rerenders retain entered costs. Import rows retain their explicit acquisition costs. Catalog buying price is a suggestion, independent of movement-backed weighted-average cost; purchase submission and receipt remain server-authoritative.

The existing selected unit, SKU and serial/expiry requirements already assist purchase entry. Search results now display brand/model metadata already supplied by the endpoint. Quantity, actual serial numbers, expiry dates and batch references cannot safely be inferred from catalog defaults. Current stock, last supplier price and sales-price tiers are intentionally deferred: they require separate stock/price provenance and permission decisions and are unnecessary for this default-price request.

## Code review

- Search remains authenticated, purchase-permission-protected, tenant-scoped, paginated, read-only and non-cacheable. Buying price is omitted unless the existing `COST_REPORT_VIEW` permission used by inventory cost displays is present. No new grants are introduced.
- Price strings are assigned directly to the input without binary floating-point conversion. UI reference text uses `textContent`; brand/model metadata also uses `textContent`.
- Defaults are applied only on product selection or adding a selected product. Product changes without an authorized default clear the previous product's cost rather than leaving a misleading price.
- Services, non-stock and inactive products remain excluded. Domain services, stock posting, historical records and package-preservation fields are unchanged. No schema change or migration is needed.
- No blocking finding remains in the scoped review. Real-browser behavior checks complement focused API permission/precision tests and Node selection/bulk tests. The browser behavior check supplies only the Tailwind `hidden` utility locally so it does not depend on CDN availability; it is not a complete visual acceptance test.

## Scope

Production changes: `inventory/views.py`, `inventory/static/inventory/js/inventory-ui.js`, `templates/inventory/includes/purchase_grid_line.html`, `products/views.py`.

Tests: `inventory/tests_purchase_product_search.py`, `inventory/tests_purchase_full_create_modal.py`, `inventory/js_tests/purchase-cost-default.test.cjs`, `inventory/browser_purchase_buying_price.py`.

Contract: `docs/unified_purchasing_phase_0b_contract.md`.

A single cohesive purchase-default change is appropriate for a future commit, selecting only this task's hunks in files with pre-existing work. The running Docker service uses an image without a source mount; these changes require a normal rebuild/redeploy before appearing there. Deployment was not performed.

## Verification commands

The configured Docker `web` image has no source bind mount, so verification uses `docker compose run --rm --no-deps -v "${PWD}:/app" web ...` to test current workspace files.

Initial focused run: `python manage.py test inventory.tests_purchase_product_search inventory.tests_purchase_grid inventory.tests_purchase_workspace --keepdb` — 31 passed.

Final isolated PostgreSQL gates use the following exact Python program via `docker compose run ... web python -c "..."`:

```python
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'internetservices.settings')
django.setup()
from django.conf import settings
settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = 'test_purchase_defaults_20261028'
from django.core.management import call_command
call_command('check')
call_command('makemigrations', check=True, dry_run=True)
call_command('test', 'inventory.tests_purchase_product_search', 'inventory.tests_purchase_grid',
             'inventory.tests_purchase_workspace', 'inventory.tests_purchase_full_create_modal', keepdb=True)
call_command('test', keepdb=True, parallel=4)
```

System check: no issues. Migration check: no changes detected. Final focused modules: 35 passed in 34.738 seconds.

`node --check inventory/static/inventory/js/inventory-ui.js` — passed.
`node --test inventory/js_tests/purchase-cost-default.test.cjs` — 2 passed.
Scoped `git diff --check` — passed.

Real Edge check: `inventory.browser_purchase_buying_price` — 1 passed in 10.373 seconds. It ran through Django's test command using `DATABASE_URL=sqlite:///:memory:` and `DJANGO_SETTINGS_MODULE=internetservices.settings`, independent of PostgreSQL test sessions.

An earlier browser run accidentally reused the default PostgreSQL test database concurrently with the full suite; that caused locking and left committed fixture residue. A later full run on that reused database reported 17 failures and 8 errors (including duplicate fixture tenant slugs and inflated product counts). Those results are not accepted as release evidence. Final gates use a new named PostgreSQL test database and separate clones. No production database cleanup or schema change was performed.

Final clean PostgreSQL full-suite result: **746 tests passed in 240.890 seconds**, no failures/errors, using four isolated clones. All final gates passed. Complete final gate output: `.purchase-buying-price-isolated-gates.log`; browser output: `.purchase-buying-price-browser.log`. No remaining scoped blocker was found. Remaining limitation: full visual acceptance and deployment/rebuild were not performed; the Edge test verifies behavior with a local `hidden` CSS utility, not CDN rendering.

Docker's recorded die event for final gate container js-internetservices-web-run-015f4339c5df confirms exitCode=0. PowerShell's redirected native-stderr wrapper emitted an ErrorRecord; it does not represent a Django/container failure.

## Follow-up: buying-price display precision (2026-10-29)

The purchase default and catalog-price hint now remove only trailing fractional zeroes using string formatting. For example, `100.000000` displays as `100`, `100.500000` as `100.5`, and `100.000001` remains `100.000001`. There is no rounding or floating-point conversion, and manual edits, persisted monetary values, permission checks, and stock/financial services are unchanged. Original entered trailing-zero precision cannot be recovered from a fixed-scale database decimal, so the display uses the shortest equivalent decimal. This change needs no migration.

Changed for this follow-up: `inventory/static/inventory/js/inventory-ui.js`, `inventory/js_tests/purchase-cost-default.test.cjs`, and `inventory/browser_purchase_buying_price.py`. Review found no scoped blocking issue; the prior dirty working tree remains intact. A cohesive commit could include these follow-up hunks with the related purchase-default implementation; no commit or deployment was performed. The running web image needs a rebuild before it serves this change.

Verification: `node --check inventory/static/inventory/js/inventory-ui.js` passed; `node --test inventory/js_tests/purchase-cost-default.test.cjs` passed all 3 tests. The Edge behavior check `inventory.browser_purchase_buying_price` passed 1 test using an isolated in-memory SQLite database with the local `hidden` CSS utility because the Tailwind CDN is not required for this behavior check.

The required Docker PostgreSQL gates used `docker compose run --rm --no-deps -v "${PWD}:/app" web python -c "..."` and the same `call_command` sequence documented above, with test database name `test_purchase_price_format_20261029`: `check` passed; `makemigrations --check --dry-run` found no changes; the four focused purchase modules passed 35 tests; `test --keepdb --parallel 4` passed all 746 tests in 223.312 seconds. Output: `.purchase-price-format-gates.log` and `.purchase-price-format-browser.log`. A full visual acceptance test was not performed.
