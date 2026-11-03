# Inventory reporting: search, valuation provenance, historical losses and workspace

Date: 2026-10-24. Branch: `feature/cart-lifecycle-management`.
Starting HEAD: `36bfa1fbe37df77dd7b3006a81b4bc233df360b3`.
Scope: all fourteen inventory reports, plus the actual-cost validation at stock-sale completion.

## Verified answers using the current local data

Read-only maintenance audit was restricted to `Organization(slug="js-tech")`,
JS-Technology, ID 3. Other organizations' financial records were not inspected.
These are the unfiltered local database values at audit time, not a certification
of physical quantities or statutory accounts.

### Why searching for 5 returned apparently unrelated rows

Previously `report()` checked every rendered cell using a case-insensitive text
substring comparison. `5` could therefore match quantity 5, amount 150000,
a date, a unit cost such as 10.123456 or a reference containing 5. It was neither
an exact numeric comparison nor a product-only search. Search applied before
pagination and changed both totals and Excel exports.

Now the default is **Names and references**: product names, SKUs, supplier names,
invoice/purchase references, descriptions and the appropriate serial/history
identifiers for that report. Financial amounts, quantities and dates are not
searched in this mode. A numeric SKU such as ITEM-5 still legitimately matches
5. **All cells (includes numbers and dates)** explicitly preserves the former
behavior when wanted. Date filters remain separate and validated. Search syntax
is literal substring matching, not an expression language or numeric comparator.

### Why Imported purchases / Imported sales are empty

The previous labels were Historical purchases (record only) and Historical sales
(record only). Both read `HistoricalInventoryRecord`, populated by explicit
historical workbook imports. They do not read live Purchase/InventorySale rows
and do not post inventory, supplier debt or sale-cost snapshots. JS-Technology
has zero records in each unfiltered imported-history report. This is expected
when no history has been imported; it is not evidence that purchases or sales
are missing. The workspace now groups these separately as Imported records,
explains their purpose, and provides an import link only to authorized users.
No synthetic history was generated to fill the empty screens.

### Why stock value exceeds purchase value

Confirmed purchases: **TZS 30,915,000** across 16 confirmed purchases.
Current stock valuation: **TZS 136,410,750**.
Five draft purchases total TZS 49,166,250 but are correctly excluded from both
confirmed purchase totals and receiving effects.

The current recorded value is reconciled as follows, using signed immutable
movement values (`quantity * unit_cost`):

| Source | TZS |
| --- | ---: |
| Opening stock | 181,050,000.00 |
| Confirmed purchase receipts | 30,915,000.00 |
| Positive adjustments | 4,200.00 |
| Negative adjustments | -60,696,555.40 |
| Stock cost removed by sales | -14,861,892.60 |
| Recorded valuation difference | -2.00 |
| **Current balance value** | **136,410,750.00** |

The TZS 2 residual belongs to LEGACY-3-2: its summed movement quantities are
zero but its summed signed movement values leave TZS 2. The report discloses
this residual instead of silently adjusting stock or confirmed history. In
general a residual must not automatically be labelled harmless rounding: it
can also reflect historical cost changes or balances without movement backing.

The largest contributor is **CAT6-BOX-SAMPLE / Dahua Cat6 UTP Box**:
602 boxes * TZS 200,000 = **TZS 120,400,000**. Its opening movement on
2026-09-26 recorded 603 boxes at TZS 200,000, with the note
"Opening sample stock after explicit unit-identity reconciliation." One box
was subsequently sold. This explains the figure mathematically. It does not
prove there are physically 602 boxes. Any sample-data correction requires a
verified stock count and an approved audited adjustment; it must not be inferred
by converting boxes/meters or rewriting historical movements.

The new expandable **How is this stock value calculated?** section shows this
bridge dynamically for the matching tenant/product set. Excel includes the same
Valuation bridge worksheet. Sorting stock value descending makes major
contributors visible first. Purchases measure receipts over a period, whereas
valuation measures remaining balances, also affected by opening stock, sales
and adjustments; the two totals are not expected to match.

### What the negative Dahua gross profit means

Five negative TZS sale snapshots currently exist: four Dahua Cat6 UTP lines and
one Hikvision line. Dahua's four lines total revenue TZS 14,000 against recorded
cost TZS 703,442.60, giving **TZS -689,442.60**. The Hikvision line records
revenue TZS 300,000 and cost TZS 350,000, giving **TZS -50,000**.

