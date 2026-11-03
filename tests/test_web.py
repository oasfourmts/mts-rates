"""API tests on a real Postgres holding the fixture sheets (see conftest.py). Skipped without PG_TEST_DSN."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.web.config import Settings
from app.web.main import create_app
from app.web.search import weight_class
from tests.conftest import DSN, pg_required, psql

NOV = "2026-11-10"


def url_for(db):
    p = conninfo_to_dict(f"{DSN} dbname={db}")
    return (f"postgresql+psycopg://{p.get('user', 'postgres')}@/{db}"
            f"?host={p.get('host', '/tmp')}&port={p.get('port', 5432)}")


def settings(db="postgres", **kw):
    base = dict(database_url=url_for(db) if DSN else "postgresql+psycopg://x@/x", auth_mode="dev",
                iap_audience=None, allowed_domain="mtslogistics.net", dev_user="pricing@mtslogistics.net")
    return Settings(**{**base, **kw})


@pytest.fixture(scope="module")
def client(db):
    with TestClient(create_app(settings(db))) as c:
        yield c


# ---------- pure ----------

@pytest.mark.parametrize("eq, w, expected", [
    ("20DV", Decimal("7.99"), "light"), ("20DV", Decimal("8"), "heavy"), ("20DV", None, None),
    ("40HC", Decimal("3"), None),
])
def test_weight_class(eq, w, expected):
    assert weight_class(eq, w) == expected


# ---------- sign-in (no database needed: auth runs before any query) ----------

def iap_client(verify):
    return TestClient(create_app(settings(auth_mode="iap", iap_audience="/projects/1/aud"), verify_iap=verify))


def test_iap_requires_token():
    assert iap_client(lambda t, a: {}).get("/api/me").status_code == 401


def test_iap_accepts_mts_account():
    def verify(token, aud):
        assert token == "signed" and aud == "/projects/1/aud"
        return {"iss": "https://cloud.google.com/iap", "email": "Osama@MTSLogistics.net"}
    r = iap_client(verify).get("/api/me", headers={"x-goog-iap-jwt-assertion": "signed"})
    assert r.status_code == 200 and r.json() == {"email": "osama@mtslogistics.net"}


@pytest.mark.parametrize("claims", [
    {"iss": "https://cloud.google.com/iap", "email": "someone@gmail.com"},
    {"iss": "https://cloud.google.com/iap", "email": "x@mtslogistics.net.evil.com"},
    {"iss": "https://accounts.google.com", "email": "a@mtslogistics.net"},
])
def test_iap_refuses_other_accounts(claims):
    r = iap_client(lambda t, a: claims).get("/api/me", headers={"x-goog-iap-jwt-assertion": "t"})
    assert r.status_code == 403


def test_iap_bad_signature_is_401():
    def verify(t, a):
        raise ValueError("bad signature")
    assert iap_client(verify).get("/api/me", headers={"x-goog-iap-jwt-assertion": "t"}).status_code == 401


def test_iap_without_audience_fails_closed():
    c = TestClient(create_app(settings(auth_mode="iap", iap_audience=None)))
    assert c.get("/api/me", headers={"x-goog-iap-jwt-assertion": "t"}).status_code == 503


# ---------- search ----------

@pg_required
def test_freight_cheapest_first_with_full_breakdown(client):
    r = client.get("/api/search/freight", params={"origin": "Alexandria", "destination": "ITGOA",
                                                  "equipment": "20dv", "on": NOV})
    assert r.status_code == 200
    body = r.json()
    assert body["origin"]["code"] == "EGALY" and body["equipment"] == "20DV"
    assert [(o["carrier"], o["total_usd"]) for o in body["options"]] == [("CMA CGM", "430.00"), ("Tarros", "475.00")]
    assert {c["code"] for c in body["options"][0]["charges"]} == {"OF", "OTHC"}


@pg_required
def test_freight_per_bl_charged_once_and_fx_shown(client, db):
    psql(db, "INSERT INTO fx_rates VALUES ('EUR', '2026-11-01', 0.92, 'test') ON CONFLICT DO NOTHING")
    params = {"origin": "EGDAM", "destination": "DEHAM", "equipment": "20DV", "on": NOV}
    one = client.get("/api/search/freight", params=params).json()["options"][0]
    assert one["total_usd"] == "551.74"                       # same as the SQL function
    three = client.get("/api/search/freight", params={**params, "containers": 3}).json()
    opt = three["options"][0]
    # (300 OF + 150 OTHC) x 3 + 30 B/L once + 198 EUR / 0.92
    assert opt["total_usd"] == "1595.22" and opt["per_container_usd"] == "531.74"
    bl = next(c for c in opt["charges"] if c["code"] == "BL")
    assert bl["quantity"] == 1 and bl["line_usd"] == "30.00"
    assert three["fx"] == [{"currency": "EUR", "per_usd": "0.920000", "on_date": "2026-11-01"}]


@pg_required
def test_freight_missing_fx_gives_no_total(client):
    opt = client.get("/api/search/freight", params={"origin": "EGALY", "destination": "LBBEY",
                                                    "equipment": "40HC", "on": NOV}).json()["options"][0]
    assert opt["total_usd"] is None and opt["missing_fx"] == ["EGP"]


@pg_required
def test_freight_expiry_flag_and_expired_hidden(client):
    p = {"origin": "EGALY", "destination": "BRNVT", "equipment": "20DV"}
    opt = client.get("/api/search/freight", params={**p, "on": "2026-11-25"}).json()["options"][0]
    assert opt["days_left"] == 5 and opt["expiring_soon"] is True
    assert client.get("/api/search/freight", params={**p, "on": "2026-12-01"}).json()["options"] == []


@pg_required
def test_unknown_location_is_a_clear_422(client):
    r = client.get("/api/search/freight", params={"origin": "Atlantis", "destination": "ITGOA", "equipment": "20DV"})
    assert r.status_code == 422 and "Unknown origin 'Atlantis'" in r.json()["detail"]


@pg_required
def test_trucking_weight_picks_class(client):
    p = {"port": "Alexandria", "zone": "EL Sadat City", "equipment": "20DV", "on": NOV}
    light = client.get("/api/search/trucking", params={**p, "weight_t": "7.5"}).json()
    heavy = client.get("/api/search/trucking", params={**p, "weight_t": "21"}).json()
    both = client.get("/api/search/trucking", params=p).json()
    assert light["weight_class"] == "light" and {o["weight_class"] for o in light["options"]} == {"light"}
    assert heavy["weight_class"] == "heavy" and {o["weight_class"] for o in heavy["options"]} == {"heavy"}
    assert {o["weight_class"] for o in both["options"]} == {"light", "heavy"}
    assert light["options"][-1] == {**light["options"][-1], "trucker": "Ebad El Rahman", "price": "12000.00",
                                    "currency": "EGP", "total_usd": None}


@pg_required
def test_trucking_trips_multiply_and_convert(client, db):
    psql(db, "INSERT INTO fx_rates VALUES ('EGP', '2026-11-01', 50, 'test') ON CONFLICT DO NOTHING")
    r = client.get("/api/search/trucking", params={"port": "EGALY", "zone": "EL Sadat City", "equipment": "20DV",
                                                   "weight_t": 5, "trips": 2, "on": NOV}).json()
    cheapest = r["options"][0]
    assert cheapest["price"] == "11000.00" and cheapest["total"] == "22000.00" and cheapest["total_usd"] == "440.00"


@pg_required
def test_location_lookup(client):
    codes = [l["code"] for l in client.get("/api/locations", params={"q": "sadat"}).json()]
    assert codes == ["EG-Z-EL-SADAT-CITY"]
    ports = client.get("/api/locations", params={"q": "psd east", "kind": "seaport"}).json()
    assert [p["code"] for p in ports] == ["EGPSE"]
    assert client.get("/api/locations", params={"q": "100%_"}).json() == []


@pg_required
def test_search_runs_read_only(client, db):
    before = psql(db, "SELECT count(*) FROM rate_cards")
    client.get("/api/search/freight", params={"origin": "x'); DELETE FROM rate_cards; --", "destination": "y",
                                              "equipment": "20DV"})
    assert psql(db, "SELECT count(*) FROM rate_cards") == before


@pg_required
def test_expiring(client):
    rows = client.get("/api/rate-cards/expiring", params={"days": 7, "on": "2026-11-25"}).json()
    assert rows and all(r["days_left"] == 5 for r in rows)
