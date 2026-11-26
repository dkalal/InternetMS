# Optional product packaging and divisible containers

Status: Phase 0 architecture; no active packaging workflow is enabled by this
document. Authorized on 2026-11-05. Baseline: main
`81ec3f8ae924acd11bf6f335a628c2fc94add906`.

The approved feature replaces the blanket package-conversion deferral only for
explicitly activated products using the new services and snapshots described
here. Existing products and preservation-only package columns keep their current
behavior. No historical conversion or automatic stock multiplication is allowed.
This contract is subordinate to tenant isolation, permissions, immutable history,
atomic posting and strict greater-than-cost rules.

## Units, scope and ownership

Each product retains one base inventory unit (`Product.sales_unit`). That unit
remains immutable after history. `UnitOfMeasure` is a tenant-owned label, never a
global conversion registry. A packaging revision belongs to one tenant/product,
references an active tenant unit, and captures a positive factor in base units
per commercial package. Products owns configuration and independent tier prices;
inventory owns receiving, physical containers, allocation and stock movements;
billing owns commercial snapshots and financial documents.

Initial enabled mode supports physical, stock-tracked, non-serialized products
with whole base quantities and whole packages. Reject services, serialized
products, fractional factors, fractions of packages and dimensional conversions.
Legacy products retain existing decimal behavior. Expiry tracking, where enabled,
is inherited by containers from their receipt; expired containers are ineligible.
No phone scanning, new SKU system, refilling or transformations are included.

| Value | Meaning and precision |
| --- | --- |
| Entered quantity | Customer/supplier commercial units; positive whole number for enabled products |
| Factor | Base units per selected package; positive whole value represented at six decimals |
| Base quantity | Entered quantity times snapshotted factor; factor 1 for base-unit entry |
| Balance, movement, container remainder, allocation | Base quantity, six decimals; whole for enabled mode |
| Purchase entered unit cost | Currency per entered unit, six decimals |
| Normalized acquisition cost / average cost | Currency per base unit, six decimals |
| Selling price and discounts | Currency per commercial unit / line / document as existing billing semantics dictate; two decimals |
| Tier quantity threshold | Base quantity of the same product across the transaction, including mixed-unit lines |
| Purchase line total | Entered quantity times entered unit cost, rounded to currency cents |
| Inventory value | Base balance times weighted average, rounded to currency cents |

Use Decimal, reject non-finite and excessive-precision input rather than silently
round quantities. Arithmetic uses ROUND_HALF_UP at six decimals for cost and two
for money. Reject overflow before writing. Conversion is exact in whole mode.
Keep weighted-average costing; container provenance is not FIFO valuation.

## Schema map

The gated implementation and concrete migration/compatibility notes are tracked
in [Phase 1 foundation](product_packaging_phase1.md).

1. Product adds disabled-by-default packaging configuration and a version.
   ProductPackaging has tenant, product, commercial unit, factor, version,
   active flag and independent Standard/Technician/Wholesale/legacy Retail
   prices. Technician falls back to that package's Standard price; Wholesale
   eligibility retains the category/allow/price/minimum checks. Never fall back
   to meter price multiplied by a factor. Base prices remain on Product.
   Retire and replace used packaging definitions; never edit a used factor/unit.
   Price-only updates retain physical identity, append an audit event and advance
   the configuration version; stored transaction prices never change.
2. StockContainer has tenant, product, UUID internal label, source purchase line
   or activation/return provenance, packaging revision, immutable capacity,
   original base quantity, current remaining base quantity, supplier identifier,
   receipt timestamp and lifecycle. UUID is not SKU or manufacturer serial.
   Tenant/label uniqueness, remaining >= 0 and remaining <= original quantity
   are database constraints. Provenance uses protected foreign keys.
3. New nullable commercial snapshot fields on PurchaseLine, CartLine and
   BillingLineItem record packaging revision, configuration version, factor,
   selected unit label, entered/base quantities and entered/normalized costs or
   prices as appropriate. Existing `quantity`, `unit_price`, `unit_snapshot`
   on sales remain commercial; purchase `quantity`/`unit_cost` remain normalized
   base values for legacy readers. New purchase total uses its new commercial
   snapshot, not the preservation-only authoritative legacy field. Existing rows
   keep NULL extension fields and follow the existing path.
4. Provisional cart/document allocations point to a line/container and base
   quantity. They do not mutate stock. Immutable posted allocations point to the
   stock movement, billing line and container, with quantity and cost snapshots.
   One unique allocation per movement/container; posting totals must equal the
   line's base quantity. PostgreSQL checks cover positive quantities; services
   enforce sums under locks. All related tenant and product IDs must agree.
