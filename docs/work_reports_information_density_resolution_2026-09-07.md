# Work Reports Information-Density Research and Resolution

Audience: JBMS product, engineering, security, accessibility, and QA teams
Date: 2026-09-07
Scope: `/work-reports/`, `/work-reports/approvals/`, and the Report history section on `/work-reports/<id>/`. Payment lifecycle behavior, report business rules, and database schema were reviewed for impact but not redesigned.

## Executive answer

The excessive page length had two server-side causes: both list views returned an arbitrary maximum of 200 rows, while report detail returned every history event. The 200-row slice was not true pagination: users could not navigate beyond it, could not choose density, and received no total/result-range context. On smaller screens, the approval table also remained a horizontally scrolling desktop table.

The resolution applies filtering and tenant authorization to the complete queryset first, then paginates the authorized result on the server. Work Reports and Work Approvals now show 10 rows by default with 10/25/50/100 density controls, total and visible ranges, elided page navigation, preserved filters, and page-aware document titles. Report history shows the newest eight events, paginates independently, and progressively discloses long notes. The approval queue uses a compact semantic table on desktop and concise cards on mobile.

## Evidence and design rationale

- [Django 5.2 pagination](https://docs.djangoproject.com/en/5.2/topics/pagination/) provides `Paginator`, safe page retrieval, counts, ranges, and page navigation over querysets. The implementation reuses the repository's existing `paginate_queryset()` wrapper so filter preservation and density behavior remain consistent across JBMS.
- The [GOV.UK Pagination component](https://design-system.service.gov.uk/components/pagination/) recommends pagination when one page harms usability or performance, discourages infinite scroll for keyboard accessibility, places navigation after the related content, and recommends applying filters to the complete list rather than only the current page.
- IBM Carbon's [Data table guidance](https://carbondesignsystem.com/components/data-table/usage/) treats pagination as the standard way to divide large tables, pairs compact row/header heights, keeps search and filters in the table workflow, and recommends progressive disclosure for supplementary row information.
- The W3C WAI [Tables Tutorial](https://www.w3.org/WAI/tutorials/tables/) requires native table structure with header and data-cell relationships. The approval table now has a caption, scoped column headers, a non-visual action heading, and record-specific accessible action names.

## Current-state findings and implemented contract

| Concern | Previous state | Resolution |
|---|---|---|
| Work Reports result limit | `queryset[:200]`; no navigation beyond row 200 | Server-side pagination after permission and filter scoping |
| Work Approvals result limit | `reports[:200]`; displayed count meant only visible slice | Accurate authorized result count and page ranges |
| Default density | Up to 200 rows in one response | 10 rows by default; user-selectable 10/25/50/100 |
| Filter continuity | No page links existed | Search, status, payment, Technician, and page size survive navigation |
| Mobile approval queue | Desktop table forced horizontal scrolling | Dedicated compact card representation below `md` breakpoint |
| History growth | Every immutable audit event rendered at once | Eight newest events per page; independent navigation |
| Long history reasons | Full reason expanded every row vertically | Native `details` disclosure retains full content on demand |
| Accessibility | Basic table headers; generic repeated action text | Caption/scoped headers on approvals, `aria-current`, `rel`, page labels, and record-specific hidden action text |
| Page identity | Browser title did not distinguish paginated pages | Page number and total pages included when pagination is active |

## Security, RBAC, and tenancy assessment

Pagination is applied only after the existing policy layer has constrained Work Reports to the active tenant and either `VIEW_ALL` or the Technician's own membership. Approval access still requires `TECHNICIAN_WORK_REPORTS_VIEW_ALL`, and the pending queryset remains tenant-scoped and `SUBMITTED`-only. Search and Technician filters narrow those authorized querysets; they do not select a tenant or broaden ownership.

History starts from `_report_or_404()`, which resolves the report through the same tenant/RBAC policy. Events are then read only through that resolved report's related manager. Pagination changes presentation and query bounds only; it does not move authorization into templates or JavaScript. Payment selection remains limited to the current authorized page and is still revalidated by the existing server-side batch service before any write.

## Verification

- Full `work_reports` regression suite: 59/59 passed.
- New regression coverage proves Work Reports pagination, approval pagination, filter preservation, history pagination, result counts, and cross-tenant exclusion.
- Existing negative-path tests for Technician ownership, manager approval scope, cross-tenant object IDs, and unauthorized finance surfaces continue to pass.
- `manage.py check`: passed with zero issues.
- `git diff --check`: passed; line-ending notices are repository/Windows normalization warnings, not whitespace errors.

## Limitations and stopping rule

An authenticated browser automation session and live screen-reader laboratory were unavailable, so this report does not claim pixel-level cross-browser or NVDA/VoiceOver sign-off. Server-rendered structure, Django template compilation, queryset scoping, pagination behavior, accessibility attributes, and the complete Work Reports regression suite were verified. Research stopped after Django, W3C, GOV.UK, and Carbon guidance converged with the repository diagnosis and further retrieval was unlikely to alter the design.

## Claim-to-source ledger

| Claim | Source | Publisher | Access note | Confidence |
|---|---|---|---|---:|
| Querysets should be divided with countable, navigable server-side pages | Pagination | Django Software Foundation | Django 5.2 docs; accessed 2026-09-07 | High |
| Pagination improves long-list usability; filters apply to the full list; infinite scroll has keyboard costs | Pagination component | GOV.UK Design System | Accessed 2026-09-07 | High |
| Data tables support compact sizes, bottom pagination, filters, and progressive disclosure | Data table usage | IBM Carbon Design System | Updated 2026-08-27; accessed 2026-09-07 | Medium-high |
| Native table headers/cells and their relationships are required for accessible data tables | Tables Tutorial | W3C WAI | Updated 2023-02-16; accessed 2026-09-07 | High |
