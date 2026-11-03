# Paid service coverage resolution — 2026-08-13

## Decision

Keep commercial agreement dates and paid service coverage as separate concepts.

- `CustomerSubscription.start_date/end_date` describes how long the package agreement exists.
- `SubscriptionPeriod.period_start/period_end` describes the exact service time being billed.
- A period becomes confirmed paid coverage only when its linked invoice is fully settled.
- `CustomerSubscription.paid_through_date` remains a denormalized renewal cursor; paid periods are the auditable source of truth.

Renaming agreement dates to payment dates would be incorrect: an agreement can be open-ended while the customer is only paid for a finite period. Invoice and receipt dates also cannot determine service coverage because a customer may pay early, late, partially, or for several months at once.

## Existing behavior confirmed

- Renewal already accepts 1–24 months.
- The backend calculates `period_end` from the normalized first day of `period_start`, paid months, and any complimentary months.
- The invoice captures the period quantity and recurring package price.
- Partial receipts leave the invoice partially paid and do not mark the subscription period paid.
- Full settlement marks the period paid, attaches the receipt, and advances `paid_through_date`.
- Reissued subscription invoices retain and update the linked period rather than creating untraceable coverage.
- Subscription invoices must be voided through the period-aware Resolve issue workflow; generic invoice voiding is rejected so invoice and coverage state cannot diverge.
- A historical period left invoiced against a void invoice may be retried only when it has no receipt history. The same period row is reopened under lock, the void invoice remains immutable, and the replacement invoice links back to it with an audited recovery event.

## Improvements implemented

- Added a shared paid-coverage resolver based exclusively on paid `SubscriptionPeriod` rows.
- Adjacent/overlapping paid periods are presented as one continuous window; genuine gaps remain separate and visible.
- Effective coverage is clipped to the subscription agreement boundaries, so a calendar-month invoice never claims service before the agreement started or after it ended.
- Service-level coverage spans immutable package-agreement history, so a package change does not erase earlier paid service time.
- Customer edit, customer detail, Internet service detail, renewal, invoice detail, payment registration, and recent billing tables now show explicit coverage ranges.
- Renewal terminology now uses `Coverage starts` and `Months to cover`.
- Payment registration explains that partial payment does not grant partial months automatically.
- New billing periods are rejected when they overlap any non-cancelled period, preventing accidental double billing.
- A data migration advances stale `paid_through_date` summaries from authoritative paid periods without reducing or guessing legacy values.

## Current-data audit

- Subscriptions: 32
- Paid periods: 64
- Continuous paid-coverage windows: 28
- Overlapping non-cancelled periods: 0
- Coverage gaps: 0
- Stale paid-through summaries before reconciliation: 1
- Stale paid-through summaries after reconciliation: 0
- Customer 29 confirmed paid service coverage: 11 June 2026 through 31 July 2026. Its accounting period begins 1 June, but the display does not claim service before the agreement began.

## Integrity rules

- Invoice issuance alone does not grant paid coverage.
- Partial payments do not grant prorated days or months.
- Full settlement confirms the whole explicit billing period.
- Promotions with free months extend the coverage end while retaining paid/free month values for audit.
- Cancelled periods do not block a corrected replacement period.
- Financial documents, receipts, prices, tax totals, tenant ownership, and historical package agreements remain unchanged.

## Verification

- Full Django regression suite: 285 tests passed.
- Focused billing and customer coverage suite: 71 tests passed.
- Clean transaction/backfill suite: 4 tests passed.
- Django system checks and migration drift checks pass.
- Production-data audit after migration reports zero paid-through mismatches.
