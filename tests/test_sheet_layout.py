"""The parsers read the live sheet layout (multi-row headers) and give the same rates as the one-row fixtures."""
from dataclasses import astuple

import pytest

from app.importers import freight, trucking
from tests.sheet_layout import FIXTURES, freight_live, to_text, trucking_live

TABS = ["trucking_alexandria", "trucking_sokhna", "trucking_psd_east", "trucking_damietta"]


def without_ref(rows):
    # The row reference is the only field that should differ: it now names the real sheet row.
    return sorted(tuple(v for k, v in vars(r).items() if k not in ("ref", "source_row", "row_ref")) for r in rows)


@pytest.mark.parametrize("name", TABS)
def test_trucking_live_layout_matches_fixture(name):
    flat = trucking.parse((FIXTURES / f"{name}.csv").read_text())
    live = trucking.parse(to_text(trucking_live(name)))
    assert len(live.rows) == len(flat.rows) > 100
    assert without_ref(live.rows) == without_ref(flat.rows)
    assert [i.severity for i in live.issues] == [i.severity for i in flat.issues]


def test_trucking_refs_name_the_sheet_row():
    live = trucking.parse(to_text(trucking_live("trucking_alexandria")))
    first = next(r for r in live.rows if r.zone_name == "6th of October")
    assert first.row_ref.startswith("row 4 (6th of October)")      # data starts on sheet row 4


def test_trucking_import_export_cell_is_not_guessed():
    rows = trucking_live("trucking_alexandria")
    zone = next(i for i, r in enumerate(rows) if r and r[0] == "10th of Ramadan")
    rows[zone] = rows[zone] + [""] * (7 - len(rows[zone]))
    rows[zone][6] = "15500 IMP-16000 EXP"           # as on the live sheet, 20'DC heavy / MTS
    res = trucking.parse(to_text(rows))
    assert any(i.raw == "15500 IMP-16000 EXP" and i.severity == "skipped" for i in res.issues)
    assert not any(r.zone_name == "10th of Ramadan" and r.trucker_key == "MTS" and r.weight_class == "heavy"
               for r in res.rows)


def test_freight_live_layout_matches_fixture():
    flat = freight.parse((FIXTURES / "freight_nov_2026.csv").read_text())
    live = freight.parse(to_text(freight_live()))
    assert len(live.rows) == len(flat.rows) == 18
    assert [astuple(o)[1:] for o in live.rows] == [astuple(o)[1:] for o in flat.rows]   # all but the row ref
    assert live.rows[0].row_ref.startswith("row 5 (Navegantes / Alexandria")              # as on the sheet
    assert live.no_offer == flat.no_offer


def test_header_not_found_says_so():
    res = freight.parse('"just","a","table"\n"1","2","3"\n')
    assert res.rows == [] and "first 10 rows" in res.issues[0].message
    res = trucking.parse('"ALEXANDRIA"\n"6th of October","100"\n')
    assert res.rows == [] and "equipment group" in res.issues[0].message
