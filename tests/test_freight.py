from decimal import Decimal
from pathlib import Path

import pytest

from app.importers import freight

FIX = Path(__file__).parent / "fixtures"


def nov():
    return freight.parse((FIX / "freight_nov_2026.csv").read_text(encoding="utf-8"))


def offer(rows, pod, pol, equipment, carrier):
    [o] = [r for r in rows if r.pod_code == pod and r.pol_code == pol and r.equipment == equipment
           and r.carrier == carrier]
    return o


def charges(o):
    return {c.code: (c.amount, c.currency) for c in o.charges}


def test_counts():
    res = nov()
    assert len(res.rows) == 18
    assert res.no_offer == 2


def test_msc_breakdown_matches_all_in():
    o = offer(nov().rows, "BRNVT", "EGALY", "20DV", "MSC")
    assert charges(o) == {"OF": (Decimal(350), "USD"), "LSS": (Decimal(40), "USD"), "FUE": (Decimal(15), "USD"),
                          "ETS": (Decimal(80), "USD"), "EFS": (Decimal(120), "USD"), "OTHC": (Decimal(8045), "EGP")}
    assert o.reported_total == Decimal(605)
    assert o.transit == (46, 50) and o.via == "Tekirdag , Valencia" and o.routing == "indirect"
    assert o.quote_ref == "R19526060000051" and o.free_days == 21


def test_spot_rates_and_carrier_spellings():
    rows = nov().rows
    maersk = offer(rows, "LBBEY", "EGPSE", "40HC", "Maersk")
    assert maersk.rate_type == "spot" and maersk.free_days == 21 and maersk.free_terms == "DET"
    assert offer(rows, "SIKOP", "EGDAM", "20DV", "COSCO").carrier_key == "COSCO"
    assert offer(rows, "TRIZM", "EGDAM", "40HC", "COSCO").carrier_key == "COSCO"      # typed "Cosco"
    assert offer(rows, "KEMBA", "EGPSE", "20DV", "Hapag-Lloyd").carrier_key == "HAPAG-LLOYD"  # "Hapag Lloyed"


def test_currency_written_in_the_cell_wins():
    o = offer(nov().rows, "KEMBA", "EGPSE", "20DV", "Hapag-Lloyd")
    assert charges(o)["OF"] == (Decimal(1500), "USD")
    assert charges(o)["OTHC"] == (Decimal(180), "USD")
    assert charges(o)["BL"] == (Decimal(36), "USD")
    zim = offer(nov().rows, "DEHAM", "EGDAM", "20DV", "ZIM")
    assert charges(zim)["OTHER"] == (Decimal(66), "EUR")


def test_bare_othc_too_high_for_usd_is_not_imported():
    res = nov()
    o = offer(res.rows, "TRIZM", "EGALY", "20DV", "Arkas")
    assert "OTHC" not in charges(o) and "BL" not in charges(o)
    msgs = [i.message for i in res.issues if "row 14" in i.row_ref]
    assert any(m.startswith("OTHC 7800") for m in msgs) and any(m.startswith("BL 750") for m in msgs)


def test_all_in_mismatch_is_flagged_not_fixed():
    res = nov()
    o = offer(res.rows, "MACAS", "EGALY", "40HC", "Arkas")
    assert charges(o)["OF"] == (Decimal(1000), "USD") and o.reported_total == Decimal(700)
    assert any("all-in total is 700" in i.message for i in res.issues)


def test_blank_ocean_freight_uses_all_in_with_warning():
    res = nov()
    o = offer(res.rows, "SIKOP", "EGDAM", "20DV", "COSCO")
    assert charges(o)["OF"] == (Decimal(440), "USD")
    assert any("all-in total was stored" in i.message for i in res.issues)


def test_no_price_is_skipped():
    res = nov()
    assert not [r for r in res.rows if r.pod_code == "SIKOP" and r.pol_code == "EGPSE"]
    assert any(i.severity == "skipped" and "no ocean freight price" in i.message for i in res.issues)


def test_text_in_bl_column_goes_to_remarks():
    o = offer(nov().rows, "ILASH", "EGDAM", "20DV", "ZIM")
    assert "BL" not in charges(o)
    assert any("Holiday fees" in r for r in o.remarks)


