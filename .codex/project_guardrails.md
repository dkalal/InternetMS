# JIMS non-negotiable guardrails — v2.0

An implementation that violates these guardrails is invalid even if its UI works or its happy-path tests pass.

## 1. Tenant isolation

- Every tenant-owned aggregate must have explicit tenant ownership.
- A child record must either carry tenant ownership or be reachable only through a protected tenant-owned parent, with service/database enforcement.
- Request and business flows derive tenant context from authenticated server-side membership, never from a tenant identifier trusted directly from user input.
- Tenant-owned queries are scoped. Explicit unscoped access is permitted only for migrations, restricted support/administration, maintenance, or an equally documented infrastructure need; it must be fail-closed, audited where applicable, and tested.
- Cross-tenant foreign keys, joins, selections, and mutations are security breaches.

## 2. Authentication and authorization

- Business-data endpoints require authentication. Explicit public authentication/infrastructure endpoints are the only exceptions.
- Stable permission codes and object/tenant checks are enforced server-side.
- UI visibility improves usability but is never an authorization boundary.
- Super Administrator access to tenant data requires the established audited support-session path.
- Financial and purchasing visibility follows least privilege; Sales and Technician roles must not gain broader financial access incidentally.

## 3. Financial and inventory integrity

- Financial and stock mutations use the owning service and a database transaction.
- Retried operations must not duplicate payments, receipts, references, stock movements, serialized units, or document transitions.
- Partial writes are forbidden: a failed operation leaves the aggregate and dependent records unchanged.
- Confirmed financial and inventory history is immutable. Corrections use explicit reversal/cancellation/credit workflows rather than silent edits or hard deletion.
- Monetary totals and inventory conversions are server-authoritative; browser calculations are informational only.

## 4. Purchasing and stock

- Each product has one selected sales/stock unit. Purchase quantity, purchase unit cost, stock balance, and selling prices use that same unit.
- Package-to-smaller-unit conversion is deferred. Legacy package fields are preservation-only and cannot drive new forms, APIs, imports, calculations, or stock posting.
- Draft purchases are stock-neutral.
- Receiving requires server-side permission and is tenant-scoped, atomic, row-locked, idempotent, and all-or-nothing.
- Every confirmed line produces the correct immutable stock-in history exactly once.
- Services/non-stock items cannot be received into inventory.
- Serialized stock requires the exact number of normalized, tenant-unique serials; duplicates within or across lines must fail before posting.
- Weighted-average movement cost remains the authoritative cost floor when positive movement-backed stock exists. Net pre-tax unit revenue must be strictly greater than the applicable floor.
- Historical snapshot totals remain historical truth after product defaults or active workflows change.

## 5. Audit and data preservation

- Critical creations, approvals, payments, confirmations, cancellations, role changes, support access, and settings changes are auditable.
- Audit history is append-only to ordinary application users.
- Destructive migrations require an explicit data audit, verified backup/archive, compatibility analysis, rollback plan, and user approval.
- Applied migrations are never rewritten.

## 6. Secure failure

- Invalid or unauthorized requests fail closed without mutation.
- Raw stack traces, secrets, internal credentials, and sensitive financial values are not exposed to unauthorized users.
- APIs return stable structured errors; HTML workflows return accessible field/non-field errors and actionable messages.
- CSRF, IDOR, XSS, injection, mass-assignment, and hostile input risks are considered at every entry point.

## 7. Release gates

- Tenant isolation, permission enforcement, financial/stock integrity, idempotency, immutability, and critical transitions require automated tests.
- `check`, migration-drift validation, focused regression tests, and the full suite must pass before a phase is complete.
- Tests cannot be weakened, skipped, deleted, or rewritten to hide a production defect.
- A changed invariant requires a replacement contract test and documentation in the same phase.
