"""Rebuild the fixture CSVs into the layout the live Google Sheets have, as the Sheets API returns it.

The fixtures were saved with every header row joined into one; the live tabs (seen 3 Nov 2026) have:
- trucking: port in A1 (merged A1:A2), equipment groups on row 2 (merged, text in the group's first cell),
  trucker names on row 3, prices from row 4;
- freight: title on row 1, notes on row 2, column groups on row 3, column names on row 4, offers from row 5.
The Sheets API leaves out trailing empty cells, so rows are trimmed the same way.
"""
import csv
import re
from pathlib import Path

from app.importers.trucking import GROUPS

FIXTURES = Path(__file__).parent / "fixtures"
FREIGHT_GROUPS = ["SHIPPER ROUTE DATA", "CARRIER", "ROUTING & SCHEDULE", "RATES & SURCHARGES (USD per container)",
                  "TERMS"]


def read(name: str) -> list[list[str]]:
    with open(FIXTURES / f"{name}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def trim(rows: list[list[str]]) -> list[list[str]]:
    return [r[: max((i + 1 for i, c in enumerate(r) if c != ""), default=0)] for r in rows]


def trucking_live(name: str) -> list[list[str]]:
    rows = read(name)
    head = rows[0]
    groups, names = [""], [""]
    for cell in head[1:]:
        for pattern, _ in GROUPS:
            m = pattern.match(cell.strip())
            if m:
                groups.append(cell.strip()[: m.end()])
                names.append(cell.strip()[m.end():].strip())
                break
        else:
            groups.append("")
            names.append(cell)
    return trim([[head[0]], groups, names] + rows[1:])


def freight_live(name: str = "freight_nov_2026") -> list[list[str]]:
    rows = read(name)
    head = rows[0]
    title, notes = re.match(r"(OCEAN FREIGHT RATE.*?2026) (Currency:.*equipment) SHIPPER ROUTE DATA #", head[0]).groups()
    groups, columns = ["SHIPPER ROUTE DATA"], ["#"]
    for cell in head[1:]:
        group = next((g for g in FREIGHT_GROUPS if cell.startswith(g + " ")), "")
        groups.append(group)
        columns.append(cell[len(group):].lstrip() if group else cell)
    return trim([[title], [notes], groups, columns] + rows[1:])


def to_text(rows: list[list[str]]) -> str:
    from app.db.import_sheets import to_csv
    return to_csv(rows)
