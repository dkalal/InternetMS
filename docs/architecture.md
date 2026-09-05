# Architecture

## Current Shape

This is a modular Django monolith. That is the right structure for the current product size because the domains share one database, one authentication model, one tenant context, and one set of templates.

The repository root is the project boundary. The virtual environment is no longer the project boundary.

## Runtime Boundary

```text
Browser
  -> internetservices.urls
  -> ActiveOrganizationMiddleware
  -> app urls/views
  -> permission + tenant policy checks
  -> forms/services/models
  -> SQLite for simple local development or PostgreSQL via DATABASE_URL
```

`users.middleware.ActiveOrganizationMiddleware` establishes tenant/organization context for requests. `TenantMembership` is the authoritative relationship used by request-time RBAC. Apps that store tenant-owned data must keep tenancy explicit in models, forms, querysets, services, audit events, and tests.

## App Responsibilities

`internetservices` owns Django settings, root URL routing, ASGI/WSGI entrypoints, and global context processors.

`main_app` owns the role-aware post-login workspace landing.

`users` owns authentication, organizations, active tenant selection, memberships, permission grants, and audited support access.

`customers` owns customer records, customer documents, customer lifecycle state, and customer-facing query rules.

`services` owns internet service packages.

`products` owns product catalog records, categories, tenant units of measure, and pricing tiers.

`billing` owns billing documents, billing line items, numbering, printable templates, receipts, and PDF-related behavior.

`audit` owns append-only audit records and audit metadata.

`inventory` owns suppliers, purchases, balances, stock movements, serialized units, carts, imports, and inventory reports.

`custom_fields` owns tenant-defined fields and values for supported domain records.

`messaging` owns message templates, previews, and delivery workflows.

`work_reports` owns technician work reports, approval transitions, manual Technician payment acknowledgements, and immutable report/payment history. It does not own billing, payroll, accounting, or money movement.

`templates` owns shared templates and cross-app UI fragments.

`integrations` owns authenticated read-only export APIs used by external systems, including the customer sync contract for AssetMS consumers.

## Development Invariants

Run all Django commands from the repository root:

```powershell
.\.venv\Scripts\python.exe manage.py <command>
```

Do not put application code, templates, media, or databases under `.venv`.

Do not import across apps for convenience if the dependency is really a domain action. Prefer a small service function in the owning app.

Keep migrations committed with their app. Never edit old migrations after they have been applied outside your machine.

Treat tenant identity as server-established state. Do not filter tenant-owned data using a tenant ID accepted directly from request data. Require an active membership, use tenant-scoped managers/querysets, and test cross-tenant denial paths.

Permission codes in `users.permissions` are the stable authorization contract. Views and services should fail closed with `require_permission`; templates may hide unavailable actions, but UI visibility is never an authorization boundary.

Super Administrator access to tenant data requires an explicit support session with a reason. Preserve the audit trail when extending this workflow.

### Inventory boundaries

`products.Product` remains the shared product/service catalog and `billing.BillingDocument` remains the only quotation, invoice, and receipt system. The `inventory` app owns suppliers, purchases, balances, immutable movements, serial units, carts, sale-cost records, reports, imports, and inventory APIs.

All balance mutations go through `InventoryService` under `transaction.atomic()` with row locks. `BillingService.create_receipt_from_invoice()` locks the invoice and invokes inventory completion in the same transaction only for catalog/inventory sales. This makes retries idempotent and prevents negative stock or double-selling serialized units.

Keep uploaded media and generated files out of source control. Use `.gitignore` for local artifacts and a real object store for production uploads when deploying.

## Production Notes

Use environment variables for secrets and host configuration:

```text
DJANGO_SECRET_KEY
DJANGO_DEBUG
DJANGO_ALLOWED_HOSTS
EMAIL_HOST_USER
EMAIL_HOST_PASSWORD
DEFAULT_FROM_EMAIL
```

The settings include WhiteNoise static-file support. For production, run:

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic
```

Use PostgreSQL for production rather than SQLite. `DATABASE_URL` is parsed by Django settings and production connections are reused with a finite connection age.

The application enables secure cookies, HTTPS redirect, proxy TLS handling, HSTS, MIME sniffing protection, and frame denial when debug mode is disabled. HSTS preload remains opt-in because enabling it is a long-lived operational commitment.

Filesystem media storage is suitable for a single persistent instance only. Use durable object storage before scaling across ephemeral or multiple application instances.

## Integration Contract

The customer sync contract is intentionally read-only and token-authenticated.

Base rule:

- Do not point consumers at a UI route such as `/customers`
- Do point them at the host root, then append the API path

Supported customer endpoints:

- `GET /api/integrations/customers/`
- `GET /api/customers/` as a compatibility alias

Payload note:

- `uuid` is the stable machine identifier for linking and updates
- `display_label` is the human-friendly label for dropdowns and tables
- The consumer UI should render `display_label` and keep `uuid` as the selected value

Deployment note:

- In Docker, `localhost` inside a container refers to that same container, not the other service
- Use the compose service name or network alias when the consumer runs in a different container
