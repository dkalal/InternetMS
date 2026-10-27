# Tenant handover cleanup (2026-10-25)

**Completed live:** [execution and verification result](handover_live_result_2026-10-25.md).
Owner-approved fingerprint `02d07ac5be9a…` was applied at 19:28:44 EAT:
565 selected rows deleted, products **33 → 0**, success audit **1464**.
All retained rows, other tenants, old audits, non-audit sequences and media were
verified unchanged. Web/db are healthy. Do not replay the consumed plan.
The pre-approval status and instructions below are historical.

**Current operational status:** [completed live preflight](handover_live_readiness_2026-10-25.md).
Full tests are green, a protected PostgreSQL-16/media backup is verified, and an
actual isolated restored-copy cleanup has passed every retained-row and sequence
check. The fresh live-bound plan awaits explicit execution approval; no live
cleanup has been run. Earlier stage descriptions below are historical context.

**Current v2 entrypoint:** [final js-tech plan and gates](handover_js_tech_final_plan_2026-10-25.md)
and [the classified maintenance contract](handover_classified_cleanup_contract_v2.md).
The user has since classified all 44 exceptional billing roots as sample; signed
v2 combined and separate dry-run plans have been prepared. This does not authorize
live apply. The original default `inventory`/`customers` descriptions below remain
valid for those operations; they do not describe the distinct classified modes.
Earlier v1/synthetic plans are historical evidence, not current executable plans.

Implementation and disposable-database testing only. No live deletion, deployment,
push or merge is authorized by this change. Commands below are an operator runbook,
not a record of operations already performed.

## Checkout and schema evidence

Branch: `feature/cart-lifecycle-management`; base commit:
`36bfa1fbe37df77dd7b3006a81b4bc233df360b3`. Pre-existing tracked reporting,
inventory service/view/test/template and architecture/contract edits were preserved.
Pre-existing untracked files were preserved. The active IDE billing print fragment
does not need modification for this workflow.

Compose services are `web` and `db`, PostgreSQL 16. Read-only inspection identified
database `js_internetservices`, host `db`, port `5432`. The running image is not
assumed to match this checkout; verification mounts current source read-only into
a disposable `web` process, without restarting the deployed web service.

[Machine-readable schema map](handover_schema_2026-10-25.json) records every model,
including auto-created M2M tables, all FK/O2O targets, nullability, deletion policies,
tenant columns and the complete observed applied-migration list. Observed domain
tips include products 0017, inventory 0016, billing 0033, customers 0023,
custom_fields 0003, integrations 0005, users 0013 and work_reports 0006.
No migration is introduced. No applied migration is edited.

| Aggregate | Dependencies / preservation boundary |
| --- | --- |
| Product | PurchaseLine, InventoryBalance, StockAdjustment, StockMovement, StockUnit, CartLine and BillingLineItem protect catalog references. Historical/import SKU references are scalar and require separate review. |
| Categories / units | Product catalog_category and sales_unit, plus category default_unit, protect masters. Masters remain unless exact IDs are approved. |
| Supplier | Purchase and SupplierPaymentRecord protect supplier; preserve by default. |
| Purchase | PurchaseLine and PurchaseProductProposal cascade; PurchaseImportSubmission protects. Lines are protected by movement/unit references. PurchaseReferenceSequence is never selected. |
| Cart | Lines and their serial selections cascade. InventorySale protects cart. Quotation/invoice forward links must be completely approved. Never follow a cart customer link for deletion. |
| InventorySale | InventorySaleLine protects sale and billing line. Invoice/receipt/cart are protected links. |
| Serialized stock | DocumentSerialSelection and CartSerialSelection protect StockUnit; unit references purchase/billing lines. All exact IDs must be approved together. |
| BillingDocument | Items cascade; receipts, credit/correction/version/source/conversion chains protect and may form self-table cycles. SubscriptionPeriod, BillingSheet/BillingItem and service/customer links are protected. Promotion may SET_NULL on retained rows, so incoming references block deletion. |
| Customer | Sites, profiles, documents, external assigned assets and M2M rows can cascade; billing/subscription/messaging references protect. This maintenance policy blocks all such dependencies instead of cascading. |
| Custom fields | Values have scalar target_model/object_id references; exact product/supplier/customer values are included automatically only with validated matching ownership. Definitions are preserved. |
| Audit / messaging | Audit scalar object identifiers are preserved. Customer audit history blocks customer deletion under existing policy. Messaging document scalar references block billing cleanup; customer FK references also block. |
| Imports / historical data | ImportJob JSON rows use SKU/company-name snapshots; HistoricalInventoryRecord protects ImportJob. Explicitly approve linked jobs/history and products; retained scalar endpoints block partial cleanup. |