5. Activation reconciliation captures actor, reason, idempotency key, balance,
   average cost, inventory value and individually counted physical containers.
   Return/wastage records capture actor/reason/idempotency key and protected
   links to original posted allocations and resulting movements/containers.
   Database uniqueness includes tenant for idempotency and human labels.

Explicit tenant managers apply to every new record. Do not rely on model clean
for database integrity: mutation services revalidate all relationships inside the
transaction; database constraints protect local invariants. New tables receive
tenant/product/status and source indexes where used by bounded list/allocation
queries. Admin exposes immutable posted records read-only. Unscoped maintenance
must explicitly recognize and block these dependencies.

## Physical lifecycle

States: sealed, opened, depleted, returned_piece, returned_full, written_off.
Sealed means received as a complete package, original capacity equals remainder,
and never opened/cut/returned/refilled. A full-box sale requires sealed state,
matching packaging revision/factor and no expired/quarantined status. Balance
equal to capacity alone never establishes eligibility. A sealed full container
can move directly to depleted in a whole-box sale.

A partial allocation changes sealed to opened; zero remainder changes it to
depleted. An opened container never becomes sealed again. A physical return
creates a separate returned_piece (or returned_full) container, linked to the
original sold allocation. It does not join lengths or restore the source
container. Returned_full is deliberately unavailable for sealed-box sales;
it can supply base-unit pieces after authorized inspection. Written-off
containers cannot supply sales. All state changes append audit/movement history.

## Posting, provisional selection and concurrency

Current behavior: `BillingService.create_receipt_from_invoice` calls
`InventoryService.complete_invoice_sale` in the same payment transaction.
Inventory invoices require full remaining payment. Preserve this boundary.

Draft carts, quotations and unpaid invoices express intended selections only.
They reserve no stock and never authorize a physical cut or dispatch. UI must
state that cutting/dispatch follows successful paid stock posting. No prepayment
cutting workflow is introduced. Expiry/abandon/cancellation releases provisional
selections, with no stock movement or reservation timeout job. Converted history
retains its selections for audit. If a proposed container becomes unavailable,
payment fails atomically with actionable review; never silently substitute a
different physical piece during payment.

Suggest opened containers first, then eligible sealed containers, ordered by
receipt timestamp and primary key. Returned pieces participate as separate
opened pieces. Review shows each piece and quantity; authorized sales staff can
change selections before payment, subject to current tenant/line access.
Allocate whole boxes from distinct eligible sealed containers only.

Lock parent cart/invoice/sale, then products and balances in ascending product ID,
then containers in ascending ID. Revalidate permission, configuration, net price,
stock and allocation totals after locks. Group repeated product lines when
checking aggregate sufficiency and tiers. Payment, movement, allocation,
container remainder, sale-cost record and audit commit together. Use existing
invoice/cart idempotency plus movement/allocation uniqueness; unchanged receipt
retries return original results before validating later configuration. Concurrent
box/meter sales serialize on product/balance, and the loser gets a stock/allocation
error without payment. Avoid any alternate container-first lock ordering.

## Pricing and snapshots

Retain customer automatic/explicit category resolution and grants. Wholesale
thresholds use aggregate base quantity for a product in the current transaction,
so splitting Box/Meter lines cannot change eligibility. Reprice every affected
draft line together after quantity/category changes. No wholesale tier is granted
solely by quantity without the effective Wholesale category. Catalog defaults
never overwrite posted financial snapshots.

Reuse the existing billing order: category/base price, promotion and line
discount calculation, document-discount allocation and tax calculation. Strict
floor comparison uses final net pre-tax line revenue divided by base quantity
against locked weighted-average cost (fallback buying price only when no positive
movement-backed stock). Equality fails. Do not compare box price against meter
cost or include tax. Permission-protected cost messages remain unchanged.

Sales quantity and price on documents remain commercial: `2 Box` and `50 Meter`;
stock deduction uses 610 + 50 base units. Receipt/PDF uses the source invoice
snapshots, including payment receipts whose own line is a payment summary.
Quotation version/conversion, invoice reissue and serial-copy helpers must copy
new snapshots explicitly. New editable transactions reject retired configuration;
posted historical printing never resolves current packaging. An unpaid issued
invoice with a retired factor fails payment until corrected through the existing
financial workflow; it is never silently recalculated.

## Valuation and final depletion

