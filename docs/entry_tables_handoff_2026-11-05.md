# Catalog-style entry tables

Branch: `feature/work-assignments`; base: `112db09`.

## Findings and implementation

`/products/create/` resolves to `products/product_workspace.html`, rather than
the single-item product form. Its spreadsheet uses a pale violet header, thin
cell borders, 36px controls and optional detail panels. Purchases already use a
dynamic grid; quotation and invoice share one formset template; suppliers use
the ordinary single-record form, including the embedded purchase dialog.

The four requested pages now use those spreadsheet visual conventions through
the scoped `inventory/css/entry-tables.css` stylesheet. Supplier identity and
contacts use a semantic table; address, notes and status remain in an expandable
details section. Submitted details and errors reopen that section. Existing
formset selectors and posted field names remain available to their controllers.

Billing's visually hidden leading header previously removed a grid cell from
layout, shifting column headings. The leading cell now occupies its column,
and headers and rows use identical column widths and gaps. At tablet widths,
billing uses labelled stacked controls to fit beside the sidebar.

## Files

- `inventory/static/inventory/css/entry-tables.css` (new shared, scoped styles).
- `templates/inventory/purchase_form.html` (stylesheet and scope class).
- `templates/billing/document_form.html` (stylesheet, scope, grid header alignment).
- `templates/inventory/supplier_form.html` (table, optional details, action footer).
- `inventory/browser_entry_tables.py` (optional real-browser verification).

Existing edits to billing templates and all unrelated user work were preserved.
No commit, push, deployment, production data write or migration was performed.
The running Docker application does not mount source code: these source changes
need the project's normal rebuild/release process to become visible there.

## Verification

Django commands ran in the configured Docker Compose `web` service, against
an isolated source copy under `/tmp/jims-entry-table-validation`. A temporary
settings module selected `test_jims_entry_tables`, avoiding contention with
another process using the ordinary test database. Static assets were collected
inside that isolated copy before template tests.

- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python manage.py collectstatic --noinput`: 179 files copied, 857 post-processed.
- `python manage.py test inventory.tests_purchase_workspace inventory.tests_purchase_full_create_modal billing.tests_document_flow billing.tests_package_descriptions --keepdb --settings=entry_table_settings`: 30 tests, OK.
- Local Edge/Playwright: `python manage.py test inventory.browser_entry_tables.EntryTableBrowserChecks --keepdb --settings=entry_table_browser_settings`: 1 test, OK. Temporary settings selected in-memory SQLite; the application Tailwind CDN script was cached for reliable rendering.
- Browser coverage: all four routes at 1440px, 1024px and 390px; no page overflow;
  all eight billing header/cell positions align; supplier detail expansion and
  server-rendered required-field errors work; no JavaScript page errors.
- Screenshots: `docs/screenshots/entry-tables/`.
- `python manage.py test --keepdb --settings=entry_table_settings`: 839 tests,
  OK (674.684s). No skipped or weakened tests were introduced.

The full-suite snapshot predates the final header alignment and tablet CSS
refinements. The final source is covered by browser verification; focused Django
checks were repeated on the final templates in `/tmp/jims-entry-table-final`
with a separate `test_jims_entry_tables_final` database: 30 tests, OK (25.726s).
Final system check: no issues; final migration-drift check: no changes detected.

## Security, compatibility and scope

No domain services, form choice querysets, authorization checks, tenant scoping,
CSRF behavior, monetary calculations, stock posting or historical records were
changed. Supplier creation remains one record per submission. This change does
not add batch supplier creation, spreadsheet paste to billing, or purchasing
package conversion. No business invariant changed.

Recommended commit: one cohesive UI change containing the files above and this
handoff, after reviewing the existing independent edits to the billing template.
