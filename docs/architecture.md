# Architecture

## Current Shape

This is a modular Django monolith. That is the right structure for the current product size because the domains share one database, one authentication model, one tenant context, and one set of templates.

The repository root is the project boundary. The virtual environment is no longer the project boundary.

## Runtime Boundary

```text
Browser
  -> internetservices.urls
  -> app urls/views
  -> forms/services/models
  -> SQLite in development, PostgreSQL-ready dependencies for production
```

`users.middleware.ActiveOrganizationMiddleware` establishes tenant/organization context for requests. Apps that store tenant-owned data should continue to make tenancy explicit in models, forms, querysets, and tests.

## App Responsibilities

`internetservices` owns Django settings, root URL routing, ASGI/WSGI entrypoints, and global context processors.

`users` owns authentication, organizations, active tenant selection, permissions, and tenant helper utilities.

`customers` owns customer records, customer documents, customer lifecycle state, and customer-facing query rules.

`services` owns internet service packages.

`products` owns product catalog records.

`billing` owns billing documents, billing line items, numbering, printable templates, receipts, and PDF-related behavior.

`audit` owns append-only audit records and audit metadata.

`templates` owns shared templates and cross-app UI fragments.

## Development Invariants

Run all Django commands from the repository root:

```powershell
.\.venv\Scripts\python.exe manage.py <command>
```

Do not put application code, templates, media, or databases under `.venv`.

Do not import across apps for convenience if the dependency is really a domain action. Prefer a small service function in the owning app.

Keep migrations committed with their app. Never edit old migrations after they have been applied outside your machine.

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

Use PostgreSQL for production rather than SQLite. The project already pins `psycopg2-binary`, but database URL parsing has not been introduced yet; add that when the deployment target is known.
