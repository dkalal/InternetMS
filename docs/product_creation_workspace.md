# Product creation workspace

SKU allocation and submission replay now follow the
[tenant SKU sequence contract](sku_sequence_contract.md). It supersedes the
name-derived allocation/retry-cap description below; blank SKU allocates only
at final save, and reused submission tokens must have identical content.

Implemented on `feature/cart-lifecycle-management`, based on `36bfa1f`.

## Contract

Normal `GET /products/create/` renders five new-product rows inside the existing
application shell and inventory navigation. Each row owns its product fields,
prices, optional picture, wholesale settings and tenant-specific custom fields.
SKU is optional and retains the existing uppercase allocation convention.
The reorder threshold editor is 105px wide on desktop.

The grid supports adding/removing unsaved rows, local horizontal scrolling,
sticky headers/name/row number, optional columns, inline row details, keyboard
entry, upload previews and a mobile card layout using the same controls.
Descriptions expand while editing. Paste accepts TSV, quoted multiline cells,
CRLF, empty optional cells and leading zeros. Supported headers are Name, Model,
SKU, Category, Sales unit, Buying price, Standard price, Technician price and
Description; price labels with `(TZS)` are also accepted. Headerless paste starts
at the selected cell in displayed column order. Multi-cell paste requires a
preview confirmation. Unknown/ambiguous choices remain visible as cell errors;
no category/unit is created. Pictures require real file uploads; pasted URLs
are never fetched. Formula text is never evaluated.

`purchase_modal=1` and legacy single-product POST requests retain the existing
single-product path. Product editing remains a single-product workflow.
Category quick creation stays available through the existing modal flow; it is
not added to the batch grid.

## Validation and persistence

The multipart request carries one JSON `rows` field, a signed batch token and
files keyed by stable row IDs. This avoids increasing Django's global form-field
limits. Each meaningful row runs the existing `ProductForm`, with organization
and actor, image optimization, model validation and dynamic-field validation.
Defaults alone do not count as a product. An invalid row prevents every write.
Errors identify stable row and field keys, and the browser retains its controls
and selected files because validation uses fetch without replacing the page.

`save_product_batch` owns persistence. It rechecks creation permission and form
ownership, locks the organization row and creates products, custom-field values
and audit events inside one transaction. Explicit SKUs reserve names before
automatic allocation; the existing purchase creation helper now accepts an
optional allocation-attempt cap (its original default stays 20). The workspace
uses 1000 attempts, savepoints and existing database uniqueness constraints.
Products always start with zero stock; no inventory movements are posted.
A failed transaction deletes any newly stored image files tracked by the batch.

The signed submission key is tenant- and actor-bound and expires after 24 hours.
An append-only batch audit event consumes it under the organization lock.
Replaying a completed key redirects to the catalog without creating products.
The browser disables repeated submit clicks; there is no autosave or persistent
browser storage of prices/pictures. Cost controls are omitted for users without
cost-view permission, and forged cost input is ignored through a disabled form
field using a zero initial acquisition cost for new records.

## Limits and compatibility

- Maximum 200 rows per request, including supplied blank rows.
- Maximum 50 uploaded pictures, 6 MB each, 24 MB combined.
- Existing image pixel, format and metadata-stripping rules remain active.
- Existing request body/form/upload limits remain enabled. Extremely long text
  can still exceed Django's existing non-file request-body limit.
- No schema changes, migrations, dependencies, stock receipt or package conversion.
- Pending values/files survive validation and recoverable network errors in the
  current page. They cannot survive a full page refresh or browser shutdown.
- File cleanup compensates for ordinary transaction failures. A process/storage
  outage during a write can still require operational orphan-file reconciliation.
- A batch token expires after 24 hours; an expired workspace needs a fresh token.

## Files in this change

- `products/views.py`: route normal create GET/batch POST into the workspace.
- `products/workspace.py`: bounded validation and atomic batch creation service.
- `products/creation.py`: optional SKU allocation retry cap.
- `templates/products/product_workspace.html`: workspace and row/detail templates.
- `products/static/products/workspace.js`: interaction, paste, errors and submission.
- `products/static/products/workspace.css`: compact grid and mobile cards.
- `products/tests_workspace.py`: batch/security/storage regressions.
- `products/tests.py`: replacement GET assertions for the approved new workspace;
  existing single-product mutation and modal coverage remains.
- `products/browser_workspace.py`: optional real Edge verification.
- `docs/screenshots/product-workspace/desktop.png` and `mobile.png`.
- This contract/handoff document.

## Verification

Django checks and tests run in disposable Docker Compose `web` containers with the
current workspace bind-mounted at `/app`, PostgreSQL and a separate test database.
The pre-existing shared test database contained stale migration fixture data;
the isolated database avoids changing or deleting that unrelated test state.
Static files are collected into `/tmp/product-grid-static` inside the disposable
container, avoiding the running container's pre-existing static ownership issue.

Final results (2026-10-27):

| Check | Exact outcome |
| --- | --- |
| `python manage.py check` | System check identified no issues (0 silenced). |
| `python manage.py makemigrations --check --dry-run` | No changes detected. |
| `python manage.py test products inventory.tests_purchase_workspace --keepdb --noinput` | 58 tests, 30.082 seconds, OK. |
| `python manage.py test --keepdb --noinput` | 710 tests, 431.136 seconds, OK. |
| Local Edge `products.browser_workspace.ProductWorkspaceBrowserChecks` | 2 tests, 8.998 seconds, OK. |
| `node --check products/static/products/workspace.js` | Exit 0. |
| `git diff --check -- products templates/products docs/product_creation_workspace.md` | Exit 0; existing CRLF conversion advisory only. |

