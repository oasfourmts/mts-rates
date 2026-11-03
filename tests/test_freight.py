from decimal import Decimal
from pathlib import Path

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
