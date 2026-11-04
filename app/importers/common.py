"""Shared helpers: reading messy sheet cells without guessing."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

BLANKS = {"", "-", "_", "--", "n/a", "na", "tbc", "tba"}
CURRENCY_WORDS = {
    "USD": "USD", "US$": "USD", "$": "USD", "DOLLAR": "USD", "DOLLARS": "USD",
    "EUR": "EUR", "€": "EUR", "EURO": "EUR",
    "EGP": "EGP", "LE": "EGP", "L.E": "EGP", "L.E.": "EGP",
}


@dataclass
class Issue:
    row_ref: str
    severity: str          # 'skipped' (row or value not imported) or 'warning' (imported, but check it)
    message: str
    raw: str = ""


@dataclass
class Money:
    amount: Decimal
    currency: str | None   # None = the cell did not say; the caller applies the sheet's default
    raw: str


@dataclass
class Result:
    rows: list = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def read_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def clean(cell: str | None) -> str:
    return re.sub(r"\s+", " ", (cell or "").replace(" ", " ")).strip()


def is_blank(cell: str | None) -> bool:
    return clean(cell).lower() in BLANKS


def parse_number(cell: str | None) -> Decimal | None:
    """'1,160' -> 1160, '22,000.00' -> 22000.00. None for blanks. Raises ValueError for anything else."""
    text = clean(cell)
    if text.lower() in BLANKS:
        return None
    text = text.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise ValueError(f"not a number: {cell!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValueError(f"not a number: {cell!r}") from exc


_MONEY = re.compile(
    r"^(?:(?P<pre>[A-Za-z$€.]{1,4})\s*)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<post>[A-Za-z$€.]{1,4})?$")


def parse_money(cell: str | None) -> Money | None:
    """'EGP 8045', '400 EGP', '180$', '35 USD', '1,500' -> Money. None for blanks.
    Raises ValueError when the cell holds more than one amount and a currency (e.g. 'Holiday fees: 50%')."""
    text = clean(cell)
    if text.lower() in BLANKS:
        return None
    m = _MONEY.match(text)
    if not m:
        raise ValueError(f"not an amount: {cell!r}")
    words = [w.upper() for w in (m.group("pre"), m.group("post")) if w]
    currencies = {CURRENCY_WORDS.get(w) for w in words}
    if None in currencies or len(currencies) > 1:
        raise ValueError(f"unknown currency in {cell!r}")
    currency = currencies.pop() if currencies else None
    return Money(Decimal(m.group("num").replace(",", "")), currency, text)


def parse_money_sum(cell: str | None) -> list[Money] | None:
    """'5850 EGP + 20 USD', '151$ + 700 EGP' -> one Money per part. Only when the cell is two or more amounts
    joined by '+' and every part names its currency; anything else (e.g. '10$ / 400 EGP') returns None."""
    parts = [x.strip() for x in clean(cell).split("+")]
    if len(parts) < 2 or any(not x for x in parts):
        return None
    out = []
    for part in parts:
        try:
            money = parse_money(part)
        except ValueError:
            return None
        if money is None or money.currency is None:
            return None
        out.append(money)
    return out


def parse_labelled_list(cell: str | None) -> list[tuple[str, Money]] | None:
    """'Manifest 35 USD; Doc 15 USD' -> [('Manifest', 35 USD), ('Doc', 15 USD)]. Every part must be a label and
    an amount with its currency; otherwise None."""
    parts = [x.strip() for x in clean(cell).split(";") if x.strip()]
    if len(parts) < 2:
        return None
    out = []
    for part in parts:
        labelled = parse_labelled_money(part)
        if not labelled or labelled[1].currency is None:
            return None
        out.append(labelled)
    return out


_LABELLED_MONEY = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /.&-]*?)[:\s]+(?P<money>.*?)(?:\s*/\s*CNTR)?$", re.I)


def parse_labelled_money(cell: str | None) -> tuple[str, Money] | None:
    """'NEF 66 EUR' -> ('NEF', 66 EUR); 'OWS: USD 10' -> ('OWS', 10 USD). None if it doesn't fit."""
    text = re.sub(r"\s*/\s*CNTR$", "", clean(cell), flags=re.I)
    if ":" in text:
        label, _, rest = text.partition(":")
    else:
        m = _LABELLED_MONEY.match(text)
        if not m:
            return None
        label, rest = m.group("label"), m.group("money")
    try:
        money = parse_money(rest)
    except ValueError:
        return None
    return (label.strip(), money) if money and label.strip() else None


def parse_range(cell: str | None) -> tuple[int, int] | None:
    """Transit time: '46-50', '3 - 4', '1 Day', '15 Days' -> (min, max). Raises ValueError otherwise."""
    text = clean(cell).lower()
    if text in BLANKS:
        return None
    text = re.sub(r"\s*days?$", "", text)
    m = re.fullmatch(r"(\d+)\s*(?:-|to)\s*(\d+)|(\d+)", text)
    if not m:
        raise ValueError(f"not a day range: {cell!r}")
    if m.group(3):
        n = int(m.group(3))
        return n, n
    lo, hi = int(m.group(1)), int(m.group(2))
    if hi < lo:
        raise ValueError(f"range runs backwards: {cell!r}")
    return lo, hi


def parse_date(cell: str | None) -> date | None:
    text = clean(cell)
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"not a date: {cell!r}")


def match_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().upper()


KEEP_UPPER = {"MTS", "3M", "MSC", "ONE", "ZIM", "COSCO", "CMA", "CGM", "HMM", "OOCL", "PIL", "UAE", "USA"}


def display_name(name: str) -> str:
    """'EL SHAZLY' -> 'El Shazly', 'MTS' stays 'MTS'."""
    words = clean(name).split(" ")
    return " ".join(w.upper() if w.upper() in KEEP_UPPER else w[:1].upper() + w[1:].lower() for w in words)


def slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", match_key(name)).strip("-")
