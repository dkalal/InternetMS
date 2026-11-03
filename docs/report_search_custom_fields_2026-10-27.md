# Report search simplification and product-grid custom fields

Branch: `feature/cart-lifecycle-management`; base `36bfa1f`.
Date: 2026-10-27. No commit, push or deployment.

## Report search

The Search in selector and its long help text have been removed. There is one
search box, matching the report's names, SKUs, references and text identifiers.
Financial amounts, quantities and dates no longer become accidental text matches.
Date/supplier/profit-result filters remain the supported controls for those
specific report dimensions. Old `search_in` parameters cannot change the new
search semantics and are removed from generated pagination/export links.

Search is still applied before pagination, totals and Excel generation, with the
existing tenant, cost-view and export permission checks. No financial, valuation,
stock or historical snapshot data is mutated by a report request.

## Add custom field in Additional information

The product grid now exposes Add custom field to staff holding the established
`CUSTOM_FIELD_MANAGE` permission. It reuses the existing definition modal, model
form, CSRF protection, server-side tenant context and inline-create endpoint.
The label explains that a definition belongs to the business's product catalog,
rather than to just the current row.

Both Save custom field and Save & use now perform an AJAX definition save in this
grid context. A successful active/show-on-create definition is inserted into all
current row editors and the template used for future rows. Its real default,
required flag and widget are rendered by the existing server-side custom-field
pipeline. New controls have stable per-row names/IDs and accessible label/help
references. Required-field indicators and existing server-side validation apply.
Unsaved products and pending image files stay attached to their rows.

Other screens retain their existing modal behavior. Definition JSON adds two
backward-compatible visibility flags. Inactive or hidden-on-create definitions
are not inserted into creation rows. Creating a definition is a separate explicit
metadata action; canceling unsaved products does not undo that reusable definition.
Other already-open browser tabs do not receive real-time definition updates.

## SKU

SKU allocation code is unchanged. See the researched
[SKU numbering recommendation](sku_numbering_recommendation_2026-10-27.md).
It recommends a single tenant-scoped sequential allocation service and explicitly
preserves every existing SKU/history record. Implementing that proposal would
require a separately reviewed additive numbering-state migration.

## Files touched in this task

- `inventory/reporting.py`, `inventory/views.py`, `inventory/tests_reporting.py`,
  `inventory/browser_reporting.py`.
- `products/workspace.py`, `products/static/products/workspace.js`,
  `products/tests_workspace.py`, `products/browser_workspace.py`.
- `custom_fields/services.py`, `custom_fields/views.py`.
- `templates/products/product_workspace.html`,
  `templates/includes/custom_field_definition_modal.html`.
- Reporting follow-up contract, this handoff, and the SKU recommendation.
- Updated product-workspace screenshots and new `docs/screenshots/report-simple-search`
  desktop/mobile screenshots.

Unrelated tracked and untracked user work is preserved. No migrations or new
runtime dependencies were added. Recommended commit split: one implementation
commit for the two approved UI fixes and their regressions/contracts, and a
separate documentation commit for the SKU research/proposal.

## Verification

Django commands run in disposable Docker Compose web containers with the current
workspace mounted at `/app`, PostgreSQL and separate test databases. Static assets
are collected under `/tmp/simple-reports-fields-static`. The management operations
are invoked through `call_command` after `django.setup()` so the isolated database
and static root can be selected without changing project settings.

- `python manage.py check`: no issues (0 silenced).
- `python manage.py makemigrations --check --dry-run`: No changes detected.
- `python manage.py test products custom_fields inventory.tests_reporting --keepdb --noinput`:
  81 tests, 32.648 seconds, OK.
- `python manage.py test --keepdb --noinput`: 714 tests, 509.166 seconds, OK.
- Headless Edge: product workspace tests plus the targeted simplified-report
  browser test, 3 tests, 13.269 seconds, OK. Browser database is isolated SQLite;
  backend regression verification uses PostgreSQL. The existing Tailwind CDN URL
  is served an unchanged temporary download for browser-test connectivity.
- `node --check products/static/products/workspace.js`: exit 0.
- `git diff --check`: exit 0, CRLF conversion advisories only.

Regression coverage includes existing totals/export/permission/tenant rules,
old search-mode parameters, metadata permission denial, forged organization input,
required custom-field defaults, row insertion, pending-picture retention and
successful batch custom-value persistence. Desktop and 390px mobile rendering
were checked for the simplified report search.

Logs: `.report-custom-field-focused.log`, `.report-custom-field-browser.log`,
`.report-custom-field-full.log`. No check was weakened or skipped to hide a failure.
The running application image has not been rebuilt/restarted; its final source
must be loaded through the normal local build/restart or release process.
