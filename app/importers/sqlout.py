"""Turn parsed rows into one SQL script (a single transaction) that psql can run.

Used until the database driver can be installed; the same statements will move to the app's DB layer.
Every value goes through lit(), so sheet text can never break out of a string literal."""
from __future__ import annotations

from datetime import date
from decimal import Decimal


def lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, (list, tuple)):
        return "ARRAY[" + ",".join(lit(v) for v in value) + "]::text[]" if value else "'{}'::text[]"
    text = str(value).replace("\x00", "")
    return "'" + text.replace("'", "''") + "'"


class Script:
    def __init__(self, source_kind: str, source_ref: str):
        self.lines = [
            "BEGIN;",
            "CREATE TEMP TABLE _run ON COMMIT DROP AS WITH r AS (INSERT INTO import_runs (source_kind, source_ref) "
            f"VALUES ({lit(source_kind)}, {lit(source_ref)}) RETURNING id) SELECT id FROM r;",
            "CREATE TEMP TABLE _cards (key text PRIMARY KEY, id bigint) ON COMMIT DROP;",
            "CREATE TEMP TABLE _lines (key text PRIMARY KEY, id bigint) ON COMMIT DROP;",
        ]

    def add(self, sql: str) -> None:
        self.lines.append(sql)

    def partner(self, name: str, kind: str, key: str) -> None:
        self.add("INSERT INTO partners (name, kind, match_key) VALUES "
                 f"({lit(name)}, {lit(kind)}, {lit(key)}) ON CONFLICT (kind, match_key) DO NOTHING;")

    def location(self, code: str, name: str, kind: str, country: str | None, aliases: list[str]) -> None:
        self.add("INSERT INTO locations (code, name, kind, country_code, aliases) VALUES "
                 f"({lit(code)}, {lit(name)}, {lit(kind)}, {lit(country)}, {lit(aliases)}) "
                 "ON CONFLICT (code) DO UPDATE SET aliases = "
                 "ARRAY(SELECT DISTINCT unnest(locations.aliases || EXCLUDED.aliases));")

    def rate_card(self, key: str, *, mode, direction, partner_kind, partner_key, rate_type, valid_from, valid_to,
                  currency, status, source_ref, notes=None) -> None:
        approved = "'legacy-sheet-import', now()" if status == "active" else "NULL, NULL"
        self.add(
            "WITH c AS (INSERT INTO rate_cards (mode, direction, partner_id, rate_type, valid_from, valid_to, currency, "
            "status, import_run_id, source_ref, approved_by, approved_at, notes) SELECT "
            f"{lit(mode)}, {lit(direction)}, p.id, {lit(rate_type)}, {lit(valid_from)}, {lit(valid_to)}, "
            f"{lit(currency)}, {lit(status)}, (SELECT id FROM _run), {lit(source_ref)}, {approved}, {lit(notes)} "
            f"FROM partners p WHERE p.kind = {lit(partner_kind)} AND p.match_key = {lit(partner_key)} RETURNING id) "
            f"INSERT INTO _cards SELECT {lit(key)}, id FROM c;")

    def issue(self, row_ref: str, severity: str, message: str, raw: str = "") -> None:
        self.add("INSERT INTO import_issues (import_run_id, row_ref, severity, message, raw) "
                 f"SELECT id, {lit(row_ref)}, {lit(severity)}, {lit(message)}, {lit(raw or None)} FROM _run;")

    def finish(self, rows_read: int, rows_imported: int) -> str:
        self.add(f"UPDATE import_runs SET rows_read = {rows_read}, rows_imported = {rows_imported} "
                 "WHERE id = (SELECT id FROM _run);")
        self.add("COMMIT;")
        return "\n".join(self.lines) + "\n"


def loc(code: str) -> str:
    return f"(SELECT id FROM locations WHERE code = {lit(code)})"


def card(key: str) -> str:
    return f"(SELECT id FROM _cards WHERE key = {lit(key)})"


def line(key: str) -> str:
    return f"(SELECT id FROM _lines WHERE key = {lit(key)})"
