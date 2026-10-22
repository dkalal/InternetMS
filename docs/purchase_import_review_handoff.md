# Purchase import review redesign

The subsequent seven-column sample investigation and explicit mapping implementation are documented in [purchase_import_column_mapping_investigation.md](purchase_import_column_mapping_investigation.md), including the latest 604-test regression result.

Branch: `feature/purchase-import-review-redesign`.
Baseline: `d188010` (`feature/unified-purchasing-mixed-import`).

## Reproduction and correction

The original clipboard text was unavailable. The supplied screenshot shows a single apparent input record with product-name text in Quantity. It cannot establish whether delimiters were lost before copying, during clipboard handling, or in another source.

The synthetic fixture below reproduces that mapping failure against the baseline parser:

```text
LAPTOPDELL Latitude 5420LAP00151,200,000LAPTOPHP ProBook G9LAP00231350,000
```

It contains zero tabs and zero newlines. Baseline parsing produces one row and assigns `Latitude` to Quantity. The updated parser rejects it before catalog resolution, retaining the source input at Add data. Selecting the mixed format cannot bypass the structural rejection. Reordered/partial recognized headers previously became purchase data; they now map by normalized header names. Duplicate/unknown headers and excess columns fail explicitly.

Regression fixtures also cover quoted names, embedded tabs/newlines, LF/CRLF, blank leading/intermediate cells, blank source rows, leading-zero text identifiers, numeric grouping, formula cells, incorrect worksheets/headers, 200 non-empty rows and upload/paste equivalence. Physical source references survive review and draft-save errors. Excluded serial allocations no longer invalidate included rows.

## Resulting workflow

1. Upload the XLSX template or paste spreadsheet cells. Source methods are explicit; headerless mixed input requires its format selection.
2. Review compact, paginated rows with functional All/Matched/New/Needs attention/Excluded filters. Resolve one item in a desktop side panel or mobile editor. Select eligible rows for category/unit changes or explicit exclusion.
3. Inspect the final summary and add the included items to the purchase workspace. Save the draft to persist approved new products and purchase lines; receive stock through the existing purchase review.

The dialog preserves edits on close, traps/restores focus, supports Escape and arrow-key tabs, announces errors/counts, escapes spreadsheet text, ignores stale preview/search responses, and invalidates edited resolutions until server validation. Existing catalog selection is explicitly confirmed and displays name, SKU, brand/model, unit and tracking. Category/unit choices remain tenant-scoped. Estimates round each line with HALF_EVEN cents to match the existing purchase total calculation.

## Verification performed on 21 October 2026

Checks used the current source, not the old running web image. Docker verification mounted the workspace into temporary Compose `web` containers. Full and browser runs used separate PostgreSQL test database names; the ordinary web/database services were not replaced.

| Check | Result |
| --- | --- |
| `python manage.py check` in Docker | Passed, zero issues |
| `python manage.py makemigrations --check --dry-run` in Docker | Passed, no changes detected |
| `node --check inventory/static/inventory/js/inventory-ui.js` | Passed |
| `node --check inventory/static/inventory/js/purchase-import.js` | Passed |
| Focused parsing/import/paste/contract/workspace suite in Docker | 70 tests passed |
| Final structure/search/mixed-import suite in Docker | 33 tests passed |
| `python manage.py test --keepdb --verbosity 0` in Docker | 590 tests passed in 393.407 seconds |
| `python manage.py test inventory.browser_purchase_import --keepdb` with isolated test DB | Passed in headless Microsoft Edge |
| `python manage.py collectstatic --noinput --verbosity 0` | Passed |
| `git diff --check` | Passed |

Browser coverage includes structural errors retaining source, mixed existing/new rows, explicit unknown-SKU selection, row-edit invalidation, counts/filters, selected-only bulk changes, exclusion/reinclusion, source replacement, line rounding, 200-row pagination/application, desktop/mobile layout, viewport/footer bounds, both directions of focus wrapping, arrow-key filters, Escape/restoration, and no product/purchase/movement writes after application. Screenshots are local artifacts under `.backups/purchase-import/`: `desktop.png`, `desktop-200.png`, `mobile-list.png`, and `mobile-editor.png`.

An early parallel verification attempt shared a test database and encountered a lock conflict; runs were restarted with separate test database names. The first completed full suite found an illustrative example name colliding with a catalog-preload assertion. The example was corrected and the existing test retained; the final full suite passed. Browser execution required installing Playwright only in the development virtual environment; it is not a new application dependency.

## Files and rollout

- `inventory/purchase_import.py`: parsing, header aliases, numeric/source normalization and excluded-serial correction.
- `inventory/views.py`: explicit format selection, detected-format metadata, workbook instructions/internal metadata, bounded catalog display attributes and prefetched category units.
- `templates/inventory/purchase_form.html`: dialog include and dedicated script.
- `templates/inventory/includes/purchase_import_dialog.html`: three-step dialog.
- `inventory/static/inventory/js/purchase-import.js`: review state, editor, search, bulk actions, totals and accessibility.
- `inventory/static/inventory/js/inventory-ui.js`: removes the superseded import controller.
- `inventory/static/inventory/css/purchase-workspace.css`: scoped desktop/mobile dialog styles and scroll lock.
- `inventory/tests_purchase_import_structure.py`: synthetic parsing regressions.
- `inventory/tests_purchase_mixed_import.py`: normalized save/source/exclusion regressions.
- `inventory/tests_purchase_paste_rows.py`: updated structural-failure expectations while preserving business validation tests.
- `inventory/tests_purchase_product_search.py`: search-result display metadata assertions.
- `inventory/browser_purchase_import.py`: optional repeatable browser regression check.
- `docs/unified_purchasing_phase_0b_contract.md`: current parsing/review contract.
- This handoff document.

No migration is added. Existing tenant permissions, product/SKU persistence, atomic draft save, submission receipts and receiving service remain the persistence boundaries. Existing save/review/receive regressions passed; this change does not claim new concurrent-submission verification. CSV and comma-decimal formats are intentionally unsupported; identifiers must be stored as text in the spreadsheet. The current running Docker web image does not mount application source: a later authorized image rebuild/restart with `collectstatic` is required to view the changes there. No deployment, merge, commit or push was performed for this task. Pre-existing billing template edits and unrelated local artifacts were preserved.
