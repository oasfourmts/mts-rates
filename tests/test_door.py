"""Door-to-port search: pickup trucking (port -> zone price, used zone -> port as MTS confirmed) + ocean freight.
Uses real fixture rows: 10th of Ramadan to Beirut in 40HC is reachable via Alexandria (COSCO) and Port Said East
(Maersk). Skipped without PG_TEST_DSN."""
import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app
from tests.conftest import pg_required, psql
from tests.test_web import settings

pytestmark = pg_required
NOV = "2026-11-10"
LANE = {"origin": "10th of Ramadan", "destination": "LBBEY", "equipment": "40HC", "on": NOV}


@pytest.fixture(scope="module")
def client(db):
    with TestClient(create_app(settings(db))) as c:
        yield c


def test_without_fx_ranks_by_freight_and_names_the_missing_rate(client):
    body = client.get("/api/search/freight", params=LANE).json()
    assert body["mode"] == "door" and body["origin"]["code"] == "EG-Z-10TH-OF-RAMADAN"
    assert [o["port"]["code"] for o in body["options"]] == ["EGPSE", "EGALY"]
    assert all(o["total_usd"] is None and "EGP" in o["missing_fx"] for o in body["options"])


def test_door_total_is_trucking_plus_freight(client, db):
    psql(db, "INSERT INTO fx_rates VALUES ('EGP', '2026-11-01', 50, 'test') ON CONFLICT DO NOTHING")
    body = client.get("/api/search/freight", params={**LANE, "containers": 2}).json()
    best, other = body["options"]
    # Port Said East: Maersk (251 OF + 255 OTHC) x 2 = 1012; Ocean 14,500 EGP x 2 trips / 50 = 580
    assert best["port"]["code"] == "EGPSE" and best["freight"]["carrier"] == "Maersk"
    assert best["trucking"]["trucker"] == "Ocean" and best["trucking"]["total"] == "29000.00"
    assert best["total_usd"] == "1592.00" and best["per_container_usd"] == "796.00" and best["other_truckers"] == 3
    # Alexandria: COSCO (880 + 175) x 2 + 400 EGP B/L once = 2118; Ebad El Rahman 15,500 x 2 / 50 = 620
    assert other["port"]["code"] == "EGALY" and other["total_usd"] == "2738.00"
    assert {p["code"] for p in body["ports_checked"]} >= {"EGALY", "EGPSE"}


def test_via_limits_to_one_port(client):
    body = client.get("/api/search/freight", params={**LANE, "via": "Alexandria"}).json()
    assert [o["port"]["code"] for o in body["options"]] == ["EGALY"]


def test_20dv_door_needs_weight(client):
    r = client.get("/api/search/freight", params={**LANE, "equipment": "20DV"})
    assert r.status_code == 422 and "cargo weight" in r.json()["detail"]


def test_no_freight_from_any_port_says_which_ports_were_tried(client):
    body = client.get("/api/search/freight", params={**LANE, "destination": "ESVLC"}).json()
    assert body["options"] == [] and body["ports_without_rates"]


def test_port_origin_still_prices_port_to_port(client):
    body = client.get("/api/search/freight", params={**LANE, "origin": "EGPSE"}).json()
    assert body["mode"] == "freight" and body["options"][0]["carrier"] == "Maersk"
