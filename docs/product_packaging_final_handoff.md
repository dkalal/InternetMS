# Packaging implementation handoff - 2026-11-26

Phases 0-4 are implemented and verified in isolated synthetic PostgreSQL.
The stacked PRs are ready for review. No merge, office-checkout transition,
production migration, media change or deployment has been performed.

| Phase | Branch | Source/delivery commit | PR base | PR |
| --- | --- | --- | --- | --- |
| 0 | feature/packaging-phase0 | 1dec966 | main | [#5](https://github.com/dkalal/InternetMS/pull/5) |
| 1 | feature/packaging-phase1 | c3baa76 | feature/packaging-phase0 | [#6](https://github.com/dkalal/InternetMS/pull/6) |
| 2 | feature/packaging-phase2 | a458dc7 | feature/packaging-phase1 | [#7](https://github.com/dkalal/InternetMS/pull/7) |
| 3 | feature/packaging-phase3 | 39fee66 | feature/packaging-phase2 | [#8](https://github.com/dkalal/InternetMS/pull/8) |
| 4 | feature/packaging-phase4 | 5c698f5 | feature/packaging-phase3 | [#9](https://github.com/dkalal/InternetMS/pull/9) |

## Verification

Final implementation SHA: `5c698f595b15ae364a69ea304327e71bcd2cc9fe`.
[PR CI](https://github.com/dkalal/InternetMS/actions/runs/45785535320) and
[push CI](https://github.com/dkalal/InternetMS/actions/runs/45785479077)
both passed. PR CI: 100 focused, 913 full-suite and 10 Chromium browser tests;
Django check, migration drift, fresh PostgreSQL migrations, static collection,
whitespace and nine JavaScript regressions passed. Local Python 3.13/PostgreSQL
full suite: 913 passed in 287.406s; local Edge browser suite: 10 passed in 37.430s.
Source and verification details remain in each phase document. This closure
changes documentation only; it does not replace exact-source CI evidence.

## Acceptance demonstration

Starting empty, receive 10 packages with factor 305: 3,050 base units and ten
sealed containers. Sell two packages: 2,440 and eight sealed. Sell 50 base units:
2,390, seven sealed and one opened remainder of 255. Sell 300 as 255 + 45:
2,090, six sealed and an opened remainder of 260. Retrying payment returns the
original receipt without another deduction. A physical 20-unit return creates a
new separate returned piece and increases base stock to 2,110; the depleted
source length is unchanged. Tenant/permission denial, concurrent stock mutation,
insufficient/stale stock, historical snapshots and rounding are tested.

Direct invoice and quotation browser acceptance includes save, conversion,
reviewed physical selections and issuance. The saved line is 2 Box, base quantity
610 and independent price 900 per Box; stock stays 3,050 before payment.

## Compatibility and limitations

Migrations are additive: disabled product defaults, nullable historical snapshots,
container/allocation provenance, deliberate mixed-unit cart uniqueness and the
Phase 4 `inventory.0023` append-only correction table. Historical quantities,
amounts and documents are preserved; migrations infer no containers and multiply
no stock. Old unaware code is unsafe for writes after commercial transactions
exist. Populated migration reversal is not a supported rollback.

Automatic commercial prices/totals are confirmed on save. Cutting or dispatch
follows successful full-payment stock posting; provisional selections reserve
nothing. Returned full packages are not automatically resealed. Monetary refunds,
QR/phone scanning and a new product-code system are outside this feature.

## Remaining release actions

These are separate review/operator actions, not unfinished implementation:

1. Review the PRs and explicitly authorize merges in dependency order. Preserve
   protected-main checks; retarget/update subsequent branches after each merge
   and verify their new heads without force-pushing. Deployment PR #4 is separate.
2. Follow [the rollout runbook](product_packaging_rollout.md): private encrypted
   backup plus verified restore, isolated rehearsal against the actual release
   schema, historical-document checks, read-only tenant preflight and staging
   acceptance. Synthetic tests do not substitute for real restore evidence.
3. Obtain separate explicit production deployment approval. Quiesce all writers,
   apply the reviewed feature-aware release and explicitly reconcile/activate
   selected products. Keep office database, media and credentials private.

Do not perform these actions merely because implementation CI is green.
