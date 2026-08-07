# Technician pricing compatibility

This implementation uses Strategy B. The existing `retail_price` is materially used by the
legacy POS, billing-line pricing modes, quantity-break behavior, tests, and historical data.
It is therefore retained and is not copied into or reinterpreted as Technician Price.

New products have an optional `technician_price`. Its effective value is the configured value,
or `selling_price` when blank. New carts, quotations, invoices, and API-created invoices select
an explicit Standard, Technician, or Wholesale sale category. Existing documents and draft carts
are migrated to the internal `retail` compatibility category so their behavior and stored values
remain unchanged.

Historical line-item `unit_price` and `base_unit_price` values remain the financial snapshots.
Quotation conversion and invoice reissue preserve those snapshots even when the product catalog
changes later.

After the business confirms that no active clients or draft carts depend on legacy retail pricing,
a later cleanup can archive the compatibility category and decide separately whether to retire the
old `retail_price` field. No automatic data copy should be performed without that decision.
