"""
admin_gate.py — the affiliate API requires the SAME Studio admin session as the IG backend.

Why a remote check: the IG backend's admin tokens are HMAC-signed with a secret that includes a
per-process boot nonce (instagram_automation/app/business/auth.py), so they can't be verified here.
Instead a Bearer token is confirmed by asking the IG backend (`GET /api/v1/admin/me`, internal Docker
network) and the answer is cached briefly (valid 60 s, invalid 10 s). Logout, expiry and the
"every restart logs you out" rule therefore apply to this API exactly as they do to /api.

Who gets in:
  * the Studio            — `Authorization: Bearer <admin token>` (verified as above)
  * other services        — `X-Internal-Key: <SK_INTERNAL_KEY>` (shared secret in both .env files);
                            the network location is NOT trusted, because public traffic also arrives
                            from inside Docker (Caddy → Vite proxy → this service)
  * everyone (read-only)  — the public storefront `/hub`, its data `/api/hub`, and `/api/health`
  * scrape workers        — `/api/scrape/jobs` + `/api/scrape/result` carry their own worker token
Never logs tokens or keys.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

OPEN_PATHS = {"/api/health", "/hub", "/api/hub", "/api/scrape/jobs", "/api/scrape/result"}
GATED_EXTRA = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

_CACHE: Dict[str, Tuple[bool, float]] = {}      # sha256(authorization) → (valid, expires_at)
_OK_TTL, _BAD_TTL = 60.0, 10.0


def _verify_url() -> str:
    return (os.getenv("ADMIN_VERIFY_URL") or "http://backend:8000/api/v1/admin/me").strip()


def needs_auth(method: str, path: str) -> bool:
    if method == "OPTIONS" or path in OPEN_PATHS:
        return False
    return path.startswith("/api/") or path in GATED_EXTRA


def internal_ok(key: Optional[str]) -> bool:
    want = (os.getenv("SK_INTERNAL_KEY") or "").strip()
    return bool(want) and bool(key) and hmac.compare_digest(key.strip(), want)


def verify_admin(authorization: Optional[str]) -> Optional[bool]:
    """True = valid admin session · False = not · None = the IG backend could not be asked.
    Blocking (urllib) — call through asyncio.to_thread."""
    if not authorization or not authorization.lower().startswith("bearer ") or len(authorization) > 4096:
        return False
    key = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        req = urllib.request.Request(_verify_url(), headers={"Authorization": authorization})
        with urllib.request.urlopen(req, timeout=5) as r:
            ok = r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            ok = False
        else:
            return None
    except Exception:
        return None
    if len(_CACHE) > 1000:                          # bounded: drop expired entries
        for k in [k for k, v in _CACHE.items() if v[1] <= now]:
            _CACHE.pop(k, None)
    _CACHE[key] = (ok, now + (_OK_TTL if ok else _BAD_TTL))
    return ok
