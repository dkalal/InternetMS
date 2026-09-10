# Unified Purchasing Redesign — Phase 0B Contract

## Purpose

This contract freezes the trusted purchasing behavior at commit
`86c3952569eb634a5832452cb141238b0a03e453` before the Category → Product →
Supplier Purchase → Stock → Cart workflow is redesigned. Phase 0B changes no
screen layout, URL, database schema, or existing external API contract.

## Source-of-truth branch

All phased redesign work starts from `feature/product-images-pos`. It contains
the secure multi-tenant workflow history plus product images, purchase-pack
conversion, authoritative purchase totals, weighted-average cost, and sale cost
floor enforcement. The older `main` branch is not an acceptable redesign base.

## Invariants that every later phase must preserve

1. Tenant context comes from authenticated server-side membership. Supplier,
   category, unit, product, purchase, line, balance, movement, serial, and cart
   records must belong to the same tenant.
2. A draft purchase never changes live stock. Only an authorized confirmation
   may post stock.
3. Purchase confirmation is atomic, locked, idempotent, and all-or-nothing.
   One invalid line must leave every balance, movement, serial, and purchase
   status unchanged.
4. Every confirmed purchase line creates exactly one immutable stock-in
   movement. Repeating confirmation must not create another movement or serial.
5. Inventory is stored in the product's canonical base sales/stock unit.
   Purchase-pack entry is an input convenience, not a second inventory ledger.
6. Pack quantity, pack label, conversion factor, pack cost, normalized base
   quantity, normalized cost, and authoritative monetary total are server
   validated. Posting re-derives derived values instead of trusting the browser
   or a saved draft.
7. Confirmed line snapshots remain historical truth when product defaults later
   change.
8. Weighted-average cost remains the authoritative movement-backed sale cost
   floor. Receiving higher-cost stock is allowed, but unsafe future prices are
   flagged and blocked in selling workflows.
9. Serialized products require one unique available serial per base unit.
   Duplicate serials within a line, across lines, or against existing tenant
   stock must fail before stock posting.
10. Services and non-stock items cannot be received into inventory.
11. Product images remain optional, optimized through the existing image
    pipeline, and must not become a prerequisite for purchase or stock receipt.
12. Existing purchase numbering remains tenant-specific and concurrency safe.
13. RBAC is enforced server-side. UI visibility is never an authorization
    boundary.
14. Confirmed purchases, purchase lines, stock movements, and financial history
    are not hard-deleted or casually edited.

## High-volume acceptance scenario

The baseline scenario is one supplier delivery containing three boxes of UTP
cable, five serialized routers, and many ordinary accessory product types. The
UTP cable is purchased by box but stocked and sold by meter. The entire delivery
must either post once in full or not post at all. Repeating the confirmation
request must leave balances, movements, serials, and totals unchanged.

## Phase 0B release gates

- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- Focused purchasing contract tests
- Existing inventory, product-image, cost-floor, tenant-isolation, RBAC, and
  serialized-stock tests
- Full Django test suite
- Review confirms no UI, URL, schema, or API drift

## Explicit non-goals

- Unified purchasing UI implementation
- Inline supplier, category, or product creation
- Generic multi-UoM or packaging graph
- Purchase orders, partial receiving, returns, refunds, supplier accounting,
  multi-warehouse stock, or manufacturing/repacking
- Changing cart deduction, payment, invoice, or quotation lifecycles
- Merging unrelated branches or modifying `main`

## Later phased delivery

Phase 1 introduces the unified purchase shell and explicit draft/review/confirm
actions. Phase 2 introduces the compact high-volume line grid and server-side
product search. Phase 3 introduces tenant-safe inline quick creation. Phase 4
adds the final review and cart-readiness workflow. Spreadsheet paste/import is
considered only after the grid is measured in real use.
