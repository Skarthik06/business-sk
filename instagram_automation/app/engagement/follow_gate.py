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
    """The legacy two-step 'reply DONE' gate (deprecated; default OFF)."""
    return os.getenv("ENGAGE_FOLLOW_GATE", "0").strip().lower() not in ("0", "false", "no", "off")


def _env_official_default() -> bool:
    return os.getenv("FOLLOWGATE_OFFICIAL", "1").strip().lower() not in ("0", "false", "no", "off")


def official_enabled() -> bool:
    """The OFFICIAL follow gate via is_user_follow_business (verified, compliant). Runtime-toggleable
    from the Engagement panel (stored in Redis), falling back to the FOLLOWGATE_OFFICIAL env default.
    Strictly safe: follower → links, non-follower → nudge, unreadable → send anyway (never blocks)."""
    r = _redis()
    if r is not None:
        try:
            v = r.get("followgate:cfg:official")
            if v is not None:
                return v not in ("0", "false", "no", "off")
        except Exception:
            pass
    return _env_official_default()


def set_official(on: bool) -> bool:
    """Toggle the official gate at runtime (persisted in Redis). Returns the new value."""
    r = _redis()
    if r is not None:
        try:
            r.set("followgate:cfg:official", "1" if on else "0")
        except Exception:
            pass
    return on


# ── lightweight decision counters (for the panel insight) ─────────────────────
def incr(metric: str) -> None:
    """Bump a follow-gate counter: 'verified' | 'nudged' | 'sent' | 'fallback'. Best-effort."""
    r = _redis()
    if r is not None:
        try:
            r.incr(f"followgate:stat:{metric}")
        except Exception:
            pass


def stats() -> dict:
    """Panel insight: gate state, backend, pending queue size, and decision counts."""
    out = {"official": official_enabled(), "legacy": enabled(), "backend": backend(),
           "pending": 0, "verified": 0, "nudged": 0, "sent": 0, "fallback": 0, "held": 0}
    r = _redis()
    if r is not None:
        try:
            pend = 0
            for k in r.scan_iter(match="followgate:*", count=500):
                if not k.startswith(("followgate:cfg:", "followgate:stat:", "followgate:chk:")):
                    pend += 1
            out["pending"] = pend
            for k in ("verified", "nudged", "sent", "fallback", "held"):
                out[k] = int(r.get(f"followgate:stat:{k}") or 0)
        except Exception:
            pass
    return out


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


# Held (unverified) commenters are re-checked at most once per _RECHECK seconds, and only while
# Instagram still allows a private reply to their comment (7 days).
_RECHECK = int(os.getenv("FOLLOW_RECHECK_SECS", "1800"))
RECHECK_DAYS = int(os.getenv("FOLLOW_RECHECK_DAYS", "7"))


def recheck_due(account_id, user_id) -> bool:
    """True at most once per _RECHECK seconds per (account, user) — throttles follow re-checks."""
    if not user_id:
        return False
    k = f"followgate:chk:{account_id}:{user_id}"
    r = _redis()
    if r is not None:
        try:
            return bool(r.set(k, "1", nx=True, ex=_RECHECK))
        except Exception:
            pass
    now = time.time()
    ent = _mem.get(k)
    if ent and ent[0] > now:
        return False
    _mem[k] = (now + _RECHECK, "1")
    return True


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


def neutral_reply() -> str:
    """Public reply for a commenter who ISN'T a verified follower yet: warm, no follow nag and no
    "sent to your DM" (nothing was sent). The post itself carries the follow CTA.
    Override with FOLLOW_GATE_NEUTRAL_REPLY."""
    return os.getenv("FOLLOW_GATE_NEUTRAL_REPLY", "").strip() or "Thanks for the love! 💛"


def public_reply(handle: Optional[str]) -> str:
    """Public comment reply nudging a NON-follower to follow, then re-comment to unlock (official
    gate). Override with FOLLOW_GATE_PUBLIC."""
    h = (handle or "").strip()
    at = f"@{h.lstrip('@')}" if h else "us"
    default = f"Follow {at} to unlock 🔒 — once you follow, comment again and I’ll DM your links instantly! 💛"
    return os.getenv("FOLLOW_GATE_PUBLIC", "").strip() or default
