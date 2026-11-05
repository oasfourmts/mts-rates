"""Parser for the pricing team's "MTS customs clearance rates" sheet (setup of 5 Nov 2026).

The sheet's tabs, each read as CSV text (the Sheets API's displayed values):
    Clearance Fees  one fee per row -> rate cards (mode 'customs') and customs_rates
    Duty Rates      one HS code per row -> hs_codes and agreement_rates
    Agreements, Countries, Purposes, Documents   reference data
Only rows with Row status "Active" are loaded from Clearance Fees and Duty Rates; Draft and Example rows are counted
and skipped. A value that cannot be read with confidence becomes an issue, never a guess.

Pure: no I/O beyond the text it is given.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import sqlout
from .common import Issue, clean, match_key, parse_date, parse_number, read_csv, slug

TAB_NAMES = ["Documents", "Agreements", "Countries", "Purposes", "Duty Rates", "Clearance Fees", "Tariff 2025"]
TARIFF_SOURCE = "Customs Encyclopedia (الموسوعة الجمركية), January 2025: Decree 218/2022 with amendments"
POINTS = {"ANY": None, "ALEXANDRIA": "EGALY", "EL DEKHEILA": "EGEDK", "DAMIETTA": "EGDAM",
          "PORT SAID EAST": "EGPSE", "SOKHNA": "EGSOK", "ADABIYA": "EGADA", "CAIRO AIRPORT": "CAI"}
SHIPMENT_TYPES = {"ANY": "any", "FCL": "fcl", "LCL": "lcl", "AIR": "air", "COURIER": "courier", "ROAD": "road"}
PURPOSES = {"COMMERCIAL IMPORT": "commercial", "TEMPORARY ADMISSION": "temporary_admission", "RE-EXPORT": "re_export",
            "PROJECT CARGO": "project_cargo", "PERSONAL EFFECTS": "personal_effects", "SAMPLES": "samples",
            "FREE ZONE": "free_zone"}
BASES = {"PER SHIPMENT": "per_shipment", "PER CONTAINER": "per_container", "PER B/L": "per_bl",
         "PER HS LINE": "per_line", "% OF INVOICE VALUE": "percent"}
DIRECTIONS = {"IMPORT": "import", "EXPORT": "export"}
CURRENCIES = {"EGP", "USD", "EUR"}
DUTIES = {"YES": "yes", "GUARANTEE": "guarantee", "NO": "no"}

# Column key -> header prefix (lower case). Headers are matched by prefix so a clarifying suffix can change.
FEE_COLS = {"status": "row status", "direction": "direction", "point": "clearance point", "type": "shipment type",
            "purpose": "purpose", "chapter": "hs chapter", "from": "invoice value from", "to": "invoice value to",
            "item": "fee item", "basis": "basis", "amount": "amount", "currency": "currency", "minimum": "minimum",
            "broker": "broker", "valid_from": "valid from", "valid_to": "valid to", "documents": "documents",
            "remarks": "remarks"}
DUTY_COLS = {"status": "row status", "hs": "hs code", "description": "description", "duty": "general duty",
             "schedule": "schedule tax", "vat": "vat", "controls": "import controls", "notes": "tariff notes",
             "source": "source", "remarks": "remarks"}
AGREEMENT_COLS = {"code": "code", "name": "agreement", "partners": "partners", "certificate": "certificate",
                  "checked": "checked", "notes": "notes"}
COUNTRY_COLS = {"country": "country", "iso": "iso", "agreement": "agreement", "notes": "notes"}
PURPOSE_COLS = {"purpose": "purpose", "arabic": "arabic", "duties": "duties at clearance", "documents": "documents",
                "checked": "checked", "notes": "notes"}
TARIFF_COLS = {"code": "hs code", "duty": "general duty", "description": "description (hs",
               "description_ar": "description (arabic", "page": "encyclopedia page", "note": "note"}
DOCUMENT_COLS = {"code": "code", "name": "document", "issued_by": "issued by", "notes": "notes"}


@dataclass
class Fee:
    row_ref: str
    direction: str
    point_code: str | None
    shipment_type: str
    purpose: str
    hs_chapter: str | None
    value_from: Decimal | None
    value_to: Decimal | None
    fee_item: str
    basis: str
    amount: Decimal
    currency: str
    minimum: Decimal | None
    broker: str
    valid_from: date
    valid_to: date
    documents: list[str]
    remarks: str


@dataclass
class DutyRate:
    row_ref: str
    hs_code: str
    description: str
    duty_pct: Decimal
    schedule_tax_pct: Decimal | None
    vat_pct: Decimal | None
    agreement_rates: dict[str, Decimal]
    controls: list[str]
    source: str
    notes: str = ""


@dataclass
class CustomsResult:
    fees: list[Fee] = field(default_factory=list)
    duty_rates: list[DutyRate] = field(default_factory=list)
    agreements: list[dict] = field(default_factory=list)
    countries: list[dict] = field(default_factory=list)
    purposes: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    tariff: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    skipped_rows: int = 0          # Draft and Example rows


def _header(table: list[list[str]], wanted: dict[str, str], required: list[str]):
    """(row index, {key: column}) for the first of the top 10 rows naming every required column."""
    for i, row in enumerate(table[:10]):
        names = [clean(c).lower() for c in row]
        cols = {}
        for key, prefix in wanted.items():
            for c, name in enumerate(names):
                if name.startswith(prefix) and c not in cols.values():
                    cols[key] = c
                    break
        if all(k in cols for k in required):
            return i, cols
    return None, {}


def _cell(row: list[str], cols: dict[str, int], key: str) -> str:
    c = cols.get(key)
    return clean(row[c]) if c is not None and c < len(row) else ""


def _codes(text: str) -> list[str]:
    return [clean(x).upper() for x in re.split(r"[;,]", text) if clean(x)]


def parse(tabs: dict[str, str]) -> CustomsResult:
    """tabs: tab name -> CSV text. Missing tabs are reported, not fatal."""
    res = CustomsResult()
    known_docs: set[str] = set()
    if "Documents" in tabs:
        _documents(tabs["Documents"], res)
        known_docs = {d["code"] for d in res.documents}
    if "Agreements" in tabs:
        _agreements(tabs["Agreements"], res, known_docs)
    agreements = {a["code"] for a in res.agreements}
    if "Countries" in tabs:
        _countries(tabs["Countries"], res, agreements)
    if "Purposes" in tabs:
        _purposes(tabs["Purposes"], res, known_docs)
    if "Duty Rates" in tabs:
        _duty_rates(tabs["Duty Rates"], res, agreements, known_docs)
    if "Clearance Fees" in tabs:
        _fees(tabs["Clearance Fees"], res, known_docs)
    if "Tariff 2025" in tabs:
        _tariff(tabs["Tariff 2025"], res)
    for name in TAB_NAMES:
        if name not in tabs:
            res.issues.append(Issue(name, "warning", "tab not found; nothing read from it"))
    return res


def _rows(text: str, tab: str, wanted, required, res):
    table = read_csv(text)
    h, cols = _header(table, wanted, required)
    if h is None:
        res.issues.append(Issue(tab, "skipped", f"header not found in the first 10 rows (need: {', '.join(required)})"))
        return []
    return [(f"{tab} row {i + 1}", row) for i, row in enumerate(table[h + 1:], start=h + 1)
            if any(clean(c) for c in row)], cols


def _documents(text, res):
    out = _rows(text, "Documents", DOCUMENT_COLS, ["code", "name"], res)
    if not out:
        return
    rows, cols = out
    for ref, row in rows:
        code, name = _cell(row, cols, "code").upper(), _cell(row, cols, "name")
        if not code or not name:
            res.issues.append(Issue(ref, "skipped", "code and document name are both needed"))
            continue
        res.documents.append({"code": code, "name": name, "issued_by": _cell(row, cols, "issued_by") or None,
                              "notes": _cell(row, cols, "notes") or None})


def _agreements(text, res, docs):
    out = _rows(text, "Agreements", AGREEMENT_COLS, ["code", "name"], res)
    if not out:
        return
    rows, cols = out
    for ref, row in rows:
        code, name = _cell(row, cols, "code"), _cell(row, cols, "name")
        if not code or not name:
            res.issues.append(Issue(ref, "skipped", "code and agreement name are both needed"))
            continue
        cert = _cell(row, cols, "certificate").upper() or None
        if cert and docs and cert not in docs:
            res.issues.append(Issue(ref, "warning", "certificate code is not on the Documents tab", cert))
        res.agreements.append({"code": code, "name": name, "partners": _cell(row, cols, "partners") or None,
                               "certificate": cert, "checked": _cell(row, cols, "checked").upper() == "YES",
                               "notes": _cell(row, cols, "notes") or None})


def _countries(text, res, agreements):
    out = _rows(text, "Countries", COUNTRY_COLS, ["country", "iso"], res)
    if not out:
        return
    rows, cols = out
    for ref, row in rows:
        name, iso, ag = _cell(row, cols, "country"), _cell(row, cols, "iso").upper(), _cell(row, cols, "agreement")
        if not re.fullmatch(r"[A-Z]{2}", iso):
            res.issues.append(Issue(ref, "skipped", "ISO code must be 2 letters", iso))
            continue
        if ag and ag not in agreements:
            res.issues.append(Issue(ref, "skipped", "agreement is not on the Agreements tab", ag))
            continue
        res.countries.append({"country_code": iso, "country_name": name, "agreement": ag or None,
                              "notes": _cell(row, cols, "notes") or None})


def _purposes(text, res, docs):
    out = _rows(text, "Purposes", PURPOSE_COLS, ["purpose", "duties"], res)
    if not out:
        return
    rows, cols = out
    for ref, row in rows:
        label = _cell(row, cols, "purpose")
        purpose, duties = PURPOSES.get(match_key(label)), DUTIES.get(match_key(_cell(row, cols, "duties")))
        if purpose is None:
            res.issues.append(Issue(ref, "skipped", "purpose not in the list", label))
            continue
        if duties is None:
            res.issues.append(Issue(ref, "skipped", "Duties at clearance must be Yes, Guarantee or No",
                                    _cell(row, cols, "duties")))
            continue
        documents = _codes(_cell(row, cols, "documents"))
        unknown = [d for d in documents if docs and d not in docs]
        if unknown:
            res.issues.append(Issue(ref, "warning", "document codes not on the Documents tab", ";".join(unknown)))
        res.purposes.append({"purpose": purpose, "label": label, "label_ar": _cell(row, cols, "arabic") or None,
                             "duties_at_clearance": duties, "documents": documents,
                             "checked": _cell(row, cols, "checked").upper() == "YES",
                             "notes": _cell(row, cols, "notes") or None})


def _tariff(text, res):
    out = _rows(text, "Tariff 2025", TARIFF_COLS, ["code", "duty"], res)
    if not out:
        return
    rows, cols = out
    seen = set()
    for ref, row in rows:
        code = re.sub(r"[.\s]", "", _cell(row, cols, "code"))
        if not re.fullmatch(r"\d{6}(\d{2})?", code):
            res.issues.append(Issue(ref, "skipped", "tariff code must be 6 or 8 digits", _cell(row, cols, "code")))
            continue
        ok, duty = _pct(_cell(row, cols, "duty"), ref, "General duty %", res)
        if not ok or duty is None:
            if ok:
                res.issues.append(Issue(ref, "skipped", "General duty % is empty"))
            continue
        if code in seen:
            res.issues.append(Issue(ref, "skipped", "tariff code listed twice", code))
            continue
        seen.add(code)
        page = _cell(row, cols, "page")
        res.tariff.append({"code": code, "duty_pct": duty, "description_en": _cell(row, cols, "description") or None,
                           "description_ar": _cell(row, cols, "description_ar") or None,
                           "source_page": int(page) if page.isdigit() else None,
                           "note": _cell(row, cols, "note") or None})


def _status(row, cols, ref, res) -> bool:
    """True for Active rows. Draft and Example are skipped quietly; anything else is reported."""
    status = _cell(row, cols, "status").upper()
    if status == "ACTIVE":
        return True
    if status in ("DRAFT", "EXAMPLE"):
        res.skipped_rows += 1
    else:
        res.issues.append(Issue(ref, "skipped", "Row status must be Active, Draft or Example", status))
    return False


def _pct(text: str, ref: str, what: str, res) -> tuple[bool, Decimal | None]:
    try:
        value = parse_number(text.rstrip("%").strip())
    except ValueError:
        res.issues.append(Issue(ref, "skipped", f"{what} is not a number", text))
        return False, None
    if value is not None and not (0 <= value <= 1000):
        res.issues.append(Issue(ref, "skipped", f"{what} is out of range", text))
        return False, None
    return True, value


def _duty_rates(text, res, agreements, docs):
    out = _rows(text, "Duty Rates", DUTY_COLS, ["status", "hs", "duty", "vat", "source"], res)
    if not out:
        return
    rows, cols = out
    table = read_csv(text)
    header_row = next(r for r in table[:10] if any(clean(c).lower().startswith("hs code") for c in r))
    ag_cols = {}
    for c, name in enumerate(header_row):
        m = re.fullmatch(r"(.+?)\s*%", clean(name))
        if m and m.group(1) in agreements:
            ag_cols[m.group(1)] = c
    for ref, row in rows:
        if not _status(row, cols, ref, res):
            continue
        hs = re.sub(r"[.\s]", "", _cell(row, cols, "hs"))
        if not re.fullmatch(r"\d{10}", hs):
            res.issues.append(Issue(ref, "skipped", "HS code must be 10 digits", _cell(row, cols, "hs")))
            continue
        ok, duty = _pct(_cell(row, cols, "duty"), ref, "General duty %", res)
        if not ok:
            continue
        if duty is None:
            res.issues.append(Issue(ref, "skipped", "General duty % is empty"))
            continue
        ok, schedule = _pct(_cell(row, cols, "schedule"), ref, "Schedule tax %", res)
        if not ok:
            continue
        ok, vat = _pct(_cell(row, cols, "vat"), ref, "VAT %", res)
        if not ok:
            continue
        if vat is None:
            res.issues.append(Issue(ref, "warning", "VAT % is empty; the app will show the VAT as unknown"))
        source = _cell(row, cols, "source")
        if not source:
            res.issues.append(Issue(ref, "skipped", "Source and date checked is empty"))
            continue
        rates = {}
        for code, c in ag_cols.items():
            ok, value = _pct(clean(row[c]) if c < len(row) else "", ref, f"{code} %", res)
            if ok and value is not None:
                rates[code] = value
        controls = _codes(_cell(row, cols, "controls"))
        unknown = [d for d in controls if docs and d not in docs]
        if unknown:
            res.issues.append(Issue(ref, "warning", "import control codes not on the Documents tab", ";".join(unknown)))
        res.duty_rates.append(DutyRate(ref, hs, _cell(row, cols, "description"), duty, schedule, vat, rates, controls,
                                       source, _cell(row, cols, "notes")))


def _fees(text, res, docs):
    required = ["status", "direction", "point", "type", "purpose", "chapter", "item", "basis", "amount", "currency",
                "broker", "valid_from", "valid_to"]
    out = _rows(text, "Clearance Fees", FEE_COLS, required, res)
    if not out:
        return
    rows, cols = out

    def bad(ref, message, raw=""):
        res.issues.append(Issue(ref, "skipped", message, raw))

    for ref, row in rows:
        if not _status(row, cols, ref, res):
            continue
        get = lambda k: _cell(row, cols, k)  # noqa: E731
        direction = DIRECTIONS.get(match_key(get("direction")))
        point = POINTS.get(match_key(get("point")), "?")
        stype = SHIPMENT_TYPES.get(match_key(get("type")))
        purpose = PURPOSES.get(match_key(get("purpose")))
        basis = BASES.get(match_key(get("basis")))
        currency = get("currency").upper()
        checks = [(direction, "Direction"), (stype, "Shipment type"), (purpose, "Purpose"), (basis, "Basis")]
        missing = next(((label, k) for (v, label), k in zip(checks, ("direction", "type", "purpose", "basis"))
                        if v is None), None)
        if missing:
            bad(ref, f"{missing[0]} not in the list", get(missing[1]))
            continue
        if point == "?":
            bad(ref, "Clearance point not in the list", get("point"))
            continue
        if currency not in CURRENCIES:
            bad(ref, "Currency must be EGP, USD or EUR", get("currency"))
            continue
        chapter_text = get("chapter")
        if match_key(chapter_text) == "ALL":
            chapter = None
        elif re.fullmatch(r"\d{1,2}", chapter_text):
            chapter = chapter_text.zfill(2)
        else:
            bad(ref, "HS chapter must be 2 digits or All", chapter_text)
            continue
        try:
            amount = parse_number(get("amount"))
            minimum = parse_number(get("minimum"))
            v_from, v_to = parse_number(get("from")), parse_number(get("to"))
        except ValueError as e:
            bad(ref, f"not a number: {e}")
            continue
        if amount is None or amount < 0:
            bad(ref, "Amount is empty or below 0", get("amount"))
            continue
        if basis == "percent" and amount > 100:
            bad(ref, "a percent above 100", get("amount"))
            continue
        if v_from is not None and v_to is not None and v_to <= v_from:
            bad(ref, "Invoice value to must be above Invoice value from", f"{get('from')} - {get('to')}")
            continue
        broker = get("broker")
        if not broker:
            bad(ref, "Broker / supplier is empty (write MTS for MTS's own fees)")
            continue
        try:
            valid_from, valid_to = parse_date(get("valid_from")), parse_date(get("valid_to"))
        except ValueError as e:
            bad(ref, str(e))
            continue
        if valid_from is None or valid_to is None:
            bad(ref, "Valid from and Valid to are both needed")
            continue
        if valid_to < valid_from:
            bad(ref, "Valid to is before Valid from", f"{valid_from} - {valid_to}")
            continue
        documents = _codes(get("documents"))
        unknown = [d for d in documents if docs and d not in docs]
        if unknown:
            res.issues.append(Issue(ref, "warning", "document codes not on the Documents tab", ";".join(unknown)))
        res.fees.append(Fee(ref, direction, point, stype, purpose, chapter, v_from, v_to, get("item") or "Clearance fee",
                            basis, amount, currency, minimum, broker, valid_from, valid_to, documents, get("remarks")))
    _overlaps(res)


def _overlaps(res):
    """Two fees for the same shipments and fee item whose value bands overlap would both be charged: warn."""
    groups: dict[tuple, list[Fee]] = {}
    for f in res.fees:
        key = (f.broker.upper(), f.direction, f.point_code, f.shipment_type, f.purpose, f.hs_chapter, f.fee_item.upper())
        groups.setdefault(key, []).append(f)
    for fees in groups.values():
        for i, a in enumerate(fees):
            for b in fees[i + 1:]:
                dates = a.valid_from <= b.valid_to and b.valid_from <= a.valid_to
                lo_a, hi_a = a.value_from or Decimal(0), a.value_to or Decimal("Infinity")
                lo_b, hi_b = b.value_from or Decimal(0), b.value_to or Decimal("Infinity")
                if dates and lo_a < hi_b and lo_b < hi_a:
                    res.issues.append(Issue(b.row_ref, "warning",
                                            f"overlaps {a.row_ref} (same fee, dates and value band): both would be charged"))


def to_sql(res: CustomsResult, *, source_ref: str) -> str:
    """One transaction: reference data upserted, fees loaded as new rate cards."""
    s = sqlout.Script("sheet", source_ref)
    lit = sqlout.lit
    for d in res.documents:
        s.add("INSERT INTO customs_documents (code, name, issued_by, notes) VALUES "
              f"({lit(d['code'])}, {lit(d['name'])}, {lit(d['issued_by'])}, {lit(d['notes'])}) ON CONFLICT (code) DO "
              "UPDATE SET name = EXCLUDED.name, issued_by = EXCLUDED.issued_by, notes = EXCLUDED.notes;")
    for a in res.agreements:
        s.add("INSERT INTO trade_agreements (code, name, partners, certificate, checked, notes) VALUES "
              f"({lit(a['code'])}, {lit(a['name'])}, {lit(a['partners'])}, {lit(a['certificate'])}, {lit(a['checked'])}, "
              f"{lit(a['notes'])}) ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, partners = EXCLUDED.partners, "
              "certificate = EXCLUDED.certificate, checked = EXCLUDED.checked, notes = EXCLUDED.notes;")
    for c in res.countries:
        s.add("INSERT INTO origin_agreements (country_code, country_name, agreement, notes) VALUES "
              f"({lit(c['country_code'])}, {lit(c['country_name'])}, {lit(c['agreement'])}, {lit(c['notes'])}) "
              "ON CONFLICT (country_code) DO UPDATE SET country_name = EXCLUDED.country_name, "
              "agreement = EXCLUDED.agreement, notes = EXCLUDED.notes;")
    for p in res.purposes:
        s.add("INSERT INTO customs_purposes (purpose, label, label_ar, duties_at_clearance, documents, checked, notes) "
              f"VALUES ({lit(p['purpose'])}, {lit(p['label'])}, {lit(p['label_ar'])}, {lit(p['duties_at_clearance'])}, "
              f"{lit(p['documents'])}, {lit(p['checked'])}, {lit(p['notes'])}) ON CONFLICT (purpose) DO UPDATE SET "
              "label = EXCLUDED.label, label_ar = EXCLUDED.label_ar, duties_at_clearance = EXCLUDED.duties_at_clearance, "
              "documents = EXCLUDED.documents, checked = EXCLUDED.checked, notes = EXCLUDED.notes;")
    for r in res.duty_rates:
        # Government reference data, so updated in place; the audit log keeps the previous rate.
        s.add("INSERT INTO audit_log (actor, action, table_name, detail) SELECT 'import_sheets', 'hs_code.update', "
              f"'hs_codes', jsonb_build_object('hs_code', code, 'old_duty_pct', duty_pct, 'new_duty_pct', {lit(r.duty_pct)}::numeric) "
              f"FROM hs_codes WHERE code = {lit(r.hs_code)} AND duty_pct IS DISTINCT FROM {lit(r.duty_pct)};")
        s.add("INSERT INTO hs_codes (code, description, duty_pct, vat_pct, permits, schedule_tax_pct, tariff_notes, "
              f"source, updated_at) VALUES ({lit(r.hs_code)}, {lit(r.description or r.hs_code)}, {lit(r.duty_pct)}, "
              f"{lit(r.vat_pct)}, {lit(r.controls)}, {lit(r.schedule_tax_pct)}, {lit(r.notes or None)}, {lit(r.source)}, "
              "now()) ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description, duty_pct = EXCLUDED.duty_pct, "
              "vat_pct = EXCLUDED.vat_pct, permits = EXCLUDED.permits, schedule_tax_pct = EXCLUDED.schedule_tax_pct, "
              "tariff_notes = EXCLUDED.tariff_notes, source = EXCLUDED.source, updated_at = now();")
        s.add(f"DELETE FROM agreement_rates WHERE hs_code = {lit(r.hs_code)};")
        for code, pct in r.agreement_rates.items():
            s.add(f"INSERT INTO agreement_rates (hs_code, agreement, duty_pct) VALUES ({lit(r.hs_code)}, {lit(code)}, {pct});")
    if res.tariff:
        # Reference data, replaced as a whole: the tab is rebuilt from the encyclopedia.
        s.add("DELETE FROM tariff_lines;")
        for i in range(0, len(res.tariff), 500):
            values = ", ".join(
                f"({lit(t['code'])}, {lit(t['duty_pct'])}, {lit(t['description_en'])}, {lit(t['description_ar'])}, {lit(t['source_page'])}, "
                f"{lit(t['note'])}, {lit(TARIFF_SOURCE)})" for t in res.tariff[i:i + 500])
            s.add(f"INSERT INTO tariff_lines (code, duty_pct, description_en, description_ar, source_page, note, source) VALUES {values};")
    cards = {}
    for f in res.fees:
        key = f"{slug(f.broker)}|{f.direction}|{f.currency}|{f.valid_from}|{f.valid_to}"
        if key not in cards:
            cards[key] = f
            s.partner(f.broker, "customs_broker", match_key(f.broker))
            s.rate_card(key, mode="customs", direction=f.direction, partner_kind="customs_broker",
                        partner_key=match_key(f.broker), rate_type="contract", valid_from=f.valid_from,
                        valid_to=f.valid_to, currency=f.currency, status="active", source_ref=source_ref)
        s.add("INSERT INTO customs_rates (rate_card_id, clearance_point_id, shipment_type, hs_chapter, purpose, fee, "
              "basis, documents, notes, fee_item, value_from_usd, value_to_usd, minimum, source_row) VALUES "
              f"({sqlout.card(key)}, {sqlout.loc(f.point_code) if f.point_code else 'NULL'}, {lit(f.shipment_type)}, "
              f"{lit(f.hs_chapter)}, {lit(f.purpose)}, {f.amount}, {lit(f.basis)}, {lit(f.documents)}, "
              f"{lit(f.remarks or None)}, {lit(f.fee_item)}, {lit(f.value_from)}, {lit(f.value_to)}, {lit(f.minimum)}, "
              f"{lit(f.row_ref)});")
    for i in res.issues:
        s.issue(i.row_ref, i.severity, i.message, i.raw)
    read = len(res.fees) + len(res.duty_rates) + res.skipped_rows + sum(i.severity == "skipped" for i in res.issues)
    return s.finish(read, len(res.fees) + len(res.duty_rates))