DEC_HEADER = ('"#","Destination Port","UN/LOCODE","Country","Port of Loading (POL)","Equipment",'
              '"CARRIER / SERVICE Shipping Line\n (Carrier)","Service Available\n (Y / N)","ROUTING & SCHEDULE Routing\n'
              ' (Direct / INDirect)","Transshipment Port(s)","Transit Time\n (days)","RATES & SURCHARGES (USD per '
              'container) Ocean\n Freight","BAF","ALL-IN TOTAL\n (USD)","B/L fees per shipment","TERMS Free Time at '
              'Destination\n (days)","Valid From","Valid To","Remarks / Restrictions"\n')


def test_december_layout_and_service_available_flag():
    text = DEC_HEADER + (
        '"1","Genoa","ITGOA","Italy","Damietta","40\'HC","MSC","Y","Direct","","9","700","50","750","","14",'
        '"01-Dec-2026","31-Dec-2026",""\n'
        '"","Genoa","ITGOA","Italy","Sokhna","40\'HC","MSC","N","","","","","","","","","01-Dec-2026","31-Dec-2026",""\n')
    res = freight.parse(text)
    assert len(res.rows) == 1 and res.no_offer == 1 and res.issues == []
    o = res.rows[0]
    assert charges(o) == {"OF": (Decimal(700), "USD"), "BAF": (Decimal(50), "USD")}
    assert o.valid_to.isoformat() == "2026-12-31"


def test_multiline_cell_in_free_time():
    text = DEC_HEADER + ('"","Genoa","ITGOA","Italy","Damietta","20\'DV","CMA Spot","Y","Indirect","Via Malta",'
                         '"15 - 19 ","190","","190","27","7 Merged\nDetention","01-Dec-2026","31-Dec-2026",""\n')
    o = freight.parse(text).rows[0]
    assert o.free_days == 7 and o.free_terms == "Merged Detention" and o.via == "Malta"


def test_missing_required_column_stops_the_import():
    res = freight.parse('"Destination Port","Equipment"\n"Genoa","20\'DV"\n')
    assert res.rows == [] and res.issues[0].message.startswith("columns not found")


def test_adabiya_is_a_port_of_loading():
    from app.importers.freight import POL_CODES
    from app.importers.common import match_key
    assert POL_CODES[match_key("Adabya")] == "EGADA" == POL_CODES[match_key("Al Adabiyah")]


def _offer_with(**cells):
    """One live-layout freight row (Beirut / Alexandria 20'DV, as on the sheet) with some cells replaced."""
    from tests.sheet_layout import freight_live, to_text
    rows = freight_live()
    names = [c.strip() for c in rows[3]]
    row = next(r for r in rows[4:] if len(r) > 6 and r[1].strip() == "Beirut" and r[4].strip() == "Alexandria"
               and r[5].strip() == "20'DV")
    row += [""] * (len(names) - len(row))
    for key, value in cells.items():
        col = next(i for i, n in enumerate(names) if n.lower().startswith(key))
        row[col] = value
    res = freight.parse(to_text(rows))
    offer = next(o for o in res.rows if o.pod_code == "LBBEY" and o.pol_code == "EGALY" and o.equipment == "20DV")
    return offer, [i for i in res.issues if i.row_ref == offer.row_ref]


def test_amounts_joined_by_plus_are_separate_charges():
    offer, issues = _offer_with(othc="5850 EGP + 20 USD")
    othc = sorted((c.amount, c.currency) for c in offer.charges if c.code == "OTHC")
    assert othc == [(20, "USD"), (5850, "EGP")]
    assert not any("OTHC" in i.message for i in issues)


@pytest.mark.parametrize("cell", ["10$ / 400 EGP", "5850 + 20 USD"])
def test_unclear_multi_amount_cells_stay_flagged(cell):
    offer, issues = _offer_with(othc=cell)
    assert not any(c.code == "OTHC" for c in offer.charges)
    assert any(i.raw == cell for i in issues)


def test_other_surcharges_list_gives_one_charge_per_fee():
    offer, _ = _offer_with(**{"other surcharges": "Manifest 35 USD; Doc 15 USD"})
    other = sorted((c.raw.rsplit("(", 1)[-1].rstrip(")"), c.amount) for c in offer.charges if c.code == "OTHER")
    assert other == [("Doc", 15), ("Manifest", 35)]
