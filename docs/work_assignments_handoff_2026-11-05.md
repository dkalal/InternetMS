# Makabidhiano ya Work Assignments — 2026-11-05

## Kazi iliyotekelezwa

Manager anaweza kuandaa draft, kutuma offer, kurekebisha na kutuma revision
mpya, kugawa kwa Technician mwingine kwa sababu iliyoandikwa, au kufuta kazi
kabla report haijatengenezwa. Technician anaona offer zake pekee; anaweza
kukubali revision husika, kukataa kwa sababu, au kuomba mabadiliko na kupendekeza
kiasi/tarehe. Mapendekezo hayabadilishi offer moja kwa moja.

Acceptance haifanyi kazi kuwa payable. Technician huunda Work Report moja
kutokana na assignment iliyokubaliwa, akijaza tarehe halisi na kazi iliyofanyika.
Kiasi hutoka kwenye accepted terms za server. Report iliyorudishwa hurekebishwa
na kutumwa tena. Assignment huwa COMPLETED ndani ya transaction ya report
approval. Malipo, batches, confirmation, dispute na voiding hutumia workflow
iliyokuwapo. Accepted terms hazibadilishwi na report correction.

Kila transition ina history na audit; stale revisions hukataliwa. Draft POST
iliyorudiwa hutumia signed token ili kurudisha draft ileile. Row locks na unique
constraints hulinda retries na concurrency. Sales hana ruhusa. Inactive
users/memberships hukataliwa. Super Administrator husoma kupitia support session
iliyopo; management actions zimewekewa tenant Manager pekee. Management notes
hazipo kwenye Technician HTML wala offer history snapshots.

Lists na history zina pagination; list/detail hutumia `select_related` kwa
Technician user, customer, report na history actor. Pending counters hutumia
tenant-scoped queries; hakuna external notification au infrastructure mpya.

## Baseline na Git

Branch: `feature/work-assignments`, ilianza kwenye `ba89500`.
`git fetch origin main` ilifanikiwa. Wakati wa audit, `origin/main` ilikuwa
commits 12 mbele na hakuna local commit ya ziada kwenye baseline hiyo.
Branch mpya ilihifadhi working tree iliyokuwapo; hakuna historical branch
iliyounganishwa. Mabadiliko ya billing, inventory, pricing na nyaraka nyingine
za mtumiaji hayajumuishwi kwenye feature commits.

Mtumiaji ameidhinisha feature commits moja kwa moja. Commits zilizoundwa:

- `8b3e14c`: implementation contract.
- `e9f34ec`: assignment workflow, migrations, UI na verified tests.
- Handoff hii ina documentation commit yake inayofuata.

Faili za feature pekee zimewekwa kwenye commits; screenshots, test logs na
mabadiliko mengine ya mtumiaji yameachwa nje. Hakuna push iliyoidhinishwa.

## Faili kuu

- `work_reports/models.py`, `assignment_services.py`, `services.py`: schema,
  transitions, accepted terms, audit, retries, linking na completion.
- `work_reports/assignment_forms.py`, `assignment_views.py`, `urls.py`, `forms.py`:
  forms na trusted HTTP entry points; existing routes zimehifadhiwa.
- `templates/work_reports/assignment_*.html`, `report_form.html`,
  `templates/includes/app_sidebar.html`, `work_reports/context_processors.py`:
  responsive UI, navigation na pending indicators.
- `users/permissions.py`: permission codes na non-delegable role defaults.
- `work_reports/tests_assignments.py`, `test_migrations.py`,
  `browser_assignments.py`: security, lifecycle, migration, concurrency na browser.
- [Implementation contract](work_assignments_contract.md): state/permission rules.

## Migrations na compatibility

`work_reports.0007` inaongeza assignment/history tables na nullable unique
assignment link kwenye Work Report. Reports za zamani hubaki na NULL; hakuna
historical amount, payment, stock au customer inayorekebishwa.
`work_reports.0008` inaongeza nullable unique UUID ya draft retries; hakuna
backfill ya historical rows. Existing public report routes na independent
reports zinaendelea kutumika.

