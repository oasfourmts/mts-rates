"""Settings, read once from the environment.

DATABASE_URL     SQLAlchemy URL, e.g. postgresql+psycopg://user@/mts?host=/cloudsql/PROJECT:REGION:INSTANCE
AUTH_MODE        "iap" (default, production) or "dev" (local only: every request is DEV_USER)
IAP_AUDIENCE     IAP's expected JWT audience, e.g. /projects/123/global/backendServices/456 or
                 /projects/123/locations/europe-west1/services/mts-rates (needed when AUTH_MODE=iap)
ALLOWED_DOMAIN   Only these Workspace accounts get in (default mtslogistics.net)
DEV_USER         The user every request runs as when AUTH_MODE=dev (always an admin)
ADMIN_EMAILS     Comma-separated accounts that may enter exchange rates (until roles live in the database)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    auth_mode: str
    iap_audience: str | None
    allowed_domain: str
    dev_user: str
    admin_emails: frozenset[str] = frozenset()


def load() -> Settings:
    mode = os.environ.get("AUTH_MODE", "iap").strip().lower()
    if mode not in ("iap", "dev"):
        raise ValueError(f"AUTH_MODE must be 'iap' or 'dev', not {mode!r}")
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres@/mts?host=/tmp&port=5433"),
        auth_mode=mode,
        iap_audience=os.environ.get("IAP_AUDIENCE") or None,
        allowed_domain=os.environ.get("ALLOWED_DOMAIN", "mtslogistics.net").lower().lstrip("@"),
        dev_user=os.environ.get("DEV_USER", "dev@mtslogistics.net"),
        admin_emails=frozenset(e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()),
    )
