# Internet package invoice description restoration

Branch: `feature/work-assignments`.
Base: `112db09814ee16c823e9ea1ec6fb54755953a9a0`.

## Cause and scope

`SubscriptionBillingService.format_invoice_description` still generates the
inclusive service-period range and speed, and renewal stores that description on
the billing line. The invoice PDF route selects `billing/sales_document_print.html`.
That template selected a package's name and omitted its saved description. The
legacy `billing/includes/print_items_table.html` had the same omission.

Commit `2c01238` removed the secondary description together with duplicated product
copy. That suppression also hid meaningful subscription billing periods. The new
regression reproduced the problem before the fix: one test failed in both print
template subtests while the stored line description still contained the dates.

Both templates now show a nonempty, distinct saved package description under its
name. The active sales template uses a small wrapping secondary text style. Product
duplicate-description suppression remains unchanged, as do custom items, internal
notes, customer data, quantity, prices, discounts, tax and totals.

## Data and security

This is a read-only presentation repair. Existing stored descriptions become
visible without updating issued/paid invoices, subscription periods, receipts or
audit history. No migration, backfill, reissue, financial mutation, permission grant
or database maintenance is required. Existing tenant/owner authorization remains
on the PDF endpoint, and automatic HTML escaping remains enabled.

Prints use saved wording, including legacy month-only descriptions. They do not
reconstruct dates from issue/due dates, quantity, current package details or current
subscription state. Documents genuinely missing a saved billing description still
need a separately reviewed correction; this repair does not invent historical data.

## Files

* `templates/billing/sales_document_print.html`
* `templates/billing/includes/print_items_table.html`
* `billing/tests_package_descriptions.py`
* `docs/subscription_billing_reporting_2026-10-08.md`
* This handoff.

All other pre-existing tracked and untracked work is preserved. No commit, push,
deployment or live-container rebuild is included. Recommended commit scope is this
single print-description repair, its regression tests and documentation.

## Verification

The current source is mounted into one-off Compose containers, with generated
static assets under the container's temporary directory. Test databases are
separate from the live application database.

The `.backups/document_flow_gates.py` local verification wrapper runs
`python manage.py check`, `python manage.py makemigrations --check --dry-run` and
`python manage.py test <labels> --keepdb --noinput` after collecting matching test
assets. Focused labels:

```text
billing.tests_package_descriptions billing.tests billing.tests_document_flow billing.tests_receipt_totals
```

* Before fix: stored-period rendering regression failed in both template subtests.
* Django check: passed, zero issues.
* Migration drift: passed, no changes detected.
* Focused suite: 120 tests passed in 35.623 seconds.
* Final markup regression suite: 6 tests passed in 3.218 seconds.
* Full PostgreSQL suite with four workers: 839 tests passed in 192.514 seconds.
* Changed-file `git diff --check`: passed.

Coverage includes stored start/end dates, real PDF text extraction, HTML fallback,
legacy wording, duplicate/empty descriptions, XSS escaping, unchanged stored amounts
and descriptions, and denied cross-tenant invoice access. Existing billing tests
also retain the product-description and internal-note suppression contracts.

The full suite uses the same wrapper without labels, with
`JIMS_GATE_TEST_DATABASE=test_jims_package_description_release` and
`JIMS_TEST_PARALLEL=4`. Results are in `.package-description-full.log`;
focused evidence is in `.package-description-focused.log`.
Final markup evidence is in `.package-description-final-regression.log`.
