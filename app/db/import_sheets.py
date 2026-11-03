"""Import one month of the legacy Google Sheets into the rate database.

    python -m app.db.import_sheets --month 2026-11 [--dry-run] [--only trucking|freight]

Runs as the Cloud Run job `mts-rates-import` under the rates-agent service account, which must have Viewer
access to both sheets. Each tab is read through the Sheets API (the same text the sheet shows), parsed by
app/importers, and loaded in one transaction per tab.

- Trucking tabs have no dates, so their prices are valid for the whole --month.
- The freight tab for the month is named like "November 2026"; validity comes from its own columns.
- A tab whose exact contents were already imported is skipped (the run's source_ref carries a SHA-256 of the
  text), so running the job twice never duplicates rate cards. A changed tab is a new import with new rate
  cards: rates are never overwritten.
- Cells the parsers cannot read with confidence go to import_issues; nothing is guessed.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import os
import sys
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import psycopg

from app.db.connect import conninfo
from app.importers import freight, trucking
from app.importers.common import clean

TRUCKING_SHEET = os.environ.get("TRUCKING_SHEET_ID", "1t8rVYcNeku5JZG7vEfXAAoj-prAFdgGAheVs_byg2xI")
FREIGHT_SHEET = os.environ.get("FREIGHT_SHEET_ID", "1VAGxvxIU5E1GiNjbSuO4DmoTekKWvgK7U8z7RUxPoxM")
TRUCKING_TABS = {"ALEXANDRIA": "EGALY", "SOKHNA": "EGSOK", "PSD EAST": "EGPSE", "DAMIETTA": "EGDAM"}
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class SheetAccessError(RuntimeError):
    pass


def sheets_fetcher():
    """Returns fetch(sheet_id, tab) -> rows, using the job's own service account (no key file)."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=[SCOPE])
    session = AuthorizedSession(creds)

    def fetch(sheet_id: str, tab: str) -> list[list[str]]:
        rng = quote("'" + tab.replace("'", "''") + "'", safe="")
        r = session.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}",
                        params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"}, timeout=60)
        if r.status_code in (403, 404):
            who = getattr(creds, "service_account_email", "the job's service account")
            raise SheetAccessError(f"Cannot read tab '{tab}' of sheet {sheet_id} ({r.status_code}). "
                                   f"Share the sheet with {who} as Viewer, and check the tab name.")
        r.raise_for_status()
        return r.json().get("values", [])

    return fetch


def to_csv(rows: list[list[str]]) -> str:
    """The Sheets API drops trailing empty cells; pad rows to one width so it reads like a CSV export."""
    width = max((len(r) for r in rows), default=0)
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for r in rows:
        w.writerow(list(r) + [""] * (width - len(r)))
    return out.getvalue()


@dataclass
class TabResult:
    tab: str
    status: str          # imported, skipped, dry-run, empty
    rows: int
    issues: int


def source_ref(sheet_id: str, tab: str, text: str) -> str:
    return f"sheet {sheet_id} tab '{tab}' sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def already_imported(conn, ref: str) -> bool:
    """Only a run that created rate cards counts: an earlier run that loaded nothing must not block a retry."""
    return conn.execute(
        "SELECT 1 FROM import_runs ir WHERE ir.source_ref = %s "
        "AND EXISTS (SELECT 1 FROM rate_cards rc WHERE rc.import_run_id = ir.id)", (ref,)).fetchone() is not None


class EmptyTab(RuntimeError):
    """A tab produced no rows at all: its layout was not understood. Nothing from it is loaded."""


def run(conn, fetch, month: date, *, only: str | None = None, dry_run: bool = False,
        freight_tab: str | None = None, log=print) -> list[TabResult]:
    first = month.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    results = []
    jobs = []
    if only in (None, "trucking"):
        jobs += [("trucking", TRUCKING_SHEET, tab, port) for tab, port in TRUCKING_TABS.items()]
    if only in (None, "freight"):
        jobs.append(("freight", FREIGHT_SHEET, freight_tab or first.strftime("%B %Y"), None))

    for kind, sheet_id, tab, port in jobs:
        text = to_csv(fetch(sheet_id, tab))
        ref = source_ref(sheet_id, tab, text)
        if kind == "trucking":
            parsed = trucking.parse(text, port)
            script = lambda: trucking.to_sql(parsed, source_ref=ref, valid_from=first, valid_to=last)  # noqa: E731
        else:
            parsed = freight.parse(text)
            script = lambda: freight.to_sql(parsed, source_ref=ref, direction="export")  # noqa: E731
        res = TabResult(tab, "dry-run", len(parsed.rows), len(parsed.issues))
        if not parsed.rows:
            res.status = "empty"
        elif not dry_run:
            if already_imported(conn, ref):
                res.status = "skipped"
            else:
                conn.execute(script())   # one BEGIN ... COMMIT script per tab
                res.status = "imported"
        log(f"{kind:8} {tab:16} {res.status:9} {res.rows:4} rows, {res.issues} issue(s)")
        shown = parsed.issues if dry_run else parsed.issues[:5]    # a trial run is for review: list them all
        for i in shown:
            log(f"           [{i.severity}] {i.row_ref}: {i.message}" + (f" ({clean(i.raw)})" if i.raw else ""))
        if len(parsed.issues) > len(shown):
            log(f"           ... {len(parsed.issues) - len(shown)} more in import_issues")
        results.append(res)
    empty = [r.tab for r in results if r.status == "empty"]
    if empty:
        raise EmptyTab(f"no rates read from: {', '.join(empty)}. The tab layout was not understood (see the issues "
                       "above); nothing from those tabs was loaded.")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import one month of the legacy rate sheets")
    ap.add_argument("--month", required=True, help="e.g. 2026-11")
    ap.add_argument("--only", choices=("trucking", "freight"))
    ap.add_argument("--freight-tab", help='tab name if not like "November 2026"')
    ap.add_argument("--dry-run", action="store_true", help="read and parse, write nothing")
    args = ap.parse_args(argv)
    y, m = (int(x) for x in args.month.split("-"))
    try:
        fetch = sheets_fetcher()
        with psycopg.connect(conninfo(), autocommit=True) as conn:
            run(conn, fetch, date(y, m, 1), only=args.only, dry_run=args.dry_run, freight_tab=args.freight_tab)
    except (SheetAccessError, EmptyTab, psycopg.Error) as e:
        print(f"IMPORT FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # google-auth: no credentials, or the token request failed
        if type(e).__module__.startswith("google.auth"):
            print(f"IMPORT FAILED: no Google credentials for the Sheets API ({type(e).__name__}). "
                  "Run this as the Cloud Run job, which uses the rates-agent service account.", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
