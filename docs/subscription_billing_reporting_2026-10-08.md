# Customer subscription billing overview

The four cards on `/customers/` use tenant-wide subscription data, independent
of search, status, and pagination. They require the existing finance-all
permission. Sales and Technician users receive no monetary summary.

- **Paid, selected month:** receipts linked to subscription-period invoices,
  selected by `payment_date` in the organization-local calendar month. Partial
  receipts count when received. A mixed invoice is attributed by the ratio of
  subscription line totals to all line totals. Legacy renewal invoices without
  line source references use linked period charges as the ratio fallback.
- **Unpaid, all months:** positive current balances on eligible issued
  subscription invoices, after receipts and issued credits. Each invoice is
  counted once. `balance_brought_forward` is excluded because it is prior debt.
  Draft, void, superseded, and cancelled invoices are excluded.
- **Expected, month/year:** persisted period `final_amount` is allocated by
  cent across its charged service months; complimentary months receive zero.
  Months without a period use the agreement's captured signup fee and known
  percent/fixed promotion when valid within its start and end dates. The year
  total adds January through December.
  These are service-charge forecasts, including charges already paid.

Cash and debt use posted document totals including any posted tax. Expected
charges use the persisted pre-tax period amount or the captured agreement fee;
no new tax is assumed for uninvoiced service. The cards therefore have
different tax bases, stated here because cash/debt and service forecasts are
different accounting measures.
Historical months without periods use the captured agreement price because
dated price changes are not modeled. Paused agreements are omitted from
uninvoiced forecasts because effective pause dates are not persisted; their
existing periods remain historical evidence. Cancelled agreements contribute
existing periods but no invented future charges. A free-month promotion has no
safe future placement without a persisted renewal period, so only persisted
complimentary months are forecast as zero.

Paid and Unpaid pills select customers with respective subscription periods
overlapping the selected service month. A mixed-status customer may appear in
both worklists when different services have different states. These are
service-status filters, not cash-receipt filters. Partial payment does not
grant coverage. Receipt and credit attribution here is read-only; it does not
change invoice accounting or entitlement.
