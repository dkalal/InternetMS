# Handover cleanup verification

**Final live result:** [completed execution](handover_live_result_2026-10-25.md).
The owner-approved plan committed at 19:28:44 EAT: **565 records deleted,
products 33 → 0**, audit **1464**. Full retained-row hashes, other tenants, old
audits, non-audit sequences and media are unchanged. Final backup is protected;
web and db are healthy, and products remain 0 after restart.
Earlier pre-approval statements below are chronological history.

**Latest completed remaining steps:** [live readiness](handover_live_readiness_2026-10-25.md).
The reporting regression is fixed while preserving the user's hidden panel.
Focused tests **26/26** and full tests **698/698** now pass. A protected full
PostgreSQL-16/media backup was restored, all 71 table hashes/sequences matched,
and the actual 565-row cleanup succeeded only on the isolated restored copy,
preserving all other rows/tenants, old audits, sequences and media. Normal import,
opening stock and paid POS passed afterward on that copy. A fresh production-bound
plan now awaits explicit live approval; no live product deletion has occurred.

**Latest v2 result:** [final signed js-tech plan](handover_js_tech_final_plan_2026-10-25.md)
is complete with 0 data blockers: 33 products, 565 affected rows, explicit sample
classification for all 44 exceptional financial roots and a zero-products
postcondition. The separate 17-row alternative is also prepared. All 25 cleanup
regressions passed; the final full run executed 698 tests with the same single
unrelated reporting failure. No live cleanup or deployment was performed. Earlier
sections below retain the chronological implementation/verification history.

Date: 2026-10-25 (Africa/Dar_es_Salaam). Branch:
`feature/cart-lifecycle-management`; base commit
`36bfa1fbe37df77dd7b3006a81b4bc233df360b3` plus preserved pre-existing worktree
changes and this uncommitted implementation. No commit, push, merge or deployment
was performed.

## Files in this change

- `inventory/handover_cleanup.py`: closed cleanup policy, ownership/dependency
  resolution, content/relationship/source/migration fingerprints, signing,
  freshness checks, PostgreSQL write locks, private exact deletion and audits.
- `inventory/management/commands/handover_cleanup.py`: read-only planning by
  default, human/JSON output and explicitly confirmed apply.
- `inventory/tests_handover_cleanup.py`: disposable-database safety and complete
  import/opening-stock/POS cleanup rehearsal.
- `docs/handover_schema_2026-10-25.json`: current-checkout model dependency map and
  observed business-database identity/applied migrations (schema-only inspection).
- `docs/handover_synthetic_plan.json`: signed zero-blocker plan from the successful
  synthetic rehearsal, already applied in the disposable test database.
- `docs/handover_cleanup_runbook.md`: policy, backup/restore, write-quiescence,
  commands, approval evidence, verification and recovery.
- `docs/architecture.md`: appended maintenance-exception contract/link, preserving
  the user's existing edits.
- This verification report. Host verification logs are untracked local artifacts;
  unrelated pre-existing tracked and untracked work is preserved.

No models, migrations, normal domain services, permissions, signals, templates or
HTTP routes were changed. There is no schema or package-conversion change.

## Runtime and commands

Compose services `db` and `web` were inspected with `docker compose ps`. Both were
healthy. Only non-secret database identity and applied migrations were read from
the connected business database. No business records were classified or deleted.

Tests use the configured `web` image in disposable Compose processes, current
checkout mounted read-only at `/app`, and PostgreSQL via `db`. Each test invocation
overrides the Django **test database name**, not the business database, and directs
media to a container temporary directory. No service restart or application
deployment is involved. The dedicated test databases are kept for inspection under
`--keepdb`; they are not business-data rehearsal copies.

Final full-suite invocation from PowerShell (the internal command uses Django's
`call_command`, equivalent to the required manage.py commands):

```powershell
docker compose run --rm --no-deps `
  -e HANDOVER_BUILD_COMMIT=36bfa1fbe37df77dd7b3006a81b4bc233df360b3 `
  -v "${PWD}:/app:ro" web python -c "import os,tempfile; os.environ.setdefault('DJANGO_SETTINGS_MODULE','internetservices.settings'); import django; django.setup(); from django.conf import settings; settings.DATABASES['default']['TEST']['NAME']='test_handover_20261025_731'; settings.MEDIA_ROOT=tempfile.mkdtemp(prefix='handover-media-'); from django.core.management import call_command; call_command('check'); call_command('makemigrations',check=True,dry_run=True); call_command('test',keepdb=True,interactive=False,verbosity=1)"
```

