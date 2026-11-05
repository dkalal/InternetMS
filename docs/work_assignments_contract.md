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
