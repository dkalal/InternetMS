# Classified handover maintenance contract — version 2

This contract implements the user's explicit classification of the four mixed
sample documents (50, 66, 67, 326) and the subsequently confirmed forty
registered-customer documents. It does not authorize live execution, blanket
financial deletion, a new HTTP reset endpoint, or removal of real internet data.

## Closed operations

| Operation | Policy |
| --- | --- |
| inventory | Existing policy: exact inventory/POS roots, registered-customer/mixed billing blocked. No classification argument accepted. |
| customers | Existing separate bare-customer policy, dependencies/audit history block. No classification argument accepted. |
| sample_documents | Exact explicitly classified draft invoices/draft-or-sent quotations, their exact items and exact neutral InventorySale dependencies. Catalog and stock must remain. |
| classified_inventory | Exact all-product inventory manifest, with explicit classification for exceptional registered-customer/mixed document IDs. Complete dependency closure and a zero-product postcondition are mandatory. |

`sample_documents` allows only BillingDocument, BillingLineItem and InventorySale
manifest labels. Every selected document root must be explicitly classified. An
invoice must remain draft; a quotation must remain draft or sent. Payment-date,
payment-reference or payment-method metadata blocks this narrow operation, as do
payment/correction/conversion document links. A sale must have no receipt, cart,
completion timestamp or posted stock. Any cost snapshot, stock, serial, period,
messaging or unselected document reference blocks rather than cascades or detaches.

The actual BillingService stamps `issued_at` on initial construction even for
drafts. This field alone is not issuance proof. The policy uses actual lifecycle
status and payment/stock relationships, preserves the timestamp in record hashes,
and rejects any state/relationship change between planning and apply.

`classified_inventory` keeps the default inventory protections unless the exact
document root is present in a separately supplied classification. Package/manual
lines are allowed only in those classified draft invoices or draft/sent
quotations, without payment metadata. Direct `subscription` or `internet_service`
line references remain blocked in every policy. A line with both product and
package ownership is ambiguous and blocked. The complete financial chain is
still required; classifying one invoice does not classify its receipt/correction
or any other registered-customer document automatically.

Classification input has exactly two keys: `document_ids` (positive integers) and
`reason` (nonempty, maximum 1000 characters). IDs must be selected document roots;
arbitrary model labels, filters, extra force flags and outside IDs are rejected.
For `sample_documents`, the classified set must equal all selected document roots.
Classification is covered by the signed fingerprint and cannot be overridden at
apply time. CLI apply also rejects an explicitly requested operation that differs
from the signed plan.

## No remaining products, no silent scope growth

For `classified_inventory`, the exact product manifest must cover the tenant's
entire catalog, including inactive/non-stock/service products. Missing product
IDs and conflicting tenant/organization ownership are blockers. No newly found
product is automatically enrolled. An independent product created after dry-run
changes the revalidated plan and causes stale-plan rejection.

After exact deletions, still inside the same durable PostgreSQL transaction, the
executor asserts that no Product remains for that tenant. Failure rolls back the
entire cleanup. The signed `postconditions` and successful audit metadata identify
`products_remaining=0`. This is a condition at commit, not permission to remove
unreviewed products or stop the company importing new genuine products afterward.

## Existing safety mechanisms remain

Both new operations require the actual active Super Administrator membership,
active actor/tenant, existing reasoned support session, exact manifest, fresh
signed plan, tenant-specific typed confirmation, backup/restore/write-quiescence
evidence and a reason. Apply regenerates the plan under PostgreSQL table write
locks, compares it exactly, rejects blockers, keeps constraints active, checks
counts and retained endpoints, and audits success. Failures roll back before a
failure audit; validated operation/classification are included in that audit.

Only the private offline maintenance deletion path bypasses model deletion
overrides/signals for these reviewed sample records. Normal financial/stock
immutability, customer deletion policy, permissions and HTTP routes are unchanged.
No schema migration, package conversion, constraint disabling, counter reset or
media deletion is introduced. Customers/sites/profiles/assets/packages/
subscriptions/periods/unrelated billing and old audits remain protected. Actual
backup restoration and writer shutdown are operational verifications; evidence
strings alone do not prove they happened.

## Version and plan lifecycle

Plans use payload version **2** and signing salt `inventory.handover-cleanup.v2`.
Earlier v1 plans, including the preserved synthetic example, are historical
evidence and cannot be applied through the v2 command. Source-code fingerprints
also invalidate old plans after code changes. Never rewrite an old signed plan;
regenerate and review a new one. The existing maximum age is two hours.

Generate a classified dry-run using a JSON manifest and a separate classification
file. Default remains dry-run:

```sh
python manage.py handover_cleanup --tenant TENANT --actor ACTOR_ID \
  --support-session SUPPORT_ID --operation classified_inventory \
  --manifest reviewed-manifest.json --classification-file classification.json \
  --output new-plan.json
```

Use `--operation sample_documents` for the financial-only alternative. The
classification file is **planning input only**; apply reads the unchanged signed
plan and forbids changing manifest/classification through new arguments.

Actual tenant artifacts and operational gates are in the
[final js-tech plan](handover_js_tech_final_plan_2026-10-25.md). The combined and
financial-only plans overlap: **never apply both unchanged**. The combined plan
already includes the 17-row financial-only scope; if the separate stage is ever
executed first, regenerate/review the remaining complete plan from the new state.
