# Packaging Phase 2: unit-aware receiving

Branch: `feature/packaging-phase2`, stacked on Phase 1
`c3baa76de781edec67cc211fd46181084291bc8f`. No new migration.

The existing purchase workspace accepts an explicit base unit or product packaging
for activated products. Quantity and acquisition cost are entered in that selected
commercial unit. The form/service computes exact base quantity, six-decimal base
cost and the cent-rounded supplier total from server-owned factors. Changing units
clears the cost input so a base-unit default is never silently treated as box cost.
Bound values survive validation errors. Ordinary product decimal behavior remains.

PurchaseLine uses its new commercial total only when that complete new snapshot
exists; preservation-only legacy totals keep their original path. Draft persistence
now belongs to `inventory.purchase_drafts`, rechecks permission/ownership, locks the
parent and validates snapshots against locked current product versions. Line saves
also lock their actual parent, closing cached-draft writes after confirmation.

Receiving locks the purchase and all products in order, checks current tenant,
supplier, permission, complete snapshots, configuration version, exact normalized
cost, expiry/serial rules and existing container/balance reconciliation. It creates
one movement per line, updates the existing weighted average from the commercial
acquisition amount and creates one sealed container per full package, or one
opened loose container for base-unit entry. All writes share the existing atomic
receipt; a failed line or container write rolls everything back. Authorized retries
return the confirmed result without more stock or physical containers. Operations
are bounded to 2,000 containers per purchase; nothing is inferred from old stock.

The protected search returns bounded packaging choices without new cost disclosure.
Purchase review shows entered units/costs separately from normalized stock/costs.
Confirmed review lists internal container UUIDs, remaining base quantities and
states; native browser Print can print that list. These IDs are not SKUs or serials.
Spreadsheet/paste preview and save explicitly reject activated packaging products
until a unit-aware import format exists; ordinary imports/proposals keep working.

Activation remains hard-gated off during this intermediate phase. Tests patch the
capability only for synthetic fixtures; no production product has been enabled.
The sales/allocation and returns/reporting phases are still required before use.

Verification is recorded with the exact source SHA in the Phase 2 PR. Initial
focused receipt/purchase/grid run: 29 tests OK. Broader workspace/search/import
regressions: 67 tests OK. Final focused receiving/security/workspace/import gate:
76 tests OK (27.741s). Django check and migration drift passed. Full suite: 865 tests OK (239.410s). The actual desktop/mobile Edge browser
unit-selection/receipt flow passed against a separate loopback-only synthetic
PostgreSQL database, using the existing Python installation read-only with bytecode
writes disabled. Browser screenshots remain in ignored test artifacts.
No browser result is inferred from template tests. CI includes the new browser
module beside existing checkout/workspace gates. No test was weakened.

Synthetic demonstration covers 10 x 305 = 3,050 base units and 10 sealed containers,
multiple sizes/costs, base-unit loose stock, retries, concurrent confirmation,
permissions, cross-tenant requests, stale configuration, cost tampering, atomic
rollback, cached-draft immutability and count/balance mismatch. Complete sale/return
acceptance remains Phase 3/4. No office checkout/database/media/container or deployment
was changed. No merge or production migration is part of this phase.

The real browser gate exposed a pre-existing no-serial submit reentrancy bug:
an immediately resolved serial check called requestSubmit while the native submit
was still dispatching. Resubmission now runs on the next task and preserves the
chosen action. Nine Node regressions pass, including a behavioral regression that
fails under the old timing. The full browser flow then saved and received 10 boxes
correctly. An initial browser assertion expected decimal padding; the supported
whole-quantity display is 1. Diagnostic payload logging was removed before commit.
The final source SHA and CI outcomes are recorded on the phase PR.

Final receiving/isolation gate: 19 tests OK (4.995s). Final real Edge desktop/mobile
receipt flow: 1 test OK (4.108s). Synthetic screenshots were visually reviewed.
The JavaScript regression was verified against the prior source (expected failure)
and the corrected source (9 tests passed). No diagnostic POST payloads are logged
by the committed browser test.
