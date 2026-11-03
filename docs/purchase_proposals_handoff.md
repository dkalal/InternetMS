# Purchase proposals until receiving

New definitions from the mixed purchase import are stored in `PurchaseProductProposal`, linked to the tenant's draft purchase. They are not Product records and have no allocated SKU, inventory balance, movements or serial inventory. Existing registered products, including out-of-stock products, remain visible in cart builder with ordinary stock and pricing enforcement.

Draft save validates the shared product rules, persists existing-product lines and proposed rows, calculates the combined total and records the existing idempotency receipt atomically. Opening an existing draft hydrates its proposed rows into the import review. Saving replaces that proposal snapshot instead of appending it. Proposed rows appear on receipt review with an explicit label and tracking/cost details.

Receiving locks the purchase and tenant allocation boundary, validates current category/unit/catalog/serial rules and the receiver's product-creation permission, creates approved products with safe SKU allocation, creates their purchase lines and posts stock in the existing atomic receiving transaction. It removes staging within that transaction. A failure preserves the draft and proposals and rolls back products, lines, stock, serials and audit writes. Confirmed retries return the same result. Cancellation never materializes proposals.

This changes imported new definitions only. Explicit catalog/quick-product creation still registers a product immediately as a separate authorized action. Existing products are never removed from the catalog because a purchase is draft or their stock is zero.

## Schema and rollout

Migration `inventory/0016_purchase_product_proposals.py` adds one staging table. It does not rewrite existing purchases, Product IDs, balances or historical movements. Apply migrations before serving this code and rebuild static assets:

```sh
python manage.py migrate
python manage.py collectstatic --noinput
```

Existing drafts created by the old importer already refer to registered products. This migration deliberately does not guess which old catalog records can be removed. For each affected draft, the tenant-scoped management command previews eligible conversion candidates:

```sh
python manage.py stage_purchase_proposals --tenant-id TENANT_ID --purchase-id PURCHASE_ID
```

Review the product IDs/SKUs printed by that command, then use the same command with `--apply` to convert those candidates transactionally. The command requires importer creation audit evidence and rejects confirmed/cancelled drafts. Products with stock fields, any inventory records, other transaction relationships, images, descriptions, custom fields, reorder settings or advanced pricing remain registered for manual reconciliation. Only unused imported products referenced by the specified draft are restaged, preserving supplied SKUs and purchase allocations. Conversion records an audit trail; receiving later allocates the registered Product IDs. Failed validation rolls back the conversion.

No business migration or legacy conversion was executed against the running application database during development. Deployment and conversion are explicit rollout steps. If reverting application code, retain staging support or receive/export all proposals first; do not drop a populated staging table.

## Verification

Tests use isolated PostgreSQL databases in Docker Compose with the current source mounted. Focused suites cover mixed import validation, stock-neutral save, proposal receipt/idempotency, late-write rollback, stale catalog conflict, category/unit snapshots, shared definitions with separate cost lines, product-creation permission, editing/retries, cancellation and conservative legacy repair. Existing cart pricing tests preserve zero-stock visibility and price-floor checks.

Optional actual Edge test `inventory.browser_purchase_proposals` verifies desktop/mobile review and edit hydration, an existing zero-stock product visible in cart builder, the proposed product absent, then its appearance after successful receipt. Synthetic screenshots are under `.backups/purchase-proposals/`.

No commit, push or deployment is part of this implementation request. Unrelated print-template work and user artifacts are preserved.

Verified on branch `feature/cart-lifecycle-management`, baseline `0b937d6`:

- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no changes detected after adding migration 0016.
- Mixed import/proposal/cart pricing modules: 44 tests passed in 31.659 seconds.
- Proposal and conservative-repair module after final safety checks: 9 tests passed in 5.336 seconds.
- Original cart pricing module: 13 tests passed in 11.276 seconds.
- `python manage.py test --keepdb`: 642 tests passed in 432.325 seconds. An earlier full run also passed 638 tests before the extra repair cases were added.
- Actual Edge import/proposal browser modules: 4 tests passed in 35.480 seconds.
- `collectstatic --noinput`, JavaScript syntax check and `git diff --check`: passed (Git line-ending notices only).

Principal files: `inventory/models.py`, `inventory/migrations/0016_purchase_product_proposals.py`, `inventory/purchase_batch.py`, `inventory/services.py`, `inventory/views.py`, the purchase import controller/dialog, purchase detail template, proposal lifecycle/browser tests and `inventory/management/commands/stage_purchase_proposals.py`. Existing mixed-import and zero-stock/cart regression expectations were updated to the approved lifecycle. No new runtime dependency was added.
