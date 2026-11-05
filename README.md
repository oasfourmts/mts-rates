# MTS Rate Management Platform

One rate database for MTS Logistics: inland transport, freight (sea, air, road, rail; import and export)
and customs clearance. An AI agent keeps it current from email (later phases); people approve every
rate and every quote.

Plan: see the "MTS Rate Management Platform — Plan" doc. Roadmap deck: "MTS Rate Platform Roadmap".

## Layout

| Path | What it holds |
| --- | --- |
| `db/migrations/` | PostgreSQL schema, applied in file-name order |
| `db/functions/` | SQL functions such as rate search |
| `app/importers/` | Parsers for the legacy Google Sheets (trucking, ocean freight) |
| `app/web/` | FastAPI app: JSON API under `/api`, sign-in through Identity-Aware Proxy |
| `frontend/` | React + TypeScript screens (Vite), served by the same app from `frontend/dist` |
| `tests/` | Unit tests (pure Python) and database tests (need `psql` and a Postgres server) |
| `tests/fixtures/` | Real rows copied from the legacy sheets |
| `app/db/` | Cloud Run jobs: `migrate` (schema, functions, app role) and `import_sheets` (one month of the sheets) |
| `templates/` | Sheets MTS fills in for the app, e.g. the customs clearance rate sheet |
| `tools/` | Scripts that build the templates |
| `setup/` | Google Cloud scripts, run in Cloud Shell: `setup_access.sh` once, then `deploy.sh` |

## Phase 1 status

- [x] Schema: reference data, rate cards, freight, transport, customs, surcharges, import issues, audit
- [x] Trucking sheet importer (4 port tabs, EGP per one-way trip)
- [x] Ocean freight sheet importer (monthly tabs; header-driven, so the Nov and Dec layouts both work)
- [x] Rate search as SQL functions
- [x] Rate search API: ocean freight (any number of boxes, per-B/L fees once, FX shown) and inland trucking
- [x] Exchange rates screen: monthly rates entered by admins (`ADMIN_EMAILS`), audited, 25% jump check
- [x] Door to port: from a factory zone, pickup trucking + ocean freight via every port, ranked by total
- [x] Rate Search screen
- [ ] Other screens: Rate Cards, Bulk Import, Customs Matrix, Expiry Dashboard, Admin
- [x] Deploy script: Cloud Run behind IAP, Cloud SQL Postgres 16, migrate and import jobs (`setup/deploy.sh`)
- [x] Customs clearance sheet (`templates/MTS_Customs_Clearance_Rates.xlsx`, built by `tools/build_customs_sheet.py`) and its importer (`app/importers/customs.py`)
- [ ] Customs Matrix screen and customs in Rate Search
- [x] Read the sheets directly with the `rates-agent` service account (`app/db/import_sheets.py`)

## Running the importers

Export a sheet tab as CSV, then:

    python -m app.importers.trucking tests/fixtures/trucking_alexandria.csv --port EGALY \
        --valid-from 2026-11-01 --valid-to 2026-11-30 --sql > /tmp/trucking.sql
    python -m app.importers.freight tests/fixtures/freight_nov_2026.csv --direction export --sql > /tmp/freight.sql
    psql "$DATABASE_URL" -f db/migrations/0001_initial.sql -f /tmp/trucking.sql -f /tmp/freight.sql

Without `--sql` the importers print a summary and every row they could not import, with the reason.

## Running the web app locally

    pip install --break-system-packages -r requirements-dev.txt
    (cd frontend && npm install && npm run build)
    AUTH_MODE=dev DATABASE_URL="postgresql+psycopg://postgres@/mts?host=/tmp&port=5433" \
        uvicorn app.web.main:create_app --factory --port 8000

Open http://localhost:8000. For screen work, run `npm run dev` in `frontend/` as well and open :5173
(it forwards `/api` to :8000). API docs are at `/api/docs`.

Sign-in: in production the app runs behind Identity-Aware Proxy, which signs every request. The app verifies
that signature (`IAP_AUDIENCE`) and admits only `@mtslogistics.net` accounts. `AUTH_MODE=dev` skips this and
must never be set on Cloud Run. Search runs in read-only transactions. `ADMIN_EMAILS` (comma-separated) lists
who may enter exchange rates until roles move into the database.

## Deploying

In Cloud Shell, signed in with an @mtslogistics.net account, after `setup/setup_access.sh` has run once:

    git clone https://github.com/oasfourmts/mts-rates.git && cd mts-rates
    ADMIN_EMAILS="you@mtslogistics.net" bash setup/deploy.sh

The script builds the image with Docker, creates what is missing (Cloud SQL, secrets, service accounts),
runs migrations, deploys the app behind IAP and imports the current month's sheets. Run it again to ship new
code. Share both rate sheets with `rates-agent@mts-rates.iam.gserviceaccount.com` (Viewer) for the import.
Import another month: `gcloud run jobs execute mts-rates-import --region=europe-west1 --wait
--args=-m,app.db.import_sheets,--month,2026-12`.
Add `,--dry-run` to review first, or `,--reload` to load unchanged tabs again after an importer change.

Customs clearance sheet: the pricing team fills in `templates/MTS_Customs_Clearance_Rates.xlsx` (uploaded to Google
Drive as a Google Sheet and shared with rates-agent). Import it with
`--args=-m,app.db.import_sheets,--customs,<SHEET_ID>` (add `,--dry-run` first). Only rows with Row status Active
are loaded; duty rates and the reference tabs update in place, clearance fees become rate cards.
The sheet's Tariff 2025 tab (general duty for about 6,000 tariff lines) is built from the Customs Encyclopedia PDF
of January 2025: `python tools/extract_tariff_pdf.py <pdf> data/tariff_2025.csv`, then
`python tools/build_customs_sheet.py`.

| Piece | Runs as | Database role |
| --- | --- | --- |
| Cloud Run service `mts-rates` (IAP, @mtslogistics.net only) | `rates-web` | `rates_app`: read all, write `fx_rates` and `audit_log` |
| Jobs `mts-rates-migrate`, `mts-rates-import` | `rates-agent` | `rates_owner` |

Passwords are generated into Secret Manager (`db-owner-password`, `db-app-password`) and never printed.

## Tests

    python -m pytest -q                  # parser and sign-in tests; database and API tests run when PG_TEST_DSN is set
    PG_TEST_DSN="host=/tmp port=5433 user=postgres" python -m pytest -q

## Rules the data follows

- Rates are never overwritten. A new offer is a new rate card; the old one expires.
- Every imported value keeps where it came from (sheet, tab, row) so a reviewer can check it.
- A value the importer cannot read with confidence is not guessed: the row goes to `import_issues`.
- Money is stored as `numeric(12,2)` with an explicit ISO currency code on every amount.
