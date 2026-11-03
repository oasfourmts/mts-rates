"""Monthly exchange rate entry. Skipped without PG_TEST_DSN (except the parsing tests)."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.web import fx
from app.web.main import create_app, today
from tests.conftest import pg_required, psql
from tests.test_web import settings


@pytest.mark.parametrize("value, expected", [("50.25", Decimal("50.25")), ("1,234.5", Decimal("1234.5"))])
def test_parse_rate(value, expected):
    assert fx.parse_rate(value) == expected


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.1234567", "NaN", "Infinity"])
def test_parse_rate_refuses(value):
    with pytest.raises(fx.FxError):
        fx.parse_rate(value)


def test_parse_month():
    assert fx.parse_month("2026-11") == date(2026, 11, 1)
    with pytest.raises(fx.FxError):
        fx.parse_month("Nov 2026")


def month(offset=0):
    t = today()
    y, m = divmod(t.year * 12 + t.month - 1 + offset, 12)
    return f"{y}-{m + 1:02d}"


@pytest.fixture(scope="module")
def client(db):
    with TestClient(create_app(settings(db))) as c:
        yield c


def iap(db, email, admins):
    s = settings(db, auth_mode="iap", iap_audience="/aud", admin_emails=frozenset(admins))
    c = TestClient(create_app(s, verify_iap=lambda t, a: {"iss": "https://cloud.google.com/iap", "email": email}))
    c.headers["x-goog-iap-jwt-assertion"] = "t"
    return c


@pg_required
def test_monthly_rate_is_used_by_search_from_the_first(client, db):
    r = client.put("/api/fx/EGP/2026-11", json={"per_usd": "50"})
    assert r.status_code == 200 and r.json()["on_date"] == "2026-11-01" and r.json()["source"] == "manual monthly"
    opt = client.get("/api/search/freight", params={"origin": "EGALY", "destination": "LBBEY", "equipment": "40HC",
                                                    "on": "2026-11-10"}).json()["options"][0]
    assert opt["total_usd"] == "1063.00"   # 880 + 175 + 400 EGP / 50


@pg_required
def test_big_jump_needs_confirming(client, db):
    client.put("/api/fx/EUR/2026-10", json={"per_usd": "0.92"})
    r = client.put("/api/fx/EUR/2026-11", json={"per_usd": "9.2"})
    assert r.status_code == 409 and "900% above the October 2026 rate of 0.92" in r.json()["detail"]
    assert psql(db, "SELECT count(*) FROM fx_rates WHERE currency='EUR' AND on_date='2026-11-01'") == "0"
    assert client.put("/api/fx/EUR/2026-11", json={"per_usd": "9.2", "confirm": True}).status_code == 200


@pg_required
def test_correction_is_audited_with_old_value(client, db):
    client.put("/api/fx/SAR/2026-11", json={"per_usd": "3.75"})
    client.put("/api/fx/SAR/2026-11", json={"per_usd": "3.76"})
    assert psql(db, "SELECT per_usd FROM fx_rates WHERE currency='SAR'") == "3.760000"
    log = psql(db, "SELECT action || ' ' || (detail->>'old_per_usd') || ' ' || (detail->>'new_per_usd') || ' ' || actor "
                   "FROM audit_log WHERE detail->>'currency' = 'SAR' AND action = 'fx_rate.update'")
    assert log == "fx_rate.update 3.750000 3.76 pricing@mtslogistics.net"
    assert psql(db, "SELECT count(*) FROM audit_log WHERE detail->>'currency' = 'SAR'") == "2"


@pg_required
@pytest.mark.parametrize("cur, m, why", [("USD", "2026-11", "base currency"), ("XYZ", "2026-11", "Unknown currency"),
                                         ("EGP", month(3), "not later ones")])
def test_refused_inputs(client, cur, m, why):
    r = client.put(f"/api/fx/{cur}/{m}", json={"per_usd": "1"})
    assert r.status_code == 422 and why in r.json()["detail"]


@pg_required
def test_only_admins_can_save(db):
    body = {"per_usd": "3.67"}
    assert iap(db, "sales@mtslogistics.net", ["boss@mtslogistics.net"]).put("/api/fx/AED/2026-11", json=body).status_code == 403
    assert psql(db, "SELECT count(*) FROM fx_rates WHERE currency='AED'") == "0"
    boss = iap(db, "boss@mtslogistics.net", ["boss@mtslogistics.net"])
    assert boss.put("/api/fx/AED/2026-11", json=body).json()["entered_by"] == "boss@mtslogistics.net"
    assert boss.get("/api/me").json()["is_admin"] is True


@pg_required
def test_list_and_currencies(client):
    client.put(f"/api/fx/TRY/{month()}", json={"per_usd": "41.5"})
    rows = client.get("/api/fx").json()
    assert {"currency": "TRY", "per_usd": "41.500000"}.items() <= next(r for r in rows if r["currency"] == "TRY").items()
    assert "USD" not in {c["code"] for c in client.get("/api/currencies").json()}
