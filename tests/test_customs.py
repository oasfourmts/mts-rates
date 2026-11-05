"""The customs clearance sheet (templates/MTS_Customs_Clearance_Rates.xlsx) and its importer.
Fixtures in tests/fixtures/customs are the template's tabs as the Sheets API returns them (displayed values)."""
import csv
import io
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.importers import customs
from tests.conftest import DSN, ROOT, pg_required, psql

FIX = Path(__file__).parent / "fixtures" / "customs"
TABS = {"Clearance Fees": "fees", "Duty Rates": "duty_rates", "Agreements": "agreements", "Countries": "countries",
        "Purposes": "purposes", "Documents": "documents", "Tariff 2025": "tariff"}


def template_tabs():
    return {tab: (FIX / f"{name}.csv").read_text(encoding="utf-8") for tab, name in TABS.items()}


def edit(text, fn):
    rows = list(csv.reader(io.StringIO(text)))
    fn(rows)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def activated():
    """The template with its Example rows switched to Active (and a broker filled in), as the team will do."""
    tabs = template_tabs()

    def fees(rows):
        for r in rows:
            if r and r[0] == "Example":
                r[0] = "Active"
                r += [""] * (18 - len(r))
                r[13] = "Test Broker"

    def duties(rows):
        for r in rows:
            if r and r[0] == "Example":
                r[0] = "Active"

    tabs["Clearance Fees"] = edit(tabs["Clearance Fees"], fees)
    tabs["Duty Rates"] = edit(tabs["Duty Rates"], duties)
    return tabs


def test_template_as_delivered_reads_cleanly():
    res = customs.parse(template_tabs())
    assert res.issues == []
    assert res.fees == [] and res.skipped_rows == 4                 # the 4 fee rows are examples
    assert len(res.duty_rates) == 3                                    # filled in from the official tariff
    assert [a["code"] for a in res.agreements] == ["EU", "UK", "EFTA", "Turkey", "Agadir", "GAFTA", "COMESA",
                                                   "Mercosur", "AfCFTA", "Serbia"]
    assert {p["purpose"] for p in res.purposes} == set(customs.PURPOSES.values())
    assert len(res.documents) == 17 and len(res.countries) == 67
    tariff = {t["code"]: t["duty_pct"] for t in res.tariff}
    assert len(tariff) == 5989
    # Matched against customs.gov.eg on 5 Nov 2026
    assert (tariff["841810"], tariff["610910"], tariff["851713"], tariff["847130"], tariff["87032330"]) == (60, 40, 10, 0, 40)
    assert tariff["382751"] == 5 and tariff["210130"] == 30 and tariff["551311"] == 10 and tariff["030499"] == 0
    arabic = {t["code"]: t["description_ar"] for t in res.tariff}
    assert arabic["010121"] == "خيول وحمير وبغال ونغال، حية. — خيول — أصيلة للإنسال"
    assert arabic["74198010"] == "أصناف أخر من نحاس. — غيرها — منشآت وخزانات وأوعية"
    assert arabic["150600"].startswith("شحوم ودهون وزيوت حيوانية أخر")       # a heading with one line, text above its code
    assert sum(1 for a in arabic.values() if a) == 5988
    de = next(c for c in res.countries if c["country_code"] == "DE")
    assert de["agreement"] == "EU"
    assert next(c for c in res.countries if c["country_code"] == "CN")["agreement"] is None


def test_active_rows_parse_into_fees_and_duty_rates():
    res = customs.parse(activated())
    assert res.issues == [] and len(res.fees) == 4 and len(res.duty_rates) == 3
    general, machinery, line, air = res.fees
    assert (general.point_code, general.shipment_type, general.purpose, general.hs_chapter, general.basis,
            general.amount, general.currency) == (None, "fcl", "commercial", None, "per_shipment", 4500, "EGP")
    assert machinery.hs_chapter == "84" and "GOEIC" in machinery.documents
    assert line.basis == "per_line" and line.shipment_type == "any"
    assert (air.basis, air.amount, air.minimum, air.value_from, air.value_to) == ("percent", Decimal("0.5"), 50, 0, 5000)
    fridge = next(d for d in res.duty_rates if d.hs_code == "8418100090")
    assert (fridge.duty_pct, fridge.schedule_tax_pct, fridge.vat_pct) == (60, 8, 14)      # customs.gov.eg, 5 Nov 2026
    assert fridge.agreement_rates == {"EU": 0, "UK": 0, "EFTA": 0, "Turkey": 0}
    assert "ر6501" in fridge.notes and fridge.controls == ["NFSA"]


