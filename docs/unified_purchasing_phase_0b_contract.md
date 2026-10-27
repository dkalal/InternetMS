# Unified purchasing same-unit contract

## Status and purpose

This document is the active regression contract for the unified purchasing redesign. Phase 0B originally included a package-to-smaller-unit experiment. That experiment is intentionally deferred. The supported workflow now uses one selected sales/stock unit for each product.

This contract protects the purchasing foundation while the workspace, high-volume grid, search, and later quick-create phases evolve.

## Supported unit and costing rule

- A product has one selected sales/stock unit.
- Product buying price, purchase quantity, purchase unit cost, stock balance, and all selling-price tiers refer to that same unit.
- Fractional same-unit quantities and six-decimal inventory costing remain supported.
- Weighted-average movement cost is the authoritative future-sale cost floor whenever positive movement-backed stock exists; otherwise the product buying price is used.
- Net pre-tax unit revenue must be strictly greater than the applicable cost floor. Equality is not allowed.
- Receiving higher-cost stock is allowed even when it produces a pricing warning.
- Once inventory movement history exists, the product's selected sales/stock unit is immutable. A unit correction requires an explicitly approved inventory reconciliation; an ordinary catalog edit must never reinterpret historical quantities or costs.
- Adopting a different unit after any stock, purchase, cart, or billing history creates a separate tenant-scoped successor product with a new SKU and zero stock. The old product becomes inactive, all historical references remain attached to it, and an idempotent audit event records the decision and reason.
- The successor workflow never performs package conversion, stock transfer, cost conversion, or historical reassignment. Verified physical quantities enter only through the approved receiving or inventory-reconciliation boundary.
- Deployments must run the read-only `audit_product_units --fail-on-drift` gate. Any reported mismatch requires explicit review before the affected invoices are paid or a new release starts.

## Transaction invariants

1. Tenant context comes from authenticated server-side membership. Supplier, product, purchase, line, balance, movement, and serial records must belong to the active tenant.
2. A draft purchase never changes live stock.
3. Only an authorized final confirmation can receive stock.
4. Confirmation is atomic, row-locked, idempotent, and all-or-nothing.
5. Every confirmed purchase line creates its stock-in history exactly once. A repeated confirmation cannot create another movement or serial unit.
6. One invalid line prevents the whole purchase from posting.
7. Quantity, unit cost, and totals are server-validated and server-authoritative.
8. Confirmed purchases, purchase lines, stock movements, cost snapshots, and serial history are immutable.
9. Services and non-stock items cannot be received.
10. Serialized products require exactly one normalized, tenant-unique serial for every whole unit. Duplicate serials within a line, across lines, or against existing tenant stock fail before posting.
11. Existing product-image behavior remains optional and independent of stock receipt.
12. Purchase references remain tenant-specific and concurrency-safe.
13. RBAC is enforced server-side; hidden UI is never sufficient authorization.

## Deferred package conversion

Package labels, factors, costs, and earlier purchase-line snapshots remain in the schema only for preservation and historical compatibility. They must not be exposed or used by new product forms, purchase forms, APIs, imports, calculations, or stock-posting services.

Historical purchase lines with an `authoritative_purchase_total` continue to display and aggregate that immutable total. New same-unit lines leave that legacy field empty and calculate total as quantity multiplied by unit cost.

Before any removal migration:

1. Re-run the production data audit.
2. Export affected tenant/product/purchase-line identifiers and legacy values to a tenant-controlled archive.
3. Verify the archive and rollback path.
4. Obtain explicit approval.
5. Remove only fields proven safe to remove through a new migration.

## High-volume acceptance scenario

One supplier delivery contains five serialized routers and at least 24 ordinary product types in the same purchase. All quantities and costs use each product's selected unit.

- The whole delivery posts once or not at all.
- One invalid serialized line leaves every balance, movement, serial, and purchase status unchanged.
- Duplicate serials across separate purchase lines fail before stock posting.
- Repeating confirmation does not duplicate any movement or serial.
- Another tenant cannot view, edit, or confirm the purchase.

