# Customer-tier sales pricing policy

## Contract

New carts, quotations, invoices, and API invoices default to **Customer tier
(automatic)**. The selected customer's profile resolves to one concrete catalog
category:

| Customer pricing tier | Effective catalog category |
| --- | --- |
| Standard | Standard |
| Technician | Technician |
| Wholesale | Wholesale |

Staff can explicitly override an individual transaction with Standard,
Technician, or Wholesale. An explicit override always wins over the customer
profile. Walk-in sales using automatic mode resolve to Standard.

Wholesale pricing is never granted merely because a quantity is large. It
requires an effective Wholesale category, `allow_wholesale`, a configured
`wholesale_price`, and a quantity at or above `wholesale_min_quantity`. Otherwise
the selling price is used. Technician pricing falls back to selling price when a
technician price is not configured.

## Financial integrity

The policy is evaluated while building an editable transaction. Cart lines are
repriced when draft sale details change. On document creation, the authoritative
server-side resolver runs again. Stored line `unit_price`, `base_unit_price`, and
`pricing_mode` remain immutable financial snapshots through quotation conversion
and invoice reissue; later customer or catalog changes do not rewrite history.

The internal Legacy Retail category remains only for records created before the
explicit-category rollout. Existing records are not migrated to automatic mode.

## Channel parity

The same mapping is enforced by POS cart pricing, billing document creation, and
the inventory invoice API. Browser calculations are previews only; server-side
pricing remains authoritative.

## POS financial-control authorization

No new workspace roles are introduced. Administrator / Manager retains full
control and can grant three independent exceptions under Team > Manage access:

- override customer category, with an allow-list of Standard, Technician and
  Wholesale;
- apply cart discount, with percentage and/or fixed TZS caps (the lower cap
  wins); and
- edit the cart tax rate as a free decimal value.

Without the corresponding grant, the customer-category, discount, or tax-rate
control is disabled and its posted value is ignored server-side. The invoice API
enforces the same boundary. Discounts above the configured cap are rejected.