Focused verification uses the same wrapper with
`call_command('test','inventory.tests_handover_cleanup',keepdb=True,interactive=False,verbosity=1)`.
Its final independent database is `test_handover_focused_20261025_731`. The final
focused invocation also captured the complete synthetic POS plan returned by
`make_plan`, writing its JSON to the review artifact only after successful tests.

| Gate | Actual result |
| --- | --- |
| `python manage.py check` | Passed: no issues, 0 silenced. |
| `python manage.py makemigrations --check --dry-run` | Passed: no changes detected. No new migration. |
| Focused `inventory.tests_handover_cleanup --keepdb` | Final 16 tests passed in 67.022 seconds, including retained real invoice/receipt/payment fields. |
| Earlier full `python manage.py test --keepdb` | 684 tests passed in 509.287 seconds; additional boundary regressions were subsequently added. |
| Final full suite | 689 tests passed in 527.542 seconds; Django reported OK. See host log-redirection note below. |
| `git diff --check` | Passed; Git also reports the pre-existing CRLF conversion notice for `inventory/tests_cart_readiness.py`. |

Full logs are `.handover-full-tests.log` and `.handover-final-full-tests.log`.
PowerShell's native-stderr redirection wrapped Docker progress output in a
`NativeCommandError` record and the host execution wrapper returned status 1 for
the redirected full run, despite the Django runner's final `Ran 689 tests ... OK`.
The focused runs without that redirection returned status 0. A separate zero-exit,
stderr-writing Docker probe was used to check this shell behavior; no failing
Django test was suppressed or reclassified. The probe confirmed `docker_exit=0`
when the native process exit was explicitly propagated. Final Django check,
migration-drift check and saved-plan fingerprint/signature verification were then
rerun with explicit exit propagation and returned status 0.

The first focused invocation encountered a test-harness TEST configuration error;
correcting only TEST.NAME fixed it. Subsequent focused runs found a file-field
serialization defect and the actual receipt-summary schema distinction; both were
fixed before the passing runs. The retained real-billing fixture was also corrected
to create line items while its invoice is draft, respecting ordinary immutability.
That expanded fixture passed independently and in the final focused run after the
full suite had already discovered its tests; no product code changed at that step.
No test was removed, weakened, skipped or xfailed
to hide a failure. PostgreSQL-only apply assertions deliberately execute against
PostgreSQL, including concurrent writer blocking.

## Covered safety boundaries

The 16 focused tests cover zero-write dry-run and command output; closed manifests;
forged foreign tenant IDs; staff without Super Administrator membership; ended
support contexts; tenant/organization mismatch and null ownership; incoming child
dependencies; M2M children without tenant columns, including cross-tenant parents;
assigned serial assets; custom-field definition/target mismatch; partial stock
history; real/non-product billing blockers; separate bare-customer planning;
generic walk-in protection; JSON plan transport; signature/content tampering;
expired plans; changed row contents and newly added dependencies; microsecond
timestamp fingerprinting; typed confirmation and operational evidence; self-table
billing cycles; rollback after injected post-deletion failure and surviving failure
audit; replay rejection; tenant B persisted fields; unchanged real customer/profile,
site/package/subscription/period/assigned-asset fields; numbering counters and audit
history; exact custom-value cleanup while preserving definitions/unrelated values;
and no media deletion.

An ordinary concurrent writer attempts to update a tenant B product while the
maintenance transaction holds PostgreSQL table locks. Its update times out, proving
the locks cover normal writers and all tenants, not only another cleanup command.

The end-to-end fixture removes an approved sample product, imports a fresh product
with the same SKU and a larger PK, commits opening stock, completes a normal POS
invoice and receipt, verifies remaining stock, then explicitly classifies the
whole synthetic history and removes its exact IDs. The generic walk-in customer,
audit and numbering remain. This exercises actual application import/stock/cart/
billing services rather than merely manufacturing a successful deletion graph.

## Security impact, limits and operational blockers