For example, invoice INV-JS-20260725-0001 records one Dahua unit with revenue
TZS 2,000 and cost TZS 100,491.80: gross profit **TZS -98,491.80**.
The old weighted cost is explained by its movement history:
`(300 * 200000 + 300 * 2000 + 10 * 70000) / 610 = 100491.803278...`.
Its sale snapshots recorded TZS 100,491.80 per unit. Later September adjustment
notes explicitly describe mixed-unit sample-data repair; later movements use
TZS 700 per meter. Changing today's buying price to TZS 700 cannot legitimately
recalculate July/August completed-sale costs.

Thus the gross-profit addition is correct for the persisted snapshots, while
the legacy unit/cost history needs business review before claiming an actual
economic loss of that size. Confirmed history is preserved. The report now
shows Loss/Profit/Break-even/FX required per line, counts negative matching
lines, explains snapshot limitations and offers a Profit result filter.
Negative amounts remain included in totals and exports. Non-TZS revenue stays
separate; no foreign-currency profit is fabricated without an FX snapshot.

## Research and design decisions

Primary sources reviewed on 2026-10-24:

- [NN/g: Data tables, four major tasks](https://www.nngroup.com/articles/data-tables/):
  report tasks include finding, comparing and reviewing values; transparent
  filter behavior supports a named search scope and consistent summary/export
  filtering rather than an unexplained search over numeric/date cells.
- [W3C tables](https://design-system.w3.org/styles/tables.html) and
  [table captions](https://www.w3.org/WAI/tutorials/tables/caption-summary/):
  retain native table semantics, scoped headers, captions and clear relationships.
- [W3C form labels](https://www.w3.org/WAI/tutorials/forms/labels/):
  explicit labels and associated help/error descriptions. Reuse `apply_tailwind`
  and the existing supplier combobox; the server validates supplier ownership.
- [IFRS Foundation: IAS 2](https://www.ifrs.org/issued-standards/list-of-standards/ias-2-inventories/):
  inventory cost and the cost recognized on sale are distinct from cumulative
  purchase receipts. This supports explaining the stock/purchase difference.
  This application report does not perform net-realisable-value assessment and
  must not be described as a certified IAS 2 balance-sheet valuation.

Design implementation: a grouped report sidebar on desktop replaces fourteen
competing pills; mobile uses an expandable grouped chooser. The report header
keeps one contextual export action and suppresses unrelated global create CTAs
on report screens. Filters precede summaries and results. Search help is visible,
numeric columns are right-aligned with tabular digits, dates use consistent ISO
display, loss states include text as well as color, and result ranges/pagination
are explicit. A scoped stylesheet uses existing JIMS colors/components without
changing unrelated screens. All fourteen report types share this layout.

## Preventing new negative snapshots

The pre-existing weighted-average cost check could run before the balance row
lock. Selected serialized units can also have higher acquisition costs than the
weighted average. Sale completion now rechecks net unit revenue against the
locked balance cost and rejects any negative comparison with actual selected
stock cost before completing the atomic transaction. Failure rolls back receipt,
payment effects, serial status and stock movements. Error messages disclose no
cost amount to unauthorized staff. Existing strict weighted-cost-floor checks
remain in place. No historical sale, purchase, serial or movement is rewritten.

## Changed files and delivery

- `inventory/reporting.py`: explicit search scopes, result filters, valuation
  bridge, stock-value ordering and typed table presentation metadata.
- `inventory/views.py`: reporting orchestration, grouped permission-filtered
  navigation and export bridge worksheet.
- `inventory/services.py`: actual locked-cost completion validation.
- `templates/inventory/report.html` and new
  `templates/inventory/includes/report_navigation.html`: shared report workspace.
- New `inventory/static/inventory/css/reports-workspace.css`: scoped layout.
- `inventory/tests_reporting.py`, new `inventory/tests_sale_cost_guard.py`,
  `inventory/browser_reporting.py`: regression, rollback and Edge browser checks.
- Architecture, purchasing contract, preceding audit and this handoff.

No model change, migration, dependency, commit, push or deployment. Preserve the
pre-existing `inventory/tests_cart_readiness.py` edit and all unrelated untracked
files. Deliver as one cohesive reporting/audit change with the completion guard
and its contract/tests included, or split reporting and the guard into two commits
only after explicit commit authorization.

## Verification

Docker tests use isolated PostgreSQL databases; native Windows Edge uses the
same Compose PostgreSQL instance with a separate test database. The business
database audit was read-only. All runs use the current workspace source.

- `docker compose run --rm --no-deps -v "${PWD}:/app" web python manage.py check`:
  exit 0, zero issues.
- Same Compose prefix with `python manage.py makemigrations --check --dry-run`:
  exit 0, no changes detected; no migration added or edited.
- Focused management arguments:
  `test inventory.tests_reporting inventory.tests_sale_cost_guard inventory.tests_financial_controls inventory.tests --keepdb`:
  **108 tests, OK**, 87.083 seconds, native exit 0.
- The selected-serial regression was strengthened to use two actual receipts:
  one serial at 100 and one at 180, weighted average 140, selling price 150.
  Re-running `test inventory.tests_sale_cost_guard --keepdb`: **2 tests, OK**,
  0.848 seconds, exit 0. Payment and stock changes roll back for the expensive
  selected serial despite its price being above the weighted average.
- Windows Edge management arguments `test inventory.browser_reporting --keepdb`:
  **2 tests, OK**, 13.956 seconds, native exit 0. Covers 1440px/390px layouts,
  supplier and date filtering, search scopes, numeric alignment, pagination,
  Excel download, stock breakdown, loss review, imported-history empty state,
  no page overflow and no JavaScript errors. Screenshots under
  `.backups/inventory-reports/` were visually inspected.
- Full management arguments `test --keepdb`: **673 tests, OK**,
  458.392 seconds, native exit 0; test database preserved.
- `git diff --check`: exit 0, only the unrelated existing line-ending notice.

The focused/full Docker runners set only the process-local test database name
before calling `execute_from_command_line`, respectively
`test_jims_reports_followup` and `test_jims_reports_followup_full`.
The Edge runner uses `test_jims_reports_followup_browser`. No settings file or
business database is changed by this isolation. PowerShell wrappers explicitly
`exit $LASTEXITCODE` so expected negative-path stderr logs do not mask the
native test result.

The first template regression run exposed a stale generated static manifest
for the new CSS. A container `collectstatic --noinput` then hit a Windows bind
mount timestamp permission error while compressing existing admin assets.
Native `.venv/Scripts/python.exe manage.py collectstatic --noinput` completed
successfully (173 unmodified, 784 post-processed, exit 0), retaining the configured
WhiteNoise storage. No manifest checks or tests were disabled. New payment tests
were corrected to assert the public BillingServiceError wrapper, preserving the
service's existing exception contract.

Logs: `.reports-followup-focused.log`, `.reports-followup-full.log`,
`.reports-followup-browser.log`.

Read-only valuation reproduction (maintenance shell, explicitly scoped tenant):

```python
from users.models import Organization
from inventory.reporting import report_rows, report_totals, valuation_bridge
organization = Organization.objects.get(slug="js-tech")
headers, rows = report_rows(organization, "stock-valuation")
print(report_totals("stock-valuation", rows))
print(valuation_bridge(organization, rows))
```


## Remaining work / limits

- Historical physical counts and mixed-unit sample corrections need verified
  business input and an approved audit-preserving adjustment; no guessed repair.
- Stock values are operational recorded acquisition values, not historical,
  retail or net-realisable values. FX snapshot capture remains separate work.
- Server-side row materialization remains as documented in the previous handoff;
  this change does not claim a large-data streaming/export performance benchmark.
- Desktop/mobile Edge checks are not live screen-reader or all-browser certification.
- The user's referenced picture was not attached; UI assessment used the actual
  templates and new authenticated screenshots rather than an unseen image.


## Daily-use search simplification (2026-10-27)

The earlier Search in selector is superseded by a single report search box.
Search always matches the appropriate names, SKUs, references and text identifiers
listed in `SEARCH_COLUMNS`. Amounts, quantities and dates are excluded; use the
existing date/supplier/result filters for those dimensions. There is no search-mode
selector or explanatory mode text to configure. The old `search_in` URL parameter
is ignored and is removed from generated pagination/export links, so an old link
cannot silently change the meaning of the simplified search. Totals, pagination
and Excel continue to use the same filtered result set. Tenant/cost/export
permissions and historical valuation/snapshot rules are unchanged.
