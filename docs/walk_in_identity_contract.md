# Walk-in POS identity and billing contract

Walk-in means a POS cart with no selected customer. Cashiers keep the existing
cart flow: an optional name, no mandatory customer profile. Billing requires a
Customer foreign key, so new walk-in sales use one hidden internal POS account
per tenant, identified by `Customer.is_pos_placeholder`. That account is not a
person and must never be selectable as a registered customer.

Every new POS quotation and invoice freezes `is_walk_in_sale` and
`walk_in_name_snapshot`. Receipt, quotation conversion/versioning, invoice
reissue and credit note copy those attributes. Prints, receipt context, lists
and invoice API read the frozen name. Registered customers continue using
their customer record. A walk-in invoice has no previous account balance; its
outstanding amount belongs only to that invoice. Account balance queries skip
walk-in invoices, even when historical data shares a customer reference with
a registered person. This does not alter invoice totals, payments or stock.

Migration `billing.0032` classifies historical documents only when a linked POS
cart has no selected customer, then follows same-customer document ancestry.
It does not guess whether a manually created document was a walk-in sale.
Historical `balance_brought_forward` values and their issued-document displays
remain as issued; new walk-in invoices record zero prior balance, and current
walk-in account summaries reflect only that invoice's remaining balance.
Before applying in production, back up the database and review existing
walk-in invoices with nonzero brought-forward values; previously issued PDFs
may already have shown another person's balance and need an explicit
correction workflow. Rollback of the schema migration would
remove the added snapshots, so deploy the previous app version against a
restored pre-migration backup if full rollback is required.

### Office deployment preflight

Run this from the existing repository root on the office host, **before
starting the updated web container** (its startup command runs migrations
automatically). Fetching the PR branch only downloads its Git objects; it does
not switch the office checkout, replace its files, or restart its containers.
Keep the backup outside the repo:

```bash
git fetch origin refs/heads/fix/walk-in-sale-identity:refs/remotes/origin/fix/walk-in-sale-identity
umask 077
walk_in_backup="../jims-pre-walk-in-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db pg_dump -U postgres -d js_internetservices -Fc > "$walk_in_backup"
test -s "$walk_in_backup"
docker compose exec -T db pg_restore --list < "$walk_in_backup" > /dev/null
set -o pipefail
git show origin/fix/walk-in-sale-identity:docs/walk_in_predeploy_audit.sql | \
  docker compose exec -T db psql -X -U postgres -d js_internetservices -v ON_ERROR_STOP=1
```

The `git show` command reads the audit file directly from the fetched branch.
Do not run `git pull`, `git switch`, or `docker compose up` to perform this
preflight. Confirm that the fetch succeeds before running the other commands.
`pg_restore --list` verifies that the archive can be read; verify a full restore
in an isolated database before relying on it as a recovery plan. The SQL file
uses a read-only transaction and the pre-migration schema. Review every invoice
in `review_references` with the authorized finance owner; a nonzero prior
balance remains as issued and must be corrected through the approved financial
workflow. No rows means no invoices with a qualifying POS cart were found; it
does not prove manually created invoices are walk-in sales. Do not share raw
audit output or the backup publicly.

After backup, audit, review and a staging migration, smoke test two unpaid
walk-in invoices with the same name, a registered customer with that name,
quotation conversion, a receipt, reissue and tenant isolation. Check names
on detail and print views and that a new walk-in invoice has zero brought-forward
balance. Keep the PR in draft until those deployment gates are complete.

Fulfillment and delivery tracking remain a separate later phase.

### One-time walk-in shipping address

A draft POS cart may hold an optional shipping address (up to 500 characters)
when no registered customer is selected. Leaving it blank means collection or
no recorded delivery address. Cart conversion freezes the address on the
quotation or invoice; quotation conversion/versioning, invoice reissue, credit
notes and receipts carry the snapshot forward. The detail and printed sales
documents show it as “Ship to,” separate from the registered customer's billing
address. Two walk-in sales sharing the internal POS account never share an
address. Forms, the invoice API and the billing service reject an address
supplied with a registered customer. No customer record, shipping charge,
fulfillment status or stock movement is created by recording this address.

Migrations `inventory.0012` and `billing.0033` add blank columns; existing
carts and documents remain blank, with no inference or data rewrite. A previous
app version can read the additive schema; reversing these migrations removes
addresses recorded after deployment, so preserve a database backup before
rollback. Issued documents retain the original snapshot and require existing
correction workflows for changes.
