"""Who is calling. In production the app sits behind Identity-Aware Proxy (Google sign-in, MTS accounts only):
IAP signs every request with a JWT, and we verify that signature rather than trusting a plain header, so a
request that reaches Cloud Run some other way is refused. Fails closed: no audience configured, no entry."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"


@dataclass(frozen=True)
class User:
    email: str
    is_admin: bool = False


def _verify_iap(token: str, audience: str) -> dict:
    # Imported here so the dev server and unit tests do not need network access or google-auth's transport.
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    return id_token.verify_token(token, g_requests.Request(), audience=audience, certs_url=IAP_CERTS_URL)


def current_user(request: Request) -> User:
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return User(settings.dev_user, is_admin=True)
    if not settings.iap_audience:
        raise HTTPException(503, "Sign-in is not configured (IAP_AUDIENCE is not set)")
    token = request.headers.get("x-goog-iap-jwt-assertion")
    if not token:
        raise HTTPException(401, "Sign in with your MTS Google account")
    try:
        claims = request.app.state.verify_iap(token, settings.iap_audience)
    except Exception:  # bad signature, expired, wrong audience: all the same to the caller
        raise HTTPException(401, "Your sign-in could not be verified; reload the page") from None
    email = str(claims.get("email", "")).lower()
    if claims.get("iss") != IAP_ISSUER or not email.endswith("@" + settings.allowed_domain):
        raise HTTPException(403, f"Only @{settings.allowed_domain} accounts can use this app")
    return User(email, is_admin=email in settings.admin_emails)


def require_admin(request: Request) -> User:
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Only admins can change exchange rates. Ask an admin to add yours.")
    return user
