# Portfolio readiness review — 2026-11-26

Scope: repository structure, current working changes, permission boundaries,
configuration, migrations, delivery documentation and executable regression gates.
This is a repository review, not a penetration test or a guarantee of security.

## Findings and actions

| Area | Evidence | Action / remaining work |
| --- | --- | --- |
| Architecture | Modular Django monolith; domain services, tenant middleware, permission codes | README now gives reviewers a synthetic demonstration path and links to contracts |
| Dependency security | Requirements pinned Django 5.2 without subsequent patches | Upgrade within the same LTS line to 5.2.19; verify on PostgreSQL |
| Delivery | No existing GitHub test workflow | Add Python 3.13 / PostgreSQL 16 CI with system, migration, full-suite and JS gates |
| Contributor experience | Architecture and contracts exist, contribution/security guides absent | Add CONTRIBUTING.md and SECURITY.md |
| Data hygiene | Local test logs, handover plans, screenshots and exports are untracked | Ignore local evidence and exclude live maintenance artifacts from commits |
| Historical privacy | Tracked domain-ownership audit JSON and operational runbook entries exist | Owner must review full history before making repository public; no history rewrite performed |
| Secrets | Environment example uses placeholders; payout encryption has a separate key ring and fails closed | Preserve secret-manager keys and backup rotation procedures; pattern scans are not proof of absence |
| Deployment | Compose has development passwords/debug and exposed ports; filesystem media | Development only; production needs independent configuration, durable media, deployment checks and restore rehearsal |
| Licensing | No explicit redistribution license | Owner must choose licensing and confirm rights before public redistribution |
| Presentation | Existing README documents setup and architecture | Add synthetic screenshots, approved demo hosting and an authored case study as follow-up |

The Django version decision follows the
[official November security release](https://www.djangoproject.com/weblog/2026/nov/03/security-releases/).
The package index was checked for 5.2.19 availability. Other dependencies still
need a dedicated advisory scan; this review does not claim that all pins are safe.

## Existing application changes being delivered

The working tree contains document-flow/cash-received improvements, customer
reporting and receivables permissions, POS/purchase usability, technician report
archival and encrypted payout destinations. New migrations are additive:
`billing.0034`, `work_reports.0009` and `work_reports.0010`. They were inspected,
not applied to a live database. Reverse migration can remove newly collected
data; retain backups and encryption keys and rehearse before production rollout.
The owning contracts describe the business and payout rollback boundaries.

## Verification

Current-source Docker verification uses a source mount, container-local static
files and separately named disposable PostgreSQL test databases. Commands run:
`check`, `makemigrations --check --dry-run`, focused Work Report/document-flow/POS
tests, full suite with `--keepdb --parallel 4`, and Node serial/purchase tests.
Final results are recorded in the delivery message; CI must also run after push.

Initial release blockers: both the baseline and Django 5.2.19 sequential suites
ran 875 tests and failed 9 receipt regressions. Eight involve the receipt label /
breakdown contract (including PDF); one expects a foreign-owner request to return
404 but receives 200. The changed receivables policy must be reconciled with that
ownership test before classifying this as either intended access or disclosure.
Do not weaken the assertion to pass the gate. System and migration-drift checks
passed, and all eight Node tests passed. The initial parallel full run failed to
serialize a traceback; sequential runs provide the accepted failure evidence.
Follow-up repair restores the explicit gross-subtotal label and installment
explanation in the shared print/PDF template. The access fixture now distinguishes
another Sales member's invoice (denied) from a Manager-issued invoice (allowed by
the existing architecture collections contract). Cross-tenant and foreign-owner
404 assertions remain; a separate Manager-receipt 200 regression was added.
No permission implementation was loosened. All 24 focused receipt/RBAC tests and
all eight JavaScript tests pass. The final sequential PostgreSQL suite under
Django 5.2.19 passed all **876 tests in 506.472 seconds**, exit code 0. System
check reported no issues and migration-drift validation detected no changes.
Focused receipt/RBAC tests passed **24 tests in 11.250 seconds**. Source was
mounted read-only; static files were built inside the disposable container.
The dependency patch was installed only in that container for verification;
the deployed image still requires a normal rebuild and controlled rollout.

No production deployment, repository visibility change, license grant, live data
cleanup or history rewrite is included. Public portfolio readiness remains
conditional on historical privacy/ownership review and production/dependency
checks. A feature-branch push is not a release certification.
