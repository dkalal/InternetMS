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

### Phase 3A supplier quick-create contract

The purchase workspace may create a supplier from company name and phone only. The endpoint is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, duplicate-safe, atomic, and audited. It ignores unsupported advanced fields, returns structured validation errors, and selects the created active supplier without submitting or clearing the purchase workspace. Advanced supplier details remain owned by the full Suppliers workflow.

### Phase 3B category quick-create contract

The purchase product workflow may create a category from name and one existing active tenant unit only. The selected default unit is also the category's sole initial allowed unit. The endpoint is authenticated, POST-only, CSRF-protected, permission-checked, tenant-scoped, duplicate-safe, atomic, and audited. It rejects foreign-tenant and inactive units, ignores unsupported advanced category fields, and returns structured validation errors. This endpoint is a foundation for the product quick-create dialog; it must not add a disconnected category control to the purchase workspace.
