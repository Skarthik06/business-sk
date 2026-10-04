"""Server-side record of every Post-to-IG publish, keyed by the Studio's post key.

Why: a carousel publish can take minutes (render + Instagram). On a phone the Studio page is often
paused or reloaded while that runs (switch to the home screen and back) — the post goes LIVE but
the page never hears the answer, so it stayed in the Post-to-IG queue and could be posted twice.

Now the server is the source of truth:
  begin(key)   when /api/sk/carousel starts  → a second publish of the same post is refused (409)
  finish(key)  when it is live (media id + permalink kept)
  fail(key)    when it did not go live      → posting again is allowed
  status(keys) the Studio asks on load / when the app comes back → live posts leave the queue,
               and get recorded (dedup memory + storefront) if the page missed that step
  ack(key)     the Studio recorded it        → never recorded twice
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from app import settings

PATH = settings.IMAGES_DIR / "sk_post_ledger.json"
STALE_POSTING = 45 * 60          # a 'posting' older than this is treated as dead (server restarted mid-post)
KEEP = 21 * 86400                # forget entries after 3 weeks
_LOCK = threading.Lock()


class AlreadyPosted(Exception):
    def __init__(self, rec: Dict[str, Any]):
        super().__init__(rec.get("state", ""))
        self.rec = rec


def _load() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(PATH.read_text("utf-8"))
    except Exception:
        return {}


def _save(d: Dict[str, Dict[str, Any]]) -> None:
    from app import cache
    cache.invalidate("post_plan")                     # posts today changed → the plan re-counts
    now = time.time()
    d = {k: v for k, v in d.items() if now - float(v.get("at", now)) < KEEP}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), "utf-8")
    tmp.replace(PATH)


def _clean(key: str) -> str:
    return "".join(c for c in (key or "") if c.isalnum() or c in "-_:.")[:80]


def begin(key: str, label: str = "", account_id: Optional[int] = None) -> None:
    key = _clean(key)
    if not key:
        return
    with _LOCK:
        d = _load()
        cur = d.get(key)
        if cur and (cur.get("state") == "posted"
                    or (cur.get("state") == "posting" and time.time() - cur.get("at", 0) < STALE_POSTING)):
            raise AlreadyPosted(cur)
        d[key] = {"state": "posting", "at": time.time(), "label": label[:80], "acct": account_id}
        _save(d)


def finish(key: str, media_id: str = "", permalink: str = "") -> None:
    key = _clean(key)
    if not key:
        return
    with _LOCK:
        d = _load()
        d[key] = {**d.get(key, {}), "state": "posted", "at": time.time(), "media_id": media_id or "",
                  "permalink": permalink or "", "recorded": False}
        _save(d)


def fail(key: str, error: str = "") -> None:
    key = _clean(key)
    if not key:
        return
    with _LOCK:
        d = _load()
        if d.get(key, {}).get("state") != "posted":
            d[key] = {**d.get(key, {}), "state": "failed", "at": time.time(), "error": str(error)[:200]}
            _save(d)


def status(keys: List[str]) -> Dict[str, Dict[str, Any]]:
    d = _load()
    now = time.time()
    out = {}
    for k in keys[:100]:
        rec = d.get(_clean(k))
        if not rec:
            continue
        if rec.get("state") == "posting" and now - rec.get("at", 0) >= STALE_POSTING:
            rec = {**rec, "state": "failed", "error": "the server stopped before this post finished — check Instagram before posting again"}
        out[k] = rec
    return out


def ack(key: str) -> bool:
    key = _clean(key)
    with _LOCK:
        d = _load()
        if key not in d:
            return False
        d[key]["recorded"] = True
        _save(d)
        return True


def in_flight(account_id: int) -> int:
    """Posts of this account being published right now (counted in today's posts for the best-time
    plan while they run)."""
    now = time.time()
    return sum(1 for v in _load().values() if v.get("state") == "posting" and v.get("acct") == account_id
               and now - float(v.get("at", 0)) < STALE_POSTING)
