# JBMS Multi-Tenant, RBAC, and Work Reports Audit

## Audit summary

JBMS is a modular Django monolith using server-rendered templates and the existing Tailwind design layer. Global Django users are linked to the concrete tenant model, `users.Organization`, through the authoritative `TenantMembership`. `users.Tenant` is a naming proxy; it does not introduce another table. The request tenant is resolved by `ActiveOrganizationMiddleware` from active server-side memberships or an explicit, audited Super Administrator support session. Browser-supplied tenant identifiers are not used as tenant context.

Legacy `Membership`, `UserAccessProfile`, and duplicate `organization` fields remain as compatibility bridges while authoritative access and scoping use `TenantMembership` and `tenant`. Removing those bridges should be a later, separately verified cleanup rather than part of this security change.

The existing apps already contain tenant-aware view and service work, own/assigned Sales document ownership, non-delegable financial permissions, append-only audit records, protected document lifecycles, and locked document sequences. The missing confirmed domain was Technician Work Reports. Before this implementation there was no report model, history, approval service, URL, template, queue, or role permission set.

## Tenant-owned model inventory

The current authoritative tenant field covers:

- Customers: `Customer`, `CustomerSite`, `InternetCustomer`, `CustomerDocument`.
- Service catalog: `Package`.
- Product catalog: `ProductCategory`, `Product`.
- Billing: `BillingDocument`, `BillingLineItem`, `DocumentSequence`, `Promotion`, `CustomerSubscription`, `BillingSheet`, `BillingItem`, `SubscriptionPeriod`.
- Inventory: `Supplier`, `SupplierPaymentRecord`, `Purchase`, `PurchaseLine`, `InventoryBalance`, `StockAdjustment`, `StockMovement`, `StockUnit`, `Cart`, `CartLine`, `CartSerialSelection`, `InventorySale`, `InventorySaleLine`, `DocumentSerialSelection`, `InventorySettings`, `ImportJob`, `HistoricalInventoryRecord`.
- Configuration and integration: `CustomFieldDefinition`, `CustomFieldValue`, `IntegrationConsumer`, `MessageTemplate`, `WhatsAppManualMessageLog`, `OrganizationBranding`.
- Security and history: `TenantMembership`, `TenantPermissionGrant`, `SupportAccessSession`, `AuditLog`.
- Technician workflow: `TechnicianWorkReport`, `TechnicianPaymentRecord`, `WorkReportHistory`.

`Organization` is the tenant itself. Global authentication users and platform constructs are intentionally not tenant-owned business rows.

## Minimal relationship design

Keep the single `Customer` table and its existing `customer_type` distinction. Internet customers may have `CustomerSite` rows, and sites may carry operational addressing and package relationships. Walk-in customers remain lightweight and do not require internet fields. `Package` and `Product` remain tenant catalogs rather than customer children. Subscriptions remain separate from customer identity, and `BillingDocument` remains the shared quotation/invoice/receipt system. A Work Report may optionally reference a tenant customer, but is an internal accountability record and never a billing or inventory line.

This preserves existing IDs, document history, customer relations, and workflows while avoiding duplicate customer or document models.

## Migration and backfill plan

The repository's existing migration chain follows the required staged approach: create a default organization, add transitional nullable tenant fields, atomically backfill legacy rows, establish memberships and Super Administrator access, add tenant-aware constraints/indexes, then make authoritative tenant fields non-null. Historical document numbers are preserved. Future document numbering uses transactionally locked `DocumentSequence` rows rather than record counts.

The Work Reports migration is additive and reversible: it creates only the two new tenant-owned tables and indexes. It does not mutate historical customer, inventory, subscription, or financial rows.

Before production deployment:

1. Back up PostgreSQL and rehearse the full migration chain on a restored copy.
2. Run `migrate --plan`, then `migrate` in a controlled window.
3. Verify no authoritative tenant foreign keys are null and no cross-tenant relations exist.
4. Compare document counts, IDs, numbers, totals, and inventory balances before and after.
5. Exercise support-context and role-specific smoke tests before reopening access.

## Final base-role matrix

| Area | Super Administrator | Administrator / Manager | Sales | Technician |
|---|---|---|---|---|
| Platform tenants/settings/audit | Platform context | No | No | No |
| Tenant data | Explicit audited support context only | All own-tenant operational data | Own/assigned permitted sales operations | No general business data |
| Tenant-wide finance, cost, margin, purchases, valuation, exports | Support context, audited | Yes | No | No |
| Quotations/invoices/receipts | Support context | Tenant-wide lifecycle authority | Own/assigned permitted workflow; no protected reversals | No |
| Team & Access | Platform-managed Super Admin controls | Own tenant; no self-elevation or Super Admin management | No | No |
| Work Reports | Support context, audited | View all tenant reports; approve/reject; controlled correction | No | Create/view/edit/submit own reports only |
| Agreed Technician amount | Support context, audited | Own tenant Work Reports only | No | Own Work Reports only |

