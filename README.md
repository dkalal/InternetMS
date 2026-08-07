# JS Internet Services

Django SaaS application for tenant-scoped customer, service, inventory, billing, messaging, technician work-report, and access management.

The codebase is a modular monolith: one Django deployment and database, with domain boundaries enforced by app-level services, tenant-aware querysets, role-based permissions, and audit logs.

## Project Layout

```text
JS-InternetServices/
  manage.py
  requirements.txt
  internetservices/     # Django project settings, URLs, ASGI/WSGI
  main_app/             # Authenticated workspace landing and dashboard
  audit/                # Audit log domain
  billing/              # Documents, numbering, PDFs, billing workflows
  customers/            # Customer records and lifecycle
  custom_fields/        # Tenant-defined fields for supported records
  integrations/         # Token-authenticated external APIs
  inventory/            # Stock, purchases, carts, serials, and reports
  messaging/            # Message templates and delivery workflows
  products/             # Product catalog, categories, and units
  services/             # Internet service packages
  users/                # Auth, tenants, memberships, RBAC, support access
  work_reports/         # Technician reports and approval history
  templates/            # Shared and app-level templates
  docs/                 # Architecture and onboarding notes
```

Generated/local directories such as `.venv/`, `media/`, `staticfiles/`, `.env`, and `db.sqlite3` are intentionally excluded from Git.

## Local Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Run these commands from the repository root. Copy `.env.example` to `.env` and replace placeholders for the environment you are running. Never commit `.env` or real credentials. With no usable `DATABASE_URL` in debug mode, local development falls back to SQLite.

For a PostgreSQL-backed local environment, install Docker and run:

```powershell
docker compose up --build
```

The application is then available at `http://localhost:8000`; PostgreSQL is exposed locally on port `5434`.

## Tenancy and Access Control

- `TenantMembership` is the authoritative user-to-tenant relationship.
- The active tenant is established by `ActiveOrganizationMiddleware`; tenant-owned queries must never trust a tenant identifier supplied by the client.
- Administrator/Manager, Sales, Technician, and Super Administrator roles map to explicit permission codes. Delegated grants are restricted to a safe allow-list.
- Super Administrators enter an explicit, audited support session before accessing tenant data.
- Financial documents and approved technician reports preserve immutable ownership/history data.

See [architecture notes](docs/architecture.md), [RBAC and work-report audit](docs/multi_tenant_rbac_work_reports_audit.md), and [pricing compatibility notes](docs/technician_pricing_compatibility.md) before changing these boundaries.

## Customer Sync API

This project exposes the customer export API for AssetMS-style consumers at:

- Canonical: `GET /api/integrations/customers/`
- Compatibility alias: `GET /api/customers/`

Both endpoints require DRF token auth:

```http
Authorization: Token <token>
```

The token must belong to an active `IntegrationConsumer` user that is tied to the same organization as the customers being exported.

### Correct base URL

Use the host root as the base URL, not a UI path such as `/customers`.

Examples:

- Local development: `http://localhost:8000`
- Docker to Docker on the same compose network: `http://web:8000` or `http://internetms:8000`

Then request one of the API paths above.

### Example response shape

The list endpoint returns paginated JSON with these customer fields:

- `uuid`
- `full_name`
- `phone`
- `email`
- `address`
- `customer_status`
- `customer_type`
- `created_at`

Only active, non-deleted customers from the integration consumer's organization are returned.

## Inventory Module

The tenant-scoped inventory module is available at `/inventory/`. It reuses JIMS customers, quotations, invoices, receipts, token authentication, permissions, and audit logs.

Key lifecycle rules:

- Purchase drafts, carts, and quotations never change or reserve stock.
- Confirmed purchases and authorized adjustments create immutable movements.
- Inventory invoices require full payment; receipt creation and stock deduction are one atomic operation.
- Existing package-only invoices retain the established partial-payment lifecycle.
- Serialized units must be explicitly selected and can be sold only once.
- Historical purchase/sales imports are record-only; opening-stock imports are the explicit live-stock workflow.
- Confirmed purchases have no cancel/delete action; any future reversal workflow must create explicit audited reversal movements.

Inventory API endpoints use the same active `IntegrationConsumer` token convention under `/api/inventory/` for products, suppliers, stock, movements, and inventory invoices.

## Validation

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Before production deployment, also run `manage.py check --deploy` with production environment variables. HSTS preload is intentionally opt-in; enable it only after every subdomain is permanently HTTPS-only.

## Deployment

The Docker image runs as a non-root user, installs the native libraries required by WeasyPrint, and serves the app with Gunicorn. Production requires at least:

- `DJANGO_SECRET_KEY` with a unique high-entropy value
- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL` for PostgreSQL
- SMTP variables when outbound email is enabled

Run migrations and `collectstatic` as release steps. Uploaded media currently uses filesystem storage; configure durable object storage before running multiple ephemeral application instances.

## Architecture Rules

Application code belongs in the root-level Django project and app folders. The `.venv` directory is only for the Python interpreter and installed packages.

Keep domain logic close to the app that owns it. Cross-app behavior should be explicit through services, model methods, or permissions rather than hidden template or view side effects.
