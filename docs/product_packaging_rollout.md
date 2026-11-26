# Optional packaging: private-data-safe rollout and rollback

This is a runbook, not deployment authorization. No production database, office checkout, media, volumes or running business services were changed during feature development. Deployment PR #4 remains separate. Merge and production release require separate explicit approval.

## Before any production write

A restricted operator must identify the exact tenant, reviewed feature-aware commit and migration plan. Stop all web, worker, import and scheduled writers for the release window. Record the existing image, full commit, schema state, tenant counts, stock quantities/values, document totals, serial history and protected dependencies in private evidence. Never copy office data, customer PDFs, secrets, media or backups into Git/PRs.

Take an encrypted, access-restricted database backup and the corresponding media snapshot. Record checksums and retention/location privately. Restore both into an isolated environment with all email, payment, integration and production credentials disabled. Verify checksums, row/tenant counts, foreign keys, financial totals, serial history, base stock and historical invoice/receipt PDFs. A backup without a successful verified restore is not release evidence. This task's synthetic tests are not a substitute for a real restore.

Rehearse forward migrations from the actual pre-release schema in that isolated restore. `python manage.py check`, `python manage.py makemigrations --check --dry-run`, the focused/full test gates and the reviewed migration plan must pass. No applied migration may be edited. Foundation migrations preserve existing NULL snapshots and disabled product flags; inventory.0022 changes cart identity only, and inventory.0023 adds an empty append-only correction table. No migration multiplies stock or creates physical containers for old rows.

Run read-only preflight with the exact private tenant slug:

```text
python manage.py audit_product_units --fail-on-drift
python manage.py audit_product_packaging --tenant <exact-private-tenant-slug> --fail-on-drift
```

The existing unit audit is an explicitly unscoped maintenance audit; keep its output private. The new packaging audit is explicitly tenant-scoped; its authorized-product dependency probes detect foreign ownership rather than hiding corrupt links. Resolve every difference through the owning audited workflow. Neither command adjusts records.

## Explicit existing-stock activation

1. Verify that the product's immutable sales/stock unit is the actual base inventory unit. An old quantity of 10 is not 10 packages. If the base identity is wrong after history, use the existing successor-product workflow; never edit old history or reinterpret quantities.
2. Configure a tenant/product commercial unit and a whole positive factor. Enter each independent package selling price. Package prices are never derived from base prices and must satisfy the strict base-cost floor.
3. Resolve draft purchases, carts, active quotations and unpaid invoices. Product configuration alone leaves packaging disabled.
4. If positive legacy base quantity has no aggregate balance, use the explicit authorized 'Prepare existing base balance' action with a reason and expected quantity/cost. It initializes the existing base quantity/opening ledger once, preserving quantity and acquisition value. It does not receive packages.
5. Count every distinct physical container/piece. One row is one continuous piece; a sealed package must equal its configured factor. Loose stock is opened, even if its amount happens to equal a box capacity. Enter supplier identifier/expiry where applicable. Zero-stock activation has no rows.
6. Counted remainders must equal the current base balance exactly. Resolve real count differences separately through the approved adjustment workflow before activation. The activation transaction checks current quantity, cost and configuration, and stores actor/reason/retry/provenance without a stock-in or value change.
7. Activate only after the product's staging demonstration and reconciliation pass. Existing products remain disabled until this explicit action. The code capability is not an environment-variable bypass or automatic migration activation.

Base quantities are whole and bounded by both snapshot precision and the existing catalog stock integer capacity (PostgreSQL: 2,147,483,647). Oversized receiving/corrections fail atomically with review guidance; no truncation or clamping is permitted.

## Staging demonstration and operational checks

Using synthetic data: receive 10 packages of factor 305 (3,050 base units/10 sealed), sell 2 packages (2,440/8 sealed), sell 50 base units (2,390/7 sealed plus 255 opened), then sell 300 as 255+45 pieces (2,090/6 sealed plus 260 opened). Retry payment: no additional stock/payment/allocation. Cross-tenant pieces and insufficient/stale selections must roll back payment and stock together.

Return 20 from a sold cut: a new separate 20-unit returned piece restores only the original allocated sold cost. The source continuous length is unchanged. A full return is returned_full, never sealed-box eligible; it may supply base-unit cuts. Monetary credits alone create no stock-in. Physical return creates no cash refund and does not change paid/credited amounts. Cash-refund accounting is outside this feature.

Exercise strict equality-at-cost rejection after all discounts and before tax, mixed-unit wholesale thresholds, concurrent last-box versus meter sales, duplicate returns/wastage, stale factor/version, partial/final depletion cents, positive-stock mapping and historical PDF/receipt printing after catalog changes. Verify denied Sales/Technician roles cannot see acquisition costs or invoke stock/configuration actions.

Use Container reconciliation and Physical returns/wastage reports (cost-authorized), their XLSX exports, the packaging catalog export and the read-only preflight. Base balance must match physical remainders and the catalog mirror. Allocation costs must match posted sale costs; cumulative returns cannot exceed original quantity/cost. Weighted-average rounding differences remain disclosed in the valuation bridge rather than rewriting old movements.

The legacy purchase/opening templates keep same-unit semantics and explicitly reject opted-in products. Use the separate Commercial purchase v1 template for existing activated products: SKU, explicit package reference (blank means base), configuration version, entered quantity and entered unit cost. The server derives factor/normalized cost; formulas, forged columns, new-product creation, stale versions and cross-tenant IDs fail. Imports create review drafts, never stock. Reconfirm through the normal receiving workflow.

Draft/cart/quotation selections reserve nothing. Review each physical piece before payment. Cut or dispatch only after full paid stock posting succeeds. Stale pieces require explicit reselection; payment never silently substitutes stock. Abandonment, rejected/expired quotations and unpaid invoice voiding release provisional selections without movements; converted history and posted allocations remain protected.

## Rollback and compatibility

Before any packaging data is used, a reviewed rollback may return to the previous feature-aware image while leaving additive schema in place and the capability unused. Never reverse migrations automatically.

After commercial transactions, multi-unit cart lines, physical events or containers exist, old unaware code is unsafe for writes: its commercial quantities differ from base stock. Quiesce writers, retain the additive schema and run feature-aware maintenance/read-only code. Roll forward with a corrected build whenever possible. Reversing inventory.0022 can violate old cart/product uniqueness, and reversing populated foundation/0023 tables removes protected provenance. Such reversal requires a separate audited archive/restore plan and explicit approval; deleting or consolidating business history to force reversal is forbidden.

Disabling a product requires zero aggregate stock, zero positive container remainder and no open transactions; it preserves all definitions, snapshots, activation, movement, allocation, correction and audit history. Retiring a package preserves its physical identity/history. No cleanup allow-list was expanded: incoming new dependencies block old cleanup plans and require separate review. Never reuse a signed cleanup plan after code/schema/data changes.

Actual backup/restore, restored-data rehearsal, approved staging acceptance, release writer quiescence and production deployment remain operator-controlled actions with separate approval. Synthetic verification is recorded in the phase evidence and PRs.
