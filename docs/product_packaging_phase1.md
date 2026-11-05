# Product packaging Phase 1: gated data foundation

Base: `feature/packaging-phase0` at `1dec966d34f3ca54a078687b89f0dfe5fab3414b`.
Dependency: [Phase 0 PR #5](https://github.com/dkalal/InternetMS/pull/5).
Implementation branch: `feature/packaging-phase1`.
Status: implementation and verification in progress; no activation release.

## Implemented boundary

Products now have disabled-by-default, non-form-editable `packaging_enabled` and
`packaging_version`. ProductPackaging belongs to a tenant and product, uses an
existing tenant unit, and owns an exact positive whole factor and independent
Standard/Technician/Wholesale/legacy Retail prices. Definition identity/factor is
immutable. Audited price changes preserve that identity, keeping existing sealed
containers eligible for their original packaging; posted financial prices remain
on transaction snapshots. Retiring a definition preserves it and advances the
product configuration version. Whole-mode quantity validation leaves ordinary
products' decimal quantities unchanged.

`products.packaging.PackagingService` owns configuration, retirement and repricing.
It requires existing product-edit and stock-cost-view permissions. Activation also
requires existing stock-adjustment authority. Actor, membership, product and units
are checked server-side. Platform support requires an active persisted audited
support session, including when the platform membership belongs to another tenant.
No new role or permission grant is introduced. Errors do not reveal acquisition
costs to unauthorized staff.

`inventory.packaging.PackagingActivationService.change` requires a UUID retry key,
configuration version, reason, expected base quantity/average cost and individually
counted containers. It locks the organization (no-key lock), product and balance;
checks open carts/quotations/unpaid invoices and existing physical records; and
requires the count to equal the existing base stock exactly. Activation creates
immutable reconciliation provenance and physical containers, never stock-in or
value changes. Zero stock invents no physical containers. Positive legacy stock without an
existing aggregate balance must first complete the approved audited aggregate
preparation; activation never guesses or silently initializes its ledger. Counted expiry dates
are required for expiry-tracked products. Disable requires zero base stock,
no positive container remainder and no open sales documents. Changed retries,
stale stock/value/configuration and cross-tenant relationships fail atomically.

The production capability gate is `products.packaging.workflows_ready()`, currently
hard-coded False. It has no environment toggle. Tests temporarily patch it to
exercise the complete reconciliation transaction; ordinary callers cannot activate
packaging during this foundation phase. Existing stock mutation, receipt posting,
cart and direct billing paths reject activated products or commercial snapshots
until their owning workflow is implemented. There are no partial HTML/API controls.

## Schema, snapshots and preservation

New inventory models are PackagingActivation, StockContainer, ContainerAllocation
and ProvisionalContainerAllocation. They have explicit tenant ownership, protected
provenance, uniqueness and bounded quantity/cost checks. A container is not a
serialized StockUnit. Its UUID label is an internal identity, not a SKU or serial.
Sealed eligibility requires untouched capacity and original contents; model guards
prevent resealing, changing provenance or refilling an existing continuous length.
Returned pieces have a separate original sold-allocation relationship.

PurchaseLine adds nullable packaging/version/factor, entered quantity/cost,
commercial total and selected-unit snapshots. Its existing quantity/unit_cost stay
in base units. CartLine and BillingLineItem add nullable packaging/version/factor
and base quantity; commercial quantity/unit price remain in existing fields.
CartLine also captures the selected commercial unit label. Snapshot checks require
complete bundles and exact whole conversion. Existing NULL bundles retain the old
path and legacy package fields are never read as active conversion configuration.
Shared validation lives in the product unit domain; transaction records remain in
the owning billing/inventory apps.

Migrations are additive. The two new non-null Product fields retain database
defaults as well as Python defaults, so previous code can insert ordinary disabled
products after the complete foundation upgrade. A historical-model write test
verifies this compatibility. Active products additionally have a database check
requiring whole base quantity and a matching legacy stock mirror.

No data migration infers boxes, multiplies stock or rewrites
history. Separate constraint-refinement migrations preserve the already-rehearsed
migration sequence. Old rows retain their amounts and NULL extension fields. The
migration rehearsal compares every original concrete field on synthetic products,
purchases, purchase lines, balances, movements, invoices, receipts and billing lines
before/after upgrade, unused-foundation rollback and upgrade again.

No new models were added to the offline cleanup allow-list. Its existing incoming
relationship discovery blocks configured products and linked documents instead of
cascading through new provenance. Posted provenance has model write/delete guards;
ordinary code has no admin or external write route for those records. Aggregate
allocation sums and cross-row concurrency remain owning-service responsibilities
for later posting phases, not claims about local CHECK constraints.

## Reproducible isolated verification

`compose.packaging-test.yml` is standalone synthetic verification configuration.
Never combine it with office Compose. It uses PostgreSQL 16 tmpfs storage, no host
ports, no office env_file or volumes, explicit disposable credentials, an internal
project network and the repository Python/Django runtime. Source is mounted from
this isolated worktree; generated static files stay in an anonymous container
volume. The office checkout, database, media, containers and deployment are unused.

Build/start only this isolated test project first:

```powershell
docker compose -f compose.packaging-test.yml -p jims-packaging-phase1 build web
docker compose -f compose.packaging-test.yml -p jims-packaging-phase1 up -d --wait db
```

Required command prefix:

```powershell
docker compose -f compose.packaging-test.yml -p jims-packaging-phase1 run --rm --no-deps web
```

Final verification results and migration manifest will be recorded after all gates
pass. A failed diagnostic run is not a successful gate. No test is skipped,
weakened or changed to hide a defect. Browser validation belongs to later UI phases;
this phase exposes no new rendered controls. CI retains its existing browser smoke
gate for legacy workspace/checkout behavior.

## Rollback and remaining phases

Pause writers while applying the complete migration sequence. Before use,
nullable extensions, retained database defaults and empty new tables preserve
historical reads and ordinary old-code product inserts.
Only an unused foundation may be rolled back after checking that no new tables or
snapshots contain business data; do not drop populated provenance or reverse
migrations after activation. Once packaging transactions exist, unaware code is
unsafe for stock writes even if it can read the additive schema. Retain a
feature-aware maintenance/read-only build and follow the Phase 0 rollout contract.

Phase 2 still owns receiving and purchase controls/import rejection; Phase 3 owns
all sales selection, allocation, repricing, mixed-unit cart uniqueness, snapshot
copying and historical print paths; Phase 4 owns physical returns, wastage,
report/export normalization and final rollout preflight. The ten-box end-to-end
acceptance scenario has not been executed as a released feature. This foundation
is not production-ready and no production action is authorized by its tests.

## Concrete additive migration sequence

- Products: `0019_packaging_foundation`, `0020_packaging_finite_bounds`,
  `0021_packaging_write_compatibility`.
- Billing: `0034_packaging_foundation`, `0035_packaging_foundation`,
  `0036_packaging_finite_bounds`, `0037_packaging_write_compatibility`.
- Inventory: `0017_packaging_foundation`, `0018_packaging_foundation`,
  `0019_packaging_snapshot_completeness`, `0020_packaging_finite_bounds`,
  `0021_packaging_write_compatibility`.

The initial model migrations were rehearsed before later integrity refinements.
Their files were preserved and follow-up changes use new migrations; no applied
migration was rewritten or squashed. The split foundation migrations are Django's
cross-app dependency ordering. Only synthetic PostgreSQL databases have received
this sequence. Production migration remains a separate approved release action.
