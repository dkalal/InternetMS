# JS Internet Services

Django application for managing customers, services, products, billing, audit records, and tenant-aware user access for JS Internet Services.

## Project Layout

```text
JS-InternetServices/
  manage.py
  requirements.txt
  internetservices/     # Django project settings, URLs, ASGI/WSGI
  audit/                # Audit log domain
  billing/              # Documents, numbering, PDFs, billing workflows
  customers/            # Customer records and lifecycle
  products/             # Product catalog
  services/             # Internet service packages
  users/                # Auth, organizations, tenancy, permissions
  templates/            # Shared and app-level templates
  media/                # Local development uploads
  docs/                 # Architecture and onboarding notes
  .venv/                # Local virtual environment only
```

The application now runs from the repository root:

```powershell
cd C:\Users\JSSD\JS-InternetServices
.\.venv\Scripts\python.exe manage.py runserver
```

## Setup

```powershell
cd C:\Users\JSSD\JS-InternetServices
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

For local configuration, copy values from `.env.example` into your environment variables or your deployment secret manager. Do not commit real secrets.

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
.\.venv\Scripts\python.exe manage.py test
```

## Architecture Rules

Application code belongs in the root-level Django project and app folders. The `.venv` directory is only for the Python interpreter and installed packages.

Keep domain logic close to the app that owns it. Cross-app behavior should be explicit through services, model methods, or permissions rather than hidden template or view side effects.
