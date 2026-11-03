"""The Cloud Run jobs: migrations and the monthly sheet import, on a fresh database. Skipped without PG_TEST_DSN.
The Sheets API is replaced by the fixture CSVs, read back into rows the way the API returns them."""
import csv
import shutil
import uuid
from datetime import date

import psycopg
import pytest

from app.db import connect, import_sheets, migrate
from tests.conftest import DSN, ROOT, pg_required, psql

pytestmark = pg_required


@pytest.fixture
def fresh():
    name = "mts_jobs_" + uuid.uuid4().hex[:8]
    psql("postgres", f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(f"{DSN} dbname={name}", autocommit=True) as conn:
            yield name, conn
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_migrate_runs_once_and_refuses_edited_files(fresh, tmp_path):
    name, conn = fresh
    assert migrate.apply(conn, log=lambda *_: None) == ["0001_initial.sql", "0002_fx_entry.sql",
                                                       "0003_trucking_direction.sql", "0004_adabiya.sql"]
    assert migrate.apply(conn, log=lambda *_: None) == []
    assert psql(name, "SELECT count(*) FROM search_freight('EGALY', 'ITGOA', '20DV')") == "0"   # functions exist
    root = tmp_path / "db"
    shutil.copytree(ROOT / "db", root)
    (root / "migrations" / "0002_fx_entry.sql").write_text("-- edited\n")
    with pytest.raises(migrate.MigrationError, match="0002_fx_entry.sql changed"):
        migrate.apply(conn, root=root, log=lambda *_: None)


def test_app_role_can_read_and_enter_fx_only(fresh):
    name, conn = fresh
    migrate.apply(conn, log=lambda *_: None)
    migrate.grant_app_role(conn, "test-pass-1", log=lambda *_: None)
    migrate.grant_app_role(conn, "test-pass-2", log=lambda *_: None)   # rerun rotates the password
    app_dsn = f"{DSN} dbname={name}".replace("user=postgres", "user=rates_app") + " password=test-pass-2"
    with psycopg.connect(app_dsn, autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM rate_cards").fetchone() == (0,)
        app.execute("INSERT INTO fx_rates (currency, on_date, per_usd) VALUES ('EGP', '2026-11-01', 48.9)")
        app.execute("INSERT INTO audit_log (actor, action, table_name) VALUES ('t', 'fx_rate.create', 'fx_rates')")
        for forbidden in ("DELETE FROM fx_rates", "UPDATE rate_cards SET status = 'expired'",
                          "INSERT INTO currencies VALUES ('XXX', 'x')", "DROP TABLE audit_log"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(forbidden)


def fixture_fetch(sheet_id, tab):
    """The fixture tabs, in the live sheet layout the Sheets API returns (multi-row headers, trimmed rows)."""
    from tests.sheet_layout import freight_live, trucking_live
    files = {"ALEXANDRIA": "trucking_alexandria", "SOKHNA": "trucking_sokhna", "PSD EAST": "trucking_psd_east",
             "DAMIETTA": "trucking_damietta"}
    return freight_live() if tab == "November 2026" else trucking_live(files[tab], groups_row=1)


def test_import_month_then_rerun_skips(fresh):
    name, conn = fresh
    migrate.apply(conn, log=lambda *_: None)
    lines = []
    first = import_sheets.run(conn, fixture_fetch, date(2026, 11, 1), log=lines.append)
    assert [r.status for r in first] == ["imported"] * 5
    assert psql(name, "SELECT count(*) FROM transport_rates") == "816"      # same as the psql-loaded fixtures
    assert psql(name, "SELECT count(*) FROM freight_rates") == "18"
    assert psql(name, "SELECT DISTINCT valid_from || ' ' || valid_to FROM rate_cards WHERE mode = 'inland'") == \
        "2026-11-01 2026-11-30"
    again = import_sheets.run(conn, fixture_fetch, date(2026, 11, 1), log=lines.append)
    assert [r.status for r in again] == ["skipped"] * 5
    assert psql(name, "SELECT count(*) FROM rate_cards") == psql(name, "SELECT count(DISTINCT id) FROM rate_cards")
    assert psql(name, "SELECT count(*) FROM import_runs") == "5"


def test_dry_run_writes_nothing(fresh):
    name, conn = fresh
    migrate.apply(conn, log=lambda *_: None)
    res = import_sheets.run(conn, fixture_fetch, date(2026, 11, 1), only="freight", dry_run=True, log=lambda *_: None)
    assert res[0].status == "dry-run" and res[0].rows == 18
    assert psql(name, "SELECT count(*) FROM import_runs") == "0"


def test_cloud_run_connection_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "/cloudsql/mts-rates:europe-west1:mts-rates-db")
    monkeypatch.setenv("DB_USER", "rates_app")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/w:rd")
    url = connect.sqlalchemy_url()
    assert url.startswith("postgresql+psycopg://rates_app:p%40ss%2Fw%3Ard@/mts?host=")
    from sqlalchemy.engine import make_url
    assert make_url(url).password == "p@ss/w:rd"
    assert make_url(url).query["host"] == "/cloudsql/mts-rates:europe-west1:mts-rates-db"
    assert "password='p@ss/w:rd'" in connect.conninfo() or "password=p@ss/w:rd" in connect.conninfo()


def test_empty_tab_fails_and_does_not_block_a_retry(fresh):
    name, conn = fresh
    migrate.apply(conn, log=lambda *_: None)
    # 3 Nov 2026: the first live run loaded 0 rows (header not understood) yet was recorded in import_runs.
    psql(name, "INSERT INTO import_runs (source_kind, source_ref) VALUES ('sheet', 'placeholder')")
    with pytest.raises(import_sheets.EmptyTab, match="ALEXANDRIA"):
        import_sheets.run(conn, lambda s, t: [["ALEXANDRIA"], ["", "nothing here"]], date(2026, 11, 1),
                          only="trucking", log=lambda *_: None)
    assert psql(name, "SELECT count(*) FROM rate_cards") == "0"
    text = import_sheets.to_csv(fixture_fetch(None, "ALEXANDRIA"))
    ref = import_sheets.source_ref(import_sheets.TRUCKING_SHEET, "ALEXANDRIA", text)
    conn.execute("INSERT INTO import_runs (source_kind, source_ref) VALUES ('sheet', %s)", (ref,))  # empty earlier run
    res = import_sheets.run(conn, fixture_fetch, date(2026, 11, 1), only="trucking", log=lambda *_: None)
    assert res[0].status == "imported" and int(psql(name, "SELECT count(*) FROM transport_rates")) > 100
