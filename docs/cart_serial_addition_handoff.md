# Repeated serialized product additions

Reviewed 2026-10-23 on `feature/cart-lifecycle-management`, base `0b937d6`.

## Diagnosis and design

`cart_line_form` created a new `CartLine` for every add request. Its ModelForm
excludes the parent cart, so form constraint validation could not detect the
existing `(cart, product)` pair. PostgreSQL correctly rejected the second insert
under `uniq_cart_product`. Multiple serials already belong to one product line
through `CartSerialSelection`; removing the unique constraint would undermine
that existing aggregate rather than repair the command.

The existing `cart_access` decorator already locks the authorized parent for
POST requests. The new `CartService.save_line` also enforces scope and obtains
that lock independently, protecting internal service callers. It resolves an
existing product line only after the lock, adds quantities and serials, then
validates the combined state. Editing remains replacement. Duplicate serial
selection and editing to an existing product produce field errors, preserving
the submitted HTML values. The add screen explains the behavior.

The service reuses `CartLineForm` for tenant-scoped product/serial choices, stock
limits, serial counts, pricing floors and discount authority. Merged quantity
determines effective pricing. Final discount validation, line save, selection
replacement and audit creation share one transaction. Drafts remain stock-neutral
and do not reserve serials; checkout/payment owns final availability checks.

## Research basis

- [Django 5.2 ModelForm validation](https://docs.djangoproject.com/en/5.2/topics/forms/modelforms/#validation-on-a-modelform): excluded model fields affect model validation. This explains why the form cannot enforce the omitted cart/product pair.
- [Django 5.2 transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/): atomic boundaries roll back all dependent writes on failure. A generic catch of `IntegrityError` would only mask the missing add/merge behavior.
- [Django 5.2 row locks](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update): lock the stable parent before inspecting children, including when no product line exists yet. Locking only an existing child cannot serialize two first additions.

## Security and preservation review

The service requires cart management permission, visible tenant/owner scope and
draft status. Form choices scope active products and available serials to the
tenant/product. Serial selections for nonserialized products are rejected.
Discount controls and cost-floor checks remain authoritative on the server.
No database constraint is removed; no migration, balance/movement update,
historical rewrite or package conversion is introduced. Existing unrelated
working-tree modifications were preserved. No commit or push was requested.
The pre-existing purchase-proposal migration `0016` was already applied; this
task does not change it. Keep this fix as one cohesive commit, excluding unrelated
purchasing changes and user files, if committing is subsequently authorized.

## Changed files

- `inventory/services.py`: transactional scoped add/edit service and audit.
- `inventory/forms.py`: duplicate-product edit and unsupported-serial validation.
- `inventory/views.py`: service delegation and field error rendering.
- `templates/inventory/cart_line_form.html`: add/merge guidance.
- `inventory/tests_cart_line_merge.py`: regression and PostgreSQL concurrency tests.
- `docs/cart_lifecycle.md`: supported behavior contract.
- This handoff.

## Verification

Commands use `.venv/Scripts/python.exe` from the repository root. The configured
connection reports `postgresql`; only test databases are mutated by tests.
Docker Compose access was unavailable (`permission denied` on Docker engine pipe).

- `manage.py check`: zero issues.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- `manage.py test inventory.tests_cart_line_merge inventory.tests_cart_lifecycle inventory.tests_financial_controls --keepdb`: 47 tests, OK.
- Expanded `manage.py test inventory.tests_cart_line_merge --keepdb`: 11 tests, OK, including two simultaneous first additions preserving both serials.
- `git diff --check`: clean apart from Git line-ending notices.

Coverage includes different serials, repeated serial rejection, aggregate stock
limits, invalid counts, unavailable serials, replacement edits, duplicate-product
edits, audit-failure rollback, foreign tenant denial and nonserialized merging.
Interactive browser and assistive-technology verification are not claimed.

`manage.py test --keepdb` completed 653 tests in 664.409 seconds with
`FAILED (failures=1)` (Django exit code 1). The sole failure was
`billing.tests.BillingListViewTests.test_annual_number_displays_in_list_detail_and_print_view`:
the response was `text/html; charset=utf-8` rather than `application/pdf`.
The runtime logged that WeasyPrint could not load `libgobject-2.0-0` and served
its existing printable HTML fallback. No billing code or tests were changed.
All other tests, including cart merge/concurrency tests, passed. At the original
2026-10-23 handoff, the full release gate remained blocked by the missing PDF
native libraries. That runtime repair is recorded below. Interactive browser
sign-off remains deferred; tests were not weakened or skipped.

## Windows PDF runtime repair — 2026-10-24

The original single-test failure was reproduced unchanged before repair. It was
an absent native runtime, not a billing rendering or numbering defect.

Installed the official stable MSYS2 base `20260927` at `C:\msys64`, verified
against its published SHA256:
`ad336cccfda47758b5e15cda993fbba421115cb0b126697daef1ee4dfe37209f`.
Initialized/updated MSYS2 and installed the signed
`mingw-w64-ucrt-x86_64-pango` package through pacman, including its GObject,
HarfBuzz and font dependencies. Pango is `1.58.2-1`; the application's existing
WeasyPrint remains pinned at `69.0`. No global PATH or Python package changes
were needed for Django.

Set `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\ucrt64\bin` in the ignored local
`.env`. README verification now runs through Django so `.env` is loaded and
checks actual PDF generation; standalone `python -m weasyprint` does not load
the project's `.env` automatically. Existing server processes must be restarted
after changing the runtime/configuration; no listener on port 8000 was found
during this repair.

The in-memory smoke check generated a 4,017-byte PDF with WeasyPrint `69.0`;
`pypdf` parsed one page and recovered the expected text. No business data was
read or changed by that check. The unchanged original regression is included
in `manage.py test billing.test_pdf billing.tests.BillingListViewTests --keepdb`:
**14 tests passed**, Django exit code 0.

`manage.py check` reports zero issues; `manage.py makemigrations --check
--dry-run` reports no changes. This follow-up changes only README, this handoff,
the ignored local `.env`, and the installed machine runtime. Billing code,
tests, tenant/RBAC rules, financial history and migrations remain unchanged.
Other pre-existing working-tree changes are preserved; no commit/push occurred.

Installation references: [WeasyPrint Windows setup and DLL configuration](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows),
[MSYS2 installer verification](https://www.msys2.org/docs/installer/), and the
[stable release](https://github.com/msys2/msys2-installer/releases/tag/2026-09-27).

Final full-suite verification on 2026-10-24:

```text
.venv\Scripts\python.exe manage.py test --keepdb
Ran 653 tests in 741.354s
OK
DJANGO_TEST_EXIT=0
System check identified no issues (0 silenced).
```

All 653 tests passed without modifying, skipping or weakening any test. The PDF
native-runtime failure and the previously blocked automated release gate are
resolved for this Windows/PostgreSQL environment. The remaining browser sign-off
noted above is unchanged and is separate from these automated checks.
