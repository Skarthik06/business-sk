"""Device pairing for worker apps (e.g. the Business-SK Helper Android app).

  admin  → create a one-time pairing code (8 chars, 10 min, single use) + QR (skhelper://pair?…)
  device → POST /v1/workers/pair {code, name, model}  → its OWN worker id + token (shown once)
  admin  → list / revoke devices

Codes and tokens are stored only as SHA-256 hashes. Pair attempts are rate-limited per IP, so an
8-char code (≈40 bits) valid for 10 minutes cannot be guessed. A device token is bound to its own
worker id — a phone can never act as the laptop or another device."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from . import metrics, store

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L
CODE_TTL = 600
PAIR = "scraper:pair:"
PAIR_RL = "scraper:pairrl:"
PAIR_MAX_ATTEMPTS = 10                               # per IP per 10 minutes
_TOKEN_CACHE: Dict[str, tuple] = {}                  # sha256(token) → (device row | None, expires)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def public_worker_url() -> str:
    return (os.getenv("SCRAPER_PUBLIC_WORKER_URL") or "").strip().rstrip("/")


def qr_svg(text: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=7, border=2, dark="#111111", light="#ffffff",
                                     xmldecl=False)
    return buf.getvalue().decode("utf-8")


async def create_pairing(created_by: int) -> Dict[str, Any]:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    await metrics.redis.set(PAIR + _h(raw), json.dumps({"t": time.time(), "by": created_by}), ex=CODE_TTL)
    code = f"{raw[:4]}-{raw[4:]}"
    server = public_worker_url()
    link = f"skhelper://pair?server={quote(server, safe='')}&code={raw}" if server else ""
    # The QR holds a normal https link: phone cameras / Google Lens open https (not skhelper://).
    # With the app installed Android opens it straight in SK Helper (App Link); otherwise a small
    # page shows the code + "Open in SK Helper" (android-helper/web/pair.html).
    page = pair_page_url(raw)
    return {"code": code, "expires_in": CODE_TTL, "server": server, "pair_url": link, "pair_page": page,
            "qr_svg": qr_svg(page or link) if (page or link) else ""}


def pair_page_url(raw_code: str) -> str:
    base = (os.getenv("SCRAPER_PAIR_PAGE_URL") or "").strip()
    if not base:
        server = public_worker_url()
        if not server.endswith("/scraper-worker"):
            return ""
        base = server[: -len("/scraper-worker")] + "/helper/pair.html"
    return f"{base}?code={raw_code}"


async def rate_limited(ip: str) -> bool:
    key = PAIR_RL + (ip or "unknown")
    n = await metrics.redis.incr(key)
    if n == 1:
        await metrics.redis.expire(key, CODE_TTL)
    return n > PAIR_MAX_ATTEMPTS


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "phone").lower()).strip("-")[:24] or "phone"
    return s if s.startswith("phone") else f"phone-{s}"[:30]


async def redeem(code: str, name: str, model: str) -> Optional[Dict[str, Any]]:
    """Single use: the code is deleted the moment it is read. None = invalid / expired."""
    raw = normalize_code(code)
    if len(raw) != 8:
        return None
    val = await metrics.redis.getdel(PAIR + _h(raw))
    if not val:
        return None
    worker_id = f"{_slug(name)}-{secrets.token_hex(2)}"
    token = "skw_" + secrets.token_urlsafe(32)
    row = await store.q("""insert into devices (worker_id, name, model, token_hash)
                           values (%s,%s,%s,%s) returning id, worker_id, name, created_at""",
                        (worker_id, (name or "Phone")[:60], (model or "")[:60], _h(token)), one=True)
    return {"device_id": row["id"], "worker_id": row["worker_id"], "token": token}


async def by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or not token.startswith("skw_") or len(token) > 200:
        return None
    k = _h(token)
    now = time.time()
    hit = _TOKEN_CACHE.get(k)
    if hit and hit[1] > now:
        return hit[0]
    row = await store.q("select id, worker_id, name from devices where token_hash = %s and revoked_at is null",
                        (k,), one=True)
    _TOKEN_CACHE[k] = (row, now + 60)
    return row


async def touch(device_id: int) -> None:
    await store.q("update devices set last_seen = now() where id = %s", (device_id,))


async def list_devices() -> list:
    rows = await store.q("""select id, worker_id, name, model, created_at, last_seen, revoked_at
                            from devices order by id desc limit 100""") or []
    return [{**r, **{k: (r[k].isoformat() if r[k] else None) for k in ("created_at", "last_seen", "revoked_at")}}
            for r in rows]


async def revoke_worker(worker_id: str) -> bool:
    """Revoke the paired device that owns this worker id (if any)."""
    row = await store.q("select id from devices where worker_id = %s and revoked_at is null", (worker_id,), one=True)
    return bool(row and await revoke(row["id"]))


async def revoke(device_id: int) -> Optional[str]:
    row = await store.q("update devices set revoked_at = now() where id = %s and revoked_at is null returning worker_id",
                        (device_id,), one=True)
    _TOKEN_CACHE.clear()                                   # revocation is immediate
    if row:
        from . import workers
        await metrics.redis.delete(workers.HB + row["worker_id"], workers.QW + row["worker_id"])
        return row["worker_id"]
    return None
