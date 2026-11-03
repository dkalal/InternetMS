# Customer/service UI switch — 2026-08-12

## Research findings

The additive domain migration was correct, but application presentation still exposed several legacy assumptions:

- customer detail counted active subscriptions as “active services” and rendered package cards independently of connection identity;
- customer and site edit forms could still synchronize package M2Ms and legacy subscriptions, bypassing the explicit package-change history operation;
- IP/VLAN appeared as customer or site configuration even after `InternetService` became canonical;
- no first-class page existed for an installed connection, its operational state, or its subscription history;
- package pages described customers as subscribers and did not identify the installed service using the package;
- package deletion did not provide an application-level protected-history explanation;
- the workspace KPI described active subscriptions as active services;
- manually created quotations/invoices could not capture optional single-site context, despite the additive schema;
- receipt context propagation existed in the service layer but was not surfaced consistently on document pages.

## Application switch implemented

### Customer workspace

- Customer detail now groups physical sites and their installed Internet services.
- Service count is derived from `InternetService`; disconnected services are excluded from the operational KPI.
- Each connection row exposes reference, operational status, IP/VLAN, current package, speed, and captured recurring price.
- Walk-in customer detail does not render Internet subscription workspace content.
- Legacy profile/package panels appear only when no explicit service topology exists.
- Customer list shows the primary service reference and operational status alongside its current package.
- Existing inactive sites remain visible for historical context.

### Safe edit boundaries

- Once a customer has explicit services, ordinary customer edit is account/profile-only: customer type, legacy network fields, legacy profile dates, and package selection are disabled.
- Site forms now edit physical site data only. Network identity and package agreement controls are no longer presented there.
- Existing compatibility columns and M2Ms remain in the database; this switch stops new UI writes from treating them as canonical.

### Explicit service workflows

- Added tenant/RBAC-safe create-service page with optional initial subscription.
- Added service detail page with technical context, current agreement, and immutable subscription history.
- Added explicit package-change confirmation showing current agreement and historical impact.
- Added reason-required block, unblock, and disconnect flows. These operations do not cancel subscriptions.
- Internet-customer creation now atomically creates a primary site/service topology. Optional selected packages create initial service subscriptions without replacing history.

### Package pages

- Package type is described as an offering classification, not a customer classification.
- Package list counts active service subscriptions for all authorized customer viewers; financial arrears remain finance-gated.
- Package detail shows the service reference and operational status for each historical/current subscription.
- Packages referenced by subscriptions or financial lines cannot be deleted through normal UI; they must be marked inactive.

### Financial documents

- Quotation/invoice creation accepts optional tenant/customer-validated site context.
- Quotation revisions preserve or deliberately update that site context.
- Subscription invoices continue to carry site/service/subscription context automatically.
- Receipts inherit site context from their invoice and cannot select an inconsistent customer/site.
- Historical lines, totals, numbers, dates, snapshots, and ownership were not rewritten.

## Compatibility and deferred contract work

- Integration customer API response fields remain unchanged during the compatibility window.
- Customer/site IP and VLAN columns, package M2Ms, and `InternetCustomer` fields remain stored for rollback compatibility.
- No automatic two-way signals were introduced.
- A later contract release may freeze/remove legacy fields only after API consumers, imports/exports, and production telemetry confirm no remaining writes.
- Dedicated immutable customer/address snapshot columns for financial documents remain deferred; existing issued-document line/price/tax/total immutability was preserved rather than retroactively guessed.

## Verification

- Django system checks pass with no issues.
- Migration drift check reports no pending model changes.
- The complete automated regression suite passes: 276 tests.
- Focused coverage includes walk-in and Internet customer creation, service retention on customer edit, tenant-scoped package/site choices, service package changes, status transitions, dynamic fields, document/receipt site propagation, protected package history, RBAC, and integration APIs.
- A post-switch domain audit reports no blocking ownership conflicts. Legacy compatibility records remain review-only until the later contract-release gate.
- Responsive templates were structurally reviewed at the required desktop, tablet, and mobile breakpoints. Automated browser screenshot tooling is not present in this repository/environment, so screenshot-based visual sign-off remains a deployment-stage check.
