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

## Validation

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

## Architecture Rules

Application code belongs in the root-level Django project and app folders. The `.venv` directory is only for the Python interpreter and installed packages.

Keep domain logic close to the app that owns it. Cross-app behavior should be explicit through services, model methods, or permissions rather than hidden template or view side effects.
