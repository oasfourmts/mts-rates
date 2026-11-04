"""Legacy ocean freight sheet: one tab per month, one row per destination x POL x equipment x carrier.

Columns are found by their header text, so both the November layout (with OTHC) and the December
layout (with "Service Available") import. Amounts are USD per container unless the cell names another
currency. Nothing is guessed: a cell the importer cannot read becomes an import issue.

Usage: python -m app.importers.freight FILE.csv [--direction export] [--sql]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import sqlout
from .common import (Issue, Money, Result, clean, display_name, is_blank, match_key, parse_date,
                     parse_labelled_list, parse_labelled_money, parse_money, parse_money_sum,
                     parse_range, read_csv)

DEFAULT_CURRENCY = "USD"

# header text (lower-cased, spaces collapsed) -> field
def _find(h: str) -> str | None:
    rules = [
        ("dest", lambda h: h == "destination port"),
        ("locode", lambda h: h == "un/locode"),
        ("country", lambda h: h == "country"),
        ("pol", lambda h: h.startswith("port of loading")),
        ("equipment", lambda h: h == "equipment"),
        ("carrier", lambda h: "shipping line" in h),
        ("available", lambda h: "service available" in h),
        ("routing", lambda h: "routing" in h and "transship" not in h),
        ("via", lambda h: h.startswith("transshipment")),
        ("transit", lambda h: "transit time" in h),
        ("OF", lambda h: h.endswith("ocean freight")),
        ("BAF", lambda h: h == "baf"),
        ("EBS", lambda h: h == "ebs"),
        ("LSS", lambda h: h in ("eca / lss", "lss", "eca")),
        ("FUE", lambda h: h == "fue"),
        ("ETS", lambda h: h == "ets"),
        ("EFS", lambda h: h == "efs"),
        ("WAR", lambda h: h.startswith("war risk")),
        ("OTHER", lambda h: h.startswith("other surcharges")),
        ("all_in", lambda h: h.startswith("all-in total")),
        ("OTHC", lambda h: h == "othc"),
        ("BL", lambda h: h.startswith("b/l fee")),
        ("free", lambda h: "free time" in h),
        ("valid_from", lambda h: h == "valid from"),
        ("valid_to", lambda h: h == "valid to"),
        ("remarks", lambda h: h.startswith("remarks")),
    ]
    for name, test in rules:
        if test(h):
            return name
    return None


REQUIRED = ("dest", "locode", "pol", "equipment", "carrier", "OF", "valid_from", "valid_to")
SURCHARGES = ("BAF", "EBS", "LSS", "FUE", "ETS", "EFS", "WAR", "OTHER")
# Per-shipment charges with a ceiling in USD: above it the bare number is almost certainly EGP.
USD_CEILING = {"OTHC": Decimal(600), "BL": Decimal(150)}

POL_CODES = {"ALEXANDRIA": "EGALY", "EL DEKHEILA": "EGEDK", "DEKHEILA": "EGEDK", "DAMIETTA": "EGDAM",
             "PORT SAID EAST": "EGPSE", "PSD EAST": "EGPSE", "SOKHNA": "EGSOK", "AIN SOKHNA": "EGSOK",
             "ADABYA": "EGADA", "ADABIYA": "EGADA", "AL ADABIYAH": "EGADA", "EL ADABIYA": "EGADA"}

CARRIERS = {
    "MSC": "MSC", "MAERSK": "Maersk", "CMA": "CMA CGM", "CMA CGM": "CMA CGM", "COSCO": "COSCO", "ONE": "ONE",
    "HAPAG": "Hapag-Lloyd", "HAPAG LLOYD": "Hapag-Lloyd", "HAPAG LLOYED": "Hapag-Lloyd", "HAPAG-LLOYD": "Hapag-Lloyd",
    "ZIM": "ZIM", "ARKAS": "Arkas", "MEDKON": "Medkon", "TARROS": "Tarros", "EVERGREEN": "Evergreen",
    "OOCL": "OOCL", "HMM": "HMM", "YANG MING": "Yang Ming", "PIL": "PIL", "EMIRATES": "Emirates Shipping",
    "GRIMALDI": "Grimaldi", "TURKON": "Turkon", "SEALEAD": "Sealead", "ESL": "ESL",
}

EQUIPMENT = {"20DV": "20DV", "20GP": "20DV", "20DC": "20DV", "40DV": "40DV", "40GP": "40DV", "40DC": "40DV",
             "40HC": "40HC", "40HQ": "40HC", "40RF": "40RF", "40RH": "40RF", "20RF": "20RF",
             "20OT": "20OT", "40OT": "40OT", "20FR": "20FR", "40FR": "40FR"}


@dataclass
class Charge:
    code: str
    amount: Decimal
    currency: str
    basis: str
    raw: str


@dataclass
class FreightOffer:
    row_ref: str
    pol_code: str
    pod_code: str
    pod_name: str
    equipment: str
    carrier: str
    carrier_key: str
    rate_type: str
    valid_from: date
    valid_to: date
    routing: str | None = None
    via: str | None = None
    transit: tuple[int, int] | None = None
    free_days: int | None = None
    free_terms: str | None = None
    quote_ref: str | None = None
    remarks: list[str] = field(default_factory=list)
    reported_total: Decimal | None = None
    charges: list[Charge] = field(default_factory=list)

    @property
    def card_key(self) -> str:
        return f"{self.carrier_key}|{self.rate_type}|{self.valid_from}|{self.valid_to}"


def normalise_carrier(raw: str) -> tuple[str, str, str, bool]:
    """'Maersk Spot ' -> ('Maersk', 'MAERSK', 'spot', known=True)."""
    key = match_key(raw)
    rate_type = "contract"
    if key.endswith(" SPOT"):
        key, rate_type = key[:-5].strip(), "spot"
    name = CARRIERS.get(key)
    if name:
        return name, match_key(name), rate_type, True
    # Not on the known list: keep the sheet's own spelling ("NVOCC (Crew)", "EGL"), without a " Spot" suffix.
    written = clean(raw)
    if rate_type == "spot":
        written = re.sub(r"\s+spot$", "", written, flags=re.I)
    return written, key, rate_type, False


def map_columns(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, raw in enumerate(header):
        name = _find(clean(raw).lower())
        if name and name not in cols:
            cols[name] = i
    return cols


def parse(text: str) -> Result:
    result = Result()
    table = read_csv(text)
    if not table:
        result.issues.append(Issue("file", "skipped", "empty file"))
        return result
    # The live sheet has a title row, a notes row and a row of column groups above the column names, so the
    # header is the first of the top 10 rows that names every required column.
    best, header_at = None, None
    for i, row in enumerate(table[:10]):
        cols = map_columns(row)
        missing = [c for c in REQUIRED if c not in cols]
        if not missing:
            header_at = i
            break
        if best is None or len(missing) < len(best):
            best = missing
    if header_at is None:
        result.issues.append(Issue("header", "skipped", f"columns not found in the first 10 rows: {', '.join(best)}"))
        return result
    result.no_offer = 0  # rows with no carrier: lanes nobody has quoted yet

    for r, row in enumerate(table[header_at + 1:], start=header_at + 2):
        get = lambda name: row[cols[name]] if name in cols and cols[name] < len(row) else ""  # noqa: E731
        ref = f"row {r} ({clean(get('dest'))} / {clean(get('pol'))} / {clean(get('equipment'))})"
        if not clean(get("dest")):
            continue
        carrier_raw = get("carrier")
        if is_blank(carrier_raw) or clean(get("available")).upper() == "N":
            result.no_offer += 1
            continue
        offer = _parse_row(get, ref, carrier_raw, result.issues)
        if offer:
            result.rows.append(offer)
    return result


def _parse_row(get, ref: str, carrier_raw: str, issues: list[Issue]) -> FreightOffer | None:
    def skip(msg, raw=""):
        issues.append(Issue(ref, "skipped", msg, raw))

    def warn(msg, raw=""):
        issues.append(Issue(ref, "warning", msg, raw))

    pol = POL_CODES.get(match_key(get("pol")))
    if not pol:
        return skip("port of loading not recognised", get("pol"))
    pod = clean(get("locode")).upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", pod):
        return skip("destination UN/LOCODE missing or malformed", get("locode"))
    equipment = EQUIPMENT.get(re.sub(r"[\s'’]", "", get("equipment")).upper())
    if not equipment:
        return skip("equipment not recognised", get("equipment"))
    try:
        valid_from, valid_to = parse_date(get("valid_from")), parse_date(get("valid_to"))
    except ValueError as exc:
        return skip(str(exc))
    if not valid_from or not valid_to or valid_to < valid_from:
        return skip("validity dates missing or reversed", f"{get('valid_from')} - {get('valid_to')}")

    carrier, carrier_key, rate_type, known = normalise_carrier(carrier_raw)
    if not known:
        warn("carrier not in the known list; check the spelling", carrier_raw)
    offer = FreightOffer(ref, pol, pod, clean(get("dest")), equipment, carrier, carrier_key, rate_type,
                         valid_from, valid_to)

    routing = clean(get("routing")).lower()
    offer.routing = "direct" if routing.startswith("dir") else "indirect" if routing.startswith("ind") else None
    if routing and not offer.routing and not is_blank(routing):
        warn("routing not recognised", get("routing"))
    via = clean(get("via"))
    if not is_blank(via):
        offer.via = re.sub(r"^via\s+", "", via, flags=re.I).strip()
    try:
        offer.transit = parse_range(get("transit"))
    except ValueError:
        warn("transit time not readable; left blank", get("transit"))

    free = clean(get("free"))
    if not is_blank(free):
        m = re.match(r"^(\d+)\s*(?:days?)?\s*(.*)$", free, re.I)
        if m:
            offer.free_days = int(m.group(1))
            offer.free_terms = m.group(2).strip() or None
        else:
            offer.free_terms = free

    remarks = clean(get("remarks"))
    if re.fullmatch(r"R\d{10,}", remarks):
        offer.quote_ref = remarks
    elif remarks:
        offer.remarks.append(remarks)

    # Charges
    for code in ("OF",) + SURCHARGES + ("OTHC", "BL"):
        raw = get(code)
        if is_blank(raw):
            continue
        basis = "per_bl" if code == "BL" else "per_container"
        label = None
        try:
            money = parse_money(raw)
        except ValueError:
            # Several amounts in one cell, each with its currency: one charge each (sheet rules, 3 Nov 2026).
            parts = [(None, m) for m in parse_money_sum(raw) or []]
            if not parts and code == "OTHER":
                parts = parse_labelled_list(raw) or []
            if parts:
                for part_label, part in parts:
                    offer.charges.append(Charge(code, part.amount, part.currency, basis,
                                                clean(raw) + (f" ({part_label})" if part_label else "")))
                continue
            labelled = parse_labelled_money(raw) if code == "OTHER" else None
            if not labelled:
                warn(f"{code} cell is not an amount; kept in remarks", raw)
                offer.remarks.append(f"{code}: {clean(raw)}")
                continue
            label, money = labelled
        currency = money.currency or DEFAULT_CURRENCY
        if code in USD_CEILING and money.currency is None and money.amount > USD_CEILING[code]:
            warn(f"{code} {money.amount} has no currency and is too high for USD; probably EGP, not imported", raw)
            continue
        offer.charges.append(Charge(code, money.amount, currency, basis, clean(raw) + (f" ({label})" if label else "")))

    try:
        total = parse_money(get("all_in"))
    except ValueError:
        total = None
        warn("all-in total not readable", get("all_in"))
    if total:
        offer.reported_total = total.amount

    freight_lines = [c for c in offer.charges if c.code in ("OF",) + SURCHARGES]
    if not any(c.code == "OF" for c in freight_lines):
        if total and not freight_lines:
            offer.charges.insert(0, Charge("OF", total.amount, total.currency or DEFAULT_CURRENCY, "per_container",
                                           f"{clean(get('all_in'))} (all-in total; ocean freight cell blank)"))
            warn("ocean freight blank; the all-in total was stored as ocean freight", get("all_in"))
        else:
            return skip("no ocean freight price")
    elif total:
        currencies = {c.currency for c in freight_lines} | {total.currency or DEFAULT_CURRENCY}
        computed = sum(c.amount for c in freight_lines)
        if len(currencies) == 1 and computed != total.amount:
            warn(f"charges add up to {computed}, but the sheet's all-in total is {total.amount}",
                 clean(get("all_in")))
    return offer


def to_sql(result: Result, *, source_ref: str, direction: str = "export", status: str = "active") -> str:
    s = sqlout.Script("sheet", source_ref)
    partners, pods, cards = {}, {}, set()
    for o in result.rows:
        partners.setdefault(o.carrier_key, o.carrier)
        pods.setdefault(o.pod_code, o.pod_name)
    for key, name in sorted(partners.items()):
        s.partner(name, "shipping_line", key)
    for code, name in sorted(pods.items()):
        s.location(code, name, "seaport", code[:2], [match_key(name)])
    for n, o in enumerate(result.rows):
        if o.card_key not in cards:
            cards.add(o.card_key)
            s.rate_card(o.card_key, mode="sea_fcl", direction=direction, partner_kind="shipping_line",
                        partner_key=o.carrier_key, rate_type=o.rate_type, valid_from=o.valid_from,
                        valid_to=o.valid_to, currency=DEFAULT_CURRENCY, status=status, source_ref=source_ref)
        lk = f"line{n}"
        lo, hi = o.transit or (None, None)
        lit = sqlout.lit
        s.add("WITH f AS (INSERT INTO freight_rates (rate_card_id, origin_id, destination_id, equipment_code, routing, "
              "via, transit_days_min, transit_days_max, free_days, free_time_terms, quote_ref, reported_total, remarks, "
              f"source_row) VALUES ({sqlout.card(o.card_key)}, {sqlout.loc(o.pol_code)}, {sqlout.loc(o.pod_code)}, "
              f"{lit(o.equipment)}, {lit(o.routing)}, {lit(o.via)}, {lit(lo)}, {lit(hi)}, {lit(o.free_days)}, "
              f"{lit(o.free_terms)}, {lit(o.quote_ref)}, {lit(o.reported_total)}, {lit('; '.join(o.remarks) or None)}, "
              f"{lit(o.row_ref)}) RETURNING id) INSERT INTO _lines SELECT {lit(lk)}, id FROM f;")
        for c in o.charges:
            s.add("INSERT INTO freight_charges (freight_rate_id, charge_code, amount, currency, basis, raw_text) VALUES "
                  f"({sqlout.line(lk)}, {lit(c.code)}, {c.amount}, {lit(c.currency)}, {lit(c.basis)}, {lit(c.raw)});")
    for issue in result.issues:
        s.issue(issue.row_ref, issue.severity, issue.message, issue.raw)
    skipped = sum(1 for i in result.issues if i.severity == "skipped")
    return s.finish(rows_read=len(result.rows) + skipped, rows_imported=len(result.rows))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--direction", choices=("export", "import"), default="export")
    ap.add_argument("--sql", action="store_true", help="print the SQL load script instead of a summary")
    args = ap.parse_args(argv)
    with open(args.file, encoding="utf-8") as fh:
        result = parse(fh.read())
    if args.sql:
        sys.stdout.write(to_sql(result, source_ref=f"freight sheet: {args.file}", direction=args.direction))
    else:
        print(f"{len(result.rows)} offers, {getattr(result, 'no_offer', 0)} lanes with no offer, "
              f"{len(result.issues)} issues")
        for i in result.issues:
            print(f"  [{i.severity}] {i.row_ref}: {i.message} ({i.raw!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
