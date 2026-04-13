🛑 INTERNET CUSTOMER MANAGEMENT SYSTEM
GUARDRAILS v1.0

If any rule is violated, implementation is invalid.

1️⃣ TENANCY ISOLATION (NON-NEGOTIABLE)

Every business model MUST include tenant_id.

All database queries MUST filter by tenant.

No global queries allowed.

No cross-tenant joins.

Tenant context required in all services.

Violation = security breach.

2️⃣ SERVICE LAYER ENFORCEMENT

Business logic MUST NOT exist in views.

Models contain data + invariants only.

All financial operations must pass through services.

Invalid:

view.create_invoice()

Valid:

InvoiceService.create_invoice()
3️⃣ FINANCIAL DATA INTEGRITY

No invoice without customer.

No receipt without invoice.

No duplicate payments.

No overlapping active subscriptions per vehicle/customer.

Financial operations must use database transactions.

Partial writes are forbidden.

4️⃣ RBAC SECURITY

All endpoints require authentication.

Role-based access control enforced server-side.

Object-level permissions required.

No hidden frontend-only restrictions.

Security must not depend on UI.

5️⃣ PERFORMANCE DISCIPLINE

Pagination required for list endpoints.

No unbounded queries.

Avoid N+1 queries.

Frequently filtered fields must be indexed.

Heavy operations must be async.

If system slows as tenants grow, design is flawed.

6️⃣ ERROR HANDLING STANDARD

No raw stack traces exposed.

All errors return structured response:

message

code

actionable hint

Silent failure is forbidden.

7️⃣ AUDIT TRAIL

Must log:

Invoice creation

Payment registration

Status changes

Role changes

Critical settings modifications

Audit logs must be immutable.

8️⃣ CONSISTENCY RULES

One naming convention.

One response format.

One validation strategy.

One notification pattern.

If two modules solve the same problem differently, refactor.

9️⃣ ACCESSIBILITY BUILT-IN

All forms have labels.

Keyboard navigation supported.

Color contrast meets accessibility standards.

No action depends solely on color.

Accessibility is not optional.

🔟 UX SIMPLICITY RULE

No feature without business justification.

No form exceeding cognitive load threshold (split if needed).

Dashboard must show:

Active customers

Unpaid invoices

Expiring services

Alerts

User must understand system state in under 5 seconds.

11️⃣ OBSERVABILITY

System must support:

Error logging

Performance metrics

Tenant usage tracking

You cannot scale blind.

12️⃣ TESTING REQUIREMENT

Mandatory automated tests for:

Tenant isolation

Financial flows

Permission enforcement

Expiry logic

Data integrity constraints

No merge without tests.

13️⃣ MIGRATION SAFETY

All DB migrations reversible.

No destructive changes without data preservation strategy.

Backward compatibility preserved.

14️⃣ NO OVER-ENGINEERING

Avoid premature microservices.

Avoid unnecessary abstractions.

Solve today’s validated problem, not imagined future complexity.

Elegance > complexity.