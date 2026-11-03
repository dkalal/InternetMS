# JBMS CTA Deduplication Audit

Date: 2026-09-04

## Decision

JBMS now uses one contextual instance of a create action per page. The global topbar retains permission-gated defaults, while child templates can suppress a matching shortcut through narrow template blocks. This preserves convenience on unrelated pages without competing with a page’s own primary action.

The approach follows the [GOV.UK button guidance](https://design-system.service.gov.uk/components/button/) to avoid competing main actions, [Carbon’s action hierarchy](https://v10.carbondesignsystem.com/components/button/usage/), [WAI accessible-name guidance](https://www.w3.org/WAI/ARIA/apg/practices/names-and-descriptions/), and Django’s documented [template inheritance extension points](https://docs.djangoproject.com/en/dev/ref/templates/language/#template-inheritance).

## Implemented coverage

- Workspace: one `New customer` and one `New invoice`.
- Inventory overview: one `Start sale`.
- Sales carts: one `Start sale`, including the empty state.
- Invoice list and create form: no duplicate or self-referential invoice CTA.
- Customer create and detail: no duplicate create/self link; customer-prefilled invoice action wins.
- Billing sheet detail: one `Add item` action.
- Customer detail: one `Add Internet service` action.
- Invoice issue state: one contextual resolution action.

Row-level actions, mutually exclusive desktop/mobile variants, filters, and Back/Cancel navigation were retained because they act on different records, appear at different breakpoints, or serve different workflow semantics.

## Security and verification

No tenant-scoping, queryset, model, middleware, or authorization-enforcement logic changed. The shared topbar defaults remain permission-gated, with `Start sale` expressed through the cart-management permission context.

- Django system check passed.
- All 10 targeted action/RBAC rendering tests passed.
- The broader 230-test suite produced 227 passes. The three unrelated failures are a pre-existing quotation-print label mismatch and two date-sensitive subscription expectations that are stale on 2026-09-04.

Visual behavior was verified structurally through rendered Django responses; pixel-level browser QA was unavailable in this environment.
