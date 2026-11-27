# Local office packaging integration - 2026-11-27

Purpose: make the verified packaging feature available together with the newer
local office sales/work-report features, without discarding user work or treating
an office PostgreSQL database containing samples as a disposable database.

## Source boundaries

Integration branch: `integration/local-packaging-20261127`, based on local source
`f91bf4e`, merging reviewed packaging main `cb3dfc3`. Work is performed in a
separate worktree. The office checkout remains on its existing branch until a
verified release transition. Its modified handover runbook and untracked private
handover documents are preserved and excluded from this integration's commits.

Conflict reconciliation retains cash tender/change, immutable receipt snapshots,
serial-line identity and tests, purchase base-unit selling-price edits, compact
customer UI and assignment/payout features. It also retains packaging selection,
base/commercial snapshots, allocation review, idempotent stock posting and
permission checks. Purchase mutations remain in the owning draft service.
The purchase selling-price field explicitly refers to the base unit; package
prices remain independent in product packaging configuration.

Privacy-redacted published handover descriptions are retained. Docker context
excludes media, backup/rehearsal output, dumps and environment files. No private
backup, office data, credentials or test logs belong in Git or PRs.

## Migration compatibility

`billing.0038_merge_20261127_1536` has no operations. It joins the independently
existing cash-received migration branch with the packaging migration branch.
No applied migration is edited. On the restored office schema the forward plan
contains 15 packaging/constraint/merge migrations. Packaging defaults off, old
commercial snapshots remain NULL, and migrations infer no physical containers
or multiply any existing stock. Populated rollback must retain feature-aware
code and protected provenance, or follow a separately reviewed restore plan.

## Verification so far

- System check: no issues; migration drift: no changes.
- Purchasing/serial JavaScript regressions: nine passed.
- Initial host focused run: 68 tests, one failure and 18 errors. Missing static
  manifest and unavailable native PDF libraries prevented valid UI/PDF evidence.
  Assertions were retained. Static assets were collected only in the isolated
  worktree; native PDF verification belongs to the configured Linux runtime.
- Subsequent focused host domain run: 54 passed in 47.771s.
- Actual Edge browser integration suite: ten passed in 40.926s.
- Read-only consistent snapshot backup was encrypted outside all worktrees and
  access-restricted. The recovery key is Windows-profile-bound via DPAPI.
  This rehearsal backup includes the database only, not media or credentials,
  and is not an off-machine disaster-recovery or final release-window backup.
- Restore verification matched all 75 original table fingerprints to the source
  snapshot. Fifteen migrations then passed on that isolated restore. Every
  pre-existing row/field remained unchanged; only appended Django migration,
  content-type and permission metadata was permitted. Original business table
  counts were required to remain exact.
- Restored-data unit preflight: zero drift lines/products/open invoice lines.
- Final pinned Python 3.13 / Django 5.2.19 / PostgreSQL verification passed:
  68 focused domain/receipt/PDF tests in 24.745s and 993 full tests in 241.472s.
  System check and migration-drift validation passed in the same runtime.
- A second encrypted rehearsal snapshot also includes media, effective recovery
  configuration and existing local working files. Its database restore matched
  all 75 original table fingerprints; included private file bytes were recovered
  with matching checksums. The 15 migrations then passed in the pinned runtime,
  preserving every original business row/field and prior framework records.
  Only appended migration/content-type/permission metadata was allowed.
- The only local dependency mismatch is Django 5.2 versus the tested 5.2.19 pin.
  The update wheel is prepared separately; no office environment package is
  changed during preflight. Final installation belongs to the controlled cutover.
- The local server is still running the original office source and schema.
  Concurrent-writer/quiescence confirmation remains required before any cutover.

Detailed backup locations, fingerprints, logs and restore details are private.
No office migrations, activation, stock changes or runtime cutover occurred
while preparing this document.

## Release gate

Before any office write: finish final synthetic verification; preserve local
source and dependency state; positively identify all writers and obtain a
quiescent release window; take a fresh access-restricted encrypted database and
media/configuration backup with verified recovery; inspect the exact migration
plan and rollback compatibility; approve the concrete source/runtime transition.
Never activate or reinterpret real stocked products automatically. No active
server process may autoreload halfway through a source/schema transition.

## CI database teardown correction

The first integration CI run passed all 993 assertions but failed while dropping
the sequential test database: worker threads left healthy persistent Django
connections open. Every concurrency worker now closes its own connections in
its finally block using `connections.close_all()`. Initial stale-connection
cleanup and all business assertions remain unchanged; no workflow gate was
skipped or weakened. The focused 22 concurrency tests passed in 11.911s with
normal test-database destruction (without `--keepdb`).

The operator confirmed that they are the only current local runtime user.
This permits planning the short maintenance window; a fresh quiesced backup
and verified recovery remain mandatory before changing the office database.
