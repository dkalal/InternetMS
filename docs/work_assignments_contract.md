# Work Assignments contract

## Baseline and ownership

The 2026-11-05 audit found Work Reports in `work_reports`, with lifecycle services
in `services.py`, tenant policies in `policies.py`, report/date forms, approval
views, immutable `WorkReportHistory`, and independent payment/batch services.
`users.permissions` owns non-delegable role defaults. Middleware establishes
tenant/support context. No internal notification model exists in this module;
use scoped pending counters in its context processor and existing sidebar.
Existing actual work dates reject future dates; planned dates may be future.

Keep assignments in this app, with dedicated assignment services/forms/views.
Do not change billing, inventory or external messaging. Preserve existing routes
and independent reports. Existing approved corrections require a reason and
cannot alter amounts after any payment history.

## State and accepted terms

One assignment has one active tenant Technician. DRAFT -> OFFERED on send;
OFFERED -> ACCEPTED, DECLINED or CHANGES_REQUESTED by its owner. Managers may
revise DRAFT/OFFERED/CHANGES_REQUESTED/DECLINED/ACCEPTED into DRAFT and resend;
every send increments revision. Revision is a concurrency token on all actions.
Cancellation with reason is allowed before any linked report, from any state
except CANCELLED/COMPLETED. Reassignment requires a reason and returns to DRAFT.
Cancelled/completed records cannot be reopened. Invalid transitions fail closed.

Every lifecycle operation locks the assignment within a transaction. History is
append-only, with actor, tenant, event, prior/result state, revision, reason and
public terms snapshot. Management notes are deliberately excluded from snapshots.
The new-draft form carries a signed tenant/creator-bound request token with a
24-hour validity. A nullable unique UUID key and a creator-membership row lock
make repeat/concurrent POSTs return the same assignment. NULL preserves records
created before this additive retry-protection migration; independent service
calls without a request token intentionally represent separate drafts.
Accepted terms and revision remain recorded when a replacement offer is drafted;
only acceptance of the current offered revision activates its new agreement.
Technician suggestions are history payloads, never applied directly to the offer.
Any edit of sent terms requires resend and renewed acceptance.

## Authorization and privacy

Non-delegable stable permissions cover manage/view-all and own view/respond/
create-report. Managers act within their tenant; Technicians only on their own
sent assignments. Sales has no access. Super Administrator read access requires
the existing support-session middleware; assignment management is reserved to
tenant Managers. Recheck current database membership, user and tenant activity
at each mutation, and validate customer and assignee tenancy server-side.
Never render management notes or arbitrary internal snapshot data to Technicians.

## Reports and financial integrity

An optional PROTECT OneToOne assignment link enforces at most one report.
Creation locks the accepted assignment, verifies its accepted revision/owner,
uses stored accepted payment, and delegates report/date creation to the existing
service. Duplicate requests return the same report. Actual dates and work
description remain Technician input; planned scope is displayed separately.
Once linked, block assignment edits, reassignment and cancellation. Corrections
use the existing report lifecycle and cannot change linked accepted payment.
Approval completes the assignment in the same transaction; acceptance and report
submission never authorize payment. Payment services remain authoritative.

## Removing reports that need correction

A Technician may remove only their own `REJECTED` (Needs correction) report,
using the existing `TECHNICIAN_WORK_REPORTS_UPDATE_OWN` permission. Confirmation
is a CSRF-protected POST. This is an archive/soft-delete: `archived_at` and
`archived_by` are recorded, an immutable `ARCHIVED` history event and audit log
entry are written atomically, and the report is excluded from ordinary report
lists, detail, editing, submission, summaries and exports. The report, work-date
rows, correction reason and earlier history remain stored. Hard deletion remains
prohibited. Reports linked to assignments cannot be removed, preserving the
one-report-per-assignment and retry behavior. Draft, submitted and approved
reports cannot be removed; the service also fails closed if any payment record
is attached. The confirmation screen explains retention and the absence of a
self-service restore action.

Migration 0009 additively adds nullable archive metadata and the new history
event choice; it does not rewrite existing report rows. Rolling back removes the
archive metadata, so archived reports would reappear in ordinary lists. Before
rollback, export or review archived IDs, restore their visibility intentionally,
and preserve their history/audit evidence; never roll back this migration as a
routine release step.

