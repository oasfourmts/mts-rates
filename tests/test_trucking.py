from decimal import Decimal
from pathlib import Path

from app.importers import trucking

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return trucking.parse((FIX / name).read_text(encoding="utf-8"))


def find(rows, zone, trucker, equipment, weight=None):
    return [r for r in rows if r.zone_name == zone and r.trucker == trucker
            and r.equipment == equipment and r.weight_class == weight]


def test_alexandria_counts_and_port():
    res = load("trucking_alexandria.csv")
    assert res.issues == []
    assert {r.port_code for r in res.rows} == {"EGALY"}
    assert len(res.rows) == 232
    assert {r.trucker for r in res.rows} == {"MTS", "El Motwakel", "El Shazly", "El Masreya", "Ebad El Rahman"}


def test_heavy_group_prices_both_20dv_heavy_and_40hc():
    rows = load("trucking_alexandria.csv").rows
    [heavy] = find(rows, "6th of October", "MTS", "20DV", "heavy")
    [hc] = find(rows, "6th of October", "MTS", "40HC")
    assert heavy.price == hc.price == Decimal("15000")
    [light] = find(rows, "6th of October", "El Masreya", "20DV", "light")
    assert light.price == Decimal("11500")


def test_reefer_and_double_twenty():
    rows = load("trucking_alexandria.csv").rows
    [rf] = find(rows, "New Menia", "El Motwakel", "40RF")
    assert rf.price == Decimal("35000")
    [two] = find(rows, "Borg el Arab", "El Motwakel", "2X20DV")
    assert two.price == Decimal("14000")


def test_blank_cells_are_not_prices():
    rows = load("trucking_alexandria.csv").rows
    assert find(rows, "East Owainat", "MTS", "20DV", "light") == []
    assert find(rows, "EAST OWAINAT", "MTS", "20DV", "light") == []


def test_psd_east_zero_prices_become_issues_and_commas_parse():
    res = load("trucking_psd_east.csv")
    assert {r.port_code for r in res.rows} == {"EGPSE"}
    zeros = [i for i in res.issues if "0 or less" in i.message]
    assert len(zeros) == 2 and all(i.severity == "skipped" for i in zeros)
    [cell] = find(res.rows, "6th of October", "El Sheikh", "20DV", "heavy")
    assert cell.price == Decimal("22000.00")


def test_sokhna_one_by_forty_reefer_label():
    res = load("trucking_sokhna.csv")
    assert {r.port_code for r in res.rows} == {"EGSOK"}
    [rf] = find(res.rows, "EAST OWAINAT", "Mesco", "40RF")
    assert rf.price == Decimal("77000")
    assert "Bayier" in {r.trucker for r in res.rows}


def test_unknown_port_needs_flag():
    res = trucking.parse('"NOWHERE","20\'DC < 8 tons X"\n"Zone A","100"\n')
    assert res.rows == [] and res.issues[0].message.startswith("unknown port")
    res = trucking.parse('"NOWHERE","20\'DC < 8 tons X"\n"Zone A","100"\n', port_code="EGALY")
    assert len(res.rows) == 1


def test_text_in_price_cell_is_an_issue_not_a_guess():
    res = trucking.parse('"DAMIETTA","20\'DC < 8 tons Seif"\n"Zone A","call me"\n')
    assert res.rows == [] and res.issues[0].message == "price is not a number"