Membership-level grants are restricted to the approved non-financial delegable set. Platform permissions, tenant-wide financial permissions, financial reversals, and Work Report management authority are not delegable to Sales or Technicians.

## Implementation phases and principal files

1. Preserve and verify existing tenancy/RBAC: `users/models.py`, `users/middleware.py`, `users/permissions.py`, app views/services/admin modules, and existing migrations.
2. Add Work Reports domain: `work_reports/models.py`, `policies.py`, `services.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`, and migration `0001`.
3. Add permission-aware UI: `templates/work_reports/*`, `templates/includes/app_sidebar.html`, Team & Access role copy, and approval-count context.
4. Verify security and lifecycle: `work_reports/tests.py` plus the existing app suites.

Primary risks are legacy compatibility fields, any unscoped code path added outside the standard managers, production migration rehearsal, and privileged Django admin behavior. Work Report admin access is read-only and empty without explicit support tenant context.

## Tailwind UI plan

Reuse the existing full-width `jims-workspace`, sidebar, `jims-btn` controls, form-field component, slate surfaces, status patterns, keyboard focus rings, and configured purple `#786EF9` primary. Orange `#FF9A0D` is reserved for restrained pending-attention indicators. The Work Reports pages use compact responsive tables, a mobile-safe status strip, one primary action per state, clear empty states, private-amount callouts, and a focused Technician navigation path. No new frontend framework or package is introduced.

## Technician payment acknowledgement extension

`TechnicianPaymentRecord` is a small internal accountability record owned by `work_reports`. It has an independent `AWAITING_CONFIRMATION -> CONFIRMED` or `AWAITING_CONFIRMATION -> DISPUTED` lifecycle. Corrections use an immutable `VOIDED -> replacement` chain. Payment status never changes `TechnicianWorkReport.status`, and recording or acknowledging an amount never changes the approved report amount.

Security invariants:

- Manager actions lock the tenant report and payment rows, recheck active tenant and Technician memberships, and require non-delegable `technician_payments.*` permissions.
- Technician querysets start with the active tenant and add `report__technician=request.membership`; only the owner can acknowledge or dispute an awaiting record.
- Browser input never controls tenant, report owner, amount snapshot, actor, lifecycle status, response fields, or void fields.
- A conditional database uniqueness constraint prevents more than one non-voided payment per report; the report row lock serializes normal concurrent recording attempts.
- Payment and history rows reject hard deletion and unrestricted queryset updates. Each transition writes both `WorkReportHistory` and `AuditLog` with payment snapshot and request metadata.
- Once any payment history exists, the approved agreed amount cannot be corrected. The payment snapshot therefore remains anchored to the approved amount seen at recording time.

Billing isolation:

- The workflow imports no billing, invoice, receipt, supplier-payment, inventory, accounting, bank, mobile-money, or gateway service.
- Payment method is a descriptive label only. No provider identity, account credential, callback, reconciliation, instruction, PDF, or money movement is created.
- Tests assert that `BillingDocument`, `SupplierPaymentRecord`, and `StockMovement` counts remain unchanged through record and acknowledgement transitions.
- The feature is exposed only under `/work-reports/`; customer pages, Sales-owned views, billing print templates, exports, finance reports, and public integration APIs are unchanged.

Migration `work_reports.0003` is additive: it creates the payment table and widens history event/status fields. Cross-row tenant and replacement consistency is enforced by model validation and locked lifecycle services because portable SQL check constraints cannot reference related rows.

### Bulk Technician payment batches

`TechnicianPaymentBatch` groups one or more approved, unpaid Work Reports for exactly one active Technician and one descriptive payment method. Each included report retains a `TechnicianPaymentRecord` allocation, so the existing active-payment uniqueness rule, agreed-amount snapshot, adjustment approval, and report history remain authoritative. Existing standalone records remain unchanged with `batch=NULL`.

Managers select only visible reports within one Technician group. The lifecycle service re-queries every submitted ID from the active tenant, locks reports and existing payments in deterministic order, rejects an invalid line without partial creation, recalculates both totals, and writes the batch, allocations, report histories, and batch audit in one transaction. Confirmation, dispute, and void apply to the complete batch; replacements must cover the same reports and link to one voided batch. Individual allocation endpoints do not permit partial acknowledgement or voiding.

Migration `work_reports.0006` is additive: it creates the batch table, adds the nullable protected allocation relationship, extends history event choices, and adds status/date indexes plus total, method-description, and lifecycle-field constraints. It does not backfill or rewrite standalone payment records.
