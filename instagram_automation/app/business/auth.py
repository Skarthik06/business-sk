"""Single-admin authentication (Spec: ADMIN-ONLY architecture).

One administrator, no registration, no roles, no multi-tenancy. Stateless
HMAC-signed access/refresh tokens (stdlib only — no new deps). Logout revokes a
token id in-process (fine for a single-admin app; resets on restart).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

from app import settings

_ACCESS_TTL = 12 * 3600          # 12 hours
_REFRESH_TTL = 30 * 24 * 3600    # 30 days
_REVOKED: set[str] = set()

# A fresh random nonce generated ONCE per process start. It is mixed into the token
# signing secret, so every previously-issued token becomes invalid the moment the server
# (re)starts — i.e. the login page is always required after a server run. Set
# ADMIN_PERSIST_SESSIONS=1 to opt out (keep sessions across restarts).
_BOOT_NONCE = "" if os.getenv("ADMIN_PERSIST_SESSIONS", "0") in ("1", "true", "True") \
    else secrets.token_hex(16)


def _secret() -> bytes:
    s = settings.ADMIN_SECRET or hashlib.sha256(
        (settings.ADMIN_PASSWORD + "|instagram_business_admin").encode()).hexdigest()
    return (s + "|" + _BOOT_NONCE).encode()


def _sign(payload: Dict[str, Any]) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def _make(kind: str, ttl: int) -> str:
    return _sign({"sub": "admin", "kind": kind, "jti": secrets.token_hex(8),
                  "exp": int(time.time()) + ttl})


def verify(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
        if payload.get("exp", 0) < time.time():
            return None
        if payload.get("jti") in _REVOKED:
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


def login(username: str, password: str) -> Optional[Dict[str, str]]:
    ok_user = hmac.compare_digest(username or "", settings.ADMIN_USERNAME)
    ok_pass = bool(password) and hmac.compare_digest(password, settings.ADMIN_PASSWORD)
    if ok_user and ok_pass:
        return {"access_token": _make("access", _ACCESS_TTL),
                "refresh_token": _make("refresh", _REFRESH_TTL)}
    return None


def refresh(refresh_token: str) -> Optional[Dict[str, str]]:
    payload = verify(refresh_token)
    if not payload or payload.get("kind") != "refresh":
        return None
    return {"access_token": _make("access", _ACCESS_TTL)}


def revoke(token: str) -> None:
    payload = verify(token)
    if payload and payload.get("jti"):
        _REVOKED.add(payload["jti"])


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
