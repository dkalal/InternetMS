# Phase 4: physical corrections, reconciliation and rollout readiness

Branch: feature/packaging-phase4. Base: Phase 3 39fee66; dependency PR #8. No merge or production deployment.

## Delivered behavior

ContainerCorrectionService owns explicit physical returns and wastage under canonical product/cost/stock permissions, active membership and audited support-session rules. It locks product/balance first and serializes correction provenance. Tenant retry keys conflict on changed payloads. Returned stock creates a distinct returned_piece/returned_full container linked to the immutable sold allocation; it never refills or reseals the old length. Cumulative original quantity and proportional sold-cost limits prevent over-return, and the final return receives its cent residual. Returns reweight average cost without modifying any monetary receipt, credit or refund.

Wastage/damage/loss removes a measured base amount from one physical piece and the aggregate, preserves opened/returned provenance, and appends StockAdjustment, StockMovement, correction event and audit atomically. Duplicate retries post once, concurrent over-return has one winner, and failure rolls everything back. Existing catalog integer capacity is checked before stock mirror writes. Historical correction/posted allocation records cannot be edited or deleted through ordinary models/UI.

The existing product screen links to permission-protected optional configuration. Independent package price changes retain package identity and advance configuration version; factor/unit changes create revisions. Explicit counted activation preserves existing base quantity/value. Legacy positive stock without an aggregate requires the explicit audited preparation action before mapping. Products still default disabled; no migration activates them. Phase 4 completes the code capability while retaining the regression that an environment setting cannot bypass a deliberately closed capability.

Sales/gross-profit reports use base quantities and preserve commercial quantity/unit separately when packaging lines exist. Cost-protected container reconciliation and physical correction reports export the same canonical values to XLSX. Packaging catalog export includes factor/version/independent selling prices behind existing export/cost permissions. No acquisition costs enter ordinary sales/container pages. The read-only audit_product_packaging command checks base/container/catalog quantities, owner relationships, posted allocation cost/quantity and cumulative returns for one explicit tenant. The existing unit audit recognizes complete commercial snapshots rather than treating valid box units as base-unit drift.

Commercial purchase v1 is a separate explicit workbook format for existing activated products, with no quick creation or client factor/cost normalization. It validates tenant/version/precision/expiry/size, uses idempotent draft creation, and preserves stock-neutral draft semantics. Legacy templates retain same-unit behavior and reject opted-in receiving/opening paths with guidance. Cancellation/expiry/abandonment removes stock-neutral selections; converted and posted history remain protected. Cleanup dependency discovery remains fail-closed and its allow-list is unchanged.

## Schema and compatibility

inventory.0023_container_physical_corrections adds only ContainerStockEvent, with tenant/request uniqueness, finite bounded whole quantities and costs, source/kind checks, protected product/container/allocation/movement links and append-only model guards. Existing records are not rewritten. No applied migration was edited. Old unaware code must not write once commercial data exists; populated migration reversal is not a rollback plan.

Detailed operator requirements, actual backup/restore evidence, staging acceptance and feature-aware rollback: [rollout runbook](product_packaging_rollout.md). These operational production actions were not executed in this task.

## Direct billing continuation

Direct invoice and quotation line forms now accept tenant/product/version-bound selling-unit selections. The existing authoritative billing service resolves packaging snapshots and independent prices; client unit text cannot override the selected unit snapshot. The browser loads bounded product-specific options from a permission-protected tenant-scoped endpoint. Empty, forged, expired and stale choices fail validation, preserving submitted form values. Stock remains unchanged until the existing reviewed full-payment workflow succeeds.

Continuation validation: 913 full PostgreSQL tests passed in 297.895 seconds with four workers; three direct-unit/form regressions passed; the new actual Edge selector/mobile browser gate passed in 4.579 seconds. Migration drift reports no changes. The browser test initially needed corrections for the existing Items tab and fixture-specific unit label; no production assertion was removed. Duplicate encoded physical-piece payload fields now fail closed; all four transport tests passed.

Direct billing browser acceptance now covers save, quotation-to-invoice conversion, physical review and issuance. It verifies 2 Box / 610 base units, independent package price 900, two reviewed containers and unchanged stock before payment. Automatic commercial prices/totals explicitly say Confirmed on save instead of displaying base-unit price estimates; saved documents use authoritative tier/discount/tax calculations. Final source verification is recorded below. Exact-commit CI remains a delivery gate; production release is a separate approval.

## Verification evidence

- Phase 3 exact commit CI passed: 878 full tests, 100 focused, 6 browser tests; runs 42056461114 and 42056442857. PR #8 is ready for review.
- Phase 4 focused sales/correction/configuration/security/print tests: 34 passed in 16.088 seconds before the final cleanup dependency regression.
- Real Edge browser: 2 passed in 9.125 seconds, including unpatched explicit activation, dynamic counted rows, returned-piece wastage, mobile layout and no JavaScript errors.
- check: zero issues; migration drift: no changes.
- A development full run correctly rejected a stale maintenance plan while source was being edited. No test or protection was weakened; final maintenance/full gates run with source and HEAD frozen.
- Exact final full/maintenance results and tested commit are recorded in the PR; this phase is not marked ready until those gates pass.

- Continuation verification: isolated PostgreSQL full suite 908 passed in 780.271 seconds before adding the acceptance test; Django check and migration drift passed. Eight Edge browser smoke tests passed in 28.436 seconds. The added ten-box sales and separate physical-return acceptance test passed in 1.199 seconds on synthetic PostgreSQL. The full suite must be repeated after remaining source changes.

## Final local verification ? 2026-11-26

- Configured Python 3.13 / synthetic PostgreSQL 16 Compose runtime, separate project and tmpfs database; no office environment or volumes inherited.
- `python manage.py check`: zero issues.
- `python manage.py makemigrations --check --dry-run`: no changes.
- `python manage.py test --keepdb --noinput --parallel 4`: 913 passed, 287.406 seconds.
- Full actual Edge browser suite (workspace, checkout, receiving, sales, corrections, administration, direct invoice and direct quotation): 10 passed, 37.430 seconds. Direct invoice and quotation also passed individually in 3.943 and 4.546 seconds.
- Purchasing JavaScript regressions: 9 passed.
- `git diff --check`: passed. Changed-file credential-pattern review: 50 files, zero matches for the private-key/live-token patterns inspected; this is not a claim of an exhaustive secret scan.
- Accumulated feature review covered owning-service transactions/locks, tenant and permission checks, stock-neutral drafts/selections, immutable provenance, separate returned pieces, cost-protected reporting, unsupported legacy import rejection and cleanup dependency blocking.
- Exact implementation commit and remote CI results are recorded in the stacked Phase 4 PR. This document's earlier runs remain historical evidence, not substitutes for the final result.

Limitations: commercial price previews are confirmed on save; physical cutting/dispatch follows successful full payment; returned full containers are never resealed automatically; monetary refund accounting, QR/phone scanning and production deployment remain outside this feature. All production backup/restore and rollout evidence remains operator-controlled under the rollout runbook.
