# Serialized Cart-Line Removal Research and Resolution

Audience: JBMS product, Django, security, inventory, and QA teams
Date: 2026-09-07
Scope: Removing serialized and standard lines from editable inventory carts. Checkout, paid-document reversal, and post-sale stock returns are intentionally excluded because they are separate financial/inventory lifecycles.

## Executive answer

The defect was an intent mismatch, not a serialized-stock calculation failure. JBMS already had a POST-only cart-line delete route, but neither the Current sale panel nor the cart-line edit screen exposed it. Serialized lines cannot use the standard `+`/`−` quantity control because their quantity must remain equal to the selected serial-unit count. Users therefore tried quantity `0`; `CartLineForm` correctly rejected zero as an invalid persisted quantity and displayed “Quantity must be greater than zero,” but offered no valid removal path.

The implemented design keeps the important invariant that stored line quantities are positive and gives removal its own explicit command. Every editable line now has a low-emphasis Remove action; the edit screen has a clearly separated danger area. A confirmation dialog identifies the product and explains that selected serials are cleared while stock remains unchanged. The server locks the tenant-scoped draft cart and line, captures an immutable audit snapshot, and deletes the line and its serial selections atomically.

## Research and design rationale

- Shopify exposes removal as a first-class [`cartLinesRemove`](https://shopify.dev/docs/api/storefront/latest/mutations/cartLinesRemove) mutation identified by cart and line IDs. Its [Ajax Cart API](https://shopify.dev/docs/api/ajax/reference/cart) also defines quantity `0` as removal rather than as a valid stored line quantity. The common principle is that zero represents a removal transition, not a persisted line state.
- [Django transaction guidance](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) defines `atomic()` as the boundary that commits all changes together or rolls them all back. JBMS now keeps audit creation, serial-selection cascade, and line deletion in one short transaction.
- Django's [`select_for_update()` guidance](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update) describes locking selected rows until the transaction ends. The delete path locks the exact authorized draft cart and cart line to prevent a concurrent checkout/edit race.
- The [GOV.UK button guidance](https://design-system.service.gov.uk/components/button/) recommends specific action labels, sparing use of destructive emphasis, and an additional confirmation step for consequential destructive actions. The cart keeps Remove visually secondary in the dense line list and uses the danger style only in the edit-page removal section and final confirmation.

## Current-state findings and resolution

| Concern | Previous state | Resolution |
|---|---|---|
| Serialized removal path | Only Edit was visible | Explicit Remove beside every draft line and on the edit page |
| Quantity zero | Rejected with no recovery direction | Remains invalid as stored data; error directs users to Remove item |
| Serial selections | Deletion relied implicitly on cascade | Cascade retained and explicitly regression-tested |
| Concurrent mutation | Cart and line were read without row locks | Short atomic block with tenant-scoped `select_for_update()` |
| Auditability | Draft-line deletion created no event | Immutable `inventory.cart_line.removed` snapshot with product, quantity, price, discount, serial IDs, actor, and stock-neutral metadata |
| Stock safety | Delete happened before checkout but was not asserted | Tests prove balance and movement count remain unchanged |
| Tenant/RBAC | Existing route was tenant/cart scoped | Scope retained and strengthened with an explicit line tenant predicate; forged foreign-tenant IDs return 404 |
| Lifecycle safety | Draft status was checked | GET and non-draft deletes remain rejected with 404 |
| UX safety | No confirmation | Accessible shared confirmation dialog with product-specific copy |

## Security and data-integrity assessment

`CART_MANAGE` remains required before object lookup. The cart is selected only from the active tenant and only while `DRAFT`; the line must belong to that cart and the same tenant. Product or tenant identifiers are never trusted from the POST body—the route uses server-resolved `cart_pk` and `line_pk`. This preserves non-disclosure behavior by returning 404 for unavailable lifecycle states and foreign-tenant objects.

Draft carts do not reserve or deduct inventory. Removing a serialized line deletes `CartSerialSelection` rows through the existing database cascade but does not alter `StockUnit.status`, `InventoryBalance`, or `StockMovement`. Audit creation occurs before deletion while the line ID still exists, inside the same transaction so an audit failure prevents the deletion rather than leaving an untracked mutation.

## Verification

- Focused removal/security suite: 5/5 passed cleanly.
- Full `inventory.tests` plus `inventory.tests_financial_controls`: all 72 tests completed with `OK`.
- New coverage verifies the two removal surfaces, confirmation metadata, zero-quantity guidance, serialized-selection cascade, unchanged stock and movement history, immutable audit payload, GET rejection, non-draft rejection, and cross-tenant rejection.
- Django system check passed with zero issues.
- JavaScript syntax check and `git diff --check` passed.
- After the full suite reported `OK`, PostgreSQL could not drop the temporary test database because two unrelated sessions still held it. This was teardown cleanup after successful assertions, not a test failure; the focused suite created and destroyed its database normally.

## Limitations and stopping rule

An authenticated interactive browser and live assistive-technology session were unavailable, so pixel-level and screen-reader sign-off are not claimed. Server-rendered confirmation hooks, Django responses, transactional behavior, security failures, audit data, and inventory invariants were tested. Research stopped after Shopify's explicit line-removal semantics, Django's transaction/locking guarantees, and destructive-action design guidance converged with the repository diagnosis and further retrieval was unlikely to change the solution.

## Claim-to-source ledger

| Claim | Source | Publisher | Access note | Confidence |
|---|---|---|---|---:|
| Cart removal is a distinct line mutation identified by cart and line IDs | `cartLinesRemove` | Shopify | Latest Storefront API; accessed 2026-09-07 | High |
| Quantity zero represents removal rather than a stored cart quantity | Ajax Cart API | Shopify | Accessed 2026-09-07 | High |
| `atomic()` guarantees all-or-nothing database changes | Database transactions | Django Software Foundation | Django 5.2 docs; accessed 2026-09-07 | High |
| `select_for_update()` locks selected rows through the transaction | QuerySet API | Django Software Foundation | Django 5.2 docs; accessed 2026-09-07 | High |
| Destructive actions need specific labels, restrained warning emphasis, and appropriate confirmation | Button component | GOV.UK Design System | Accessed 2026-09-07 | Medium-high |
