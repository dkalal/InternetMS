# Tenant product SKU allocation

Implemented on `feature/tenant-sku-sequence`, branched from
`feature/cart-lifecycle-management` at `d56b6a0`. No merge, commit, push, deployment,
live migration or live backfill was performed. Pre-existing untracked artifacts
are outside this change.

## Evidence and decisions

The previous catalog form cleaned SKU before Name, so its name-derived fallback
could use `ITEM`. Purchasing had a separate name-derived allocator with a default
20-candidate limit (1000 for the grid). Its broad exception conversion could hide
price/category/unit errors. API creation required an explicit SKU, catalog import
required one during preview, and legacy form persistence bypassed the purchase
allocator. The old database unique index was case-sensitive. Batch-token replay
did not compare payloads. These are repository findings, not industry requirements.

The implementation follows Django's documented
[row locking](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update),
[get-or-create uniqueness requirement](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#get-or-create),
and [savepoint error handling](https://docs.djangoproject.com/en/5.2/topics/db/transactions/).
PostgreSQL documents
[unique expression indexes](https://www.postgresql.org/docs/16/indexes-expressional.html)
as protection against case-only duplicates. `PRD-<number>` is this application's
business decision, not a universal inventory standard.

## Final rules

- One durable counter per tenant, shared by physical products and services.
- Automatic codes start at `PRD-000001`, grow beyond six digits, never reset by
  year, product deletion, catalog cleanup or routine maintenance.
- Blank creation allocates only at final persistence. Blank or omitted update
  preserves an assigned code. Rename, category and price changes do not renumber.
- Manual codes trim surrounding whitespace and uppercase, retain leading zeros,
  allow the existing character repertoire and have a 64-character limit.
  Exact ASCII `PRD-[0-9]+` is reserved for allocation in every supported manual
  creation workflow. Existing legacy PRD codes remain readable and matchable.
  There is deliberately no ordinary import escape hatch to create supplied PRD
  codes. Restore historical codes only through reviewed offline maintenance with
  counter initialization/reconciliation before reopening product writes.
- Existing nonempty codes are not rewritten by migration. Unchanged legacy code
  spelling is preserved on subsequent saves. The database enforces tenant plus
  uppercase/space-trimmed nonempty SKU uniqueness, including low-level writes.
  Normal business writes additionally strip surrounding Python whitespace.
- Sequence suffixes use ASCII digits and a signed-bigint high-water mark;
  maximum issued number is 9223372036854775806. Exhaustion fails clearly.
- A SKU identifies a catalog record; it is separate from device serial numbers,
  duplicate-product rules and submission idempotency. No inventory/serial/unit/
  package conversion rule changes.

`products.creation.save_purchase_product` is the shared creation service (the name
is retained for import compatibility). It validates tenant ownership, optional
actor permission, manual SKU and zero opening stock, then uses the existing model
validation and persistence. Existing owning workflows authorize and audit their
aggregate and call it with `actor=None`; this is a trusted internal boundary, not
an HTTP authorization option. API, admin and catalog-import callers pass actor.
Custom fields, images and specialized creation audit actions stay with their
existing form/aggregate owners, in the enclosing transaction.

The counter's unique tenant key and Django `get_or_create` savepoint handling
resolve concurrent first creation; `select_for_update` locks the persisted row.
All supported creators take the tenant parent lock before the counter, and unit
successor creation takes it before locking the source product. This matches
purchase/batch lock ordering and prevents a counter/deferred-tenant-FK deadlock.
The grid holds that parent lock across replay checking and validation, so a
concurrent manual-code retry cannot become a false duplicate validation error.
One tenant-filtered scan initializes a newly provisioned counter above the
largest valid occupied suffix. Normal allocation does not rescan the catalog.
If its next exact candidate is occupied, one scan jumps above occupied suffixes;
there is at most one recovery attempt. Only PostgreSQL errors naming the SKU
unique indexes are translated/retried; unrelated integrity and validation errors
propagate. No arbitrary deadlock retries or external effects are introduced.
Known Django SKU-constraint validation errors also recover through that bounded
path; field errors never become allocation retries. Backfill recovery retains the
existing product primary key.
Product, counter and aggregate writes roll back together. Rolled-back numbers
can be used later because they were never committed identities; committed
numbers survive product deletion through the retained counter.

## Entry points and consumers

| Path | Persistence / behavior |
| --- | --- |
| Catalog grid / pasted spreadsheet | `save_product_batch` -> shared service; up to 200 rows; custom fields, audit and image compensation retained |
| Catalog single form / embedded purchase modal | `ProductForm.save` -> shared service; existing opening-stock adjustment remains separately authorized in the same transaction |
| Purchase quick endpoint | `QuickPurchaseProductForm.save` -> shared service; existing active-name duplicate rule retained |
| Mixed purchase import | Existing proposal staging consumes no number; receiving uses `apply_purchase_batch` -> shared service in the purchase transaction; repeated staging references share one product |
| Catalog workbook | Preview accepts blank SKU; detects duplicate supplied codes and reserved namespace; commit uses shared service, zero stock and source-row errors |
| REST product create | Blank/omitted SKU allocates; response contains persisted code; canonical `products.create` permission required |
| REST update | Blank/omitted preserves; Django validation becomes field-level REST errors; SKU old/new audit recorded |
| Unit successor / sample unit command | Existing owning transition service -> shared allocator; blank successor SKU now supported; stock/history are not transferred |
| Django admin | Shared creation; manual-code form validation; tenant permission checks and old/new update audit |

Search, selection, purchase matching, exports and reports already consume the
stored SKU; no provisional number is added. Historical document snapshots are
untouched. Some reports dereference the current product code, so explicit edits
are rejected after stock, purchase, cart or billing history exists. Before that,
existing product-update permission and old/new audit apply. External consumers
should bind stable product IDs and treat an authorized pre-history manual-code
edit as a metadata change. Manual codes can still be reused after an unreferenced
record is deleted; no permanent manual-code registry is justified by the current
contract. Generated codes cannot be reused through supported business paths.

There are no independent production clone/seed/background creation paths in the
audited apps. `proposal_lines` constructs unsaved review objects only. Historical
migrations, test fixtures and approved offline maintenance may intentionally use
low-level ORM operations. `Product.save` does not allocate and there are no
allocation signals. `create`, `bulk_create`, `update`, raw SQL and fixtures are
not supported business creation APIs: they can bypass allocation, audit and
reserved-namespace validation, while the database uniqueness guard still applies.

## Retry contracts

The grid's signed tenant/actor-bound token now stores a payload/file fingerprint;
identical retries replay, changed content returns conflict. The single-product
HTML form/modal now supplies a signed tenant/actor-bound token with equivalent
behavior. Both recheck permissions before replay. Legacy clients which omit the
new single-form token retain their old request contract; callers wanting replay
must preserve the token returned by GET.

REST creation accepts an optional `Idempotency-Key` header (up to 128 characters),
scoped to tenant, actor and product-create operation. Identical authorized replay
returns the existing product (200); changed content returns 409; initial creation
returns 201. Without the header, separate POSTs remain distinct intended requests.
The legacy purchase quick-create endpoint also supports this header and checks
replay before its active-name duplicate validation.
Deleted results return 404 rather than allocating again. Existing purchase-import
UUID/fingerprint receipts and confirmed-receipt replay remain in use. Catalog
import commits lock their job, enforce creator ownership/current import and
product-create permission, and return the already committed job on retry.
Unit-successor receipts are checked under the source lock and now fingerprint
content; old pre-fingerprint historical receipts remain compatible.

## Existing blanks and compatibility boundary

Investigation confirmed that `InventoryService.requires_full_payment`,
`ensure_invoice_sale` and the billing price-selection branch use nonempty SKU as
a legacy catalog eligibility marker. Generating SKUs for existing linked blanks
could therefore change payment/stock/pricing behavior. This change preserves that
historical boundary and performs no automatic blank backfill. Replacing it needs
an explicit historical classification plan, not a blanket SKU rewrite.

`audit_product_skus` inventories a single tenant's blank IDs, transaction/stock
dependencies and normalized collision counts. Default is dry run and consumes no
numbers. Explicit apply requires an actor with tenant product-create permission,
refuses any history-dependent blank or normalized collisions, locks records and
uses the same allocator with audited backfill mode for unused blanks only.
Existing nonempty codes and stock remain unchanged.

## Deployment and rollback

Migration `products.0018_productskucounter_and_more` adds the durable counter and
normalized unique index, initializes only counter rows, and changes no Product or
historical SKU. It diagnoses the first normalized collision group with tenant
and product IDs, or out-of-range PRD suffixes, and atomically stops without
renaming/merging records. PostgreSQL holds a product-table write lock during
diagnosis, initialization and constraint creation; schedule a maintenance window
for a large catalog. The existing case-sensitive index is retained for backward
compatibility. No production database audit was executed in this session.

After backup and staging rehearsal, stop product-writing workers, audit each
tenant using the old release's approved diagnostic procedure or a staged release
with no product writes, apply the migration and start the new release together.
Do not run old name-generating workers alongside the new authority.

```powershell
# Configured Docker runtime, after releasing the new image/source:
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py audit_product_skus --tenant-id TENANT_ID
# Separate explicit operation, only after reviewing the dry run:
docker compose run --rm web python manage.py audit_product_skus --tenant-id TENANT_ID --actor-id ACTOR_ID --apply
```

These are rollout instructions, not commands executed against live data here.
Keep counter rows/table and the new unique index on application rollback. Although
the additive migration is structurally reversible, do not unapply it after any
code has been issued: dropping high-water marks could permit reuse of deleted
identifiers. Pause creation during rollback and retain the allocator or restore
it before accepting new writes. No backfill or schema reversal rewrites history.

## Verification

PostgreSQL verification uses a disposable Compose `web` container, source mounted
at `/app`, isolated test database `test_sku_sequence_20261027`, and temporary
static root `/tmp/sku-sequence-static`. The production database is not migrated.
Concurrency tests use TransactionTestCase, synchronization barriers and separate
connections for absent/existing counters and simultaneous catalog import commits.
Migration tests cover preservation, suffix initialization and diagnostic failure.
Regression tests cover tenant/permission denial, no preview allocation, import
rollback/replay, normalized duplicates, service creation, deletion, stale recovery,
historical edit restrictions, modal/API/batch retries and 200-row behavior.

Browser verification runs real headless Edge against a disposable local SQLite
test database; it does not prove PostgreSQL locking. The existing Tailwind script
is served from the pre-existing temporary browser cache. Existing screenshots are
restored byte-for-byte after the run.

The final focused run passed 39 tests in 22.439 seconds. The final real Edge run
passed 2 tests in 12.685 seconds. `check` reported no issues;
`makemigrations --check --dry-run` reported no changes. JavaScript syntax
(`node --check products/static/products/workspace.js`) and `git diff --check`
passed (Git emitted its existing LF/CRLF conversion advisories only).
The final complete PostgreSQL suite passed **744 tests in 488.284 seconds**, with
`OK` and no skips reported. It includes the final tenant/counter lock ordering
and concurrent manual submission replay fixes.

Measured PostgreSQL baseline, including normal model/form validation:

| Workload | Queries | Prefix initialization scans | Local elapsed time |
| --- | ---: | ---: | ---: |
| 200 product persistence calls in one transaction | 3806 | 1 | 1.847 s |
| Full 200-row workspace POST including validation/audit | 5024 | 1 | 3.325 s |

These are local baselines, not production capacity guarantees. Field, category,
custom-field and model validation still run per row. The improvement is a single
numbering authority and bounded indexed candidate checks, without repeated full
catalog scans; this is not a general bulk-validation rewrite.

The exact final PostgreSQL gate invocation is:

```powershell
docker compose run --rm --no-deps --user root -v "${PWD}:/app" web python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','internetservices.settings'); import django; django.setup(); from django.conf import settings; settings.DATABASES['default']['TEST']['NAME']='test_sku_sequence_20261027'; settings.STATIC_ROOT='/tmp/sku-sequence-static'; from django.core.management import call_command; call_command('collectstatic',interactive=False,verbosity=0); call_command('check'); call_command('makemigrations',check=True,dry_run=True); call_command('test','products.tests_sku','products.tests_workspace',keepdb=True,interactive=False); call_command('test',keepdb=True,interactive=False)"
```

Output: `.sku-complete-gates.log`. Browser output: `.sku-final-browser.log`.
Windows PowerShell's stderr redirection reported a shell `NativeCommandError`
for ordinary Docker/test progress output; Docker's final container `die` event
independently confirms `exitCode=0` for `web-run-b51dd5b2a702`.
Earlier verification passed 399 product/inventory tests and a 742-test full run.
Those precede the final lock-order refinements and are not substituted for the
final full-suite gate. Intermediate runs were restarted after those refinements.
The original quick-create collision regression now uses occupied PRD codes and
still verifies tenant-scoped collision resolution; obsolete name-based output
is replaced by the approved sequential contract. The old migration regression's
teardown now restores graph leaves instead of leaving later tests on schema 0017.

## Changed files and review stages

- Allocation/schema: `products/models.py`, `products/sku.py`,
  `products/creation.py`, `products/migrations/0018_productskucounter_and_more.py`,
  `products/management/commands/audit_product_skus.py`.
- Creation/update/replay integration: `products/forms.py`, `products/views.py`,
  `products/workspace.py`, `products/services.py`, `products/admin.py`,
  `inventory/api.py`, `inventory/forms.py`, `inventory/imports.py`,
  `inventory/purchase_import.py`, `inventory/views.py`.
- UX: `products/static/products/workspace.js`,
  `templates/products/product_form.html`, `templates/products/product_workspace.html`.
- Verification: `products/tests_sku.py`, `products/test_migrations.py`,
  `products/tests.py`, `products/tests_workspace.py`, `inventory/tests.py`,
  `inventory/tests_purchase_product_quick_create.py`,
  `inventory/tests_purchase_proposals.py`.
- Contracts: this document, `docs/architecture.md`,
  `docs/product_creation_workspace.md`.

Suggested review/commit split after explicit commit authorization: (1) schema,
allocator, diagnostics and core/migration tests; (2) owning workflow integrations
and their regression tests; (3) UX and rollout/performance documentation. No
files have been staged, committed or pushed.

Remaining operational work is the staging/live data audit, backup/rehearsal and
deployment. Existing history-dependent blanks intentionally remain unchanged;
reclassifying them is a separate approved data phase. Low-level ORM callers must
continue to follow the documented maintenance-only exception. There is no new
manual-SKU ownership registry, background infrastructure or approval workflow.
