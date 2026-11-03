# Inventory reports: audit, implementation and handoff

Date: 2026-10-24. Scope: the fourteen reports in `/inventory/reports/`.
Branch: `feature/cart-lifecycle-management`; starting HEAD:
`0b937d63b712358d2f01a6c8fe7cb3d9d192121d`.

Follow-up: [search, valuation provenance, loss review and workspace redesign](inventory_reports_followup_2026-10-24.md) updates the search defaults and UI described here.

## Business answers

- Purchase report: select a supplier (including an inactive supplier), choose
  inclusive From/To purchase dates, and apply. Total purchases is the sum of
  confirmed receipt line totals across every matching page. Leaving supplier
  empty reports all suppliers. An optional search further narrows that total.
- Purchase value is not cash paid, supplier balance or accounts payable. Drafts,
  cancellations and imported reference-only purchases are excluded.
- Stock valuation: Total stock value (TZS) sums the displayed product values,
  each calculated as current balance quantity times recorded weighted-average
  cost, rounded to two decimals using the existing inventory money policy.
  It includes inactive catalog identities with remaining stock. Search narrows
  both the rows and total. This is current acquisition value, not retail value
  or an as-of-date valuation.
- Excel exports contain all matching rows, total rows and a Report context
  sheet documenting filters and timezone. Pagination never limits the export.

## Research and audit evidence

Sources were read on 2026-10-24. These are design inputs, not claims of a formal
WCAG certification or an accounting audit of live production data.

