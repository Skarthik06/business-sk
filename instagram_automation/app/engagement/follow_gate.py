"""
engagement/follow_gate.py — the two-step "follow → reply → get links" DM gate (Redis-backed).

Instagram's API can't verify whether a commenter follows the account, so we use a SOFT gate that
converts hard:

  1. A commenter comments the keyword → we DM a STRONG follow nudge ("follow us, then reply DONE")
     and stash a PENDING unlock for that user (their IGSID) → we do NOT send the links yet.
  2. When that user replies in DM → we pop the pending unlock and send the real product/store links.

State is kept in REDIS (keyed by account + user IGSID) so it scales across many concurrent users
and survives restarts; if Redis is unavailable it falls back to an in-process dict so the flow
never breaks. A pending unlock expires after FOLLOW_GATE_TTL (default 24h).
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

_TTL = int(os.getenv("FOLLOW_GATE_TTL", "86400"))          # 24h


def enabled() -> bool:
    return os.getenv("ENGAGE_FOLLOW_GATE", "1").strip().lower() not in ("0", "false", "no", "off")


# ── Redis connection (lazy, cached, tolerant) ─────────────────────────────────
_redis_client = None
_redis_tried = False


def _redis():
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    try:
        import redis  # type: ignore
        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        c = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
        c.ping()
        _redis_client = c
    except Exception:
        _redis_client = None
    return _redis_client


# in-process fallback: {key: (expires_at, json)}
_mem: dict[str, tuple[float, str]] = {}


def _key(account_id, user_id) -> str:
    return f"followgate:{account_id}:{user_id}"


def set_pending(account_id, user_id, payload: dict) -> bool:
    """Stash a pending unlock for (account, user). Returns True if stored."""
    if not user_id:
        return False
    k = _key(account_id, user_id)
    val = json.dumps({**(payload or {}), "ts": time.time()})
    r = _redis()
    if r is not None:
        try:
            r.setex(k, _TTL, val)
            return True
        except Exception:
            pass
    _mem[k] = (time.time() + _TTL, val)                     # fallback
    return True


def pop_pending(account_id, user_id) -> Optional[dict]:
    """Atomically fetch + clear a pending unlock for (account, user). None if none/expired."""
    if not user_id:
        return None
    k = _key(account_id, user_id)
    r = _redis()
    if r is not None:
        try:
            val = r.get(k)
            if val:
                r.delete(k)
                return json.loads(val)
            return None
        except Exception:
            pass
    ent = _mem.pop(k, None)                                 # fallback
    if ent and ent[0] > time.time():
        try:
            return json.loads(ent[1])
        except Exception:
            return None
    return None


def has_pending(account_id, user_id) -> bool:
    if not user_id:
        return False
    k = _key(account_id, user_id)
    r = _redis()
    if r is not None:
        try:
            return bool(r.exists(k))
        except Exception:
            pass
    ent = _mem.get(k)
    return bool(ent and ent[0] > time.time())


def backend() -> str:
    return "redis" if _redis() is not None else "memory"


# ── messages (strong follow nudge) ───────────────────────────────────────────
def first_message(handle: Optional[str]) -> str:
    """Step-1 DM: the strong follow nudge. Override with FOLLOW_GATE_MSG1."""
    h = (handle or "").strip()
    at = f"@{h.lstrip('@')}" if h else "us"
    default = (f"Hey! 🎁 Your links + coupons are ready.\n\n"
               f"1️⃣ Follow {at} (I only DM my followers 💛)\n"
               f"2️⃣ Reply DONE here\n\n"
               f"…and I’ll send every product + store link right away! 🛍️")
    return os.getenv("FOLLOW_GATE_MSG1", "").strip() or default


def unlock_message() -> str:
    """Step-2 DM caption (sent with the product cards). Override with FOLLOW_GATE_MSG2."""
    default = "You’re in! 💛 Here are your links — coupons are on the store page. Happy shopping! 🛍️"
    return os.getenv("FOLLOW_GATE_MSG2", "").strip() or default


def public_reply(handle: Optional[str]) -> str:
    """Public comment reply nudging follow + check DM. Override with FOLLOW_GATE_PUBLIC."""
    default = "Sent you a DM! 💌 Follow us + reply DONE to unlock all the links 🛍️"
    return os.getenv("FOLLOW_GATE_PUBLIC", "").strip() or default
