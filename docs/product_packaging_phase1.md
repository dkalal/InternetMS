# Product packaging Phase 1: gated data foundation

Base: `feature/packaging-phase0` at `1dec966d34f3ca54a078687b89f0dfe5fab3414b`.
Dependency: [Phase 0 PR #5](https://github.com/dkalal/InternetMS/pull/5).
Implementation branch: `feature/packaging-phase1`.
Status: Phase 1 foundation verified and delivered; activation remains closed.
PR: [Phase 1 #6](https://github.com/dkalal/InternetMS/pull/6).
Tested implementation SHA: `ce2667f6dd1d11a7799b3e9c7a24e53cf6b49653`.

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

Final local results on the tested implementation SHA:

- Synthetic image build and complete fresh migration path: exit 0.
- `python manage.py check`: exit 0; no issues (0 silenced).
- `python manage.py makemigrations --check --dry-run`: exit 0; no changes.
- `python manage.py test inventory.tests_packaging_foundation
  inventory.test_packaging_migrations --keepdb --noinput`: exit 0;
  **49 tests OK, 112.698 seconds**. Includes actual PostgreSQL configuration and
  activation-retry concurrency, original-field preservation, old-writer inserts,
  rollback rehearsal, stale state, role/tenant isolation, stock/value neutrality,
  finite-number checks and protected history.
- `python manage.py test --keepdb --noinput --parallel 4`: exit 0;
  **847 tests OK, 262.043 seconds**. This run used fresh database/clones derived
  from synthetic base `jims_packaging_release`, through the same Compose prefix
  with an explicit process-local synthetic DATABASE_URL override.
- `git diff --check feature/packaging-phase0...HEAD`: exit 0.
- Secret/private-data review: only source, tests, additive migrations, disposable
  synthetic configuration and documentation; no office credentials/data/media or
  diagnostic logs were committed. The project-specific logs are Git-ignored.

Earlier diagnostics are retained locally: the initial foundation fixtures needed
role-name/Decimal corrections; a NaN test initially hit Django validation before
PostgreSQL, then was strengthened to use direct parameterized SQL and assert the
actual CHECK names. An earlier parallel full run failed while serializing an
exception without tblib. Its underlying cause was not established; subsequent
serial diagnostic (843 tests, 674.509s) and the final fresh parallel run above
passed. An attempted diagnostic stop found the serial container already finished;
no process was stopped. No assertions, tests or framework safety checks were
weakened, skipped or xfailed.

Browser validation for new packaging UI belongs to later UI phases; this phase
exposes no new rendered controls. CI retains its existing browser smoke gate for
legacy workspace/checkout behavior. CI status is recorded separately below.

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

## CI evidence

Both CI runs on tested source `ce2667f6dd1d11a7799b3e9c7a24e53cf6b49653`
completed successfully, including whitespace, static collection, Django checks,
migration drift and fresh PostgreSQL migrations. The push log records:

- Focused inventory/receipt regressions: **100 tests OK, 74.846 seconds**.
- Full suite: **847 tests OK, 400.076 seconds**.
- Browser workspace/checkout smoke: **4 tests OK, 14.041 seconds**.

These browser checks cover the existing workspace and checkout; they are not a
claim that future packaging receiving/sales UI has been implemented or tested.

- [Push CI](https://github.com/dkalal/InternetMS/actions/runs/42008410488)
- [Stacked PR CI](https://github.com/dkalal/InternetMS/actions/runs/42008630004)

The final documentation update does not alter application or test source. No merge,
release, production migration or production container operation has been performed.
