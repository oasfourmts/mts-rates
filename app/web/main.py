"""MTS Rates web app: JSON API under /api, and the React screens (frontend/dist) at /.

Run locally:
    AUTH_MODE=dev DATABASE_URL=postgresql+psycopg://postgres@/mts?host=/tmp\\&port=5433 \\
        uvicorn app.web.main:create_app --factory --reload
"""

import json
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.web import config, search
from app.web.auth import User, _verify_iap, current_user

class MoneyJSON(JSONResponse):
    """Decimals go out as strings ("1595.22"), never floats, so no amount is ever rounded on the way."""

    def render(self, content) -> bytes:
        def default(o):
            if isinstance(o, Decimal):
                return str(o)
            if isinstance(o, date):
                return o.isoformat()
            raise TypeError(f"not JSON serializable: {type(o).__name__}")
        return json.dumps(content, default=default, ensure_ascii=False, separators=(",", ":")).encode()


def today() -> date:
    """Rates are valid by Egyptian calendar date, whatever time zone the server runs in."""
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(settings: config.Settings | None = None, verify_iap=_verify_iap) -> FastAPI:
    settings = settings or config.load()
    app = FastAPI(title="MTS Rates", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
                  default_response_class=MoneyJSON)
    app.state.settings = settings
    app.state.verify_iap = verify_iap
    app.state.engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)

    def conn(request: Request) -> Iterator[Connection]:
        # Read-only transaction: searching can never change a rate, whatever reaches the query.
        with request.app.state.engine.connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            yield c

    Conn = Annotated[Connection, Depends(conn)]
    Me = Annotated[User, Depends(current_user)]

    def run(fn, *args, **kwargs):
        try:
            return MoneyJSON(fn(*args, **kwargs))
        except search.SearchError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/me")
    def me(user: Me):
        return {"email": user.email}

    @app.get("/api/locations")
    def locations(user: Me, c: Conn, q: str = Query(..., min_length=1, max_length=60),
                  kind: Annotated[list[str] | None, Query()] = None):
        return search.find_locations(c, q, kind)

    @app.get("/api/equipment")
    def equipment(user: Me, c: Conn):
        return [dict(r) for r in c.execute(text("SELECT code, description FROM equipment ORDER BY code")).mappings()]

    @app.get("/api/search/freight")
    def search_freight(user: Me, c: Conn, origin: str, destination: str, equipment: str,
                       on: date | None = None, containers: int = Query(1, ge=1, le=500),
                       weight_t: Decimal | None = Query(None, gt=0, le=60), via: str | None = None):
        """Port origin: port to port. Inland zone origin (a factory): door to port via every port with a
        trucking price, or only `via`. 20DV door searches need weight_t (trucking is priced by weight)."""
        return run(search.search_lane, c, origin, destination, equipment, on or today(), containers,
                   weight_t, via or None)

    @app.get("/api/search/trucking")
    def search_trucking(user: Me, c: Conn, port: str, zone: str, equipment: str, on: date | None = None,
                        weight_t: Decimal | None = Query(None, gt=0, le=60), trips: int = Query(1, ge=1, le=500)):
        return run(search.search_trucking, c, port, zone, equipment, on or today(), weight_t, trips)

    @app.get("/api/rate-cards/expiring")
    def expiring(user: Me, c: Conn, days: int = Query(7, ge=0, le=90), on: date | None = None):
        return run(search.expiring, c, days, on or today())

    if FRONTEND.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(FRONTEND / "index.html")

    return app

