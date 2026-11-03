"""Monthly exchange rates, entered by hand until an automatic feed exists.

A month's rate is stored on the 1st of that month as units of the currency per 1 USD (the fx_rates.per_usd
convention), so search uses it from the 1st until the next month is entered. Every change is written to
audit_log with the old and new value. A rate far from the previous month's is refused until the person
confirms it, because one mistyped digit would skew every quote that month."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_JUMP = Decimal("0.25")    # more than 25% from the previous month needs confirming


class FxError(ValueError):
    """Bad input; the message says what to fix."""


class FxNeedsConfirm(ValueError):
    """Plausible but unusual; the message says why, and saving again with confirm=True goes through."""


def parse_month(month: str) -> date:
    try:
        y, m = (int(x) for x in month.split("-"))
        return date(y, m, 1)
    except ValueError:
        raise FxError(f"Month must look like 2026-11, not {month!r}") from None


def parse_rate(value: str) -> Decimal:
    try:
        rate = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise FxError(f"{value!r} is not a number") from None
    if not rate.is_finite() or rate <= 0:
        raise FxError("The rate must be more than 0")
    if rate.as_tuple().exponent < -6 or rate >= Decimal("100000000"):
        raise FxError("Use at most 6 decimal places and fewer than 9 digits before the point")
    return rate


def currencies(conn: Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT code, name FROM currencies WHERE code <> 'USD' ORDER BY code")).mappings()]


def list_rates(conn: Connection, since: date) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT currency, on_date, per_usd, source, entered_by, entered_at FROM fx_rates "
        "WHERE on_date >= :since ORDER BY on_date DESC, currency"), {"since": since}).mappings()]


def save_rate(conn: Connection, *, actor: str, currency: str, month: date, per_usd: Decimal, today: date,
              confirm: bool = False) -> dict:
    currency = currency.strip().upper()
    if currency == "USD":
        raise FxError("USD is the base currency; it needs no rate")
    if conn.execute(text("SELECT 1 FROM currencies WHERE code = :c"), {"c": currency}).first() is None:
        raise FxError(f"Unknown currency {currency}")
    next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    if month > next_month:
        raise FxError("You can enter this month's or next month's rate, not later ones")

    prev = conn.execute(text(
        "SELECT on_date, per_usd FROM fx_rates WHERE currency = :c AND on_date < :m ORDER BY on_date DESC LIMIT 1"),
        {"c": currency, "m": month}).first()
    if prev is not None and not confirm:
        change = per_usd / prev.per_usd - 1
        if abs(change) > MAX_JUMP:
            raise FxNeedsConfirm(
                f"{_fmt(per_usd)} {currency} per USD is {abs(change) * 100:.0f}% "
                f"{'above' if change > 0 else 'below'} the {prev.on_date:%B %Y} rate of {_fmt(prev.per_usd)}. "
                "Check the figure, then save again to confirm it.")

    old = conn.execute(text("SELECT per_usd FROM fx_rates WHERE currency = :c AND on_date = :m FOR UPDATE"),
                       {"c": currency, "m": month}).scalar()
    if old is not None and old == per_usd:
        return _row(conn, currency, month)
    conn.execute(text(
        "INSERT INTO fx_rates (currency, on_date, per_usd, source, entered_by, entered_at) "
        "VALUES (:c, :m, :r, 'manual monthly', :who, now()) "
        "ON CONFLICT (currency, on_date) DO UPDATE SET per_usd = EXCLUDED.per_usd, source = EXCLUDED.source, "
        "entered_by = EXCLUDED.entered_by, entered_at = EXCLUDED.entered_at"),
        {"c": currency, "m": month, "r": per_usd, "who": actor})
    conn.execute(text(
        "INSERT INTO audit_log (actor, action, table_name, detail) VALUES (:who, :action, 'fx_rates', "
        "jsonb_build_object('currency', CAST(:c AS text), 'month', CAST(:m AS date), "
        "'old_per_usd', CAST(:old AS numeric), 'new_per_usd', CAST(:r AS numeric), 'confirmed', CAST(:conf AS boolean)))"),
        {"who": actor, "action": "fx_rate.update" if old is not None else "fx_rate.create", "c": currency,
         "m": month, "old": old, "r": per_usd, "conf": confirm})
    return _row(conn, currency, month)


def _row(conn: Connection, currency: str, month: date) -> dict:
    return dict(conn.execute(text(
        "SELECT currency, on_date, per_usd, source, entered_by, entered_at FROM fx_rates "
        "WHERE currency = :c AND on_date = :m"), {"c": currency, "m": month}).mappings().one())


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")
