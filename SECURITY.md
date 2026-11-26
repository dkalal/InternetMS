# Security policy

Security fixes target the current maintained branch. This repository does not
promise support for historical releases or certify production security.

Report suspected vulnerabilities privately to the repository owner through
GitHub private vulnerability reporting when available, or an existing private
contact channel. Do not put credentials, customer data or exploitable details in
a public issue. Include affected revision, prerequisites, reproduction with
synthetic data and expected impact.

Keep secrets in environment configuration or a hosting secret manager. Payout
account encryption keys are separate from Django's signing key. Preserve keys
needed to restore encrypted backups. If a secret is exposed, revoke or rotate it
first; deleting the current file does not remove it from Git history.

Production release checks include PostgreSQL regression tests, dependency and
secret scanning, `manage.py check --deploy` with real production settings,
backup/restore rehearsal and review of tenant and financial authorization.
Development Compose defaults are local conveniences, not production settings.
