# Form UI refinement handoff — 13 October 2026

## Baseline and scope

Branch `codex/premium-invoice-quotation-workspace`; HEAD `9762f7b65a04028146b0b1538bf45e25e3d41b83`. The checkout contained substantial tracked and untracked user work before this task, including print, account, invitation, customer, product-form and inventory JavaScript changes. Those existing edits were preserved. No branch switch, commit, push, migration, deployment or production submission was made.

The source coverage matrix is in [form-ui-audit.md](form-ui-audit.md). It distinguishes changed surfaces from forms whose current source layout already fits this design direction. All rendered appearance claims remain unverified because a browser runtime was unavailable.

## Result

The existing branded panel and control system remains the visual foundation. Customer, supplier, category, billing sheet/item, package and branding workspaces now have responsive bounds from 640px to 1120px. Marked product and purchase metadata fields use an opt-in caption inside the control, with programmatic labels retained. Purchasing retains its current draft/review/receipt actions and same-unit stock rules.

Invoice and quotation create/edit share one focused editor with Items, Document details and Internal notes panels. All panels remain in one form; without JavaScript they remain visible. New documents with no customer open on details; validation reveals an inactive panel before focus. The old checklist and toolbar Add item control were removed. Each line has a 40px add-below button; new rows come from the empty form template and keep their own formset index. The server reads a bounded presentation-order value after normal formset validation so a new line between saved lines persists in the intended order without reassigning saved IDs. The summary sits below the table. The invoice-only Renew warning remains visible.

Invoice and quotation lists have a compact primary filter toolbar, expandable secondary filters, removable active criteria, natural table height, truthful sort arrows and explicit currency on amounts. The POS keeps its existing save/checkout flow while the catalog and cart widths, search, category control and product cards are better balanced. Cart cards show totals through the existing billing total service with tenant-scoped prefetched lines.

## Files changed for this task

- Audit and handoff: `docs/form-ui-audit.md`, `docs/form-ui-handoff.md`.
- Shared opt-in controls and styling: `templates/components/inside_caption_field.html`, `templates/components/inside_caption_script.html`, `inventory/static/inventory/css/jims-ui.css`.
- Standard forms: `templates/customers/customer_form.html`, `templates/inventory/supplier_form.html`, `templates/inventory/category_form.html`, `templates/inventory/stock_adjust.html`, `templates/inventory/purchase_form.html`, `templates/products/product_form.html`, `templates/services/package_form.html`, `templates/billing/billing_sheet_form.html`, `templates/billing/billing_item_form.html`, `templates/users/branding_settings.html`, `inventory/forms.py`, `products/forms.py`.
- Billing editor and lists: `templates/billing/document_form.html`, `templates/billing/document_list.html`, `templates/billing/includes/document_meta_fields.html`, `templates/billing/includes/document_line_item_form.html`, `billing/views.py`.
- Selling: `templates/inventory/cart_detail.html`, `templates/inventory/cart_list.html`, `inventory/views.py`.
- Focused regressions and updated width assertions: `billing/tests.py`, `inventory/tests.py`, `customers/tests.py`, `services/tests.py`. The customer and billing test files already had unrelated local edits; this task changed only the relevant assertions/tests.

## Verification

| Command/check | Result |
|---|---|
| `python manage.py check` | Pass, no issues |
| `python manage.py makemigrations --check --dry-run` | Pass, no changes detected |
| `python manage.py test billing.tests.BillingDocumentEditorViewTests --keepdb` | 8 passed, including new-row order on create and edit |
| `python manage.py test inventory.tests.InventoryAcceptanceTests customers.tests.CustomerRBACIsolationTests.test_customer_create_page_renders_professional_sections --keepdb` | 55 passed |
| Focused cart-list total, package-width and document-list tests | Pass |
| Django compilation of 14 changed templates; Node syntax check of billing editor inline script | Pass |
| `git diff --check` for changed tracked files | Pass |
| First `python manage.py test --keepdb` | 536 run, 3 print/PDF failures |
| Final `python manage.py test --keepdb` | 538 run, 4 failures: same 3 print/PDF failures plus a package-width assertion updated afterward; the focused package test then passed |
| `docker compose exec -T web python manage.py check` | Pass, no issues |
| `docker compose exec -T web python manage.py makemigrations --check --dry-run` | Pass, no changes detected |
| `docker compose exec -T web python manage.py test billing.tests.BillingDocumentEditorViewTests --keepdb --verbosity 0` | 8 passed |
| `docker compose exec -T web python manage.py test billing inventory products services customers users --keepdb` | 416 run, 2 print-text assertion failures and 3 migration-test errors from duplicate `auth_permission` entries in the reused test database |
| `docker compose exec -T web python manage.py test --keepdb --verbosity 0` | 535 run, 2 print-text assertion failures and 1 concurrency-test setup error from a duplicate `auth_user` entry in the reused test database |

The Windows run has three persistent failures: `BillingListViewTests.test_annual_number_displays_in_list_detail_and_print_view`, `test_invoice_print_is_compact_multipage_safe_and_hides_blank_tax_ids`, and `test_invoice_print_shows_previous_debt_and_recorded_payment_separately`. The first expects PDF output, while the Windows runtime lacks WeasyPrint's native `libgobject` dependency and serves printable HTML. Docker generates PDFs, so only the latter two print assertions fail there. They expect `Prior Balance at Issue` in print output, while the already-modified print template renders `Balance carried forward`. Print templates and those assertions were dirty before this task and were not edited here. Docker's `--keepdb` runs also encountered duplicate permission and user records in the reused test database during migration/concurrency tests. The full suite therefore remains red; this is not a release-ready green gate.

## Functional risk and remaining evidence

No model, migration, permission, tenant lookup, receipt posting, stock service, document numbering, tax policy or financial authority rule changed. The only server behavior change is ordering already validated billing lines for the UI's insert-after action. Cart-list totals call the existing server billing calculator on tenant-scoped prefetched lines; errors display `Review total` instead of a misleading amount. Existing cost-view conditionals and internal-note rendering remain in their owning templates.

Browser checks at 1366/1440/1920px, 390px, and 200% zoom are still required. In particular, exercise product/purchase caption states, both billing tabs and row insertion by keyboard and touch, item deletion, quotation edit order after reload, POS metadata save and checkout, and sticky/scroll clearance. No before/after screenshots or measured click/scroll/time comparison were captured. Disposable test data must be used for any browser financial or stock action. Resolve the three print/PDF failures in their owning work before treating the repository-wide gate as passed.

If commits are later authorized, split standard-form styling, billing editor/order tests, and list/POS refinements into cohesive commits. No commit or push was made.
