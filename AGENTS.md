# JIMS repository instructions

These instructions apply to the entire repository.

## Read before editing

1. Read `.codex/project_guardrails.md` for non-negotiable product invariants.
2. Read `.codex/project_rules.md` for engineering and delivery rules.
3. Read `docs/architecture.md` and any phase/domain contract relevant to the task.
4. Inspect the current branch, working tree, migrations, tests, and existing implementation before proposing changes.

If documents conflict, use this order of authority:

1. The user's current explicit instruction.
2. `AGENTS.md`.
3. `.codex/project_guardrails.md`.
4. `.codex/project_rules.md`.
5. Domain and phase documents under `docs/`.

Stop and report the conflict instead of silently weakening security, tenant isolation, financial integrity, or historical data.

## Working-tree safety

- Preserve all unrelated tracked changes and all untracked user files.
- Never discard, overwrite, stage, commit, or push user work outside the requested scope.
- Do not rewrite pushed history unless the user explicitly requests it.
- Keep each commit cohesive. Do not mix unrelated formatting, domain, dependency, and workflow changes.
- Do not commit or push unless the user explicitly authorizes it.

## Change discipline

- Make the smallest cohesive change that satisfies the validated business need.
- Inspect and reuse existing services, forms, permissions, templates, and design-system components.
- Views may coordinate HTTP, forms, messages, and redirects; domain mutations belong in the owning service.
- Never use frontend visibility or JavaScript as an authorization boundary.
- Do not introduce a migration silently. Explain the schema need, preservation plan, rollback, and compatibility impact first.
- Never edit an already-applied migration. Add a new migration.
- Update the relevant architecture/domain contract when a business invariant changes.

## Required verification

Run the narrowest relevant tests while developing, then before handoff run:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test <affected apps or focused modules> --keepdb
python manage.py test --keepdb
```

Use the repository's Docker Compose service when that is the configured local runtime. Do not weaken, skip, delete, or xfail a failing test merely to make the gate green.

Report commands, exact outcomes, files changed, migration status, security impact, remaining risks, and intentionally deferred work.

## Current purchasing contract

- Each product has one selected sales/stock unit.
- Purchase quantity, purchase unit cost, inventory balance, and selling prices refer to that same unit.
- Purchase-package conversion is intentionally deferred and must not be exposed through forms, APIs, imports, or new transaction services.
- Legacy package fields are preservation-only until an explicitly approved archival/removal phase.
- Draft purchases never change stock.
- Receiving is server-authorized, atomic, tenant-scoped, row-locked, idempotent, and all-or-nothing.
- Confirmed purchases, lines, movements, cost snapshots, and serial history are immutable.
- Phase 2 may improve the high-volume grid and product search, but must not reintroduce package conversion or inline quick creation unless separately approved.