def test_agreement_rate_columns_are_read():
    phone = next(d for d in customs.parse(template_tabs()).duty_rates if d.hs_code == "8517130000")
    # Tariff: EU, UK, EFTA and Mercosur list A reduce 100%; Serbia reduces 20% (10% -> 8%).
    assert phone.agreement_rates == {"EU": 0, "UK": 0, "EFTA": 0, "Mercosur": 0, "Serbia": 8}
    assert phone.schedule_tax_pct is None


@pytest.mark.parametrize("col, value, message", [
    (5, "8471", "HS chapter must be 2 digits or All"),
    (13, "", "Broker / supplier is empty"),
    (11, "SAR", "Currency must be EGP, USD or EUR"),
    (2, "Suez", "Clearance point not in the list"),
    (15, "30/11/2025", "Valid to is before Valid from"),
    (10, "about 4500", "not a number"),
    (0, "Live", "Row status must be Active, Draft or Example"),
])
def test_bad_fee_cells_are_issues_not_guesses(col, value, message):
    tabs = activated()

    def spoil(rows):
        first = next(r for r in rows if r and r[0] == "Active")
        first[col] = value
    tabs["Clearance Fees"] = edit(tabs["Clearance Fees"], spoil)
    res = customs.parse(tabs)
    assert len(res.fees) == 3
    assert any(message in i.message for i in res.issues)


def test_overlapping_value_bands_are_flagged():
    tabs = activated()

    def overlap(rows):
        air = next(r for r in rows if r and r[0] == "Active" and r[9] == "% of invoice value")
        rows.append(air[:6] + ["4000", "9000"] + air[8:])
    tabs["Clearance Fees"] = edit(tabs["Clearance Fees"], overlap)
    res = customs.parse(tabs)
    assert len(res.fees) == 5 and any("both would be charged" in i.message for i in res.issues)


@pytest.mark.parametrize("col, value, message", [
    (1, "8418.10", "HS code must be 10 digits"),
    (3, "sixty", "General duty % is not a number"),
    (4, "8% of value", "Schedule tax % is not a number"),
    (18, "", "Source and date checked is empty"),
])
def test_bad_duty_rows_are_issues(col, value, message):
    tabs = activated()

    def spoil(rows):
        row = next(r for r in rows if len(r) > 1 and r[1] == "8418100090")
        row += [""] * (20 - len(row))
        row[col] = value
    tabs["Duty Rates"] = edit(tabs["Duty Rates"], spoil)
    res = customs.parse(tabs)
    assert len(res.duty_rates) == 2 and any(message in i.message for i in res.issues)


