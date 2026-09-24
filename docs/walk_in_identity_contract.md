# Walk-in POS identity and billing contract

Walk-in means a POS cart with no selected customer. Cashiers keep the existing
cart flow: an optional name, no mandatory customer profile. Billing requires a
Customer foreign key, so new walk-in sales use one hidden internal POS account
per tenant, identified by `Customer.is_pos_placeholder`. That account is not a
person and must never be selectable as a registered customer.

Every new POS quotation and invoice freezes `is_walk_in_sale` and
`walk_in_name_snapshot`. Receipt, quotation conversion/versioning, invoice
reissue and credit note copy those attributes. Prints, receipt context, lists
and invoice API read the frozen name. Registered customers continue using
their customer record. A walk-in invoice has no previous account balance; its
outstanding amount belongs only to that invoice. Account balance queries skip
walk-in invoices, even when historical data shares a customer reference with
a registered person. This does not alter invoice totals, payments or stock.

Migration `billing.0032` classifies historical documents only when a linked POS
cart has no selected customer, then follows same-customer document ancestry.
It does not guess whether a manually created document was a walk-in sale.
Historical `balance_brought_forward` values and their issued-document displays
remain as issued; new walk-in invoices record zero prior balance, and current
walk-in account summaries reflect only that invoice's remaining balance.
Before applying in production, back up the database and review existing
walk-in invoices with nonzero brought-forward values; previously issued PDFs
may already have shown another person's balance and need an explicit
correction workflow. Rollback of the schema migration would
remove the added snapshots, so deploy the previous app version against a
restored pre-migration backup if full rollback is required.

Shipping and fulfillment are a separate later phase. A walk-in delivery will
attach a one-time transaction address, without creating a customer profile.
