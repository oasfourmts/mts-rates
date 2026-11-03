"""End-to-end: schema + importers + search functions on a real Postgres. Skipped unless PG_TEST_DSN is set,
e.g. PG_TEST_DSN="host=/tmp port=5433 user=postgres". Creates and drops its own database."""
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.importers import freight, trucking

ROOT = Path(__file__).parent.parent
DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN or not shutil.which("psql"), reason="PG_TEST_DSN not set or psql missing")


def psql(db, sql=None, file=None):
    cmd = ["psql", f"{DSN} dbname={db}", "-v", "ON_ERROR_STOP=1", "-qAt"]
    cmd += ["-f", str(file)] if file else ["-c", sql]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def db():
    name = "mts_test_" + uuid.uuid4().hex[:8]
    psql("postgres", f"CREATE DATABASE {name}")
    try:
        psql(name, file=ROOT / "db/migrations/0001_initial.sql")
        psql(name, file=ROOT / "db/functions/rate_search.sql")
        for f in ("alexandria", "damietta", "psd_east", "sokhna"):
            res = trucking.parse((ROOT / f"tests/fixtures/trucking_{f}.csv").read_text())
            from datetime import date
            sql = trucking.to_sql(res, source_ref=f"test {f}", valid_from=date(2026, 11, 1), valid_to=date(2026, 11, 30))
            subprocess.run(["psql", f"{DSN} dbname={name}", "-v", "ON_ERROR_STOP=1", "-q"], input=sql,
                           check=True, text=True, capture_output=True)
        res = freight.parse((ROOT / "tests/fixtures/freight_nov_2026.csv").read_text())
        subprocess.run(["psql", f"{DSN} dbname={name}", "-v", "ON_ERROR_STOP=1", "-q"],
                       input=freight.to_sql(res, source_ref="test freight"), check=True, text=True, capture_output=True)
        yield name
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_everything_loaded(db):
    assert psql(db, "SELECT count(*) FROM transport_rates") == "816"
    assert psql(db, "SELECT count(*) FROM freight_rates") == "18"
    assert psql(db, "SELECT count(*) FROM import_issues WHERE severity = 'skipped'") == "3"
    assert psql(db, "SELECT count(DISTINCT match_key) FROM partners WHERE kind = 'shipping_line'") == "8"


def test_trucking_search_cheapest_first(db):
    out = psql(db, "SELECT trucker || ':' || price FROM search_trucking('Alexandria', 'el sadat city', '20DV', "
                   "'light', '2026-11-10')").splitlines()
    assert out[0] in ("El Masreya:11000.00", "El Motwakel:11000.00") and out[-1] == "Ebad El Rahman:12000.00"


def test_freight_search_needs_fx_for_mixed_currencies(db):
    assert psql(db, "SELECT total_usd IS NULL, missing_fx FROM search_freight('Damietta', 'DEHAM', '20DV', "
                    "'2026-11-15')") == "t|{EUR}"
    psql(db, "INSERT INTO fx_rates VALUES ('EUR', '2026-11-01', 0.92, 'test')")
    assert psql(db, "SELECT total_usd FROM search_freight('Damietta', 'DEHAM', '20DV', '2026-11-15')") == "551.74"


def test_expired_rates_never_returned(db):
    assert psql(db, "SELECT count(*) FROM search_freight('Alexandria', 'BRNVT', '20DV', '2026-12-01')") == "0"
    assert psql(db, "SELECT count(*) FROM search_trucking('EGALY', 'Borg el Arab', '40HC', NULL, '2026-12-01')") == "0"


def test_sheet_text_cannot_inject_sql(db):
    res = trucking.parse("\"DAMIETTA\",\"20'DC < 8 tons O'Brien'); DROP TABLE partners; --\"\n\"Zone ';--\",\"100\"\n")
    from datetime import date
    sql = trucking.to_sql(res, source_ref="inject", valid_from=date(2026, 11, 1), valid_to=date(2026, 11, 30))
    subprocess.run(["psql", f"{DSN} dbname={db}", "-v", "ON_ERROR_STOP=1", "-q"], input=sql, check=True, text=True,
                   capture_output=True)
    assert psql(db, "SELECT count(*) FROM partners WHERE name LIKE 'O''brien%'") == "1"
