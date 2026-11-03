# Customer, service, subscription, and document domain map

Status: Discovery, audit, additive expansion, deterministic backfill, and compatibility service layer completed on 2026-08-12. Legacy fields remain; contract/removal is deliberately deferred.

## Current ownership discovered

| Concept | Current owner and behavior | Transition risk |
|---|---|---|
| Customer location/address | `Customer.location` is required and `Customer.address` is optional. Both are account/contact fields, search fields, and the source for a generated primary site. | They are also copied to `CustomerSite`, so later edits can disagree. |
| IP address/VLAN | Nullable fields exist on both `Customer` and `CustomerSite`. Customer create/edit still writes the customer fields; primary-site creation copies them once. | Neither copy is consistently canonical and there is no connection record to own them. |
| Customer site/office | `CustomerSite` is tenant-owned, has location/address/network fields, an `is_primary` conditional unique constraint, active state, notes, and package M2M. Normal site forms support multiple sites. | The original backfill created a site for every customer, including walk-ins. `customer` uses `CASCADE`, which is unsuitable as the eventual normal archival policy for referenced sites. |
| Package type | `Package.package_type` classifies Indoor/Outdoor packages. `InternetCustomer.package_type` independently stores the same choice shape as a customer-level connection classification. | No filter or validation connects these values. A customer can therefore disagree with its selected package or need different types at different sites. |
| Selected package | Duplicate compatibility M2Ms exist on `Customer.packages` and `CustomerSite.packages`. Customer/site services synchronize these to active `CustomerSubscription` records. | Assignment changes can cancel active subscriptions without creating an explicit replacement history operation. M2M disagreement is possible. |
| Legacy Internet profile | One-to-one `InternetCustomer` owns customer-level package type plus optional start/end dates. It is created for Internet customers and deleted when a customer becomes walk-in/random. | Its connection identity and dates overlap package/subscription concepts and detail views currently prefer profile values. |
| Subscription | `CustomerSubscription` owns customer, nullable site, package, status, start/end dates, billing day, signup price, paid-through date, promotion, and tenant. | The active uniqueness constraint is `(tenant, customer, site, package)`, allowing multiple active packages at one site without a way to identify their installed connections. |
| Subscription periods | `SubscriptionPeriod` owns renewal/billing periods and links generated invoices and final receipts. | This is distinct from subscription agreement dates and must remain historical. |
| Legacy service status | Original customer values `using`, `pending`, and `blocked` were explicitly migrated to customer account statuses `active`, `inactive`, and `suspended` in migration `customers.0010`. | No current operational connection-status field remains. That old mapping is documented history, not a safe mapping for a new service status without product approval. |
| Quotations/invoices | `BillingDocument.customer` is required and protected. A single model represents quotations, invoices, credit notes, and receipts. Issued invoices, quotation versions, numbers, totals, and responsible memberships have immutability guards. | Documents have no optional site context. Customer identity/address/tax snapshots are not stored as dedicated immutable snapshot columns. |
| Invoice lines | `BillingLineItem` optionally references product/package and stores description, unit snapshot, prices, discounts, tax inputs, and totals. | It has no service/subscription source reference. Existing description and price fields are the historical commercial snapshot and must not be rewritten. |
| Receipts | A receipt is a `BillingDocument` with an optional protected `invoice` FK. The billing service copies customer ownership from the invoice and inventory completion references both invoice and receipt. | The model permits a receipt without an invoice, so existing data must be audited before tightening ownership. |
| APIs | Integration customer APIs expose customer identity/contact fields only. Inventory invoice APIs use existing billing services and customer ownership. | Compatibility fields must remain available throughout a later switch phase. |
| Dynamic fields | Custom-field values are tenant-scoped and target customer/package records independently. | No connection or site custom-field migration should be inferred. |
| Tenant/RBAC/audit | Tenant-aware managers use request context; domain services also validate submitted relations. `AuditLog` is tenant-owned and append-only. | Migrations and diagnostics must use unscoped/base managers deliberately. The diagnostic must never create an audit row because it is read-only. |

## Canonical ownership selected for the expanded domain

| Data | Canonical owner after switch | Compatibility policy |
|---|---|---|
| Account/legal identity, general contact, TIN/VRN, pricing preference, account status | `Customer` | Existing fields and APIs remain. |
| Physical office/home location, service address, site contact, primary/archive state | `CustomerSite` | Legacy customer location/address remain during compatibility. Conflicts are reported, never silently overwritten. |
| IP, VLAN, installed/disconnected timestamps, technical notes, operational status | `InternetService` | Customer/site network fields remain for compatibility. Deterministic primary-service accessors are additive; no legacy value was deleted. |
| Package category/type | `Package` | `InternetCustomer.package_type` remains but is deprecated as an independent source. Conflicts require reconciliation. |
| Commercial package, agreement dates, agreed price, commercial status | `CustomerSubscription` | Legacy profile dates remain; they become compatibility accessors only after conflicts are resolved. |
| Renewal/payment period | `SubscriptionPeriod` | No semantic change. |
| Financial account owner | `BillingDocument.customer` | No change. |
| Single-site document context | Nullable `BillingDocument.site` | Never required because documents may span sites. New subscription invoices set it; historical documents were not guessed. |
| Line source context | Nullable `BillingLineItem.internet_service` / `subscription` | New subscription invoice lines set both. Ordinary and historical lines remain valid without either reference; snapshots remain immutable. |
| Receipt context | Source invoice and its lines/context | No independently editable site/service values. |

## Expand–migrate–switch–contract outcome

1. The preflight audit reported zero blocking findings; R1–R5 were accepted explicitly.
2. `InternetService` and nullable document/line/subscription context were added without removing legacy columns.
3. The restartable backfill created 31 services and linked all 32 subscriptions in place. Customer 30 was skipped because neither profile nor network data proved a service.
4. Every pre/post fingerprint for customers, sites, packages, subscriptions, documents, lines, periods, and products matched exactly.
5. Atomic, tenant-scoped, permission-checked commands now cover site/service creation, initial subscription assignment, package replacement, block, unblock, and disconnect. No bidirectional signal was introduced.
6. Package change closes the old agreement and creates a new one; it never rewrites old package, price, dates, periods, invoices, or receipts.
7. One active subscription per service and valid date order are database-enforced. Legacy site/package uniqueness applies only to rows without service context.
8. Legacy field removal remains a separate contract release requiring backup and rollback approval.

## Rollback policy for these phases

- Application rollback means deploy the previous code while retaining additive tables and references. Do not reverse the backfill or delete migrated service data.
- Migration `0029` intentionally has a no-op reverse because removing established service identity would be destructive.
- Existing legacy fields remain the operational fallback until the switch phase is explicitly accepted.
