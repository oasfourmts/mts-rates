"""Apply database migrations, refresh SQL functions, and set up the web app's limited role.

    python -m app.db.migrate            # uses DATABASE_URL, or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD

Runs as the database owner (Cloud Run job `mts-rates-migrate`). Safe to run again:
- each file in db/migrations runs once, in name order, and is recorded in schema_migrations with its SHA-256;
  a recorded file whose contents changed stops the run (migrations are never edited once applied);
- db/functions/*.sql are CREATE OR REPLACE, so they are applied every time;
- when APP_DB_PASSWORD is set, the role rates_app gets that password and only what the web app needs:
  read every table, write exchange rates and the audit log. Nothing else.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from app.db.connect import conninfo

ROOT = Path(__file__).resolve().parents[2] / "db"
APP_ROLE = "rates_app"


class MigrationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(conn: psycopg.Connection, root: Path = ROOT, log=print) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 "filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    done = dict(conn.execute("SELECT filename, sha256 FROM schema_migrations").fetchall())
    applied = []
    for path in sorted((root / "migrations").glob("*.sql")):
        digest = sha256(path)
        if path.name in done:
            if done[path.name] != digest:
                raise MigrationError(f"{path.name} changed after it was applied. Add a new migration instead.")
            continue
        log(f"applying {path.name}")
        # Each file manages its own transaction (BEGIN ... COMMIT), so it runs as one script.
        conn.execute(path.read_text())
        conn.execute("INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)", (path.name, digest))
        applied.append(path.name)
    for path in sorted((root / "functions").glob("*.sql")):
        log(f"refreshing {path.name}")
        conn.execute(path.read_text())
    return applied


def grant_app_role(conn: psycopg.Connection, password: str, log=print) -> None:
    role = sql.Identifier(APP_ROLE)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    conn.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
        sql.SQL("ALTER" if exists else "CREATE"), role, sql.Literal(password)))
    for stmt in (
        # CONNECT on the database and USAGE on schema public are PUBLIC by default in Postgres 16; granting them
        # again would need the database owner (cloudsqlsuperuser on Cloud SQL), which the jobs' role is not.
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r}",
        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {r}",
        "GRANT INSERT, UPDATE ON fx_rates TO {r}",
        "GRANT INSERT ON audit_log TO {r}",
        "GRANT USAGE ON SEQUENCE audit_log_id_seq TO {r}",
        # Tables added by later migrations are readable too, without a new grant each time.
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {r}",
    ):
        conn.execute(sql.SQL(stmt).format(r=role))
    log(f"role {APP_ROLE}: read all tables, write fx_rates and audit_log")


def main() -> int:
    try:
        with psycopg.connect(conninfo(), autocommit=True) as conn:
            applied = apply(conn)
            if os.environ.get("APP_DB_PASSWORD"):
                grant_app_role(conn, os.environ["APP_DB_PASSWORD"])
    except (MigrationError, psycopg.Error) as e:
        print(f"MIGRATION FAILED: {e}", file=sys.stderr)
        return 1
    print(f"done: {len(applied)} new migration(s)" + (f": {', '.join(applied)}" if applied else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
