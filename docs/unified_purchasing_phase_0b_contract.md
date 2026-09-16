# Unified purchasing Phase 0B historical note

Phase 0B introduced a purchase-package conversion experiment. That workflow is now intentionally deferred and is not available in product forms, purchase entry, APIs, or imports.

The supported contract is one selected sales/stock unit per product. Buying price, purchase quantity, purchase unit cost, stock balance, and every selling-price tier refer to that same unit. Fractional same-unit quantities, six-decimal inventory costing, weighted-average cost, immutable confirmed transactions, tenant isolation, RBAC, and the prohibition on selling at or below cost remain active.

Legacy package-related database columns are temporarily retained because the data audit found one product with non-default package configuration. They are preservation-only and must not be exposed or used for new transactions. Before a future removal migration, export the affected product identifiers, tenant identifiers, package labels, factors, costs, normalized buying prices, and audit timestamps to a tenant-controlled archive; verify the archive; obtain explicit approval; then re-run the audit and remove only the legacy columns. No purchase-line package snapshots currently exist.