`Product.customer` is absent: products migration 0015 removed it. Assigned customer
assets are represented by `integrations.ExternalAssetReference`, whose customer FK
cascades; the cleanup planner treats it as a blocker. Current allowed child models
all have tenant columns. The relationship/ownership verifier also checks models
without tenant columns through their parents (notably implicit M2M tables) and
rejects ambiguous ownership. New unsupported deletion paths fail closed.

Existing Product post_delete deletes media after commit; CartLine/serial-selection
post_delete updates parent activity. The private maintenance executor deliberately
issues exact tenant-constrained SQL deletes without Django collectors/signals,
preserving all media and avoiding incidental parent mutation. This is the sole
explicit maintenance exception for approved sample financial/stock history.
Normal model/service immutability and customer hard-delete policy are unchanged.
Database constraints remain enabled; unreviewed database triggers or mutating
database-level FK actions block planning. Same-table billing cycles are deleted
in one statement; cross-table cycles fail and roll back rather than detach links.

No background worker or queue service is configured in Compose. Import commits
run synchronously. Existing cart cleanup may be installed externally as a host
timer; discover and stop those external writers before proceeding.

## Policy and reviewable plan

Use `inventory.handover_cleanup` via `manage.py handover_cleanup`. There is no HTTP
reset route. Planning requires an active Super Administrator TenantMembership
(matching `users.views._require_super_admin`), an active actor, active tenant and
an existing reasoned SupportAccessSession for that actor and tenant. It does not
create memberships or support sessions. Use the established audited support-access
UI to open the support context; record the session ID without exposing its key.
Customer plans additionally require the existing `is_superuser` hard-delete rule.

An operator prepares a JSON object with only allowlisted model keys and positive
integer IDs. The full allowlist is `INVENTORY` in the service. Example shape only:

```json
{
  "products.product": [123],
  "inventory.inventorybalance": [456],
  "inventory.stockmovement": [789]
}
```

These numbers are placeholders, **not approved sample IDs**. Do not copy them into
an operational manifest. Names, dates, creator, customer type and missing
subscriptions never classify records. Every dependent row other than exact-target
custom values requires explicit approval; the planner reports its model, ID and
reason instead of expanding scope. Keep audit/custom definitions, users/settings,
masters and counters by default. An empty manifest is rejected.

Inventory billing deletion is deliberately limited to explicit walk-in POS chains.
Registered-customer/site billing, subscriptions, packages, service lines, mixed
invoices and retained financial chains block. Receipt/credit summary items are
allowed only with their explicitly selected parent invoice and matching customer.
A separate `--operation customers` manifest can contain only
`{"customers.customer": [ID]}`. Only a bare explicitly approved customer can pass;
walk-in placeholders, any dependent record, assigned asset, M2M membership or
existing Customer audit record blocks. Profiles, documents and subscription
dependencies are not erased by a privileged customer override.

