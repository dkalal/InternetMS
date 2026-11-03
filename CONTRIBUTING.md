# Development and release workflow

Read AGENTS.md, the project guardrails/rules and the relevant domain contract first.

- `main` is the tested, reviewed baseline. It is not automatically deployed.
- Start one `feature/*`, `fix/*` or `chore/*` branch from current `origin/main`.
- Keep application changes with their regression tests; separate unrelated changes.
- Open a PR to `main`. Keep it draft until required checks and review pass.
- Use a merge commit for history reconciliation; do not force-push published work.
- Create a release tag only when a separately approved release actually happens.

## Required validation

Use Python 3.13 and PostgreSQL 16 in an isolated environment, never the office
DATABASE_URL, containers, volumes or ports. The ordinary Compose web startup runs
migrations; do not start or rebuild it as a test shortcut. CI provisions its own
PostgreSQL service with synthetic data. Its exact commands are in
[the workflow](.github/workflows/django-ci.yml).

```text
python -m pip install -r requirements-test.txt
python -m playwright install --with-deps chromium
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --noinput
python manage.py test <affected modules> --keepdb --noinput
python manage.py test --keepdb --noinput --parallel 4
python manage.py test inventory.browser_cart_workspace inventory.browser_cart_checkout --keepdb --noinput
git diff --check
git diff --cached --check
```

Set `PLAYWRIGHT_BROWSER_CHANNEL` to an empty string for installed Chromium; the
optional local browser runners default to Edge. Browser dependencies are test-only.
Do not run the migration command until the database has been positively identified
as disposable. Tests exercise migration upgrades with synthetic historical rows.

## Git and private artifacts

Stage explicit reviewed paths. Review new files for credentials, office records,
local paths and unsafe execution. Keep local logs, maintenance plans, approvals,
exports, screenshots and backup data outside published source. Existing office
artifacts stay preserved locally; do not run blanket add, clean, hard reset or an
automatic stash. Use exact private `.git/info/exclude` entries if needed.

The targeted .gitattributes policy uses LF for portable source and CRLF for Windows
scripts. Do not run a repository-wide renormalization as part of feature work.
Never change global Git configuration to fix a repository-local issue.

## Deployment is a separate operation

A green merge does not authorize a deployment or changing the office checkout.
Before a release, obtain a private database/media backup and verify restoration,
review office migration state and the walk-in preflight, then approve the specific
release and rollback plan. Never edit an applied migration or infer historical
financial corrections. The dirty office checkout is intentionally retained until
that separate verified transition.

Repository administrators should require the `tests` status from Django CI on
`main`, require a PR and review, and prohibit force pushes/deletion. Verify the
actual GitHub settings; documentation alone does not enable branch protection.