The Django management operations above were executed via `call_command` after
`django.setup()` to select the isolated test database and temporary static root.
The exact final Docker invocation was:

```powershell
docker compose run --rm --no-deps --user root -v "${PWD}:/app" web python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','internetservices.settings'); import django; django.setup(); from django.conf import settings; settings.DATABASES['default']['TEST']['NAME']='test_product_grid_handoff_20261027'; settings.STATIC_ROOT='/tmp/product-grid-static'; from django.core.management import call_command; call_command('collectstatic',interactive=False,verbosity=0); call_command('check'); call_command('makemigrations',check=True,dry_run=True); call_command('test','products','inventory.tests_purchase_workspace',keepdb=True,interactive=False); call_command('test',keepdb=True,interactive=False)"
```

Final output is in `.product-workspace-handoff.log`; browser output is in
`.product-workspace-browser.log`. An intermediate full run reported a stale-plan
error in the existing handover maintenance test while Python sources were still
being edited; that policy fingerprints actual source bytes. The final run with
frozen Python sources passed all 710 tests without changing that policy/test.

Browser coverage includes Enter navigation, quoted multiline TSV with leading
zeros, a paste dialog, unknown unit cell errors, expansion to 200 rows and clear
overflow rejection, hidden-column validation errors becoming visible, image
retention after row removal/failed validation, successful save, and no page
horizontal overflow at 768px and 390px. Screenshots capture the validation state
before final successful save, with the pending picture retained.
Browser checks run locally with SQLite and real headless Edge, separately from
the PostgreSQL backend suite. The browser cannot directly load the application's
existing Tailwind CDN script in this environment, so the test routes that request
to an unchanged temporary download when `PRODUCT_BROWSER_TAILWIND_PATH` is set.
No CDN/build/dependency change is made to the application.

No commit, push or deployment was performed. Unrelated working-tree changes are
preserved. One cohesive product-workspace commit is recommended after review.
The Docker application uses an image rather than a source bind mount; an eventual
local image rebuild/restart is needed to load the final source into its workers.


## Catalog entry refinement (2026-10-27)

This follow-up implements the requested classification removal, one Name / brand
editor and whole-unit monetary spinner increments.

Analysis found that `Product.category` and `Product.brand` are still consumed by
legacy filters, search, imports and historical compatibility. Removing database
columns would therefore be destructive. The grid no longer renders the legacy
classification or an independent brand editor. Its category field is disabled
server-side and uses the existing model default for new products. Existing
classification values and catalog categories/allowed-unit validation remain.

The catalog form, purchase product modal and quick purchase product form label
the visible name as `Name / brand`. New users enter the complete name, for
example `TP-Link Router`. Existing separate brand metadata is shown in the name
when an edit form opens, without duplicating a brand already present. GET does
not rewrite records. Saving the name does not infer a brand by splitting free
text or overwrite old brand metadata. Legacy POST/import clients may continue
supplying their separate brand values; catalog creation combines that value
into the name without duplication. Imported historical records are not backfilled.
The hidden compatibility brand field is not a separate visible editor.

Monetary DecimalField widgets in the shared design system now carry
`data-money-step="1"` and `step="any"`. Price, cost, fee, amount, balance and total
fields use shared arrow buttons and ArrowUp/ArrowDown to add/subtract exactly 1.
The JavaScript uses scaled BigInt arithmetic rather than floating-point rounding:
`1000.123456` becomes `1001.123456`. Min/max bounds remain enforced by the editor,
and existing Django Decimal validation and price floors remain authoritative.
`step="any"` keeps manually entered fractional prices valid in browser forms.
Quantity and percentage steps are unchanged. Dynamic grid/formset rows are
supported, including cloned previously enhanced inputs. Monetary controls in
handwritten POS-discount and permission-limit markup opt into the same behavior.
There is no schema change, migration, unit conversion or financial-history rewrite.

Follow-up files changed:

- `products/naming.py`, `products/forms.py`, `products/workspace.py`.
- `inventory/forms.py`.
- `internetservices/tailwind.py`, `internetservices/tests.py`.
- `inventory/static/inventory/js/money-inputs.js` and `css/money-inputs.css`.
- `templates/base.html`, `templates/products/product_form.html`.
- `templates/inventory/includes/pos_cart_lines.html`.
- `templates/users/includes/permission_group.html`.
- `products/static/products/workspace.js` (Name / brand paste header aliases).
- `products/tests_workspace.py`, `products/browser_workspace.py`.
- This document and the existing product-workspace screenshots.

Follow-up verification:

- Django `check`: no issues (0 silenced).
- `makemigrations --check --dry-run`: No changes detected.
- PostgreSQL focused tests (`products`, `internetservices`,
  `inventory.tests_purchase_full_create_modal`, `inventory.tests_purchase_workspace`):
  77 tests, 29.354 seconds, OK.
- Real Edge browser tests: 2 tests, 8.991 seconds, OK, including monetary arrow
  clicks/keyboard controls and retained six-decimal input precision.
- Full PostgreSQL suite: 713 tests, 414.306 seconds, OK.
- `node --check` for both monetary inputs and product workspace JavaScript: exit 0.
- `git diff --check`: exit 0 (CRLF conversion advisories only).

Follow-up output: `.catalog-refinement-focused.log`,
`.catalog-refinement-browser.log`, `.catalog-refinement-full.log`.
The disposable Docker command retains the existing configured runtime and binds
the current source, collects assets under `/tmp/catalog-refinement-static`, and
uses separate PostgreSQL test databases named `test_catalog_refinement_20261027`
and `test_catalog_refinement_full_20261027`. The management operations are invoked
via Django `call_command` as in the earlier reproducible command above.

No commit, push or deployment is included. Existing unrelated changes are preserved.