Migrations zimejaribiwa kwenye disposable PostgreSQL databases pekee. Test ya
migration inathibitisha report ya zamani inabaki sahihi baada ya forward/reverse
ya schema ya assignments. Existing migration test sasa inarudisha schema kwenye
latest migration baada ya rehearsal; assertions zake hazijapunguzwa.
Rollback inayotoa tables/columns za feature inahitaji kuarchive assignment na
history data mpya kwanza. Hakuna migration ya office/live database iliyofanywa.

## Uthibitishaji

Baseline: `python manage.py test work_reports --keepdb` — tests 59, OK.
Focused assignment run baada ya draft retry protection: tests 27, OK.
Final completion authorization ina regression test ya ziada kwenye release run.

Final gates:

| Command | Matokeo |
| --- | --- |
| `python manage.py check` | No issues (0 silenced) |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `python manage.py test work_reports --keepdb` | Tests 88, OK; sekunde 129.944 |
| `python manage.py test --keepdb` | Tests 833, OK; sekunde 786.596 |
| `python manage.py test work_reports.browser_assignments --keepdb` | Tests 2, OK; 1440px na 390px |

Commands za Django zilitumia Docker Compose `web`, zikiwa na current source
mounted read-only kwenye `/workspace` na Python entrypoint badala ya startup ya
office web service. `DATABASE_URL` ilielekezwa kwenye majina tofauti ili Django
itengeneze databases za `test_assignment_*`. Browser tests zilitumia local Edge
headless na Python ya `.venv`, zikiunganisha PostgreSQL 16 ya Compose kupitia
port 5434, kwenye separate test database. SQLite haikutumika kuthibitisha locks.

Command wrapper ya mfano (PowerShell):

```powershell
docker compose run --rm --no-deps -v "${PWD}:/workspace:ro" -w /workspace `
  -e DATABASE_URL=postgresql://postgres:postgres@db:5432/assignment_release `
  --entrypoint python web manage.py test --keepdb
```

Browser smoke ilipitia Manager create/send, Technician change request, Manager
revise/resend, Technician accept/create/submit report, Manager approve/record
payment, na Technician confirm receipt. Screenshots zilipitiwa kwa desktop na
mobile; no horizontal overflow kwenye assignment detail. Browser hutumia cached
Tailwind runtime ya repository na kuzuia external HTTPS requests. Cache hiyo na
Edge zinahitajika kurudia browser smoke; hakuna external message iliyotumwa.

Local artifacts, ambazo hazijawekwa kwenye commits: `.work-assignments-*.log`
na `docs/screenshots/work_assignments_1440.png`, `work_assignments_390.png`.
Initial focused attempt ilipata container memory error; retry ilifanikiwa.
Broad runs za source ya awali zilisimamishwa na kurudiwa baada ya final fixes;
hazingatiwi kuwa release gates zilizofaulu.

## Mipaka na kujaribu ofisini

- Assignment moja ina Technician mmoja na Work Report moja tu.
- Baada ya report kuwepo, reassignment/material edits/cancellation hukataliwa;
  tumia report correction/review lifecycle.
- Draft creation tokens zina validity ya saa 24; expired form inahitaji kufunguliwa
  upya. Service calls bila token zinawakilisha drafts tofauti kwa makusudi.
- Concurrency tests zinahitaji PostgreSQL. Browser smoke inahitaji Edge,
  Playwright na cached Tailwind runtime iliyoelezwa juu.
- Default branch integration bado inahitaji review ya divergence iliyorekodiwa.
  Hakuna push, merge, deployment au office database migration iliyofanywa.

Kwa office validation, tumia isolated staging checkout/database, review commits
na utofauti dhidi ya `origin/main`, kisha fanya backup na restore rehearsal kabla
ya kupanga production rollout. Kwenye staging tu, kagua `migrate --plan`, tumia
migrations 0007/0008, na rudia gates zilizoorodheshwa juu. Tumia synthetic Manager,
Technician wawili na Sales. Pitia workflow ya browser, stale offers, reassignment,
required reasons, future planned dates na forbidden future actual dates. Hakiki
reports na payment batches za zamani zinaendelea kusomwa na kushughulikiwa.
Office deployment itahitaji hatua na idhini tofauti baada ya review.