Receive using total commercial acquisition amount and exact base quantity:
new average = round6((old quantity * old average + incoming total) / new quantity).
Store receipt total and normalized cost independently, so repeating a rounded
per-meter cost does not redefine the supplier payable. Preserve existing
weighted-average valuation bridge and report residuals explicitly.

Stock-out uses the locked current average, not original container purchase cost.
For a line, round aggregate cost once to cents and allocate cost among its
containers deterministically; the last allocation receives the cent residual.
Final depletion sets base quantity to zero; recognized cost must reconcile to
the pre-depletion inventory value, with any rounding residual captured explicitly
in sale-cost/allocation snapshots. Never hide residuals by changing old movements.
Returns restore the original allocated sold cost proportionately, with cumulative
quantity/cost limits and final return receiving the residual. Recalculate weighted
average on physical stock-in. Monetary credit alone never creates stock-in. Current credit notes cannot
refund settled invoices; physical return is independent and does not create a
cash refund or change paid/credited amounts. A cash refund ledger is outside
this feature and must not be simulated with negative receipts.

## Activation, version changes and disabling

Activation requires existing product-edit permission plus stock-adjustment
authority and authenticated tenant membership, an explicit reason and current
configuration version. Block active carts, unpaid product invoices and active
quotations until resolved (do not reinterpret legacy commercial quantities).
Lock product/balance and verify current base unit; existing unit-history rules
still apply. Never infer that an old quantity means packages.

For stocked products require an explicit physical count of individual containers
whose sum equals the current base balance exactly and capture unchanged average
and total value. Mapping creates provenance/container records and audit only, no
stock-in movement and no value change. If measured quantity differs, perform an
independent approved adjustment first, then reconcile. Existing stock needing a
different base unit uses the existing successor-product workflow, never an in-place
unit edit. Zero-stock activation invents no containers. Retry keys must match the
original payload; changed retries conflict.

Retiring a factor creates a new revision. Old physical containers retain original
capacity; matching old sealed boxes remain base-unit saleable but are not sold as
the new box size. Drafts with old configuration require explicit review/reselection.
Disable only with zero base balance, no positive container remainder, and no open
cart/quotation/unpaid invoice using packaging; posted history remains protected.
Reactivation creates a new audited version. No historical rows are removed.

## Repository-wide implementation map

| Surface inspected | Required change / safety gate | Phase |
| --- | --- | --- |
| products/models.py, forms.py, creation.py, admin.py | Opt-in configuration, tenant units, immutable factors, activation; reuse SKU allocator unchanged | 1 |
| Product creation/batch and inventory/purchase_batch.py, purchase_import.py | New products default off; reject unsupported packaging columns explicitly; no invented conversion | 1–2 |
| inventory/models.py PurchaseLine, services.py confirm_purchase, forms.py | Commercial/base snapshots, authoritative totals, receipt containers, row locks and retry | 1–2 |
| purchase workspace/search/paste/import/template and proposals | Unit-aware entry/preview or explicit server rejection until supported; preserve proposal receiving atomicity | 2 |
| InventoryBalance, StockMovement, StockAdjustment, StockUnit | One base balance, separate divisible containers, no serial reuse, adjusted dependency guards | 1–4 |
| products/pricing.py and inventory cart readiness | Normalize net revenue by base quantity; aggregate threshold and cost permission parity | 3 |
| CartLine, CartService, cart_workspace.py, forms/views/API, POS templates/JS | Unique cart/product/packaging identity including base lines; +/- in selected units; allocation review | 3 |
| BillingService LineItemInput/build/store/copy/version/reissue/payment | Carry snapshots through every path; common allocation/posting validation | 3 |
| billing forms/views, direct invoice, quotation, inventory/api.py | Same server-authoritative unit rules; reject unsupported packaging before any write | 3 |
| receipt_print_contract, TRA templates, PDF context | Print immutable commercial quantities and units; protect costs | 3 |
| credit note and inventory adjustments | Separate monetary credits from physical return/wastage; prevent over-return | 4 |
| inventory/reporting.py, views exports, integrations APIs | Base totals and valuation; group sales commercial units, never sum Box + Meter | 4 |
| handover_cleanup.py, cleanup_carts, product deletion/unit audit | Protected dependencies, fail-closed old cleanup, allocation-aware retention; commercial units are not unit drift | 1–4 |
| .github/workflows/django-ci.yml | Already runs all pull_request events and feature pushes; stacked PRs need no filter change | all |
| Deployment PR #4 and docs/deployment_preflight_2026-11-03.md | Separate image-only release proposal; never merge into feature or use office DB/env/volumes | all |

