# Cart, quotation, invoice and receipt audit

Branch: `feature/tenant-sku-sequence`. Existing working-tree changes, including
cart services and receipt tests, are preserved. The receipt template changes only
its subtotal label back to the existing financial contract; its other edits remain.
No commit,
deployment, migration, production-data correction or permission grant is included.

Base commit: `ba89500a138b067f4243e556598a9bee2b12b884`.

## Reproduced discrepancy

The editor omitted the stored document discount when calculating its preview.
For quantity 1, price 50, item discount 10, document discount 5 and VAT 10%:

```text
Net line/subtotal = 50 - 10 = 40
Taxable amount = 40 - 5 = 35
VAT = 3.50
Invoice total = 38.50
```

The detail page's 38.50 was correct; the editor's 44 ignored the document discount.
Blank extra forms also inflated the item count. Initial row binding and unrelated
row edits could apply current catalog prices/promotions to saved lines.

## Financial and lifecycle contract

* Draft carts, quotations and draft invoices do not post stock or record payment.
* Subtotal is net of item discounts. Document discount is subtracted once.
  VAT applies only to eligible lines after proportional document-discount allocation.
* The editor shows the preserved document discount read-only. It does not introduce
  a new discount authorization path. Invalid discounts still fail at the server.
* Opening or changing a description does not reprice existing lines. Customer,
  category, product, quantity, pricing mode, billing behavior or promotion changes
  continue through the established server pricing and cost-floor validation.
* Conversion preserves the quotation's saved line prices, discounts and monetary
  totals. A later catalog tax change cannot silently change the agreed total.
* Publishing or editing only description/status preserves saved VAT when tax rate
  and the monetary/product signature are unchanged. A financial edit recalculates
  draft VAT using current catalog eligibility. Per-line tax eligibility snapshots
  are not added; introducing those would require a separate additive schema plan
  with explicit treatment of unknown historical eligibility.
* Quotation conversion, revision and status changes lock the quotation before
  validating state. Concurrent conversion retries return the same invoice. Do not
  join the nullable converted-invoice relation in the locking read: a waiting
  PostgreSQL read can otherwise retain a stale joined NULL after the FK changes.
* Draft invoice edits lock the invoice before state validation, using the same
  parent lock as payment. Issued/paid financial history remains immutable.
* Draft line IDs with serial selections survive HTTP edits. Serial selections remain linked to
  those lines. Ordinary unselected lines retain replacement/submitted-order behavior.
  Selected lines cannot silently change product/quantity or be removed;
  staff must explicitly update selections first. Foreign/repeated line IDs fail
  atomically. New rows still receive new IDs.
* Inventory invoice payment requires an issued invoice and full settlement.
  The existing payment service owns the transaction, stock checks and posting.
  Receipts represent cash received; their print breakdown reads stored invoice
  amounts, never today's catalog pricing or tax flags.

## Security and database review

Existing authenticated tenant/owner querysets, canonical permissions, CSRF and
payment authorization remain in force. Snapshot preservation is derived from
validated form changes on server-resolved existing lines, never a submitted
preserve-price flag. Product cost floors are still validated at mutation/payment
boundaries. No financial history is backfilled or rewritten.

No schema change is needed. Existing callers retain the
replacement behavior for unselected draft lines; HTTP edits supply validated IDs.
Existing selected serial lines fail safely if a caller attempts replacement.

## Verification and follow-up

Regression modules: `billing.tests_document_flow`, `billing.tests`,
`inventory.tests_cart_checkout`, `billing.tests_receipt_totals` and optional Edge
`billing.browser_document_flow`. The flow tests cover discounts, catalog changes,
publication, stock neutrality, serial identity/payment, invalid line identity and
simultaneous PostgreSQL conversion. Browser coverage exercises the actual editor JS.

The initial working tree had a receipt presentation/test mismatch: its template
printed `Subtotal`, while eight receipt regressions and the receipt contract
required `Subtotal (before discounts)` in HTML/PDF. The label now follows that
contract, making the gross receipt subtotal distinct from the net billing subtotal.
No test assertion was weakened and the other receipt template edits are preserved.

Final command results are recorded in the task handoff and `.document-flow-*.log`.
The full suite remains a release gate; a successful focused suite alone is not
release approval. Package conversion and unrelated UI redesign remain deferred.

Changed source files: `billing/services.py`, `billing/views.py`,
`templates/billing/document_form.html`, `billing/tests_document_flow.py`,
`templates/billing/receipt_print_tra.html`, `billing/browser_document_flow.py`,
`docs/architecture.md` and this contract.
Recommended commit scope: one cohesive billing-flow fix with its tests and docs;
exclude pre-existing cart/receipt edits beyond the corrected label, and generated
logs. No commit was made.

Selected serial lines retain their original ID order. Arbitrary reordering around
those protected lines would require an explicit persisted ordering contract; this
change does not add an ordering column or rewrite serial history.

Verification uses current source mounted into a one-off Compose container, with
generated static files under `/tmp/jims-document-flow-static`. The live service's
image is unchanged. The local Edge check uses its own test database and blocks
external HTTPS assets so network availability cannot own the editor assertion.

The verification wrapper `.backups/document_flow_gates.py` runs:

```text
python manage.py collectstatic --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test billing.tests_document_flow billing.tests inventory.tests_cart_checkout billing.tests_receipt_totals --keepdb --noinput
```

The same wrapper without test labels runs `python manage.py test --keepdb --noinput`.
The browser wrapper `.backups/document_browser_gate.py` runs
`python manage.py test billing.browser_document_flow --keepdb --noinput` against a
separate test database; PostgreSQL is reached through `127.0.0.1:5434` on Windows.
Both wrappers and generated assets/logs are local verification artifacts.

Final checks:

| Gate | Outcome |
| --- | --- |
| `python manage.py check` | Passed, 0 issues |
| `python manage.py makemigrations --check --dry-run` | Passed, no changes detected |
| Focused billing/flow/checkout/receipt suite | 127 tests passed in 54.131 seconds |
| Edge editor regression | 1 test passed in 5.502 seconds |
| Full suite, PostgreSQL, four workers | 804 tests passed in 198.427 seconds |
| `git diff --check` on changed source/contracts | Passed |

The earlier full run reproduced eight receipt-label failures. Those assertions
remain unchanged; correcting the label makes the focused receipt suite pass.
The final full run uses four Django workers in a separate PostgreSQL test database
(`JIMS_GATE_TEST_DATABASE=test_jims_document_release`, `JIMS_TEST_PARALLEL=4`).
Its result is in `.document-flow-release.log`.
The superseded serial run was stopped after this complete final-source run passed;
no test was skipped or weakened to obtain the green gate.
