# Purchase action submission fix

Branch: `feature/work-assignments`; baseline: `112db09`.

The live serial validator cancelled the original submit event and immediately
called `requestSubmit()` from a resolved promise. When validation completed
without a network request (empty serials or a cached successful check), browsers
were still dispatching the original submit event and ignored the nested request.
The purchase buttons appeared to do nothing.

`inventory/static/inventory/js/serial-validation.js` now resumes submission in
a separate timer task, retaining the clicked submitter and native validation.
It abandons a scheduled submission if the validation revision changes meanwhile.
The existing server-side purchase actions and receipt confirmation remain intact.

`inventory/browser_purchase_actions.py` adds actual Edge checks for empty/cached
serial validation, all three purchase buttons, stock-neutral drafts and final
receipt confirmation. The immediate-validation regression fails twice against
the original script and passes with the fix. Browser fixtures use a test database;
they do not create real purchases.

Verification:

- Compose `python manage.py check`: no issues.
- Compose `python manage.py makemigrations --check --dry-run`: no changes detected.
- Compose `python manage.py test inventory.tests_purchase_workspace inventory.tests_purchase_contract inventory.tests_serial_validation --keepdb --noinput`: 27 passed (22.151s).
- Compose `python manage.py test --keepdb --noinput`: all 833 passed (715.228s); `.purchase-actions-full.log`.
- Local `python manage.py test inventory.browser_purchase_actions --keepdb --noinput`: 2 passed (9.438s).
- `node --test inventory/js_tests/serial-validation.test.cjs`: 5 passed.
- `node --check inventory/static/inventory/js/serial-validation.js`: passed.
- `git diff --check -- inventory/static/inventory/js/serial-validation.js`: passed.

Compose commands use `docker compose run --rm --no-deps -v "${PWD}:/app" web`.
The test runner sets `settings.DATABASES['default']['TEST']['NAME']` to
`test_purchase_actions_gates` before invoking Django's `call_command('test', ...)`
to isolate these checks from existing test databases.

No migrations, domain-service changes, permission changes, commits or pushes.
Tenant scoping, receiving permissions, atomic stock posting and immutable history
remain enforced by the existing server services. The shared serial validator is
also used by other inventory forms; its existing JavaScript and server regression
tests cover those checks. Unrelated working-tree changes and user files were
preserved. Applying the fix to the running Docker service requires its normal
build/release and refreshed static assets; that release is deferred.