## Migration and rollout contract

Phase 0 introduces no schema. Phase 1 adds nullable extension fields, disabled
configuration defaults and empty new tables. Existing rows keep original amounts,
units and snapshots; no data migration creates containers. Explain each concrete
migration before running it against the isolated PostgreSQL runtime. Test the
old migration state upgraded to new state and confirm historical values unchanged.

Intermediate phases gate activation and sales server-side until required paths
exist. Foundation alone is not production-ready. Old code can read additive
schema only while the feature stays unused; once packaging transactions exist,
old code is unsafe for writes because commercial and base quantities differ.
Rollback then means feature-aware maintenance/read-only code, not returning to
unaware office code or reversing populated migrations. A release must quiesce
writers, verify backup/restore, migrate, switch to feature-aware code and explicitly
activate tested products. Production deployment requires separate approval.

Private rollout evidence must cover real backup restore, isolated migration
rehearsal, base/container/value reconciliation, denied-role/cross-tenant checks,
historical PDFs, synthetic staging demonstration and rollback-read validation.
Do not publish office data, credentials, media or backups in branches or PRs.

## Acceptance examples and phase gates

| Scenario | Expected result |
| --- | --- |
| Receive 10 boxes, factor 305 | 3,050 base units, 10 sealed containers, one stock increase per receipt line |
| Sell 2 full boxes | 2,440 base units, 8 sealed; 2 Box on receipt |
| Sell 50 meters | 2,390 base units, 7 sealed and opened remainder 255 |
| Sell 300 meters as pieces | 255 + 45 allocations; 2,090 base units, 6 sealed and opened remainder 260 |
| Retry completed payment | Original receipt/allocations; no second deduction |
| Foreign tenant container, missing permission, forged factor/cost | Reject and roll back all mutations |
| Return 20 meters of sold cut | New 20-unit returned piece; source remainder unchanged; original cost restored once |
| Demand one full box from 150 + 155 pieces | Reject; two pieces are not one sealed box |
| Concurrent 1-box and 1-meter sale against last sealed box | Exactly one succeeds with its reviewed allocations; no negative stock/payment for loser |
| Receive alternative factor 100 | Separate revision/containers; product base unit unchanged |
| Receive 305 base units without packaging | Explicit one opened loose container; never infer a sealed box |
| Change factor while draft exists | Draft stale review error; posted documents and old container capacity unchanged |
| Activate 610 stocked base units | Explicit counted 305 + 305 mapping; quantity/value unchanged; no movement |
| Activate old quantity 10 expecting 10 boxes | Reject mismatched physical sum; never multiply existing stock |
| Partial failed multi-line receipt | No purchase, container, balance, movement, proposal or SKU side effects |
| Equal net revenue and current base cost after discounts | Reject, even with positive tax |
| Small repeated cuts/final depletion and partial returns | Cent costs reconcile; no residual value lost/duplicated |
| Disable with stock/open documents | Reject with resolution guidance |
| Historical invoice/PDF after factor/price changes | Original selected unit, quantity, price and totals |

Phase checklist (unchecked means not delivered):

- [x] Baseline, worktree, main CI and unrelated deployment PR inspected.
- [x] Architecture, lifecycle, posting, precision and compatibility defined.
- [x] Phase 0 focused/full PostgreSQL verification, commit, push and PR #5.
  Evidence: [Phase 0 verification](product_packaging_phase0_verification.md).
- [x] Phase 1 gated foundation, migrations, activation and isolation tests; [PR #6](https://github.com/dkalal/InternetMS/pull/6) targets Phase 0.
  Evidence: [Phase 1 verification](product_packaging_phase1.md).
- [x] Phase 2 receiving/UI/import gates and PostgreSQL retry tests; [PR #7](https://github.com/dkalal/InternetMS/pull/7) targets Phase 1. Final local full suite: 866 passed; both CI runs passed.
- [ ] Phase 3 all sales paths, concurrency, historical print and browser tests; PR targets Phase 2.
- [ ] Phase 4 returns/wastage/reports/maintenance and rollout runbook; PR targets Phase 3.

Every phase records exact tested SHA, commands/results, schema status, dependency
PR and limitations. Required gates: Django check; makemigrations --check --dry-run;
focused tests --keepdb; full tests --keepdb; git diff --check; secret/private-data
review. Stock mutation phases add PostgreSQL concurrency tests. UI phases add
actual browser checks. No automatic merges or production operations.
