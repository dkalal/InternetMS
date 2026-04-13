🧠 CORE ARCHITECTURE PRINCIPLES
1️⃣ Tenant-First Architecture (Always)

Every core model MUST include tenant_id.

All queries MUST be tenant-scoped.

No cross-tenant data access. Ever.

Enforce tenant isolation at:

ORM level

Service layer

Database constraints (where possible)

If a query does not filter by tenant, it is wrong.

2️⃣ Separation of Concerns

Strict layers:

Presentation (Views / Controllers / API)

Service layer (Business logic)

Domain models

Infrastructure (DB, cache, queue)

Views must not contain business logic.
Models must not contain UI logic.

3️⃣ Modular Domain Structure

Organize by business domain, not by file type.

Bad:

models.py
views.py
utils.py

Good:

customers/
billing/
notifications/
tenancy/

Each module must:

Own its models

Own its services

Own its validations

Avoid circular dependencies

📈 SCALABILITY RULES
4️⃣ Horizontal Scalability Ready

No global state.

No in-memory session dependency.

Background jobs for heavy tasks.

Idempotent operations.

Design as if it will serve 10,000 tenants. Even if it won’t.

5️⃣ Database Discipline

Add indexes on:

tenant_id

foreign keys

frequently filtered fields

Use pagination everywhere.

Never load full tables.

Avoid N+1 queries (use select_related / prefetch).

If query complexity grows linearly with tenants, redesign it.

🔒 SECURITY RULES (Non-Negotiable)
6️⃣ Strong Access Control

Role-based access (RBAC).

Principle of least privilege.

All endpoints require authentication.

Object-level permission checks.

No “hidden button” security. Backend enforces everything.

7️⃣ Input Validation Everywhere

Validate at serializer/form level.

Re-validate at service level.

Sanitize user inputs.

Protect against:

SQL injection

XSS

CSRF

IDOR

Assume user input is hostile.

8️⃣ Audit Trail

Critical actions must be logged:

Invoice creation

Payment registration

Status change

User role change

Audit logs are immutable.

⚙️ ROBUSTNESS & RELIABILITY
9️⃣ Idempotency

Financial operations must be idempotent.

If a payment request is retried:

It must not double charge.

It must not duplicate receipt.

Use unique transaction references.

🔟 Graceful Failure

System must:

Fail safely

Not corrupt data

Not expose stack traces

Return meaningful errors

Never leave partial writes. Use transactions.

🧹 CLEAN CODE & MAINTAINABILITY
11️⃣ Naming Discipline

Use business language.

Avoid generic names like data, info, manager.

Function names must describe intent.

Readable code > clever code.

12️⃣ No Fat Views / No Fat Models

Business logic lives in services.

Example:

InvoiceService.create_invoice()
PaymentService.register_payment()

Models are data containers + simple invariants only.

13️⃣ Consistency Rules

One coding style.

One validation strategy.

One error response format.

One notification pattern.

Inconsistency kills maintainability.

14️⃣ Documentation is Mandatory

Every:

Service

Complex method

Domain rule

Must explain:

Why it exists

Business rule enforced

Side effects

If it’s not documented, it will be misused.

🚀 PERFORMANCE RULES
15️⃣ Cache Strategically

Cache:

Dashboard aggregates

Frequently accessed lists

Tenant settings

Do NOT cache financial mutations.

16️⃣ Async Everything Heavy

Move to background:

Notifications

Report generation

PDF generation

Email sending

UI must feel instant.

♿ BUILT-IN ACCESSIBILITY
17️⃣ Accessibility by Default

Proper semantic HTML.

Labels for all inputs.

Keyboard navigation works.

Sufficient color contrast.

No color-only meaning.

Accessibility is not a plugin.

🎯 UX/UI WORLD CLASS SIMPLICITY RULES
18️⃣ Minimal Surface Area

No feature without clear business value.

No field without purpose.

No setting without usage.

Complexity must earn its place.

19️⃣ Smart Defaults

Pre-fill tenant settings.

Intelligent due dates.

Suggested values.

Good UX predicts user behavior.

20️⃣ Clear System State

Always visible:

Payment status

Subscription status

Expiry alerts

System notifications

User must never guess.

21️⃣ Feedback Discipline

Every action must return:

Success confirmation

Clear error reason

Undo where possible

No silent failures.

🧠 SMART MANAGEMENT FEATURES
22️⃣ Observability

Built-in:

Error logging

Performance monitoring hooks

Tenant usage metrics

You can’t scale what you can’t measure.

23️⃣ Feature Flags

New features must:

Be toggleable

Be tenant-specific if needed

No risky global rollouts.

24️⃣ Data Integrity Enforcement

Examples:

No overlapping active subscriptions.

No receipt without invoice.

No invoice without customer.

No cross-tenant references.

Enforce at:

DB constraints

Service layer

🏗 DEVELOPMENT DISCIPLINE
25️⃣ Tests Required For

Financial flows

Permission rules

Multi-tenancy isolation

Expiry logic

No test = no merge.

26️⃣ Backward Compatibility

Never break:

API contracts

Database migrations

Existing tenant data

Migration scripts must be safe and reversible.

🏁 FINAL NON-NEGOTIABLE RULE

If a change:

Reduces clarity

Breaks isolation

Adds complexity without value

Sacrifices security for speed

It is rejected.