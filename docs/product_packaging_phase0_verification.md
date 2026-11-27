# Packaging Phase 0 verification ? 2026-11-05

Branch: `feature/packaging-phase0`, targeting `main` at
`81ec3f8ae924acd11bf6f335a628c2fc94add906`.
Worktree: sibling `jims-packaging-phase0-20261105`; office tracked files and
untracked user work were not edited, staged or committed.

## Change and limits

Architecture only: `docs/product_packaging_contract.md` defines the implementation
map, snapshots, units, lifecycle, payment timing, locking, activation,
reconciliation, precision, returns and phased acceptance gates. AGENTS, guardrails,
architecture and purchasing contract link the explicitly approved exception to
package deferral. Existing code, permissions, schema and transactions are unchanged.
No migration added/applied. No enabled packaging controls. No production rollout.
Phases 1?4 and the executable ten-box demonstration remain outstanding.

The independent deployment-preparation branch has two commits (`a1d1d75`,
`3f6c00a`) and open PR #4. Its isolated staging and image-only rollout proposal
were read separately, not merged, executed or included here. Open PRs #1 and #2
were also left unchanged. Main CI was green at run 41596992401.

## Runtime isolation

Built `jims-packaging-test:phase0` from the clean main source in the feature
worktree using the repository Dockerfile (Python 3.13 runtime). This phase changes
only Markdown, so the tested application source is exactly the main baseline.
The existing office Compose file hard-codes office container names and loads
`.env`; it was not executed. Equivalent Docker runtime used a separate network,
PostgreSQL 16 container with tmpfs storage, explicit synthetic-only environment,
no office mounts, no copied `.env`, no published database ports and no business
records. No production DATABASE_URL was inherited. Test databases and their four
parallel clones belong only to that isolated PostgreSQL container.

## Commands and exact results

Image build: `docker build -t jims-packaging-test:phase0 .`: exit 0.
The image retained the existing harmless Dockerfile FromAsCasing warning.
Django command prefix was `docker run --rm --network
jims-packaging-verification` with an explicit synthetic DATABASE_URL, DEBUG=1,
synthetic SECRET_KEY and test host allow-list, followed by
`jims-packaging-test:phase0 python manage.py`.

- `check`: exit 0; System check identified no issues (0 silenced).
- `makemigrations --check --dry-run`: exit 0; No changes detected.
- `test inventory.tests_purchase_contract inventory.tests_cart_checkout
  inventory.tests_sale_cost_guard billing.tests_receipt_totals
  products.test_migrations --keepdb --noinput`: exit 0; 35 tests OK,
  13.938 seconds. Includes existing PostgreSQL checkout concurrency coverage.
- `test --keepdb --noinput --parallel 4`: exit 0; 798 tests OK,
  139.883 seconds.
- `git diff --check`: exit 0.

Expected denied-request tracebacks and existing print-CSS warnings appeared in
regression output; there were no test failures or modified assertions.
Browser tests were not run: this phase changes no rendered UI or application
behavior. They are mandatory for later UI phases. The acceptance stock sequence
is a documented expected result, not a claimed executed feature demonstration.

## Security and compatibility review

Only six Markdown files are included. No credential, private business record,
media, database, generated asset or deployment configuration is included. No
permission or tenant behavior changed. Historical package fields remain
preservation-only. Later schema phases must retain activation gates until all
posting paths are safe; additive schema does not imply old code is write-safe
after packaging transactions exist. The complete feature is not production-ready.
