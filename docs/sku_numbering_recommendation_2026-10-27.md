# SKU numbering recommendation

Date: 2026-10-27. Branch: `feature/cart-lifecycle-management`, HEAD `36bfa1f`.
Status: research and proposal only; SKU generation has not been changed.

## Findings in this repository

- `Product.sku` is a 64-character, tenant-scoped catalog identifier. Normal writes
  trim and uppercase it; the database has a conditional `(tenant, sku)` unique
  constraint for nonblank values.
- `ProductForm.clean_sku` proposes a name-derived value, but SKU precedes Name in
  the declared form order. At that point `cleaned_data['name']` is not populated,
  so blank SKU can fall back to `ITEM`, then `ITEM-2`, etc. This is a code-path
  finding, not a claim that all previously saved records have this format.
- `products.creation.save_purchase_product` separately uses the actual product
  name, strips separators and truncates to 24 characters before trying numeric
  suffixes. The purchase helper defaults to 20 attempts; the grid uses 1000.
- The grid, quick purchase creation and purchase batches use that persistence
  helper. The legacy single-product view uses form.save instead. Ordinary
  imports/model construction and the successor workflow have their own paths.
- These paths are not equivalent numbering authorities. Long shared prefixes,
  punctuation and transliteration can also collapse different names to the same
  base and increase collision probing.
- Product names/brands/categories can change independently of identity. Historical
  inventory imports/proposals can contain SKU snapshots, so bulk renaming existing
  SKUs would risk breaking historical matching even though Product foreign keys
  remain intact. No existing SKU should be rewritten as part of this proposal.

## What established products document

There is no universal format that every enterprise uses. These official sources
support different strategies for different catalog structures:

| Product | Documented behavior | Relevance here |
| --- | --- | --- |
| Microsoft Dynamics 365 Supply Chain Management | Product numbers can be assigned manually or automatically through number sequences; variants can use nomenclature templates. | A shared sequential numbering authority is an established option. |
| Zoho Inventory | Variant SKU generation can use selected attributes, character counts, letter case, separators and custom text. | Attribute patterns suit stable variant dimensions, not arbitrary mutable names. |
| Shopify | Recommends short, consistent, unique SKUs and planning for growth; SKUs and barcodes serve different purposes. | Useful design guidelines; this source does not establish a universal native auto-numbering algorithm. |

Sources, checked on 2026-10-27:

- [Microsoft: Product identifiers](https://learn.microsoft.com/en-us/dynamics365/supply-chain/pim/product-identifiers).
- [Zoho: Create Items](https://www.zoho.com/us/inventory/help/items/items-overview.html).
- [Shopify: Using SKUs to manage inventory](https://help.shopify.com/en/manual/products/details/sku).
- [Zoho September 2026 release notes](https://help.zoho.com/portal/nl/community/topic/whats-new-in-zoho-inventory-september-2026)
  also announce configurable item SKU auto-numbering as Early Access. That is a
  qualified availability statement, not a claim that all Zoho organizations have it.

## Recommended design for JIMS

Use a tenant-scoped, sequential, nonsemantic generated SKU:

```text
PRD-000001
PRD-000002
PRD-000003
```

This is a recommendation derived from the repository's needs and those sources,
not a format mandated by any cited vendor. The ten-character initial format is
short enough for staff to read/type/print. Let the numeric portion grow past six
digits; do not wrap at 999999. Use one sequence for this shared Product catalog,
including its physical and service items. Do not reset annually or on deletion.

Name, brand, category, unit and price belong in their own catalog attributes and
search, rather than being encoded into this identity. Consequently a correction
to the visible name does not imply a new SKU. Supplier/manufacturer identifiers
and barcode/GTIN values are separate concepts. A SKU may be encoded in an internal
barcode label, but that does not make it a registered GTIN.

Keep the SKU input optional: blank means allocate on successful save. Existing
explicit/manual SKUs remain supported with the current normalization and tenant
uniqueness checks. Validate and handle collisions with old `PRD-...` values,
including manual imports. Do not silently overwrite an existing product or
change its code. Generated committed codes should not be recycled after deletion.
A gap in numbering is acceptable; this is not a statutory document numbering rule.

The same SKU string can legitimately exist in two independent tenants. Integrations
must identify a record using authenticated tenant context plus SKU, or an existing
stable product identifier with tenant checks. Predictability of a SKU is not an
authorization boundary. Do not add organization names/IDs into the visible SKU
merely to replace proper tenant isolation.

## Proposed implementation phase, requiring an additive migration

1. Add a tenant-owned number-sequence table, for example `CatalogNumberSequence`,
   with tenant FK, a fixed namespace and a positive next-number value, unique on
   `(tenant, namespace)`. Avoid a growing settings wizard for ordinary staff.
2. Audit existing case-normalized codes and any occupied `PRD-<digits>` range.
   Initialize each tenant's counter safely above already occupied generated-prefix
   numbers; preserve every existing product/SKU/history record. No backfill rename.
3. Put allocation in one products-owned creation service. Validate the complete
   batch before allocating. Under `transaction.atomic`, lock the sequence row with
   `select_for_update`, reserve numbers, create products and audit events, and
   save dynamic fields. Retain the current upload rollback cleanup and submission
   idempotency. Handle first-row provisioning with database uniqueness/savepoints.
4. Route normal product creation, the multi-product grid, purchase modal/quick
   create, purchase imports, ordinary catalog imports and any creation API through
   the same authority. Explicit successor SKUs remain distinct and obey their
   established permissions and zero-stock transition contract.
5. Treat database uniqueness as the final guard against legacy/manual/concurrent
   collisions. Skip or reject occupied codes clearly. Do not use COUNT or MAX as
   the runtime allocator, a timestamp, a random short string, or a name-suffix loop.
   Define how imports using the reserved prefix advance the locked counter.
6. Allocate final codes only during commit, not during GET, form cleaning or browser
   row creation. Keep code previews nonbinding. A rejected form must not consume a
   successfully issued identity; do not require gap-free numbering.
7. Protect existing historical references. Any tightening of SKU edits after stock,
   purchase, billing or import history needs its own documented policy and regression
   tests across forms/admin/services, rather than rewriting historical snapshots.

The migration would be additive and reversible: it creates numbering state and
changes no historical SKU. Rollback restores the old creation code while leaving
existing generated product codes unchanged. Verify old-code compatibility before
release and keep a database backup. This migration is not included in the current
UI change because the request asks for investigation and a recommendation.

Before enabling: PostgreSQL concurrency tests, tenant separation, prefix/manual
collisions, 200-row allocation, invalid-row rollback, retries after lost responses,
permission denial, no stock posting, and every import/embedded creation path.
