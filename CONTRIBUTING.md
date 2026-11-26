# Contributing

Read [AGENTS.md](AGENTS.md), the project guardrails, and the relevant domain
contract before changing a workflow. Use a feature branch and cohesive commits.

Run the application using the README instructions. Use PostgreSQL for transaction
and concurrency verification. Never run destructive test or maintenance commands
against an office or production database.

Before submitting a change, run:

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test <affected_module> --keepdb
python manage.py test --keepdb
node --test inventory/js_tests/*.test.cjs
git diff --check
```

Collect static files before tests that render templates using manifest storage.
Include migrations with their owning domain, explain compatibility and rollback,
and never rewrite applied migrations. Include permission, tenant isolation and
atomicity tests for sensitive workflows.

Pull requests should describe the problem, resulting behavior, verification,
migrations and remaining limitations. Use synthetic data in screenshots and demos.
Exclude credentials, customer exports, database backups and operational plans.

No redistribution license is currently provided. Obtain the owner's permission
before reusing or publishing this code outside the authorized repository.
