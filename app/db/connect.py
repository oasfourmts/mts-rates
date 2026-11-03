"""Database connection settings shared by the web app and the Cloud Run jobs.

Either DATABASE_URL (local work), or the parts Cloud Run is given:
    DB_HOST      /cloudsql/PROJECT:REGION:INSTANCE (the Cloud SQL socket directory) or a host name
    DB_NAME      database name (default mts)
    DB_USER      role to connect as
    DB_PASSWORD  injected from Secret Manager, never written to disk or logs
    DB_PORT      only if not 5432
"""
from __future__ import annotations

import os

DEFAULT_LOCAL = "postgresql+psycopg://postgres@/mts?host=/tmp&port=5433"


def sqlalchemy_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if not os.environ.get("DB_HOST"):
        return DEFAULT_LOCAL
    from sqlalchemy.engine import URL

    url = URL.create("postgresql+psycopg", username=os.environ["DB_USER"], password=os.environ.get("DB_PASSWORD"),
                     database=os.environ.get("DB_NAME", "mts"),
                     query={"host": os.environ["DB_HOST"], **({"port": os.environ["DB_PORT"]} if os.environ.get("DB_PORT") else {})})
    return url.render_as_string(hide_password=False)


def conninfo() -> str:
    """libpq connection string for psycopg (the jobs talk to Postgres directly, without SQLAlchemy)."""
    from psycopg.conninfo import make_conninfo

    if os.environ.get("DATABASE_URL"):
        from sqlalchemy.engine import make_url

        u = make_url(os.environ["DATABASE_URL"])
        params = {"user": u.username, "password": u.password, "dbname": u.database, "host": u.host, "port": u.port}
        params.update(u.query)
        return make_conninfo(**{k: v for k, v in params.items() if v is not None})
    if not os.environ.get("DB_HOST"):
        return make_conninfo(host="/tmp", port=5433, user="postgres", dbname="mts")
    return make_conninfo(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                         dbname=os.environ.get("DB_NAME", "mts"), user=os.environ["DB_USER"],
                         password=os.environ.get("DB_PASSWORD", ""))