## Release gates

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test inventory.tests_purchase_contract --keepdb
python manage.py test inventory.tests_purchase_workspace --keepdb
python manage.py test inventory products users billing --keepdb
python manage.py test --keepdb
```

Phase 2 may introduce the compact high-volume line grid, keyboard entry, bulk selection, and server-side product search. Inline supplier/category/product creation and spreadsheet import remain later, separately approved work.

### Phase 2 search contract

The purchase product search endpoint is authenticated, permission-protected, tenant-scoped, read-only, paginated, and limited to active physical stock-tracked products. It may return only the metadata required by the purchase combobox; it must not expose cost or cross-tenant catalog data. The server-side form and confirmation service remain authoritative even when the browser uses search results to populate a row.

### Phase 2 compact-grid contract

The purchase workspace must not serialize or render the full tenant catalog. Each row carries only its selected/submitted product in the Django field queryset, while the remote combobox supplies discoverability. The grid may show client-side line totals and duplicate-product guidance, but persisted totals, tenant membership, product eligibility, tracking requirements, and stock posting remain server-authoritative. Separate rows for one product remain valid when batch or expiry details differ.

The bulk picker reuses the same protected search endpoint and only creates ordinary formset rows. Already-active products are identified before selection, selected products are de-duplicated again while rows are appended, and every resulting identifier is still validated by the tenant-scoped Django formset on submission. Bulk selection never writes stock or bypasses the draft/review/confirm lifecycle.

### Legacy Phase 3A supplier quick-create endpoint contract

The earlier quick-create endpoint may create a supplier from company name and phone only. The endpoint is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, duplicate-safe, atomic, and audited. It ignores unsupported advanced fields and returns structured validation errors. The purchase dialog now uses the full Suppliers workflow described below.

### Phase 3B category quick-create contract

The purchase product workflow may create a category from name and one existing active tenant unit only. The selected default unit is also the category's sole initial allowed unit. The endpoint is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, duplicate-safe, atomic, and audited. It rejects foreign-tenant and inactive units, ignores unsupported advanced category fields, and returns structured validation errors. This endpoint is a foundation for the product quick-create dialog; it must not add a disconnected category control to the purchase workspace.

### Legacy Phase 3C product quick-create endpoint contract

The earlier quick-create endpoint may create an active physical stock product from name, tenant category, an allowed active sales/stock unit, selling price, and optional serial/expiry tracking. SKU is generated tenant-safely; quantity, stock, and initial buying cost start at zero and acquisition cost remains authoritative on the purchase line. Selling price remains required until a first-class pricing-required catalog state is introduced. The endpoint is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, atomic, and audited. The purchase dialog now uses the full catalog workflow described below; every created product still passes the normal draft and confirmation boundaries.

### Full-form creation from a purchase

The purchase workspace opens the owning supplier and catalog create forms in a large, keyboard-accessible dialog. All fields and custom product fields on those pages remain available, with their existing server validation, file handling, permissions, tenant scoping, and audit behavior. The purchase context fixes a new supplier as active and a new catalog item as active, physical, and stock-tracked so the saved record can be selected on the purchase. Creation alone never changes stock; the purchase still follows its normal draft and receipt flow. The catalog create form can use the category quick-create endpoint to add a category with one active tenant unit, then select that category and unit without losing product form entries. The earlier minimal supplier and product quick-create endpoints remain available for compatibility but are no longer used by these dialogs.

### Phase 4A receipt-review contract

The purchase detail page is the authoritative receipt review. For drafts it derives the projected post-receipt weighted-average cost from current tenant inventory plus every line for the same product, including separate batch or expiry rows. It identifies selling modes that would be at or below that projected floor without blocking receipt. Cost values remain permission-protected; users without cost access see only generic pricing-review status. Confirmed purchases display readiness against the actual movement-backed cost floor. No readiness flag is persisted because the ledger and current prices remain the sources of truth.

### Phase 4B cart-readiness contract

The POS catalog derives readiness from the effective price for the cart's active pricing category and the authoritative current cost floor. For an existing cart line it evaluates the next quantity so quantity-break pricing is represented accurately. An unsafe product remains visible with generic pricing-review guidance, but every add/select action is disabled. Existing form, adjustment, discount, checkout, tenant, and permission validation remain the server-side enforcement boundaries; UI readiness never replaces them and no readiness state is persisted.

### Phase 5A pasted-row contract

The following describes the earlier SKU-only preview. The mixed purchase import contract below supersedes its template and apply behavior while retaining six-column input compatibility.

The purchase workspace may preview up to 200 tab-separated spreadsheet rows identified by exact SKU. Preview is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, size-limited, and non-mutating. It accepts quantity and unit cost in the product's same sales/stock unit plus optional batch, ISO expiry date, and comma-separated serials. Only server-validated rows may be appended to ordinary purchase formset rows; invalid rows remain visible in preview and are never added. Draft save and final confirmation remain the only persistence and stock-posting boundaries.

Manually typed rows may use single or repeated whitespace between up to six fields, including batch, ISO expiry, and comma-separated serials. Tabs remain required when a field itself contains spaces. All rows are normalized before the bounded tenant-scoped SKU lookup. A row with only an SKU remains invalid until quantity and unit cost are supplied. Unknown SKUs are never guessed or automatically created. Products referenced by purchasing, stock, or sales history remain protected from deletion; the catalog deletion screen must report that safely rather than exposing an exception.

The purchasing workspace provides an authenticated, permission-checked XLSX template for this preview flow. Its blank Purchase rows sheet uses the exact six-column order: SKU, Quantity, Unit cost, Batch reference, Expiry, Serial numbers. The Instructions sheet contains clearly labelled illustrative examples. Only the exact known current or legacy header is skipped during preview; unknown rows remain visible with validation errors. Download and preview do not create purchases, reserve references, or alter stock. The compact grid edits the same formset fields and keeps confirmation on the separate review page.

### Mixed purchase import contract (version 2)

New-product import rows are saved as tenant-scoped `PurchaseProductProposal` records linked to the draft, outside the registered Product catalog. Draft save creates neither new Products nor SKUs nor inventory records. Existing registered products, including zero-stock products, remain visible in cart builder; existing server-side stock checks block unavailable sales. Receiving revalidates proposals and product-creation permission, allocates SKUs, creates Products and PurchaseLines, receives stock, records audit events and consumes staging in one transaction. Failure rolls back all those writes and preserves the draft/proposals. Unchanged successful retries remain idempotent. Cancelled drafts never materialize proposals.

The purchase workspace accepts one version-2 tab-separated or XLSX list with headers `sku, product_name, quantity, unit_cost, category, unit, selling_price, brand, model_number, serialized, track_expiry, batch_reference, expiry_date, serial_numbers, staging_reference`. The original six-column SKU paste format remains accepted. The downloaded workbook has only blank data rows on `Purchase rows`; its examples live on `Instructions`. Text identifiers retain leading zeroes. Formula cells are rejected. The limits are 200 non-empty import rows, 100,000 pasted/applied characters, 250 KB XLSX, 15 columns, and 250 total workspace rows including manual formset rows.

Preview and Apply to workspace write no business records. All included rows must resolve before Apply. Explicitly excluded rows are counted and never saved. Exact eligible tenant SKU matches may resolve automatically; missing and unknown SKUs require explicit product selection or Create new. Ineligible known SKUs block replacement creation. Name suggestions remain tenant-scoped, bounded, and advisory. A staging reference may join repeated new-product rows only when their definitions agree. Existing category, active tenant unit, selling price, SKU, serial, expiry, and precision rules are revalidated at draft save. No purchase-package conversion is supported.

Draft save stages approved new definitions, saves existing-product lines, recalculates totals including proposals, and records audit events atomically. Product SKU allocation occurs during receiving using the tenant SKU constraint with bounded savepoint retries. A tenant-scoped UUID submission receipt stores a payload fingerprint and purchase result so unchanged retries return the same draft and changed payloads conflict. Migration `0013_purchaseimportsubmission` supplies that receipt; additive migration `0016_purchase_product_proposals` supplies staging. Apply both before serving the workspace. Existing products, confirmed purchases and stock history are preserved. Roll back application code only with proposal support retained or after proposals have been received/exported; do not drop a populated proposal table. The explicit `stage_purchase_proposals --tenant-id ID --purchase-id ID` command previews conservative legacy conversion. `--apply` transactionally restages unused imported draft-only products with an audit trail; products with stock, other relationships, images, descriptions or advanced pricing are retained for manual review.

`purchases.create` gates preview, template, search, and draft save. `products.create` gates product creation within an import and the quick-create endpoint. Both remain distinct from category creation and receipt confirmation. Purchase creation may be explicitly delegated to a buyer without delegating product creation or finance-wide purchase visibility. That buyer may review and edit only purchases they created. The existing receiving permission is unchanged; no global legacy alias is remapped.

### Purchase import review and structural parsing

The import dialog has three steps: Add data, Review items, Add to purchase. Source input remains available after structural failures. Review uses exclusive status filters (All, Matched, New, Needs attention, Excluded), 20-row pages, explicit row selection for bulk changes, and a contextual editor. Invalid proposed products count as Needs attention until validated. Bulk category/unit changes affect only selected, included new definitions. Closing the dialog preserves edits; only Add to purchase replaces the workspace's applied snapshot. Changing source data or its selected format invalidates previous resolutions and pending preview responses. Preview and application remain stock-neutral and record-neutral.

Upload and paste share `inventory.purchase_import.parse_input`. Paste uses strict quoted TSV with physical source line numbers, preserving leading/intermediate empty cells and embedded tabs/newlines. Headers map by normalized name, with aliases `product/name → product_name`, `qty → quantity`, `cost/purchase_price/price → unit_cost`, `batch → batch_reference`, `expiry → expiry_date`, `serials → serial_numbers`, `model → model_number`, `sales_unit → unit`, and `is_serialized → serialized`. Duplicate aliases, unknown headers, excess columns, missing delimiters and text in numeric columns fail before catalog resolution. Required structural headers are Quantity, Unit cost, and SKU or Product name; new-product requirements are checked during resolution. Trailing omitted optional cells remain empty; intermediate omitted delimiters are never reconstructed. CSV and comma decimals are unsupported. Headerless mixed rows require an explicit format selection; headerless legacy rows require an unambiguous SKU/quantity/cost layout. Legacy space input is accepted only with numeric quantity/cost and up to three additional single-token fields. A concatenated record is never guessed into products.

The workbook always reads `Purchase rows` with recognized headers, independently of the active sheet. Blank rows retain source positions and do not consume the 200-record allowance; worksheets additionally have a 10,000-physical-row safety bound. Instructions and examples are never imported. Internal compatibility metadata remains version 2 and the optional shared-product reference column is hidden in the generated workbook. Numeric normalization accepts plain Decimal values or English thousands grouping with a decimal dot (for example `1,250.50`); other separator conventions produce actionable errors. Names and serial lists never undergo comma replacement. Formula cells remain row errors. Identifiers must be stored as text in the spreadsheet to preserve leading zeroes.

Synthetic reproductions live in `inventory/tests_purchase_import_structure.py`. The original clipboard bytes were unavailable: the screenshot alone cannot establish where its tabs/newlines were lost. The baseline reproducibly accepted concatenated names as a legacy row and misplaced fields; it also treated reordered/partial recognized headers as purchase data. Both now fail safely or map by header name. Source references survive revalidation; excluded serials cannot invalidate included allocations. Client summary totals sum individually rounded purchase-line totals with Decimal-compatible HALF_EVEN rounding.

No schema change is needed for this review redesign. Run `collectstatic --noinput` when building the application to include `inventory/js/purchase-import.js` and the updated workspace stylesheet. Optional real-browser checks use synthetic data and a test database: install Playwright in the development environment and run `python manage.py test inventory.browser_purchase_import --keepdb` with installed Microsoft Edge (or set `PURCHASE_IMPORT_BROWSER_CHANNEL`). Run browser and ordinary suites sequentially when their database settings point to the same test database. Browser screenshots are local artifacts under `.backups/purchase-import/`.

### Explicit source-column mapping

Selecting a category for a proposed new product fills its active allowed default sales/stock unit. If that default is unavailable, the sole active allowed unit may be selected; multiple alternatives require an explicit choice. An explicit unit choice survives unrelated editor updates. Bulk category selection follows the same rule and affects only selected, included new-product rows. Existing-product units remain authoritative, and server-side category/unit validation remains required at preview and draft save.

The purchase import's Add data step also supports custom XLSX/TSV layouts through Choose columns. The existing authenticated, purchase-permission-protected, POST/CSRF preview endpoint accepts `operation=inspect_columns` to return bounded source structure only: row/column counts and at most three 100-character examples per source column. It never queries catalog records to infer column meanings or writes import jobs/business records. For XLSX the intended sheet remains `Purchase rows`; Instructions/Examples are never selected automatically.

`column_mapping` is an ordered JSON array with one allowlisted purchase field or the explicit `__ignore__` marker for every physical source column. `has_header` must be explicitly true or false for a custom mapping. Numeric values never infer Quantity, Unit cost or Selling price. Recognized, unique header names may suggest fields; unknown/duplicate headings remain unassigned. Each imported field is assigned at most once, Quantity and Unit cost are mandatory, and at least one of SKU or Product name is required. Blank leading/intermediate cells, text identifiers, row references, quoting and all input/workspace limits are preserved. This normalizes into the existing purchase row contract; it does not expose tenant/product/category/unit IDs as mapping targets or alter catalog/opening-stock/historical import semantics.

A structural numeric mismatch in the fixed template order, or a headerless list beyond six legacy columns, returns HTTP 400 with `code=column_mapping_required` and a bounded `column_mapping` descriptor. The client opens the mapper at Add data, retains the source, and requires explicit choices before review. A mapped preview and all subsequent row/product validation use the same normalization and tenant-scoped resolution pipeline. Explicitly ignored source columns are disclosed in mapping and review. Header changes, source replacement and mapping edits invalidate row decisions and pending responses; a failed header reinspection blocks progression until a fresh inspection succeeds. All normal product permissions, pricing/precision/tracking validation, atomic draft save and receiving rules remain authoritative.

## Reporting and sale-cost follow-up

At sale completion, stock-cost authorization is rechecked after the balance is locked. Net revenue must remain above the locked weighted-average cost floor and cannot be less than the actual acquisition cost of the selected stock (including serial-specific costs). A rejection rolls back the whole receipt/payment/stock transaction. Historical negative cost snapshots remain unchanged and visible in reports.

See [the reporting audit](inventory_reports_followup_2026-10-24.md) for valuation provenance, search and historical-loss review.