No new HTTP surface or ordinary user privilege is added. The CLI requires the
existing platform Super Administrator membership plus a reasoned active tenant
support session. The customer policy also keeps the existing superuser rule and
blocks dependency/audit-backed customers. Normal immutability protections are
unchanged. Only the private, revalidated, signed-plan executor bypasses model
deletion overrides/signals for explicitly approved sample history. Constraints
remain active and are checked before success. Database triggers and mutating FK
actions outside the reviewed Django schema fail closed.

Apply creates a database-wide write maintenance window and must be rehearsed on
the company's restored copy. Evidence references are mandatory but are operator
attestations: the command cannot prove a backup was actually restored or that an
external scheduler was stopped. The runbook requires those verifications and
stopping queued writers before approval. Direct schema changes, privileged DB
administrators and filesystem edits are outside ordinary writer locks and must
also be excluded during maintenance.

Registered-customer billing, service/package/mixed invoices, customer dependencies
and messaging-linked documents remain blocked. Cross-table cycles are rejected;
same-table billing cycles are supported. Arbitrary unknown scalar links in future
schemas need a policy review; current known SKU/company-name, serial-asset,
custom-field, messaging and audit references were inspected. Media orphan removal
is intentionally excluded. Source/database changes require a new signed plan.

There is no real-ID plan, verified company backup, restore rehearsal, real-database
copy rehearsal, live apply or live-data safety claim. The user has authorized
implementation/testing only. Supply explicitly approved IDs and complete the
runbook on an isolated restored copy before seeking operational execution approval.
The synthetic plan is review evidence and cannot apply to the business database.

Recommended cohesive commit scope, if later authorized: this service/command,
focused tests and contract/runbook/evidence together; exclude all unrelated
pre-existing reporting and purchasing work and local verification logs.

## Subsequent authorized all-products planning

The user later selected `js-tech`, Admin (user ID 2), and opened audited support
session 3. PostgreSQL-enforced read-only discovery produced a real-ID **unsigned
candidate preview**, covering 33 products and 565 related candidate rows. This is
not a signed executable approval or live cleanup. There are 51 policy blockers,
including actual mixed product/package documents and billing linked to an internet
subscriber. See the [current tenant review, evidence and exact
IDs](handover_js_tech_review_2026-10-25.md). Earlier statements about no tenant-data
inspection describe the initial implementation stage, before this later
authorization. Existing preservation rules have not been weakened.

The later planning-step verification passed Django/migration checks and all 16
cleanup tests, but the **current** full suite ran 689 tests with 1 reproducible
reporting failure. The valuation bridge's expected HTML label is absent because
its panel is commented out in the separately modified reporting template. The
isolated reporting test also fails. This later outcome supersedes the earlier
full-suite pass as the latest worktree gate; no cleanup code or unrelated
reporting change was altered to hide it. See the tenant review for logs and exact
test name.

## Completed v2 classified policy and final artifacts

The user explicitly confirmed all forty remaining financial roots as sample after
the initial four-document confirmation. `inventory/handover_cleanup.py` and its
management command now implement two distinct, signed v2 policies:
`sample_documents` and `classified_inventory`. Default inventory/customer policies,
normal model/service immutability and tenant permissions remain unchanged. The
combined operation requires complete catalog coverage and verifies products
remaining = 0 inside the deletion transaction; a new independent product makes
the plan stale instead of expanding scope. Failure audits retain validated
operation/classification; successful audits identify the zero-products condition.

No model, schema migration, on_delete rule, purchasing conversion, ordinary view,
reporting template or unrelated user source file was changed by this phase.
The related architecture/contract/runbook were updated. The new regression module
is `inventory/tests_classified_handover.py`.

Actual outcomes:

- Django check: passed, no issues. Migration check: passed, no changes.
- Focused original/new-policy regressions: 24 tests passed in 96.842 seconds.
- Extra paid/issued-chain integration regression: passed in 4.990 seconds. It uses
  ordinary billing/stock services, removes only explicitly classified sample
  invoices/receipts and stock history, reaches zero products, and preserves the
  real synthetic customer's 70 receivable plus persisted internet/payment history.
