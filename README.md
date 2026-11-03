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
| `tests/` | Unit tests (pure Python) and database tests (need `psql` and a Postgres server) |
| `tests/fixtures/` | Real rows copied from the legacy sheets |
| `setup/` | One-off Google Cloud setup scripts, run in Cloud Shell |

## Phase 1 status

- [x] Schema: reference data, rate cards, freight, transport, customs, surcharges, import issues, audit
- [x] Trucking sheet importer (4 port tabs, EGP per one-way trip)
- [x] Ocean freight sheet importer (monthly tabs; header-driven, so the Nov and Dec layouts both work)
- [x] Rate search as SQL functions
- [ ] Web API and screens (waiting on Python packages, see below)
- [ ] Customs clearance importer (sheet in progress at MTS)
- [ ] Read the sheets directly with the `rates-agent` service account

## Running the importers

Export a sheet tab as CSV, then:

    python -m app.importers.trucking tests/fixtures/trucking_alexandria.csv --port EGALY \
        --valid-from 2026-11-01 --valid-to 2026-11-30 --sql > /tmp/trucking.sql
    python -m app.importers.freight tests/fixtures/freight_nov_2026.csv --direction export --sql > /tmp/freight.sql
    psql "$DATABASE_URL" -f db/migrations/0001_initial.sql -f /tmp/trucking.sql -f /tmp/freight.sql

Without `--sql` the importers print a summary and every row they could not import, with the reason.

## Tests

    python -m pytest -q                  # parser tests; database tests run too when PG_TEST_DSN is set
    PG_TEST_DSN="host=/tmp port=5433 user=postgres" python -m pytest -q

## Rules the data follows

- Rates are never overwritten. A new offer is a new rate card; the old one expires.
- Every imported value keeps where it came from (sheet, tab, row) so a reviewer can check it.
- A value the importer cannot read with confidence is not guessed: the row goes to `import_issues`.
- Money is stored as `numeric(12,2)` with an explicit ISO currency code on every amount.
