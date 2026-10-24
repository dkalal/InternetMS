# Cart lifecycle and controlled cleanup

## Operational contract

Active is every accessible draft, including Stale. Stale is a computed subset,
not a database status: `updated_at <= now - 7 days`. Converted and Abandoned are
history, not completed sales. Lists default to Active and paginate 25 rows by
`-updated_at, -pk`. Counts and owner options use the same authorized base scope
before search/date/owner filters. Search covers ID, customer/walk-in identity and
linked quotation/invoice number. Date filters mean last activity, inclusive local
calendar dates in Django's current timezone. Invalid dates fail closed with an
error. Filters persist across pages and tabs.

Canonical `sales_documents.create` is required at HTTP entry points. Owners see
their own carts; `finance.sales.view_all` permits tenant-wide cart visibility and
restoration (the existing non-delegable Admin/Manager permission). Linked document
access is also checked for own-scope rows before exposing document numbers in
search or counts. No permission aliases or grants change. The existing trusted
conversion service contract remains available to internal callers; it now also
checks actor ownership or canonical tenant-wide access. HTTP callers always pass
through canonical authorization first. Platform support-session restrictions
remain owned by the existing tenant middleware.

`updated_at` is meaningful activity: saved cart detail changes, item changes,
serial selection changes and explicit restoration. Merely opening a cart and
posting unchanged details do not touch it. Child save/delete receivers update
only draft parents. Parent row locks precede all HTTP cart mutations and child
model saves/deletes, checkout and maintenance. Django admin exposes carts for
inspection only; writes use the operational workflows. Conversion retries do not
save submitted cart details after conversion. Converted line/product snapshots
remain visible even when a product becomes inactive.

Start sale is CSRF-protected POST. It locks the stable actor row before looking
for a reusable draft, so two initial requests cannot both observe no draft. Reuse
is limited to the same tenant and owner and inactivity strictly under 24 hours.
Whitespace-only text is empty; selected customer, nonblank walk-in/address/notes,
nonzero discount/tax, non-default pricing category, lines, converted timestamp,
quotation, invoice, prior abandonment/restoration metadata or reverse InventorySale
linkage all make a cart ineligible. Restored history is therefore never considered
an accidental empty cart.
Multiple meaningful customer carts remain separate.

Manual abandonment is audited, draft-only, scoped and stock-neutral, with actor,
timestamp and an optional bounded reason. Restoration is an explicit audited
manager POST and preserves lines, quantities, serial selections and metadata.
Abandonment history metadata is retained after restoration. Last activity resets
on restoration. Existing checkout validation still applies; restored carts also
reject inactive products, changed prices and unavailable quantities/serials with
an actionable review message before conversion. Restoration itself never silently
reprices items. Intentional item/details edits continue the established pricing
policy and display the saved prices for review.

## Migrations and compatibility

### Repeated product additions

Each draft cart retains exactly one line per product (`uniq_cart_product`). The
add-item action adds the submitted quantity and additional serials to that line;
the edit action replaces its quantity and serial selections. Selecting a serial
already on that line fails with an actionable validation error and no mutation.
Changing an edited line to a product already in the cart likewise fails safely.
The add screen explains these semantics before submission.

`CartService.save_line` checks permission and visible tenant/owner scope, locks
the draft parent before looking up the existing line, and validates the submitted
and combined values with the existing stock, serial, pricing and discount policy.
Combined quantity determines the effective pricing tier. Existing discounts are
preserved and authorized additional discounts are additive. Line persistence,
serial replacement, final discount authorization and the audit event are atomic.
Draft selections do not reserve stock; payment continues to revalidate stock and
serial availability. No stock balances, unit states or movements change here.
No migration or historical data rewrite is required.

Regression coverage: `inventory.tests_cart_line_merge`, including simultaneous
first additions against PostgreSQL. The transaction and lock design follows
[Django 5.2 transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/)
and [row locking](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update).

0014 adds nullable abandoned/restored timestamps and protected actor references,
and a bounded abandonment reason. Existing lifecycle timestamps remain NULL;
unknown history is never inferred from deployment time. No new index is needed:
the tenant/status/updated_at index serves the operational filters.

0015 corrects only draft `updated_at` values older than an already recorded child
line `updated_at`, using that actual timestamp. It does not invent activity or
change lifecycle status. Removed historical lines cannot be reconstructed;
review dry-run results before the first apply. Neither migration deletes or
abandons records. Both old and new application versions can read the additive
schema; pause cart writes during the rollout and run migrations before enabling
the new application. The activity reconciliation also checks the current parent
timestamp before updating it, preserving newer recorded parent activity. Roll back the
application first. Reverting inventory to 0013 removes the new lifecycle metadata
and requires a backup if that metadata is needed; the timestamp reconciliation's
reverse is deliberately a no-op to preserve known activity.

