# Phase 3: commercial sales and payment-time physical posting

Branch: `feature/packaging-phase3`; base: Phase 2 `a458dc765ad64ad1f5b97671c1d606d1aee80b09`.
Activation remains closed until Phase 4 safety and release gates pass. Existing products stay off by default.

## Behavior

Cart entry explicitly selects the product-specific package or base unit, current configuration version, and physical pieces. Selections never reserve or deduct stock. Quantity actions identify the cart line, increment its selected commercial unit and prepare provisional pieces; staff can review/change pieces through Edit. Whole packages require intact sealed containers; cuts can use separate opened/returned pieces, then sealed stock. There is no prepayment cutting or dispatch.

Billing builds commercial snapshots from tenant-scoped server configuration, not client factors. The same product's base quantities determine mixed-unit wholesale eligibility; independent package prices are never derived from base prices. Final pre-tax net revenue divided by base quantity must exceed weighted-average cost after line/document discounts. Posted printing continues to use source invoice quantity/unit/price snapshots.

Cart conversion, quotation conversion and reissue carry unit/version and provisional selection context. The inventory API accepts product_packaging_id, packaging_version and container_selections (container_id/base_quantity); output exposes commercial and base quantities without acquisition costs. The legacy direct billing HTML form explicitly directs opted-in products to the unit-aware cart before any mutation. Existing ordinary and internet-service package billing is unchanged.

Invoice physical-piece review uses canonical permissions and invoice visibility, tenant-scoped choices, a locked parent, exact line/base totals and bounded physical review. Full payment locks the parent, products/balances in product order and containers in ID order, rechecks snapshots, cost and pieces, then commits receipt, base balance, movement, allocations and sale cost together. Stale selections fail without substitution or payment. Retried completed payments retain existing reference/cart idempotency semantics and return original results before later configuration checks.

Cost-out uses the difference between rounded before/after inventory values. Allocation costs use cumulative proportional rounding, with the last allocation receiving the residual. This preserves cents at final depletion without changing old movements.

## Migration and compatibility

`inventory.0022_cart_commercial_unit_identity` replaces cart/product uniqueness with conditional base-line and package-line uniqueness. Existing NULL-snapshot rows remain unchanged. No data conversion, deletion or old migration edit occurs. Forward migration is exercised by tests. Reverse migration is only safe before mixed-unit lines exist; afterward use feature-aware code or a reviewed consolidation/archive plan, never remove transaction history to force rollback. Old unaware code must not write after packaging transactions exist.

## Verification

Synthetic PostgreSQL Docker runtime only; no office database, media, environment, containers or checkout modified.

- `python manage.py check`: passed, zero issues.
- `python manage.py makemigrations --check --dry-run`: passed, no changes.
- Initial full suite: 874 passed in 355.166 seconds (before final concurrency/API/review additions).
- Focused packaging + checkout/merge/readiness: 46 passed in 32.654 seconds.
- Expanded sale/API/review/concurrency suite: 11 passed in 4.797 seconds; final copy regression recorded in PR evidence.
- Actual Edge browser: 1 passed in 13.890 seconds; explicit package/piece entry, stock-neutral save, 390px mobile layout, no JavaScript errors.
- Subsequent full suite: 877 passed in 263.249 seconds. The final quotation/draft-issuance regression then passed with 12 focused tests in 6.649 seconds. CI on the final commit must pass before this phase is marked ready. Exact tested commit and final CI results are recorded in the stacked PR.

## Remaining phase boundary

Phase 4 owns physical returns, container wastage, reconciliation/reporting, operational configuration/activation, maintenance retention and rollout runbook. Activation is deliberately unavailable until those workflows pass. Monetary credits alone never return physical stock and cash-refund accounting remains outside this feature. No automatic merge or deployment.
