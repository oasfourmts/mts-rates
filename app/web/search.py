"""Rate search on top of the SQL functions in db/functions/rate_search.sql.

The SQL function prices ONE container; here we price the booking the user asked for: per-container and
per-trip charges times the number of boxes, per-B/L and per-shipment charges once. FX follows to_usd() exactly:
the latest rate on or before the search date, each charge rounded to cents, and no total at all when a
currency has no rate (a quote must never be built on a missing exchange rate)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

CENT = Decimal("0.01")
PER_UNIT = {"per_container", "per_trip"}        # multiplied by the number of containers
PER_BOOKING = {"per_bl", "per_shipment"}         # charged once
LIGHT_LIMIT_T = Decimal("8")                     # 20DV under 8 t is "light"
EXPIRY_WARNING_DAYS = 7


class SearchError(ValueError):
    """The request cannot be answered as asked (unknown location, bad equipment); the message says why."""


@dataclass(frozen=True)
class Location:
    id: int
    code: str
    name: str
    kind: str


def resolve_location(conn: Connection, term: str, label: str) -> Location:
    row = conn.execute(text("SELECT id, code, name, kind::text FROM locations WHERE id = find_location(:t)"),
                       {"t": term}).first()
    if row is None:
        raise SearchError(f"Unknown {label} '{term}'. Pick it from the list, or ask Pricing to add it.")
    return Location(*row)


def check_equipment(conn: Connection, code: str) -> str:
    code = code.strip().upper()
    if conn.execute(text("SELECT 1 FROM equipment WHERE code = :c"), {"c": code}).first() is None:
        raise SearchError(f"Unknown equipment '{code}'")
    return code


def weight_class(equipment: str, weight_t: Decimal | None) -> str | None:
    """Only 20DV trucking is priced by weight. No weight given: show both classes rather than guess."""
    if equipment != "20DV" or weight_t is None:
        return None
    return "light" if weight_t < LIGHT_LIMIT_T else "heavy"


def latest_fx(conn: Connection, currencies: set[str], on: date) -> dict[str, dict]:
    """per_usd and its date for each currency, using the newest rate on or before `on`. USD is implicit."""
    wanted = sorted(c for c in currencies if c != "USD")
    if not wanted:
        return {}
    rows = conn.execute(text(
        "SELECT DISTINCT ON (currency) currency, per_usd, on_date FROM fx_rates "
        "WHERE currency = ANY(:c) AND on_date <= :on ORDER BY currency, on_date DESC"),
        {"c": wanted, "on": on}).all()
    return {r.currency.strip(): {"per_usd": r.per_usd, "on_date": r.on_date} for r in rows}


def to_usd(amount: Decimal, currency: str, fx: dict[str, dict]) -> Decimal | None:
    if currency == "USD":
        return amount
    if currency not in fx:
        return None
    return (amount / fx[currency]["per_usd"]).quantize(CENT, ROUND_HALF_UP)


def _days_left(valid_to: date, on: date) -> int:
    return (valid_to - on).days


def search_freight(conn: Connection, origin: str, destination: str, equipment: str, on: date,
                   containers: int = 1) -> dict:
    if containers < 1:
        raise SearchError("Number of containers must be at least 1")
    pol = resolve_location(conn, origin, "origin")
    pod = resolve_location(conn, destination, "destination")
    equipment = check_equipment(conn, equipment)
    rows = conn.execute(text("SELECT * FROM search_freight(:o, :d, :e, :on)"),
                        {"o": pol.code, "d": pod.code, "e": equipment, "on": on}).mappings().all()
    names = dict(conn.execute(text("SELECT code, name FROM charge_codes")).all())
    fx = latest_fx(conn, {c["currency"] for r in rows for c in r["charges"]}, on)

    options = []
    for r in rows:
        charges, total, missing = [], Decimal(0), set()
        for c in r["charges"]:
            amount = Decimal(str(c["amount"]))
            qty = containers if c["basis"] in PER_UNIT else 1
            line = (amount * qty).quantize(CENT)
            usd = to_usd(line, c["currency"], fx)
            if usd is None:
                missing.add(c["currency"])
            else:
                total += usd
            charges.append({"code": c["code"], "name": names.get(c["code"], c["code"]), "amount": amount,
                            "currency": c["currency"], "basis": c["basis"], "quantity": qty, "line_total": line,
                            "line_usd": usd})
        days = _days_left(r["valid_to"], on)
        options.append({
            "rate_card_id": r["rate_card_id"], "freight_rate_id": r["freight_rate_id"], "carrier": r["carrier"],
            "rate_type": r["rate_type"], "routing": r["routing"], "via": r["via"],
            "transit_days": r["transit_days"], "free_days": r["free_days"], "free_time_terms": r["free_time_terms"],
            "valid_to": r["valid_to"], "days_left": days, "expiring_soon": days <= EXPIRY_WARNING_DAYS,
            "total_usd": None if missing else total,
            "per_container_usd": None if missing else (total / containers).quantize(CENT, ROUND_HALF_UP),
            "missing_fx": sorted(missing), "charges": charges, "remarks": r["remarks"],
        })
    options.sort(key=lambda o: (o["total_usd"] is None, o["total_usd"] or 0, _transit_sort(o["transit_days"])))
    return {"mode": "freight", "on": on, "origin": _loc(pol), "destination": _loc(pod), "equipment": equipment,
            "containers": containers, "fx": _fx_out(fx), "options": options}


def search_trucking(conn: Connection, port: str, zone: str, equipment: str, on: date,
                    weight_t: Decimal | None = None, trips: int = 1) -> dict:
    if trips < 1:
        raise SearchError("Number of trips must be at least 1")
    if weight_t is not None and weight_t <= 0:
        raise SearchError("Cargo weight must be more than 0 t")
    p = resolve_location(conn, port, "port")
    z = resolve_location(conn, zone, "inland destination")
    equipment = check_equipment(conn, equipment)
    wc = weight_class(equipment, weight_t)
    rows = conn.execute(text("SELECT * FROM search_trucking(:p, :z, :e, :w, :on)"),
                        {"p": p.code, "z": z.code, "e": equipment, "w": wc, "on": on}).mappings().all()
    fx = latest_fx(conn, {r["currency"].strip() for r in rows}, on)
    options = []
    for r in rows:
        cur = r["currency"].strip()
        total = (r["price"] * trips).quantize(CENT)
        days = _days_left(r["valid_to"], on)
        options.append({
            "rate_card_id": r["rate_card_id"], "transport_rate_id": r["transport_rate_id"], "trucker": r["trucker"],
            "price": r["price"], "currency": cur, "weight_class": r["weight_class"], "trips": trips,
            "total": total, "total_usd": to_usd(total, cur, fx), "valid_to": r["valid_to"], "days_left": days,
            "expiring_soon": days <= EXPIRY_WARNING_DAYS,
        })
    return {"mode": "trucking", "on": on, "origin": _loc(p), "destination": _loc(z), "equipment": equipment,
            "weight_t": weight_t, "weight_class": wc, "trips": trips, "fx": _fx_out(fx), "options": options}


def find_locations(conn: Connection, q: str, kinds: list[str] | None, limit: int = 15) -> list[dict]:
    q = " ".join(q.split())
    if not q:
        return []
    rows = conn.execute(text(
        "SELECT code, name, kind::text AS kind, country_code FROM locations "
        "WHERE (code ILIKE :prefix OR name ILIKE :any "
        "       OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE :any)) "
        "  AND (CAST(:kinds AS text[]) IS NULL OR kind::text = ANY(CAST(:kinds AS text[]))) "
        "ORDER BY (upper(code) = upper(:q)) DESC, (name ILIKE :prefix) DESC, name LIMIT :lim"),
        {"q": q, "prefix": _like(q) + "%", "any": "%" + _like(q) + "%", "kinds": kinds, "lim": limit}).mappings()
    return [dict(r) for r in rows]


def expiring(conn: Connection, days: int, on: date) -> list[dict]:
    return [dict(r) for r in conn.execute(text("SELECT * FROM expiring_rate_cards(:d, :on)"),
                                          {"d": days, "on": on}).mappings()]


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _transit_sort(t: str | None) -> int:
    try:
        return int((t or "").split("-")[0])
    except ValueError:
        return 10_000


def _loc(loc: Location) -> dict:
    return {"code": loc.code, "name": loc.name, "kind": loc.kind}


def _fx_out(fx: dict[str, dict]) -> list[dict]:
    return [{"currency": c, "per_usd": v["per_usd"], "on_date": v["on_date"]} for c, v in sorted(fx.items())]
