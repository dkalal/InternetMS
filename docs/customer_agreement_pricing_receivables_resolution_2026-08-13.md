# Customer agreement, pricing tiers, and receivables resolution — 2026-08-13

## Findings

- The customer edit summary read dates from the legacy `InternetCustomer` profile even when an explicit `InternetService` and active `CustomerSubscription` existed.
- Customer 29 has an active Professional subscription starting 11 June 2026 with no scheduled agreement end. Its billing coverage is separate: paid through 31 July 2026, with later invoiced periods.
- Corporate and VIP were duplicate customer labels. Both resolved to the Wholesale catalog category in billing, POS, and browser previews; neither had distinct pricing behavior.
- The customer list's `Receivable` value was scoped to the selected billing month, while `All open receivables` summed invoiced and overdue subscription periods across every month. The calculations were correct but the presentation did not state the scopes clearly.

## Resolution

- Existing-service edit summaries now use the canonical active subscription and Internet service, displaying package, service reference/status, captured recurring charge, and agreement dates.
- `Open-ended` was replaced with `No scheduled end`, and agreement dates are explicitly distinguished from paid-through and invoice coverage.
- Corporate and VIP choices and resolver branches were removed. A data migration maps any legacy values to Wholesale, preserving the same effective catalog pricing.
- A database check constraint now permits only Standard, Technician, or Wholesale pricing tiers.
- Receivable context variables and labels now distinguish `Open for selected month` from `Total open · all months`, with an explanatory definition.

## Verification

- Current customer 29 agreement: Professional, starts 11 June 2026, no scheduled end; paid through 31 July 2026.
- Current pricing-tier records: 34 Standard; no Corporate or VIP records existed before migration.
- Current August 2026 open receivables: TZS 595,000 across 6 periods.
- Current total open receivables: TZS 695,000 across 8 periods.
- Full Django regression suite: 280 tests passed.
- Post-constraint focused suite: 18 tests passed.
- Django system checks and migration drift checks pass.