1. [Django aggregation](https://docs.djangoproject.com/en/5.2/topics/db/aggregation/)
   explains filtering before aggregation and join multiplication risks.
   Application: sum line-level immutable totals once; never sum parent purchase
   totals over joined lines. Apply date/supplier query filters before reading
   lines, and calculate summaries over the same searched rows as the report.
2. [Django time zones](https://docs.djangoproject.com/en/5.2/topics/i18n/timezones/)
   distinguishes UTC storage from local presentation. Application: sales dates,
   movement date filters and movement exports use the active application zone;
   Excel receives local naive datetimes because it cannot store aware values.
3. [W3C form labels](https://www.w3.org/WAI/tutorials/forms/labels/) requires
   identifiable form controls. Application: native Django fields with explicit
   labels, preserved values, field error associations and a visible error alert.
4. [W3C tables](https://www.w3.org/WAI/tutorials/tables/) explains header/data
   relationships. Application: captions, scoped column headers and existing
   responsive table wrappers.
5. Repository authority: `.codex/project_guardrails.md`,
   `.codex/project_rules.md`, `docs/architecture.md`,
   `docs/unified_purchasing_phase_0b_contract.md`, financial terms guide and
   existing tenant/RBAC policy. Legacy `authoritative_purchase_total` is retained;
   no package conversion is reintroduced.

| Area | Observed defect / audit result | Implemented behavior |
| --- | --- | --- |
| Purchase totals | No supplier selector or monetary summary | Tenant-validated supplier selection and all-page total |
| Dates | Lexicographic string comparison; invalid/reversed dates silently accepted | Django validation; inclusive database date filters; invalid HTML/export returns 400 with no data |
| Valuation | Product values only | Visible Decimal total and matching Excel footer |
| Sales dates | UTC `.date()` could select the wrong local day | Local-date selection and display |
| Gross profit | Cost/revenue snapshots retained, but invoice currency was invisible | Revenue totals grouped by currency; costs explicitly TZS; non-TZS profit unavailable without an FX snapshot |
| Sales labels | Current catalog name could rewrite historical presentation | Prefer billing description snapshot; gracefully handle absent product |
| Slow moving | Products with zero sales omitted | Include stocked products with no sale movement in configured window |
| Out of stock | New products without a balance row omitted | Include physical stock-tracked products without balance rows |
| Rankings | Sale movement totals used correctly | Preserve thresholds/windows; deterministic SKU tie-break |
| Low/dead stock | Existing threshold/window semantics appropriate | Preserve and regression-test boundaries |
| Serialized | Lifecycle report reads serial state | Preserve tenant-scoped state and invoice linkage |
| Imported history | Reference-only amounts intentionally separate | Preserve separation; add matching monetary summaries |
| Export | Aware movement datetimes could fail; text could become XLSX formulas | Local naive dates and explicit string cell types |
| GET safety | Every report could create InventorySettings | Use read-only persisted settings or unsaved defaults |
| Security | Existing server authorization present | Preserve report/export checks; independently filter report navigation; validate supplier tenant; harden tenant joins; no-store responses |
| Workspace | Hidden generic filters and irrelevant stock date inputs | Always-visible relevant controls, wrapped desktop navigation and a collapsed native mobile chooser, monetary KPIs and explanation of calculation scope |

## Implementation and preservation

Changed for this task:

- `inventory/reporting.py`: report filters, tenant-scoped read calculations and
  currency-aware totals, extracted from the report section of views.
- `inventory/views.py`: report orchestration, validation, summaries, safe XLSX
  export and filter-preserving URLs. Pre-existing purchasing/cart edits in this
  same file are preserved.
- `templates/inventory/report.html`: report filters, monetary summaries,
  accessibility semantics and explanatory copy using existing JIMS classes.
- `inventory/browser_reporting.py`: optional authenticated Edge desktop/mobile
  checks and screenshots under `.backups/inventory-reports/`.
- `inventory/tests_reporting.py`: regression coverage for all fourteen report
  routes, amounts, tenant separation, denial paths, snapshots, dates, exports,
  thresholds and pagination.
- `docs/architecture.md`: link to this report contract.
- This document.

No schema change or migration is required. No historical financial/stock record
is rewritten. No commit, push or deployment is performed. All unrelated tracked
and untracked work present at task start is preserved. A cohesive reports commit
must exclude unrelated pre-existing purchasing/cart hunks in `inventory/views.py`.

## Verification

Runtime: current workspace bind-mounted into Docker Compose `web`, PostgreSQL
16 from Compose `db`. The already-running web image is not assumed to contain
these edits. Test commands do not mutate the application's production database.

Commands (from the repository root):

```powershell
# Current workspace, including pre-existing user changes:
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py check
docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py makemigrations --check --dry-run
# Focused / full test arguments passed to Django's management runner:
python manage.py test inventory.tests_reporting inventory.tests inventory.tests_financial_controls --keepdb
python manage.py test --keepdb
# Optional Windows Edge check, using the Compose PostgreSQL instance:
.venv/Scripts/python.exe manage.py test inventory.browser_reporting --keepdb
git diff --check
```

The test invocations use a process-local `DATABASES['default']['TEST']['NAME']`
override before `execute_from_command_line`; no settings file is changed. Names:
`test_jims_reports_focused_20261024`, `test_jims_reports_full_20261024`, and
`test_jims_reports_browser_20261024`, respectively. This isolates them from
other local test sessions and leaves application data untouched.

Results:

- `check`: exit 0, zero issues.
- `makemigrations --check --dry-run`: exit 0, no changes detected.
- Initial report suite: 11 tests, OK; then expanded with three additional
  financial/history/threshold contracts for a total of 14 report tests.
- First broad focused attempt against the default shared test database: 99
  tests, one teardown error, PostgreSQL deadlock during concurrency-test flush.
  Tests were not weakened. Isolating the test database resolved the contention.
- Final focused suite: 102 tests, OK, 83.641 seconds.
- Edge browser suite: 1 test, OK, final confirmed exit 0; desktop/mobile screenshots,
  actual Excel download and invalid-date feedback verified. The final direct
  runner was used because a PowerShell stderr redirection wrapper initially
  returned 1 despite unittest reporting OK.
- `git diff --check`: exit 0; only Git's existing line-ending notices.
- Full suite: 667 tests, OK, 468.775 seconds; test database preserved.
  The redirected PowerShell wrapper returned 1 after native stderr output;
  Django's final summary is `OK`, with no test failures or errors. The same
  wrapper behavior affected the focused log; the direct browser runner confirmed
  a native exit 0. Logs retain the exact outcomes rather than hiding the wrapper
  status. No tests were skipped, weakened or deleted by this task.

Raw logs: `.reports-focused-tests.log`, `.reports-focused-isolated-tests.log`,
`.reports-full-tests.log` and `.reports-browser-tests.log` (local verification
artifacts, not application source).

## Limits and intentionally deferred work

- Authenticated Edge desktop (1440px) and mobile (390px) checks passed, including
  filters, totals, pagination, download, error feedback, no document overflow and
  no JavaScript errors. Screenshots were visually reviewed. Cross-browser and
  live screen-reader certification is not claimed. The first sandboxed browser
  attempt could not load the existing external Tailwind CDN and failed its
  layout assertion; running with network access loaded the real styles and passed.
- This is an implementation and contract audit of the inventory report workspace,
  not certification of production data, statutory accounts, billing subscription
  reports or technician work reports.
- Report rows are still materialized on the server before generic row search,
  summary and pagination, as in the established implementation. Date and supplier
  restrictions now run in SQL, related rows are eagerly loaded, and only 50 rows
  render per page. Large unfiltered reports/exports and very large supplier lists
  still need measured load testing before choosing a streaming/query redesign.
- No exchange-rate snapshot exists for stock acquisition costs. Non-TZS invoice
  revenue remains visible in its currency, but its gross profit cannot be stated
  accurately. The report marks that value unavailable instead of subtracting TZS
  stock costs from foreign-currency revenue. Implementing historical FX capture
  requires a separate financial-data contract and migration plan.
- No historical stock valuation, supplier payment/debt accounting, package
  conversion, new frontend framework, external analytics or dependency is added.
