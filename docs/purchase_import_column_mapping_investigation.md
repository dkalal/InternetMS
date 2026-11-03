# Purchase import: seven-column layout investigation

Date: 21 October 2026, Africa/Dar_es_Salaam.
Branch: `feature/purchase-import-review-redesign`, based on `d188010`, including the earlier uncommitted review redesign.

## Confirmed cause

The latest screenshot selects the fixed mixed-template order. The supplied sample instead has 13 rows, seven tab-separated columns, and an empty sixth column. The mismatch is reproducible with `inventory.tests_purchase_column_mapping.SAMPLE_TSV`:

| Source column | First supplied value | Fixed template interpretation |
| --- | --- | --- |
| 1 | TP-Link TL-SG1024 | SKU |
| 2 | 7 | Product name |
| 3 | 400 | Quantity |
| 4 | TL-SG1024 | Unit cost |
| 5 | 6 | Category |
| 6 | empty | Sales / stock unit |
| 7 | TP-Link | Selling price |

The parser correctly refuses to convert column 4 to a price. The defect is that the UI offered only fixed layouts and a numeric error, without a safe way to explain and confirm the user's different columns. This is distinct from the earlier concatenated-input screenshot, where the baseline whitespace fallback misplaced product-name tokens.

The provided text reproduces this error exactly. The original browser clipboard bytes are unavailable, so this investigation does not claim to locate any earlier delimiter loss. The actual meanings of the unlabelled numeric columns are not established by their values. Neither quantity, acquisition cost nor selling price is automatically inferred. The regression suite's mapping of columns 2/3/5 is explicitly a test choice, not an assertion about the user's accounting data.

## Research and implementation decision

Python's CSV reader returns strings without automatic numeric coercion and supports explicit delimiters/quoting with `newline=''`. The implementation retains strict TSV parsing, empty cells, quoted Unicode/multiword fields and text identifiers, then applies Decimal validation only to mapped numeric fields. [Python 3.13 CSV documentation](https://docs.python.org/3.13/library/csv.html).

Shopify documents choosing column headings through drop-down controls when source headers do not match its import schema. The relevant pattern adopted here is explicit source-to-field mapping before product review; its catalog overwrite semantics are not adopted. [Shopify import troubleshooting, Choose column headings](https://help.shopify.com/en/manual/products/import-export/common-import-issues).

Django's `atomic()` boundary commits or rolls back a group of writes together. Mapping remains an entirely read-only operation and feeds the existing normalized purchase payload; product creation, submission receipts, audit, purchase totals and lines still persist through the existing draft-save transaction. Receiving remains separate. [Django transaction documentation](https://docs.djangoproject.com/en/5.2/topics/db/transactions/).

## Implemented behavior

Choose columns is available for both paste and XLSX upload. For uploads the intended worksheet remains `Purchase rows`; other worksheets are never guessed into purchase lines. The mapper displays source column numbers, up to three short value examples, the detected row count, and a reversible First row contains column headings selection.

Unique recognized headers may suggest fields. Headerless numeric/text examples never assign financial or identity fields automatically. Every source column must have an explicit purchase field or Do not import choice. Quantity and Unit cost, plus SKU or Product name, are required; duplicate target fields, unknown fields, malformed JSON, excessive columns and missing choices fail server-side. The target allowlist contains no tenant, catalog product, category or unit IDs.

Fixed-order numeric mismatches and headerless inputs beyond the legacy layout return a structured `column_mapping_required` response and open the mapper at Add data while retaining the source. Confirmed mappings normalize into the same purchase-row contract as ordinary template input. Blank SKU/serial identifiers remain text, source row references remain stable, and business validation stays mandatory. Structural errors never proceed to business persistence.

Mapping edits, source replacement and header changes invalidate old row decisions and pending responses. A failed header reinspection cannot leave an earlier valid mapping active. Ignored columns are counted and disclosed throughout the review, including after row edits. Client rendering uses text nodes for spreadsheet values. Scope/CSRF/method checks apply to the existing preview endpoint for both inspection and review.

Use: Import purchase items → Paste from spreadsheet → Choose my spreadsheet columns (or Choose columns) → select the actual field for each column → Review mapped items → resolve existing/new products → Add to purchase → save draft → receive through the existing review.

## Verification

- Before implementation, the supplied sample reproduced Row 1, unit cost with the mixed template layout, and explicit mapping tests failed because mapping was unsupported.
- Parser/structure tests passed with the exact 13-row sample, explicit test mappings, blank SKU, text identifiers, upload/paste equivalence, quoted Unicode/newlines, supported numeric grouping, malicious/malformed mappings, formulas and size/row/column limits.
- Focused Docker/PostgreSQL suite: 83 tests passed in 46.783 seconds. This includes purchase workspace/contract, paste, mixed import and mapping regressions.
- Final browser suite: two tests passed in 17.589 seconds in headless Edge with an isolated PostgreSQL test database. Coverage includes the supplied sample, desktop/mobile mapper and footer bounds, no guessing, duplicate assignments, ignored columns after edits, reversible headers, failed header reinspection, source replacement, explicit new/existing resolution, 200-row pagination, keyboard/focus behavior and record-neutral application.
- `check` in Docker: no issues. `makemigrations --check --dry-run` in Docker: no changes detected. JavaScript syntax checks and `git diff --check`: passed. `collectstatic --noinput`: passed.
- `python manage.py test --keepdb --verbosity 0` in Docker/PostgreSQL: all 604 tests passed in 399.627 seconds, including the final 200-row mapped-query regression and tenant-isolation fixtures.

The first attempt using the default retained test database encountered fixtures left by earlier verification; focused/full/browser runs use separate fresh test database names instead. No business database was flushed or populated for these checks. No new concurrent-submission verification is claimed; the existing retry/atomic-save/receive regressions remain in the suites.

## Files and runtime state

Changes for this follow-up are in `inventory/purchase_import.py`, `inventory/views.py`, `inventory/static/inventory/js/purchase-import.js`, `inventory/static/inventory/css/purchase-workspace.css`, `templates/inventory/includes/purchase_import_dialog.html`, `inventory/tests_purchase_column_mapping.py`, `inventory/tests_purchase_mixed_import.py`, `inventory/browser_purchase_import.py`, and the purchasing contract/this report. Pre-existing billing edits and unrelated local files are preserved. No migration, application dependency, commit, push, merge or deployment was added.

The checked local Compose web container has `/app/inventory` but no `inventory.purchase_import` module, and Compose mounts only media rather than application source. It therefore does not represent the current workspace implementation. This does not establish which runtime produced the user's newer screenshot. Build/reload the intended application from this branch, including `collectstatic`, to serve the fix; merely clearing a browser cache cannot update code baked into an older image.

Local verification artifacts are under `.backups/purchase-import/`, including `column-mapping-desktop.png`, `column-mapping-mobile.png`, and the test logs. Tests use explicit synthetic financial mappings; no sample was imported into a business tenant.