The Work Reports workspace financial summary uses the complete authorized and
filtered queryset before pagination. Work-date ranges match authoritative
service-day rows, with the legacy report date used only where no service-day
rows exist. The summary distinguishes total agreed amounts, approved amounts,
confirmed payments, and approved amounts less confirmed payments. These values
are Technician accountability figures, not payroll, billing, or accounting
balances. Technicians see only totals for their own authorized reports and only
when they have own-payment visibility; members with Work Report management
access see tenant-wide figures. Payment-status filtering for Technicians is
likewise limited to their own reports. Filtered XLSX export additionally
requires `reports.export`. Export
uses the same server-side filtered queryset across all pages, records its filter
context, uses private no-store caching, and stores untrusted text as literal
spreadsheet strings. No new persisted financial state or migration is needed.

The design follows Django's queryset aggregation and pagination model and
OWASP's spreadsheet export guidance: calculate totals over the authorized
filtered result set and defend exported user-controlled text against formula
interpretation. References: [Django aggregation](https://docs.djangoproject.com/en/5.2/topics/db/aggregation/),
[Django pagination](https://docs.djangoproject.com/en/5.2/topics/pagination/),
[OWASP CSV Injection](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/07-Injection/21-CSV_Injection/).

## Migration and verification

Migration 0007 adds assignment/history tables and a nullable unique report FK;
0008 adds the nullable unique draft request key. No historical
rows are rewritten; existing reports retain NULL. Validate forward migration on
a disposable PostgreSQL database. Reversal removes new data: archive assignments
and linked histories first; never reverse on the office database during this work.

Run baseline Work Report tests, focused security/lifecycle/transaction tests,
existing report/payment regressions, system checks, migration drift and full
suite. Use a separate PostgreSQL test database through Docker Compose with current
source mounted read-only. Browser smoke uses synthetic data only, desktop and
390px mobile. Never claim unavailable verification passed. No deployment or live
database migration is authorized.

## Technician payout destination accounts

Technicians may manage only their own tenant-scoped destination bank accounts
from `work_reports:technician_profile`. The stable
`technician_payout_accounts.manage_own` permission is in the Technician baseline;
the endpoint and lifecycle service both verify active membership, role, tenant,
and ownership. There is no Manager CRUD endpoint for a Technician's account.
Technicians may add an account, choose one active default, or deactivate an old
account. Deactivation is soft and retained because payment history may reference
that destination. A payment may reference only an active account owned by the
Work Report's Technician in the same tenant. Bank-transfer recording requires a
selected account; other methods cannot attach one. The manager's choice is
revalidated under row locks by the payment service, for both single and batch
records.

Only the bank name and last four digits are copied into immutable payment and
batch snapshots. The complete account number is collected twice, normalized,
encrypted using a separately configured Fernet key ring before database write,
and never returned by any application page or log. The owner sees bank and masked
digits; authorized payment readers see those same masked destination details.
The app does not collect account-holder name because it is not needed to record
the destination, does not validate bank ownership, and does not initiate or
confirm transfers. Bank details remain self-declared.

Set `TECHNICIAN_PAYOUT_ACCOUNT_ENCRYPTION_KEYS` in the deployment secret manager
before enabling account entry. Generate a Fernet key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
The first comma-separated key encrypts new records; subsequent keys allow reads
during rotation. This ring is independent of `DJANGO_SECRET_KEY`, has no source
code or development fallback, and missing/invalid keys fail closed. Keep old
keys available until ciphertext rotation and backup-retention requirements are
complete. `rotate_technician_payout_account_keys` is a dry run by default;
`--apply` rewrites existing ciphertext under the primary key. The command reports
counts only and never emits account data.

The additive payout-account migration introduces a nullable account FK and masked
snapshots on payment records/batches, then adds the tenant-owned account table and
constraints. Existing payment rows remain NULL and keep their existing history;
there is no guessed account or historical backfill. Rolling back removes account
records and payout references; retain an encrypted export and the corresponding
key ring before rollback if payout destinations must be preserved. A live
migration/deployment requires the separately configured key secret and a tested
backup/restore path. No money movement, banking integration, or payroll balance
is introduced.