@pytest.fixture
def fresh():
    name = "mts_customs_" + uuid.uuid4().hex[:8]
    psql("postgres", f"CREATE DATABASE {name}")
    try:
        for f in sorted((ROOT / "db/migrations").glob("*.sql")):
            psql(name, file=f)
        yield name
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pg_required
def test_loads_into_the_database(fresh):
    sql = customs.to_sql(customs.parse(activated()), source_ref="test customs sheet")
    subprocess.run(["psql", f"{DSN} dbname={fresh}", "-v", "ON_ERROR_STOP=1", "-q"], input=sql, text=True,
                   check=True, capture_output=True)
    assert psql(fresh, "SELECT count(*) FROM customs_rates") == "4"
    assert psql(fresh, "SELECT count(*) FROM rate_cards WHERE mode = 'customs' AND status = 'active'") == "2"  # EGP, USD
    assert psql(fresh, "SELECT name || ' ' || kind FROM partners WHERE kind = 'customs_broker'") == \
        "Test Broker customs_broker"
    assert psql(fresh, "SELECT basis || ' ' || fee || ' ' || minimum || ' ' || value_to_usd FROM customs_rates "
                       "WHERE basis = 'percent'") == "percent 0.50 50.00 5000.00"
    assert psql(fresh, "SELECT duty_pct || ' ' || vat_pct || ' ' || array_to_string(permits, ';') FROM hs_codes "
                       "WHERE code = '8517130000'") == "10.00 14.00 NTRA"
    assert psql(fresh, "SELECT schedule_tax_pct FROM hs_codes WHERE code = '8418100090'") == "8.00"
    assert psql(fresh, "SELECT count(*) FROM tariff_lines") == "5989"
    assert psql(fresh, "SELECT duty_pct || ' ' || source_page FROM tariff_lines WHERE code = '87032330'") == "40.00 441"
    assert psql(fresh, "SELECT description_ar FROM tariff_lines WHERE code = '070110'") == "بطاطا(بطاطس) طازجة أو مبردة. — للبذار"
    assert psql(fresh, "SELECT duty_pct FROM agreement_rates WHERE hs_code = '8517130000' AND agreement = 'Serbia'") == \
        "8.00"
    assert psql(fresh, "SELECT agreement FROM origin_agreements WHERE country_code = 'TR'") == "Turkey"
    assert psql(fresh, "SELECT duties_at_clearance FROM customs_purposes WHERE purpose = 'temporary_admission'") == \
        "guarantee"
    # Loading again updates the reference data in place and logs changed duty rates.
    tabs = activated()
    tabs["Duty Rates"] = tabs["Duty Rates"].replace(",Smartphones,10,", ",Smartphones,15,")
    sql = customs.to_sql(customs.parse(tabs), source_ref="test customs sheet v2")
    subprocess.run(["psql", f"{DSN} dbname={fresh}", "-v", "ON_ERROR_STOP=1", "-q"], input=sql, text=True,
                   check=True, capture_output=True)
    assert psql(fresh, "SELECT duty_pct FROM hs_codes WHERE code = '8517130000'") == "15.00"
    assert psql(fresh, "SELECT detail->>'old_duty_pct' FROM audit_log WHERE action = 'hs_code.update'") == "10.00"


@pg_required
def test_import_job_runs_the_customs_sheet(fresh):
    import psycopg
    from app.db import import_sheets
    tabs = activated()

    def fetch(sheet_id, tab):
        assert sheet_id == "CUSTOMS123"
        return list(csv.reader(io.StringIO(tabs[tab])))

    with psycopg.connect(f"{DSN} dbname={fresh}", autocommit=True) as conn:
        lines = []
        first = import_sheets.run_customs(conn, fetch, "CUSTOMS123", log=lines.append)
        assert first.status == "imported" and first.rows == 7 and "4 fee(s), 3 HS code(s)" in lines[0]
        assert import_sheets.run_customs(conn, fetch, "CUSTOMS123", log=lines.append).status == "skipped"
        tabs["Clearance Fees"] = tabs["Clearance Fees"].replace(",4500,EGP,", ",4750,EGP,")   # a price changes
        again = import_sheets.run_customs(conn, fetch, "CUSTOMS123", log=lines.append)
        assert again.status == "imported" and again.replaced == 2
    assert psql(fresh, "SELECT count(*) FROM customs_rates cr JOIN rate_cards rc ON rc.id = cr.rate_card_id "
                       "WHERE rc.status = 'active'") == "4"
    assert psql(fresh, "SELECT fee FROM customs_rates cr JOIN rate_cards rc ON rc.id = cr.rate_card_id "
                       "WHERE rc.status = 'active' AND cr.fee_item = 'Clearance fee' AND cr.hs_chapter IS NULL "
                       "AND cr.basis = 'per_shipment'") == "4750.00"


def test_bad_tariff_rows_are_issues():
    tabs = template_tabs()

    def spoil(rows):
        rows.append(["8471.3", "5", "bad code"])
        rows.append(["99999999", "a lot", "bad rate"])
        rows.append(["847130", "0", "listed twice"])
    tabs["Tariff 2025"] = edit(tabs["Tariff 2025"], spoil)
    res = customs.parse(tabs)
    assert len(res.tariff) == 5989
    assert {i.message for i in res.issues} == {"tariff code must be 6 or 8 digits", "General duty % is not a number",
                                                "tariff code listed twice"}