## Cleanup command

Run from the repository root after deploying the reviewed source and migrations:

```bash
docker compose exec -T web python manage.py cleanup_carts --tenant TENANT_SLUG --batch-size 500
docker compose exec -T web python manage.py cleanup_carts --tenant TENANT_SLUG --batch-size 500 --apply
```

Omit `--tenant` only for an intentional all-tenant maintenance run. Tenant selectors
are exact slugs. Batch size is bounded to 1..5000 candidates. The default is
strictly dry-run. Output includes cart IDs, outcomes, skip reasons and
examined/eligible/deleted/abandoned/skipped counts; it excludes customer identity,
notes and amounts. `next_after_id=N` means repeat with `--after-id N` to continue;
0 means the scan is finished. Continue dry-run review through all batches before
first apply; an apply run must start again at 0. Never reuse a dry-run cursor as
the starting point for the apply pass.

All cutoffs use one timezone-aware `now` per run. A genuinely empty draft is
eligible at exactly 24 hours. A meaningful draft is eligible for abandonment at
exactly 30 days. Converted/abandoned records are never selected. Each candidate
is locked and its current status, activity, emptiness and business linkage are
rechecked inside its own transaction; stale selection cannot override an edit or
conversion committed before the lock is acquired. An editor arriving after a
cleanup transition fails closed against the new state. Unexpected draft links
(including a reverse sale or converted timestamp) are skipped for review. No
stock, serial stock-unit state, billing or payment record is changed.

Cart reverse relations were inspected: lines cascade and InventorySale protects.
Deletion requires no lines and no sale; quotation/invoice references also exclude
the cart. AuditLog stores scalar object identifiers, so deleting an eligible empty
cart preserves its append-only audit events. Every deletion/automatic abandonment
adds an audit event with policy metadata and no unnecessary personal data.
Abandoned history is never automatically hard-deleted. Broader retention is outside
this feature's scope.

## Reviewed rollout and daily scheduling

No existing scheduler is configured in this repository. No Celery/Redis service is
needed. Do not install a timer until backup, complete dry-run review and an explicit
first apply have been completed. The running Compose image does not bind-mount
application source; rebuild/recreate through your normal deployment process before
using `exec`. No live schedule or cleanup was activated during implementation.

1. Take and verify a PostgreSQL backup using your established backup/restore
   process (including audit tables); keep it outside the application container.
2. Deploy the reviewed build and run `docker compose exec -T web python manage.py
   migrate`. Review both migrations and dry-run every batch, including
   `business_link_review` findings, before applying anything.
3. Explicitly run the apply command through all batches. Check counts and audits;
   inspect retained linked/converted/abandoned samples and stock/billing totals.
4. Only then install one daily host task.

For a Linux Docker host, save this script as
`/usr/local/sbin/jims-cleanup-carts` (substitute the real deployment directory):

```sh
#!/bin/sh
set -eu
cd /srv/jims
cursor=0
while :; do
  result=$(docker compose exec -T web python manage.py cleanup_carts --apply --batch-size 500 --after-id "$cursor")
  printf '%s\n' "$result"
  next=$(printf '%s\n' "$result" | sed -n 's/^next_after_id=\([0-9][0-9]*\).*/\1/p')
  case "$next" in ''|*[!0-9]*) echo 'Invalid cleanup cursor' >&2; exit 1;; esac
  [ "$next" -eq 0 ] && break
  [ "$next" -gt "$cursor" ] || exit 1
  cursor=$next
done
```

Install it with owner-controlled permissions, then add one host crontab entry for
the deployment account that has Docker access (03:15 in the host timezone):

```cron
15 3 * * * /usr/bin/flock -n /var/lock/jims-cleanup.lock /usr/local/sbin/jims-cleanup-carts >> /var/log/jims-cleanup.log 2>&1
```

Ensure that account can write the lock/log paths, configure log rotation, and set
the host timezone intentionally (for example Africa/Dar_es_Salaam). The loop uses
bounded batches and advances past skipped candidates, so linked drafts cannot
starve later candidates. `flock` prevents overlap on that host; database row locks
keep the command safe if another maintenance run overlaps elsewhere. On Windows
Docker Desktop, keep manual execution until an equivalent single daily scheduled
host task is separately reviewed. Disable the host task before rollback.

## Verification

`inventory.tests_cart_lifecycle` covers cutoffs, visibility/counts/search,
conservative emptiness, pagination, CSRF, activity, immutability, restoration,
cleanup dry-run/apply/idempotency and PostgreSQL edit/convert/start concurrency.
The existing inventory, pricing and walk-in regressions remain required, followed
by Django checks, migration drift and the full suite. Optional browser checks must
use synthetic data and an isolated test database; never create screenshots by
mutating live carts.
