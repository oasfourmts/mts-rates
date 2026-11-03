"""Shared fixtures. Database tests need PG_TEST_DSN, e.g. PG_TEST_DSN="host=/tmp port=5433 user=postgres"."""
import os
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.importers import freight, trucking

ROOT = Path(__file__).parent.parent
DSN = os.environ.get("PG_TEST_DSN")
pg_required = pytest.mark.skipif(not DSN or not shutil.which("psql"), reason="PG_TEST_DSN not set or psql missing")


def psql(db, sql=None, file=None):
    cmd = ["psql", f"{DSN} dbname={db}", "-v", "ON_ERROR_STOP=1", "-qAt"]
    cmd += ["-f", str(file)] if file else ["-c", sql]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def run_sql(db, sql):
    subprocess.run(["psql", f"{DSN} dbname={db}", "-v", "ON_ERROR_STOP=1", "-q"], input=sql, check=True, text=True,
                   capture_output=True)


@pytest.fixture(scope="module")
def db():
    """A fresh database per test module: schema, search functions, the four trucking tabs and November freight."""
    name = "mts_test_" + uuid.uuid4().hex[:8]
    psql("postgres", f"CREATE DATABASE {name}")
    try:
        for f in sorted((ROOT / "db/migrations").glob("*.sql")):
            psql(name, file=f)
        psql(name, file=ROOT / "db/functions/rate_search.sql")
        for f in ("alexandria", "damietta", "psd_east", "sokhna"):
            res = trucking.parse((ROOT / f"tests/fixtures/trucking_{f}.csv").read_text())
            run_sql(name, trucking.to_sql(res, source_ref=f"test {f}", valid_from=date(2026, 11, 1),
                                          valid_to=date(2026, 11, 30)))
        res = freight.parse((ROOT / "tests/fixtures/freight_nov_2026.csv").read_text())
        run_sql(name, freight.to_sql(res, source_ref="test freight"))
        yield name
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
