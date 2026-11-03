# JIMS engineering and delivery rules — v2.0

## Architecture

- Maintain a modular Django monolith until measured needs justify another boundary.
- Domain-owning apps own their models, services, validation, tests, and documentation.
- Views coordinate HTTP concerns, forms, messages, redirects, and service calls; they do not implement financial or inventory mutations directly.
- Models hold state, relationships, simple invariants, and compatibility properties. Multi-record workflows belong in services.
- Avoid cross-app imports made only for convenience; expose a small service in the owning domain when an actual domain action is required.

## Tenant and permission implementation

- Use the request's authenticated active organization/membership.
- Scope form choices, object lookup, services, APIs, imports, exports, and background work.
- Use permission codes in `users.permissions`; do not invent ad-hoc role-name checks when a permission exists.
- Test allowed and denied paths, including crafted cross-tenant identifiers.

## Database and transaction discipline

- Use PostgreSQL semantics as the production reference.
- Use `transaction.atomic()` and row locks at mutation boundaries where concurrency matters.
- Add constraints and indexes for demonstrated integrity/query needs, not speculative architecture.
- Use `select_related`/`prefetch_related` deliberately and paginate growing user-facing collections.
- Never load a full growing catalog into the browser when server-side search/pagination is appropriate.
- Background work is justified by measured latency, reliability, retry, or scheduling needs; it is not mandatory for every PDF or report.

## Forms, APIs, and errors

- Validate input in forms/serializers and re-check critical invariants at the service boundary.
- Preserve submitted values on HTML validation errors and render field/non-field errors accessibly.
- APIs use consistent status codes and structured error payloads.
- Unknown actions and unsupported transitions fail safely and never fall through to a destructive default.

## UI and accessibility

- Use the existing Tailwind design system and shared components.
- Prefer compact, task-oriented screens with clear hierarchy and minimal scrolling.
- Controls require labels, keyboard access, visible focus, adequate contrast, and non-color state cues.
- Show current state, stock/financial impact, validation feedback, and irreversible consequences before confirmation.
- Do not introduce React, Vue, or another frontend framework without a demonstrated requirement and explicit approval.
- JavaScript may improve interaction but cannot own authorization, authoritative totals, or domain state.

## Performance

- Optimize for realistic catalog/tenant growth and measured query behavior.
- Avoid N+1 queries, unbounded lists, cross-tenant scans, and large serialized metadata blobs.
- Add caching only where invalidation is safe and benefit is measurable. Never cache mutation authorization or mutable financial truth as the source of record.

## Tests

- Prefer focused domain contracts plus service/view regression tests.
- Every bug fix needs a regression test that fails before the fix.
- A removed/deferred workflow receives replacement tests for the supported workflow; do not simply delete its safety net.
- Preserve tests for tenant isolation, permissions, atomic rollback, idempotency, serial uniqueness, historical snapshots, and immutable confirmed records.
- Run focused tests during development and the full suite before handoff.

## Migrations and compatibility

- Explain schema changes before implementation when they affect persisted business data.
- Commit migrations with their owning app and never edit applied migrations.
- Prefer additive/reversible transitions. Preserve historical reads while disabling new writes to a deferred legacy feature.
- Remove legacy columns only after data audit, verified archive, approval, and a tested rollback/compatibility plan.

## Scope, Git, and handoff

- Work one approved phase at a time and record intentional deferrals.
- Do not mix unrelated global formatting, dependencies, domain changes, and UI redesigns in one commit.
- Preserve dirty-worktree user changes and untracked files.
- Do not commit or push without explicit authorization.
- Handoff reports include branch/base, files changed, migrations, commands/results, security review, known risks, deferred work, and recommended commit split.
