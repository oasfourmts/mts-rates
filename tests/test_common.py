from decimal import Decimal

import pytest

from app.importers.common import (display_name, parse_labelled_money, parse_money, parse_number, parse_range)


@pytest.mark.parametrize("cell, expected", [
    ("1,160", Decimal("1160")), ("22,000.00", Decimal("22000.00")), (" 605 ", Decimal("605")),
    ("-", None), ("_", None), ("", None),
])
def test_parse_number(cell, expected):
    assert parse_number(cell) == expected


def test_parse_number_rejects_text():
    with pytest.raises(ValueError):
        parse_number("about 500")


@pytest.mark.parametrize("cell, amount, currency", [
    ("EGP 8045", "8045", "EGP"), ("400 EGP", "400", "EGP"), ("180$", "180", "USD"),
    ("35 USD", "35", "USD"), ("1,500", "1500", None), ("12$", "12", "USD"),
])
def test_parse_money(cell, amount, currency):
    m = parse_money(cell)
    assert (m.amount, m.currency) == (Decimal(amount), currency)


@pytest.mark.parametrize("cell", ["Holiday fees: 50% on loading export", "100 USD EGP", "10 GBPX"])
def test_parse_money_refuses_to_guess(cell):
    with pytest.raises(ValueError):
        parse_money(cell)


def test_parse_labelled_money():
    label, money = parse_labelled_money("NEF 66 EUR ")
    assert label == "NEF" and money.amount == 66 and money.currency == "EUR"
    label, money = parse_labelled_money("Europe Enviroment Sucharge: 72$ /CNTR")
    assert money.amount == 72 and money.currency == "USD"
    assert parse_labelled_money("Manifest: thirty five") is None


@pytest.mark.parametrize("cell, expected", [
    ("46-50", (46, 50)), ("3 - 4", (3, 4)), ("1 Day", (1, 1)), ("15 Day", (15, 15)), ("44-51 ", (44, 51)),
    ("-", None),
])
def test_parse_range(cell, expected):
    assert parse_range(cell) == expected


def test_display_name():
    assert display_name("EL SHAZLY") == "El Shazly"
    assert display_name("MTS") == "MTS"
    assert display_name("3M") == "3M"
