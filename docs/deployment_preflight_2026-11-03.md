# Office deployment preparation - 2026-11-03

Status: preparation only. No live release, migration, backup/restore or checkout
transition has been executed. Application target: tested main
`81ec3f8ae924acd11bf6f335a628c2fc94add906` (PR #3).

## Findings

Read-only PostgreSQL 16.14 metadata inspection found all current repository
migrations applied. The office and a fresh main-schema database match exactly:
791 columns, 382 constraints and 595 indexes, including type/default/nullability,
constraint definitions/validation and index definitions. Business rows were not
read for this comparison. `inventory.0010_purchase_cancelled` is an extra applied
history record absent from available Git history. It is retained unchanged;
no history record is deleted/faked and no old migration is reconstructed. No
migration is required for this image-only release. A changed migration plan or
schema comparison stops deployment for investigation.

The office web image is baked source; only media is bind-mounted. The dirty office
checkout must remain untouched. A reviewed prebuilt image can replace the web
service using the existing office Compose configuration plus the image override;
no Git switch/pull, source mount or build from the dirty checkout is needed.

## Isolated staging

`compose.staging.yml` is standalone; never combine it with the office Compose
file. It uses synthetic PostgreSQL data in tmpfs, an independent network, private
staging credentials in ignored `.env.staging`, separate cookie names, in-memory
email delivery and localhost port 18083 only. No office environment, database,
volume, media or business records are mounted. The web startup never migrates.
The image is built by the existing Python 3.13 Dockerfile. Expanded Docker ignore
rules keep env variants, backups, media, generated static output and patches out
of the build context. The private staging seed is not in the image.

```powershell
# From this preparation worktree; create the ignored file only when absent.
python -c "from pathlib import Path; import secrets; Path('.env.staging').open('x').write('STAGING_SECRET_KEY='+secrets.token_urlsafe(48)+'\nSTAGING_LOGIN_PASSWORD='+secrets.token_urlsafe(20)+'\n')"
# .env.staging contains disposable local values, never office credentials.
docker compose --env-file .env.staging -f compose.staging.yml -p jims-preflight-20261103 config --quiet
docker compose --env-file .env.staging -f compose.staging.yml -p jims-preflight-20261103 build web
docker compose --env-file .env.staging -f compose.staging.yml -p jims-preflight-20261103 up -d --wait db
docker compose --env-file .env.staging -f compose.staging.yml -p jims-preflight-20261103 run --no-deps web python manage.py migrate --noinput
docker compose --env-file .env.staging -f compose.staging.yml -p jims-preflight-20261103 up -d --no-build web
```

The loopback-only staging HTTP endpoint disables HTTPS redirects intentionally;
secure session/CSRF cookies and DEBUG=0 remain enabled. This exception is for local
staging, not a new production transport policy. Existing production settings are
not changed by the image override. Run Django checks, drift/plan validation,
affected/full tests and real deployed-image browser smoke tests before approval.

## Backup and restore plan for the actual office

The source preservation backup is not a database/media backup. Synthetic restore
rehearsal is not proof that the actual office backup restores. Before the approved
release window:

1. Identify the exact database/container, media bind, current image ID and runtime
   configuration privately. Record the tested candidate image ID and source SHA.
2. Obtain an office-wide write-quiescence window: pause all application writers,
   jobs/imports/maintenance and media uploads; stop only the approved web service
   when the window starts. Keep PostgreSQL running. Do not run cleanup scripts.
3. Create a new private backup directory outside all worktrees, with office-owner
   access only. Preserve the current image reference/configuration securely.
4. Capture PostgreSQL with `pg_dump -Fc` and the matching media tree while writers
   remain paused. In Windows PowerShell, do not redirect binary output with `>`:
   use Python subprocess with an `open(path, 'wb')` stdout file, or another verified
   binary-safe transfer. Check command exit codes before accepting the backup.
5. Record SHA-256 hashes and a manifest. `pg_restore --list` is only an archive
   readability check. Restore the entire dump to a NEW isolated PostgreSQL 16
   database using `--exit-on-error --no-owner --no-acl`; never target office.
6. Restore media to a separate directory. Verify table/sequence counts and hashes,
   tenant isolation, immutable document/stock totals, serial history and critical
   reads using the restored copy. Store business output privately; no public logs.
7. Bind the approved restore proof to that exact backup and candidate image. If
   verification fails, leave/reinstate the old web image and investigate. Reopen
   writers only under the reviewed release/abort procedure.

No actual office dump, media copy or restored business database was created in
this preparation task. That operation remains a separately approved release gate.

## Concrete image-only rollout (not executed)

Prepare two private environment files with `OFFICE_RELEASE_IMAGE=sha256:<id>`:
`release-image.env` for the tested new image and `rollback-image.env` for the exact
currently running image. Keep both images available; do not prune or overwrite
recovery artifacts. Variables below are explicit absolute paths verified against
the inspected office project and preparation worktree. The first Compose file
must be the unchanged office file so its env/media relative paths stay correct.

```powershell
$officeRoot = '<absolute office checkout>'
$prepRoot = '<absolute preparation worktree>'
$releaseImageEnv = '<private release-image.env>'
# Read-only configuration validation; do not print rendered secrets.
docker compose --project-directory $officeRoot --env-file "$officeRoot/.env" --env-file $releaseImageEnv -f "$officeRoot/docker-compose.yml" -f "$prepRoot/compose.office.release.yml" -p js-internetservices config --quiet
# ONLY after specific deployment approval and verified real backup/restore:
docker compose --project-directory $officeRoot --env-file "$officeRoot/.env" --env-file $releaseImageEnv -f "$officeRoot/docker-compose.yml" -f "$prepRoot/compose.office.release.yml" -p js-internetservices up -d --no-deps --no-build --pull never web
```

`--no-deps --no-build --pull never` prevents a DB restart, building dirty office
source or fetching a moving image tag. The override removes automatic migrations
from web startup. It preserves the existing office env/media/network/port and
worker count. Health/readiness, login, permissions, tenant boundaries, POS first
write/checkout/print, receipt PDF/totals and stock invariants must pass. Use
synthetic isolated fixtures for mutating tests; do not create test sales in office.
A later explicit decision governs transitioning the dirty checkout; no stash,
clean, hard reset or deletion is part of rollout.

## Rollback / abort

If the new web fails health or critical reads, use the same Compose command with
`rollback-image.env`, still `--no-deps --no-build --pull never web`. Keep the
existing database and media; do not reverse migrations or replay a backup over
new business writes. This release adds no schema or destructive data transition.
Verify the old image's reads/health and maintain the write freeze until resolved.
A database/media restore is disaster recovery requiring separate explicit
approval, a fresh incident backup and a data-loss decision for post-backup writes.

No packaging conversion, box/meter sale, barcode feature, data cleanup or historical
financial correction is included. No release tag is created before an actual
approved release. Private image IDs, local credentials, schema snapshots, failed
attempt logs and restore evidence stay outside published documentation.

## Executed preparation results

- Standalone Compose configuration validation and fresh Docker build: exit 0.
- Django `check`: no issues. `makemigrations --check --dry-run`: no changes.
- Fresh synthetic migration: exit 0. `migrate --plan`: no planned operations.
- Focused inventory/receipt suite: 100 passed in 45.406 seconds.
- Full Django suite, `--keepdb --noinput --parallel 4`: 798 passed in 142.977 seconds.
- Browser against the real built image on localhost: real login/CSRF, lazy first
  customer save, stock item add, desktop/mobile alignment, checkout print redirect,
  single receipt total and PDF all passed; no JavaScript errors.
- Synthetic receiving used the owning inventory service; checkout left quantity
  9 from 10 and exactly two stock movements (receive/sale).
- Binary-safe synthetic PostgreSQL dump restored to another database: all 72 table
  content hashes/counts and 68 sequence states matched. Synthetic media archive
  restored and its contents matched. Actual office data was not copied/restored.
- The exact currently running office image read the new synthetic invoice/receipt
  history and stock correctly under a READ ONLY transaction in staging.
- Future office Compose was rendered only: existing web environment, media bind
  and port were preserved; no live operation ran. Neither office container was
  restarted. The build image contains no local env, backup or Git artifacts.

Private evidence retains an early failed synthetic seed attempt: an unsupported
`organization` argument on PurchaseLine was corrected in the private seed helper;
the transaction rolled back, and subsequent seed/tests passed. No app code or test
assertion was changed to accommodate it. Production backup restoration and actual
rollout/rollback remain unexecuted and require separate release approval.
