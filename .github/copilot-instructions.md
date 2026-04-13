<!-- Short, focused instructions for AI coding agents working in this repository -->
# Copilot / AI Agent Instructions

Purpose: rapidly orient an AI coding agent so changes are safe, correct, and mergeable.

**Big picture**
- Monolith, tenant-first: every core model and DB query must include and filter by `tenant_id`.
- Layering: Views/controllers are thin; business logic belongs in the service layer; models hold data and invariants only.
- Domains are modular (by business area): e.g., `customers/`, `billing/`, `notifications/`, `tenancy/`.

**Non-negotiable guardrails**
- Tenant isolation: enforce at ORM, service, and DB levels. No cross-tenant access.
- Service layer enforcement: prefer `InvoiceService.create_invoice()` and `PaymentService.register_payment()` over view-level logic.
- Transactions: multi-step financial operations must use DB transactions; avoid partial writes.
- RBAC & object permissions: server-side checks required on all endpoints.
- Error responses: use structured JSON with `message`, `code`, and `actionable_hint`. Do not expose stack traces.
- Audit: log invoice/payment/status/role changes to an immutable audit trail.

**Key project conventions (concrete examples)**
- Directory-by-domain: look for modules like `customers/` and `billing/` which own models, services, and validations.
- Naming: use business-intent names (`create_invoice`, `register_payment`), avoid generic names like `manager`.
- DB patterns: always paginate list endpoints; add indexes on `tenant_id`, FKs, and frequently-filtered fields; use `select_related`/`prefetch_related` to avoid N+1s.
- Background work: heavy tasks (reports, PDF generation, emails) run via background jobs/queue—keep API surface synchronous and fast.

**Developer workflows & quick commands (what I found in this workspace)**
- Virtualenv activation (PowerShell example used locally):
	- `& .venv\\Scripts\\Activate.ps1`
- Tests: repository requires tests for tenant isolation, financial flows, and permissions. Run test runner if present (common):
	- `pytest` (if project uses pytest) or use project's test script if different.
- Migrations: migrations must be reversible. Use the project's migration tooling (e.g., `alembic`, `django manage.py migrate`) and verify rollback.

**Integration points & observability**
- External dependencies: DB, cache (Redis or similar), queue/worker system—treat them as stateful, tenant-aware services.
- Observability: add hooks for error logging and tenant usage metrics when touching critical flows.

**Checklist for code changes**
1. Confirm `tenant_id` present and used in queries.
2. Move business logic into a service if it's in a view.
3. Wrap multi-step financial operations in a transaction.
4. Add/adjust tests for tenant isolation and permission rules.
5. Ensure structured error responses and audit events are emitted.

**Where to look first**
- Core rules: [.codex/project_guardrails.md](.codex/project_guardrails.md#L1-L200) and [.codex/project_rules.md](.codex/project_rules.md#L1-L200) for the definitive project policies.
- Domain examples: check `customers/`, `billing/`, `tenancy/` for service patterns and tests.

If anything required by these instructions is missing or unclear, ask the maintainer before making wide-reaching changes.

Next: tell me whether to work on a bugfix, new feature, tests, or CI; I will follow these rules and iterate.
