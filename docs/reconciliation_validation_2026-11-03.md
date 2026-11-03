# Reconciliation validation ? 2026-11-03

PR: https://github.com/dkalal/InternetMS/pull/3

The application implementation is saved in `c8b3493` (receipt) and `b63911a`
(workspace/checkout and malformed identifiers). Later commits add privacy,
workflow/CI and whitespace-only adjustments. The release gate is the latest
exact PR revision's GitHub Actions result, not any historical office log.

## Local isolated verification

The existing Compose-built application image runs Python 3.13. A dedicated
PostgreSQL 16 container/network with tmpfs data and distinct database names is
used, with no office volumes, `.env`, ports, database or media mounts. Static/media
storage is temporary. The application startup is overridden with a validation
runner; the live Compose web startup is never executed.

| Command | Result |
|---|---|
| `python manage.py collectstatic --noinput` | Exit 0 |
| `python manage.py check` | Exit 0; no issues |
| `python manage.py makemigrations --check --dry-run` | Exit 0; no changes |
| `python manage.py migrate --noinput` | Exit 0; fresh PostgreSQL schema |
| Focused command below | 100 tests; exit 0 |
| `python manage.py test --keepdb --noinput --parallel 4` | 798 tests in 140.903 seconds; exit 0 |
| Browser command below | 4 tests in 22.729 seconds; exit 0 |
| `git diff origin/main --check` | Exit 0 after two inherited EOF fixes |
| `git diff --cached --check` before every commit | Exit 0 |

```text
python manage.py test inventory.tests_cart_workspace inventory.tests_cart_lifecycle inventory.tests_cart_checkout inventory.tests_cart_line_merge inventory.tests_financial_controls inventory.tests_walk_in_identity billing.tests_receipt_totals --keepdb --noinput
python manage.py test inventory.browser_cart_workspace inventory.browser_cart_checkout --keepdb --noinput
```

The local browser run uses the installed Windows Edge/Playwright and Python 3.14
against another dedicated PostgreSQL 16 container on localhost port 55439. Core
Django gates use the supported Python 3.13 container; CI also runs Chromium on
Python 3.13. Browser checks cover first item/customer save, serial-picker cancel,
checkout, print redirect, payment-reference retention and desktop/mobile layout.
Receipt HTML/PDF totals, walk-in identity/address isolation, permissions, retries,
concurrency and synthetic migration upgrades are covered by the focused/full suite.

The malformed-ID test reproduced a Unicode picker server error before the fix.
Early runs failed on that defect, a temporary static-directory permission, missing
browser CDN styles, and the original CI synthetic-merge whitespace comparison.
Each was investigated; test assertions and domain security gates were retained.
The successful final runs above supersede those attempts. CI caches the UI script
for browser routing and checks whitespace against the actual PR/push base.

## Preservation and release limits

The office checkout remains at `ba89500` with its original dirty files. All 24
selected source/handoff paths are accounted for; intentional differences are in
[the reconciliation record](git_reconciliation_2026-11-03.md). No migration was
added/rewritten and no live database, deployment or cleanup command was executed.
The source backup is separate from a database/media backup. Existing historical
address values are redacted in the candidate but remain in already-published Git
history; any history-removal decision is outside this task's authorization.

Private manifests, original patches, bundle verification, source comparison and
logs stay outside published source. Draft PRs #1 and #2 and all old branches and
worktrees are retained. Production schema inspection, backup/restore and deployment
are intentionally deferred to a separately authorized release transition. Packaging
conversion, box/meter sales and scanning remain deferred.
