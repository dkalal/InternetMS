# Category unit fix and Sales balance proposal

## Implemented: purchase import category selection

The import editor and bulk category selector now fill the category's active allowed default unit. A sole active allowed alternative is a safe fallback; ambiguous alternatives remain blank. Explicit alternative units survive unrelated editor updates. Bulk changes affect selected included new products only. Existing catalog product units and server validation remain authoritative. No schema or permission change is required. Build static assets with `python manage.py collectstatic --noinput`.

Browser coverage exercises category changes, multiple allowed units, inactive and missing defaults, explicit overrides, focus preservation, bulk selection, existing product isolation and record-neutral application.

## Implemented after approval: Sales customer balances

Current customer billing health deliberately exposes non-monetary follow-up status. Sales invoice visibility is restricted to documents created by or assigned to the active membership. Customer detail balances therefore cover accessible documents, not necessarily the customer's entire debt. The existing empty-document message can obscure this distinction.

If Sales is responsible for collections, approve a dedicated read-only customer balance-summary permission, scoped to authorized customers in the active tenant. This is a proposed new capability, not an existing grant. Show outstanding and overdue amounts separately per currency, with an as-of date, calculated by the billing domain from eligible invoices, receipts and valid credits. Do not use the latest ten visible documents as the customer's total or double-count carried-forward snapshots.

Keep invoice detail access, payment recording, finance reports, cost and margin permissions separate. Assigning invoice responsibility is not a read-only workaround: the Sales baseline can also record payments on accessible invoices. Do not grant finance-wide visibility simply to expose a summary.

Without the summary permission, retain non-monetary health with an Accounts follow-up action and an access-aware empty state. If showing only owned/assigned invoice balances, label that scope explicitly. Any new summary endpoint must authorize every request and avoid cross-tenant or unauthorized customer disclosures.

Approved on 22 October 2026: `customers.balance_summary.view` is now an optional Team & Access grant. Admin/Manager enables it for a Sales member under Customers. Customer detail then shows outstanding/overdue per currency, including documents outside that member's invoice access. No Sales baseline or payment/document permissions were widened. No schema migration is required. The preceding recommendation records the rationale. The principle follows the [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

## Sales implementation verification (22 October 2026)

Branch/base: `feature/purchase-import-review-redesign`, `d188010`, preserving prior dirty purchase-import and unrelated print-template work. No commit, push or deployment performed.

Changed: `users/permissions.py`, `users/views.py`, `billing/services.py`, `customers/views.py`, `templates/customers/customer_detail.html`, the RBAC contract and this note. Added `customers/tests_balance_summary.py` and optional `customers/browser_balance_summary.py`.

Checks ran against current mounted source in the configured Docker Compose web runtime, with isolated PostgreSQL test database names:

- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python manage.py test customers users --keepdb`: 108 tests passed in 80.274 seconds.
- `python manage.py test --keepdb`: 610 tests passed in 416.247 seconds.
- Local development environment: `python manage.py test customers.browser_balance_summary --keepdb`: one actual Edge desktop/mobile test passed in 6.061 seconds. Synthetic screenshots are under `.backups/sales-balance/`.
- `git diff --check`: passed (line-ending notices only).

The six new regression tests cover opt-in/revocation, hidden invoice and payment access, multiple currencies, receipts/credits, overdue boundaries, more than ten invoices, bounded queries, excluded statuses, tenant/inactive membership isolation, self-grant rejection, POST/CSRF enforcement, audited grants, zero flooring, record-neutral reads and untrusted membership rejection. Browser testing additionally verifies responsive summary layout and removal after revocation.

Enablement: deploy the reviewed application code through the normal release process, then explicitly grant the new Customers permission through Team & Access. Existing Sales memberships receive no automatic grant. Rollback removes the summary feature; stored permission grants become ineffective when the code no longer recognizes them. No financial history is rewritten.
