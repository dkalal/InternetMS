# Cart lifecycle implementation handoff

## Baseline and scope

- Feature branch: `feature/cart-lifecycle-management`.
- Base: `870b0a5` (`feat(customers): add opt-in balance summary access`), branched
  from `feature/purchase-import-review-redesign`.
- Initially no tracked modifications; unrelated untracked patches, screenshots,
  spreadsheet/PDF files and `report-source.md` were preserved.
- No deployment, merge, push, live cleanup, schedule activation or live migration
  was performed. Read-only `showmigrations inventory` confirmed the running
  application's baseline through `0013_purchaseimportsubmission`.

## Delivered stages

1. Lifecycle/activity: meaningful activity propagation from lines/serials;
   unchanged views/details do not reset the clock; parent locks coordinate
   mutation and cleanup; same-owner recent-empty reuse serializes on a stable
   actor row using NO KEY UPDATE so foreign-key references remain possible.
2. Workspace/history: default Active, Stale subset, Converted, Abandoned and All
   history; scoped counts/search/owners; explicit last-activity dates; stable
   25-row pagination; desktop table/mobile cards; linked-document history;
   audited manager restoration; read-only converted endpoints and admin.
3. Maintenance: dry-run-default cleanup with explicit apply, tenant selection,
   bounded batches/cursors, locked rechecks, privacy-conscious audit events and
   documented daily host scheduling and reviewed rollout.

## Changed files

| Area | Files |
| --- | --- |
| Domain and policy | `inventory/models.py`, `inventory/services.py`, `inventory/cart_lifecycle.py` |
| Activity wiring | `inventory/apps.py`, `inventory/signals.py`, `inventory/forms.py` |
| HTTP and admin | `inventory/views.py`, `inventory/urls.py`, `inventory/admin.py` |
| UI | `templates/inventory/cart_list.html`, `templates/inventory/cart_detail.html`, `templates/inventory/includes/cart_document_links.html` |
| Migrations | `inventory/migrations/0014_cart_lifecycle.py`, `inventory/migrations/0015_cart_known_line_activity.py` |
| Maintenance | `inventory/management/__init__.py`, `inventory/management/commands/__init__.py`, `inventory/management/commands/cleanup_carts.py` |
| Verification | `inventory/tests_cart_lifecycle.py`, `inventory/browser_cart_lifecycle.py` |
| Documentation | `docs/architecture.md`, `docs/cart_lifecycle.md`, this handoff |

No dependencies, stock services, billing/payment rules, product units, purchasing
flows or global permission aliases were changed.

## Migrations and preservation

0014 adds nullable lifecycle timestamps/actors and an abandonment reason; it does
not fabricate legacy dates. 0015 advances draft activity only to an actually
recorded later child-line timestamp, without overwriting newer parent activity.
Neither migration deletes carts or changes their status. Existing indexes are
reused. Pause cart writes for rollout and migrate before enabling the new build.

Revert application code before a schema rollback. Reverting inventory to 0013
removes the new lifecycle metadata; back it up first if needed. The activity
reconciliation deliberately has a no-op reverse, preserving recorded activity.
See [the lifecycle contract](cart_lifecycle.md) for compatibility and rollout.

## Verification actually performed

All Django gates used the configured Docker/PostgreSQL runtime. Workspace source
was mounted into temporary test containers because the running web image does
not bind-mount application source:

```powershell
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py check
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py makemigrations --check --dry-run
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py test inventory.tests_cart_lifecycle inventory.tests inventory.tests_cart_readiness inventory.tests_financial_controls inventory.tests_walk_in_identity --keepdb
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py test --keepdb
```

- System check: **no issues (0 silenced)**.
- Migration drift: **No changes detected**.
- Initial affected regression run: **123 passed** (before the additional five
  final lifecycle regressions).
- Final lifecycle module: **19 passed**, including PostgreSQL concurrency,
  support sessions, historical activity reconciliation and audit rollback. The
  final repeat used an isolated `test_jims_cart_final` PostgreSQL database via a
  Django settings override and the same management command.
- Final full suite: **632 passed in 406.822 seconds**.
- Optional browser module: **1 passed in 8.446 seconds**, using installed Edge
  and synthetic data in separate PostgreSQL database `test_jims_cart_browser`.
  Verified desktop table, 390px mobile cards, tab navigation, no horizontal page
  overflow, restoration and no JavaScript page errors. Screenshots were visually
  inspected and remain local under `.backups/cart-lifecycle/`.
- `git diff --check`: passed (Git emitted line-ending conversion notices).

Initial development runs exposed a nullable-join lock target error and fixture/
search-echo assertion issues; those were corrected without weakening existing
regressions. The final gate is green. Detailed logs are local, ignored artifacts:
`.backups/cart-lifecycle-regression.log`, `cart-lifecycle-final-focused.log`,
`cart-lifecycle-final-full.log`, and `cart-lifecycle-browser.log`.

## Operator commands and scheduling

After backing up, deploying the reviewed build and applying migrations, review
all dry-run batches before the first explicit apply:

```bash
docker compose exec -T web python manage.py cleanup_carts --tenant TENANT_SLUG --batch-size 500
docker compose exec -T web python manage.py cleanup_carts --tenant TENANT_SLUG --batch-size 500 --apply
```

For each pass, start at cursor 0. If output reports `next_after_id=N`, continue
that pass with `--after-id N` until the cursor is 0. Omit `--tenant` only for an
intentional all-tenant maintenance run. Empty drafts qualify at exactly 24 hours;
meaningful drafts qualify for abandonment at exactly 30 days. Business-linked,
converted and abandoned carts are preserved. Prior lifecycle metadata prevents a
restored cart being classified as accidental empty data.

[The lifecycle contract](cart_lifecycle.md#reviewed-rollout-and-daily-scheduling)
contains an exact Linux host script, one daily 03:15 cron entry, overlap locking,
log handling and rollback steps. No scheduler exists in the repository; none was
installed or activated. No Redis/Celery infrastructure was added.

## Security and remaining limitations

Cart rows/counts/search/owner options are tenant- and permission-scoped. Own-scope
users cannot expose other owners or inaccessible linked documents. Existing
canonical permissions govern manager access and restoration, and audited support
sessions remain required. Mutations are POST/CSRF protected, parent-locked and
stock-neutral; conversion idempotency and established payment/stock authority
remain intact. Converted line history survives later product deactivation.

Historical activity from already deleted lines cannot be reconstructed. Old
abandonment/restoration dates remain unknown. Review every initial dry-run batch
and unexpected linked-draft finding before first apply. Full production deployment,
backup verification, first cleanup apply, host scheduling and broader retention
policy remain operator work intentionally outside this implementation.