- Final full suite: **698 tests, 551.112 seconds, 1 reporting failure, exit 1**.
  All 25 cleanup tests pass; the known valuation-bridge HTML-label failure remains
  in the user's separately modified reporting template. It was not hidden/fixed
  outside this scope. There is no current green full-suite release gate.
- Final signatures/age/source/identity/dependency revalidation: passed read-only,
  exit 0, both saved plans exactly match regeneration (565/17 rows, 0 blockers).
- `git diff --check`: passed with the existing CRLF notice.

Commands use the same source-mounted `docker compose run --rm --no-deps ... web`
wrapper and dedicated disposable PostgreSQL database documented above, with
temporary media. Current logs are `.handover-v2-final-focused-tests.log`,
`.handover-v2-posted-chain-test.log` and `.handover-v2-final-full-tests.log`.
Final plan preparation/verification uses the read-only scripts linked in the
current final-plan report; no live apply command was invoked.

Plan files are fingerprint-named and immutable; the final index links the exact
complete and separate variants. The former includes the latter's 17 rows, so they
must not be applied together unchanged. V1 plans and preliminary v2 files remain
historical evidence. The latest plans expire after two hours and require fresh
generation/review after source/data changes or expiry.

Outstanding live gates are: resolve the reporting failure, verify a company backup
and isolated restore/deletion rehearsal, establish full write quiescence, regenerate
the fresh production plan if necessary, and obtain explicit execution approval for
its fingerprint. No company backup/restore evidence, company-copy deletion
rehearsal, live deletion, deploy, commit, push or merge is claimed.

## Completion of remaining preflight steps

The later user instruction to complete remaining steps authorized the reporting
fix and actual backup/restore rehearsal. The previous outstanding reporting,
backup and restored-copy gates listed above are now resolved; they are retained
there as chronological history.

- Minimal reporting fix: one visible valuation-difference paragraph was added to
  `templates/inventory/report.html`; the user's commented panel and other edits
  were preserved. No existing test was changed/weakened.
- `check`: no issues. Migration drift: no changes. Focused reporting/cleanup:
  **26 tests in 104.662 seconds, OK**. Full: **698 tests in 544.625 seconds, OK,
  native Docker exit 0**. Log: `.handover-remaining-gates.log`.
- Full backup used the source snapshot and matching PostgreSQL 16.14 tools, not
  the web image's client 17.11. Database/media checksums are in the readiness
  report; binaries and detailed proof are in the ACL-restricted, git-ignored
  `.backups/handover-20261025-remaining/` directory.
- Restore database `jims_handover_restore_20261025` was newly created without
  overwriting any existing database, PUBLIC connect revoked, and restored with
  `pg_restore --exit-on-error --single-transaction`. All 71 user-table hashes and
  every sequence state matched the exported source snapshot.
- Actual restored-copy cleanup: 33 products became 0 and exactly 565 selected
  records were removed. Every retained table hash matched, other tenants and old
  audit rows were unchanged, every non-audit sequence remained unchanged and all
  33 media files were preserved. A single expected cleanup audit was allowed.
- Subsequent isolated normal workflow: genuine application product-import and
  opening-stock services plus POS/payment completed (opening 5, sold 1, left 4),
  with a new product PK 123. This synthetic fixture exists only in the restore DB;
  live still has 33 products.
- Web was stopped briefly for the source backup, table write locks covered all
  user tables, active/open transactions were absent, and media/sequence stability
  was checked during capture. Eleven idle GUI/IDE clients were left intact;
  they could not write through the locks. Web was restored and is healthy. Its
  usual startup migration check reported no migrations to apply; no schema change
  or image deployment was performed.
- Final production plan generation was read-only and compared exact manifest,
  classification, selected contents, dependency edges and retained endpoints with
  the rehearsed copy-bound plan. They matched. Fresh live fingerprint:
  `02d07ac5be9a386a1a852834497489c803556d961dd0307aa96ccc1e14bcfe33`, valid until
  2026-10-25 20:19:39 EAT if relevant data/source/permissions remain unchanged.

The only remaining step is explicit authorization of live execution followed by
the immediate final maintenance/backup/revalidation/apply procedure. The initial
instruction excluded destructive live cleanup, and no such cleanup was performed.
No commit, push, merge or image deployment occurred. Source helpers and the
readiness report document actual outcomes and guards, not a claimed future apply.