The signed JSON plan includes version, actor/support/tenant, time, commit/source
hash, non-secret database identity, complete migration state, exact roots and
affected IDs, content hashes, FK relationships, retained endpoint hashes, counts,
blockers, preservation categories, media policy and planned success-audit append.
It contains no customer names, raw field contents or credentials. Protect plan
files nonetheless. The SHA256 fingerprint covers the entire payload; a Django
signature prevents changing IDs and recomputing the fingerprint. Apply regenerates
the whole plan under database write locks and compares it exactly. Plans expire
after two hours and cannot change tenant, actor or support session. Build/policy,
record or dependency changes require a new plan and review. Applied plans cannot
be reused to select later imports or newly created rows.

The [reviewable synthetic plan](handover_synthetic_plan.json) was generated by the
successful disposable-database import/opening-stock/POS-payment rehearsal. It has
zero blockers and 15 exact affected rows across 11 models (including invoice,
receipt, cost snapshots, stock adjustment/movements and import jobs). Its database
identity is `test_handover_focused_20261025_731`, not the business database. The
rehearsal already applied this plan and removed its synthetic fixtures; it is
review evidence, not an operational approval or an executable live plan.

No real-ID plan was generated: the request supplies no approved tenant, actor,
support session or sample manifest, and the connected business database is not an
authorized disposable rehearsal copy. Schema inspection is not permission to
classify business rows as sample. The tests generate and apply synthetic plans
only in the dedicated Django test database.

## Backup and isolated restore rehearsal — required before operational approval

1. Identify the exact deployment/build and all writers: web/Gunicorn, API
   integrations, administrator shells, imports, external scheduled jobs, cart
   cleanup, workers and direct database clients. Record their stop/start procedure
   and accountable owners in the maintenance ticket. Open the audited support
   session before stopping web access. Prevent inbound traffic,
   stop `web` and external writers, and drain active transactions. Do not rely on
   a cleanup-only advisory lock or a maintenance page to stop writers.
2. Create a full PostgreSQL custom-format dump with an established backup process.
   Example on a POSIX operator host (custom-format binary redirection must not be
   done with Windows PowerShell 5 text output):

   ```sh
   docker compose exec -T db pg_dump -U postgres -d js_internetservices -Fc > /secure/backups/jims-before-handover.dump
   sha256sum /secure/backups/jims-before-handover.dump
   docker compose exec -T db pg_restore --list < /secure/backups/jims-before-handover.dump
   ```

   Archive media separately; retain immutable backup, checksum, database/build
   identity, timestamp and operator evidence outside the containers.
3. Restore into a separate isolated PostgreSQL instance/database using matching
   PostgreSQL tools and a reviewed `pg_restore --exit-on-error` procedure. Never
   restore over production during rehearsal. Verify audit rows, subscription
   periods, customers/sites/assets, paid documents, stock and counters against
   baseline counts and persisted-field hashes. Disable outbound email/integrations
   and scheduling in the rehearsal environment. Retain restore logs and checksums.
4. On that restored copy, use the reviewed application build to generate the exact
   manifest/plan, review every ID/blocker, rehearse apply, compare preserved fields,
   and run a real-product import/opening-stock/paid-POS fixture. Capture elapsed
   time and recovery evidence. A backup-reference string alone does not prove the
   backup or restore worked; attach the actual evidence for operator approval.

## Dry-run and apply commands

Run only with a reviewed build containing this code. The current Compose `web`
image does not bind application source. Do not deploy/rebuild as part of this task.
For an offline maintenance process, use the reviewed image and mount an absolute
operator-owned evidence directory at `/handover`; preserve the same application
secret and database identity between planning and apply. Runtime images omit Git,
so provide the full **actual reviewed build SHA** as `HANDOVER_BUILD_COMMIT`.
The base SHA below records this checkout; replace it when the reviewed build changes.

