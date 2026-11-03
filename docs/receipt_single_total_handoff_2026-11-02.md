# Single total on fully paid receipts

Branch/base: `feature/tenant-sku-sequence` / `ba89500`.

Fully paid receipts, including product sales, now show one **TOTAL** following
subtotal, discount and VAT. The separate **TOTAL AMOUNT RECEIVED** table appears
only when this receipt's payment differs from the source document's total. This
preserves clarity for installments and other unequal-payment acknowledgements.
Both browser print and PDF use the same template. No calculations, persisted data,
permissions or tenant/owner filters change. No migration is needed.

Changed task files:

- `templates/billing/receipt_print_tra.html`: conditionally render payment table.
- `billing/tests_receipt_totals.py`: full-payment HTML/PDF assert one total; existing
  installment checks continue asserting both distinct amounts.
- `docs/receipt_print_contract.md`: updated full-payment display contract.
- `docs/receipt_single_total_handoff_2026-11-02.md`: this handoff.

All unrelated user changes, including `docs/handover_cleanup_runbook.md`, were
preserved. No commit, push, rebuild or deployment was performed. Suggested commit:
one receipt presentation change containing only these task hunks/files.

Verification uses Docker Compose `web` with current source mounted at `/app` and a
temporary `/app/staticfiles` volume. Commands after collecting static assets:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test billing.tests_receipt_totals --keepdb --noinput
python manage.py test --keepdb --noinput --parallel 4
```

Checks: no issues, exit 0. Migration check: no changes detected, exit 0.
Focused tests: **11 passed in 4.059 seconds**, exit 0;
`.receipt-single-total-focused.log`.

The full suite runs through ignored runner
`.backups/receipt-totals/run_single_total_gate.py`, setting only the isolated test
database name `test_receipt_single_total_20261102` to avoid stale parallel clones.
Container command: `PYTHONPATH=/app python /app/.backups/receipt-totals/run_single_total_gate.py`.
Full suite: **779 passed in 182.923 seconds**, exit 0;
log `.receipt-single-total-full.log`. `git diff --check` passed.

Security impact: presentation-only; no new queries, writes, authorization changes,
or disclosure paths. Historical financial amounts and stock are unchanged.
The running Docker/remote service needs the normal reviewed build/release to use
the new source. No other work is deferred for this display request.
