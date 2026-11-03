"""Legacy trucking sheet: one tab per port.

Row 1: the port name, then equipment groups. Each group's first cell is "<group label> <first trucker>",
the next cells are more truckers. Rows below: an inland destination, then one EGP price per trucker
(buy price, one-way trip, port -> destination). Blank = that trucker has no price for that lane.

Usage: python -m app.importers.trucking FILE.csv --port EGALY --valid-from 2026-11-01 --valid-to 2026-11-30 [--sql]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import sqlout
from .common import Issue, Result, clean, display_name, is_blank, match_key, parse_number, read_csv, slug

# Group label at the start of a header cell -> the (equipment, weight class) pairs that price applies to
GROUPS = [
    (re.compile(r"^20'?\s*DC\s*<\s*8\s*tons?\b", re.I), [("20DV", "light")]),
    (re.compile(r"^20'?\s*DC\s+HEAVY\s*&\s*40'?\s*HC\b", re.I), [("20DV", "heavy"), ("40HC", None)]),
    (re.compile(r"^2\s*X\s*20'?\s*DC\b", re.I), [("2X20DV", None)]),
    (re.compile(r"^(?:1\s*x\s*)?40'?\s*REF\b", re.I), [("40RF", None)]),
]

PORT_CODES = {"ALEXANDRIA": "EGALY", "SOKHNA": "EGSOK", "PSD EAST": "EGPSE", "PORT SAID EAST": "EGPSE",
              "DAMIETTA": "EGDAM", "EL DEKHEILA": "EGEDK"}


@dataclass
class TruckingRate:
    port_code: str
    zone_name: str
    trucker: str            # display name
    trucker_key: str        # match key
    equipment: str
    weight_class: str | None
    price: Decimal
    row_ref: str


@dataclass
class Column:
    index: int
    trucker: str
    targets: list[tuple[str, str | None]]


def zone_code(name: str) -> str:
    return "EG-Z-" + slug(name)


def parse_header(header: list[str], issues: list[Issue]) -> list[Column]:
    columns, targets = [], None
    for i, raw in enumerate(header[1:], start=1):
        cell = clean(raw)
        if not cell:
            continue
        for pattern, group_targets in GROUPS:
            m = pattern.match(cell)
            if m:
                targets = group_targets
                cell = cell[m.end():].strip()
                break
        if targets is None:
            issues.append(Issue(f"header col {i + 1}", "skipped", "trucker column before any equipment group", raw))
            continue
        if not cell:
            issues.append(Issue(f"header col {i + 1}", "skipped", "equipment group with no trucker name", raw))
            continue
        columns.append(Column(i, cell, targets))
    return columns


def parse(text: str, port_code: str | None = None) -> Result:
    result = Result()
    table = read_csv(text)
    if not table:
        result.issues.append(Issue("file", "skipped", "empty file"))
        return result
    header = table[0]
    sheet_port = clean(header[0]).upper()
    if port_code is None:
        port_code = PORT_CODES.get(sheet_port)
        if port_code is None:
            result.issues.append(Issue("A1", "skipped", "unknown port; pass --port", header[0]))
            return result
    columns = parse_header(header, result.issues)

    for r, row in enumerate(table[1:], start=2):
        zone = clean(row[0]) if row else ""
        if not zone:
            continue
        for col in columns:
            raw = row[col.index] if col.index < len(row) else ""
            ref = f"row {r} ({zone}), col {col.index + 1} ({col.trucker})"
            if is_blank(raw):
                continue
            try:
                price = parse_number(raw)
            except ValueError:
                result.issues.append(Issue(ref, "skipped", "price is not a number", raw))
                continue
            if price is None:
                continue
            if price <= 0:
                result.issues.append(Issue(ref, "skipped", "price is 0 or less", raw))
                continue
            for equipment, weight in col.targets:
                result.rows.append(TruckingRate(port_code, zone, display_name(col.trucker), match_key(col.trucker),
                                                equipment, weight, price, ref))
    return result


def to_sql(result: Result, *, source_ref: str, valid_from: date, valid_to: date, status: str = "active") -> str:
    s = sqlout.Script("sheet", source_ref)
    seen_partners, seen_zones, cards = {}, {}, set()
    for rate in result.rows:
        seen_partners.setdefault(rate.trucker_key, rate.trucker)
        seen_zones.setdefault(zone_code(rate.zone_name), rate.zone_name)
    for key, name in sorted(seen_partners.items()):
        s.partner(name, "trucker", key)
    for code, name in sorted(seen_zones.items()):
        s.location(code, name, "inland_zone", "EG", [match_key(name)])
    for rate in result.rows:
        ck = f"{rate.trucker_key}|{rate.port_code}"
        if ck not in cards:
            cards.add(ck)
            s.rate_card(ck, mode="inland", direction="domestic", partner_kind="trucker", partner_key=rate.trucker_key,
                        rate_type="tariff", valid_from=valid_from, valid_to=valid_to, currency="EGP",
                        status=status, source_ref=source_ref)
        s.add("INSERT INTO transport_rates (rate_card_id, origin_id, destination_id, equipment_code, weight_class, "
              f"price, basis, source_row) VALUES ({sqlout.card(ck)}, {sqlout.loc(rate.port_code)}, "
              f"{sqlout.loc(zone_code(rate.zone_name))}, {sqlout.lit(rate.equipment)}, {sqlout.lit(rate.weight_class)}, "
              f"{rate.price}, 'per_trip', {sqlout.lit(rate.row_ref)});")
    for issue in result.issues:
        s.issue(issue.row_ref, issue.severity, issue.message, issue.raw)
    priced_cells = len({r.row_ref for r in result.rows})
    return s.finish(rows_read=priced_cells + len(result.issues), rows_imported=len(result.rows))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--port", help="UN/LOCODE of the port; read from cell A1 if left out")
    ap.add_argument("--valid-from", type=date.fromisoformat, required=True)
    ap.add_argument("--valid-to", type=date.fromisoformat, required=True)
    ap.add_argument("--sql", action="store_true", help="print the SQL load script instead of a summary")
    args = ap.parse_args(argv)
    with open(args.file, encoding="utf-8") as fh:
        result = parse(fh.read(), args.port)
    if args.sql:
        sys.stdout.write(to_sql(result, source_ref=f"trucking sheet: {args.file}",
                                valid_from=args.valid_from, valid_to=args.valid_to))
    else:
        print(f"{len(result.rows)} prices, {len(result.issues)} issues")
        for i in result.issues:
            print(f"  [{i.severity}] {i.row_ref}: {i.message} ({i.raw!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