```sh
docker compose run --rm --no-deps \
  -e HANDOVER_BUILD_COMMIT=36bfa1fbe37df77dd7b3006a81b4bc233df360b3 \
  -v /secure/handover:/handover web \
  python manage.py handover_cleanup --tenant TENANT_SLUG --actor ACTOR_ID \
  --support-session SUPPORT_ID --manifest /handover/approved-inventory.json \
  --output /handover/inventory-plan.json
```

Omitting `--apply` performs no database writes, including audit/support writes.
Human output lists every planned deletion and blocker. JSON output is created
exclusively and never overwrites a previously reviewed plan. A blocked plan remains
reviewable but cannot be applied. Review appended automatic custom values too.
For customers, use a distinct manifest/output and `--operation customers`.

After verified backup/restore rehearsal, write-quiescence, unchanged zero-blocker
plan review, and separately authorized operational execution:

```sh
docker compose run --rm --no-deps \
  -e HANDOVER_BUILD_COMMIT=36bfa1fbe37df77dd7b3006a81b4bc233df360b3 \
  -v /secure/handover:/handover web \
  python manage.py handover_cleanup --tenant TENANT_SLUG --actor ACTOR_ID \
  --support-session SUPPORT_ID --apply --plan /handover/inventory-plan.json \
  --fingerprint PLAN_SHA256 --confirm 'CLEANUP TENANT_SLUG PLAN_SHA256' \
  --backup-reference 'ticket/path/checksum' --restore-reference 'verified rehearsal ticket' \
  --maintenance-reference 'all writers stopped: maintenance ticket' \
  --reason 'Approved exact sample data handover'
```

The private executor owns a durable PostgreSQL atomic transaction. It obtains
`SHARE ROW EXCLUSIVE` locks on all existing tables in sorted order with a five-second
lock timeout, blocking ordinary INSERT/UPDATE/DELETE across **all tenants** until
commit/rollback. This is a short database-wide maintenance window; do not use it
under normal traffic. Ordinary read transactions may still run. Waiting/queued
writers must be stopped operationally so they cannot resume after commit. The
database role needs permissions to obtain these locks and delete the selected
tables. Lock acquisition failure means no deletion; do not increase timeouts to
hide an active writer. Constraints are checked before success audit/commit.

No flush, TRUNCATE, database recreation, constraint disabling, sequence reset,
on_delete change or migration rollback is part of this procedure. Media removal
is deliberately excluded; any future orphan cleanup needs separate reference
verification, retry policy and authorization.

## Verification and recovery

After apply, preserve the signed plan, exact command output and successful
`handover.cleanup.applied` audit event with actor, tenant, support ID, fingerprint,
backup/restore/maintenance references, reason and counts. Compare counts and
persisted-field hashes for retained customer/internet/service/subscription/payment
history, audit history and sequences against the restored-copy baseline. Confirm
all exact approved IDs are absent, all other relevant records remain, and media
is present. Rehearse real import, opening stock and normal POS payment before
allowing company product import and reopening writers.

Unexpected dependency, stale plan, retained endpoint change, constraint or count
failure rolls back the entire cleanup. A failure audit is appended after rollback;
authorization/confirmation/evidence preflight rejection does not claim successful
execution. If writing the failure audit itself fails (database outage), retain
the command error in the operator incident log and reconcile it before retry.
No partial automatic retry/expanded scope is permitted. Investigate and regenerate
the exact reviewed plan when state changes. Repeating a completed apply fails
stale-plan validation; it cannot pick up newly imported products.

For a committed operational mistake, keep all writers stopped and use the rehearsed
whole-database restore recovery procedure plus matching media/build. There is no
safe selective automatic undo of deleted financial history. Preserve the failure
and success evidence externally before restore, then verify real customer and
subscription history and counters before reopening. Never reset numbering to
reuse deleted sample references.

## Verification results

Results and remaining limitations are recorded in
[handover verification](handover_cleanup_verification.md). Deployment, live cleanup,
real-ID classification, backup creation and restore rehearsal remain operational
work outside this implementation authorization.
