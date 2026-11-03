# Domain reconciliation gate — 2026-08-12

The read-only audit completed successfully. It found no tenant, financial-document, date, duplicate-active-relationship, site ownership, or subscription-period integrity violations. It did find legacy policy questions that must be approved before an `InternetService` backfill is allowed to write data.

Machine-readable evidence: `docs/domain_ownership_audit_2026-08-12.json`.

## Before counts

| Record type | Count |
|---|---:|
| Customers | 34 |
| Internet customers | 31 |
| Walk-in/random customers | 3 |
| Customer sites | 34 |
| Internet profiles | 30 |
| Packages | 6 |
| Subscriptions | 32 |
| Active subscriptions | 28 |
| Billing documents | 194 |
| Billing lines | 229 |
| Subscription periods | 72 |

No after counts exist yet because no schema or production data mutation has been performed.

## Confirmed clean areas

- No tenant/organization relationship mismatches were detected.
- No subscriptions without a site were detected.
- No site has multiple active subscriptions requiring an uncertain connection split.
- No duplicate active tenant/customer/site/package relationship was detected.
- No invalid subscription or legacy-profile date range was detected.
- No legacy-profile/subscription date disagreement was detected where one subscription could be matched deterministically.
- No customer/primary-site IP or VLAN conflict was detected.
- No customer/primary-site package-assignment conflict was detected.
- No duplicate cross-customer IP or VLAN value was detected.
- No invalid financial-document customer ownership was detected.
- No receipt without an invoice was detected.
- No subscription-period invoice/receipt ownership mismatch was detected.

## Reconciliation decisions required

### R1 — Internet customers with no subscription history

Customer IDs: `1`, `21`, `23`, `30`.

Decision required: should backfill create an `InternetService` at each primary site with no subscription, or should these records remain account/site-only pending manual activation?

Recommended safe policy: create a service only when network data or an explicit Internet profile proves an installed connection; leave its operational status `unknown` and create no subscription.

### R2 — Multiple subscription history across two sites

Customer ID: `5`.

- Main Office/site `26`: subscriptions `3`, `4`, `31` cancelled; subscription `32` active.
- Sub Office/site `31`: subscription `29` cancelled; subscription `30` active.
- Both active subscriptions currently use Package `2` and have preserved independent signup dates/prices.

Decision required: confirm that all subscription history at one site represents one installed connection lineage, producing two services total—one for each site.

Recommended safe policy: group legacy subscription history by existing site because there is no overlapping active subscription at either site. Preserve every subscription ID and link each row to its site’s service.

### R3 — Customer and primary-site address disagreement

Customer ID `5`, primary site ID `26`:

- Customer address: `KCC Floor No: 2 & 3`
- Site address: `KCC Floor No: 2`

Decision required: confirm that the site address is canonical for service location and the customer address remains general account/billing contact data.

Recommended safe policy: preserve both values; copy neither over the other. Future service context uses the site address.

### R4 — Empty legacy connection classification

Twenty-five `InternetCustomer.package_type` values are empty. Their packages remain classified independently, mostly as Indoor.

Decision required: confirm that empty profile values remain untouched and that `Package.package_type` is canonical.

Recommended safe policy: do not infer or write profile values. New services do not own package type; their subscriptions resolve it through Package.

### R5 — Walk-in customers with generated sites

Customer IDs `31` and `34` have primary sites created by the historical universal-site backfill, but no Internet profile or subscription.

Decision required: retain these harmless legacy sites, archive them, or remove them later after reference checks.

Recommended safe policy: retain them unchanged during expand/backfill and create no `InternetService`. A later reviewed cleanup may archive unused sites; never delete them as part of this migration.

## Gate status

R1–R5 were accepted and implemented on 2026-08-12. The additive migrations created 31 services, linked all 32 subscriptions, and correctly left customer 30 without a service because no profile/network proof existed. The post-backfill audit has zero blocking ambiguities; see `domain_ownership_audit_post_backfill_2026-08-12.json`.

## Rollback

Rollback should deploy the previous application code while retaining the additive tables and nullable references. The data-migration reverse is intentionally non-destructive. Legacy fields remain available; no customer, subscription agreement, financial document, inventory record, or audit record was deleted or renumbered.
