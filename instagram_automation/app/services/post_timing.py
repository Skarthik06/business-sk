"""Post Timing agent (spec: app/agents/post-timing.agents.md).

Picks the TWO best times to post each day and enforces the 2-posts-a-day rule. Deterministic —
no AI tokens:

  1. PRIOR — when Indian Instagram audiences engage (IST): a lunch peak (~1 PM) and a stronger
     evening peak (~8:30 PM) on weekdays; later, wider peaks at weekends; a small morning bump.
  2. YOUR AUDIENCE — Instagram's `online_followers` (hours your followers are online; Instagram
     only provides it from ~100 followers). Blended in at up to 60 %.
  3. YOUR POSTS — likes + comments of your own posts by the hour they went live (settled posts,
     relative to your median). Blended in as data accumulates (up to 40 % at ~40 posts).

  Slots: the best half hour in 10:00–16:00 and the best in 17:00–22:30 → always 2 posts ≥ 4 h apart.
  No posting limit: SK_MAX_POSTS_PER_DAY (2) is only how many best-time slots/reminders a day gets
  (reminders stop once that many are posted); you can always post more.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app import settings

TZ = ZoneInfo(os.getenv("SK_POST_TZ", "Asia/Kolkata"))
CAP = max(1, int(os.getenv("SK_MAX_POSTS_PER_DAY", "2")))
LEAD_MIN = max(0, int(os.getenv("SK_POST_REMIND_LEAD_MIN", "30")))      # reminder this long before a slot
INSIGHTS_TZ = ZoneInfo(os.getenv("SK_ONLINE_FOLLOWERS_TZ", "America/Los_Angeles"))   # Meta reports in Pacific time
CACHE = settings.IMAGES_DIR / "sk_post_timing.json"
CACHE_SECS = 6 * 3600
WINDOWS = [(10.0, 16.0), (17.0, 22.5)]                                  # slot 1 · slot 2 (local hours)
_LOCK = threading.Lock()

# (centre hour, width σ, height) — relative engagement, India / IST
_PRIOR = {
    "weekday": [(13.0, 1.4, 0.72), (20.5, 1.5, 1.0), (8.75, 1.0, 0.30)],
    "friday": [(13.0, 1.4, 0.70), (19.75, 1.6, 1.0), (8.75, 1.0, 0.28)],
    "weekend": [(12.0, 1.8, 0.85), (20.0, 1.8, 1.0), (10.0, 1.2, 0.35)],
}


def _kind(d: date) -> str:
    return "weekend" if d.weekday() >= 5 else "friday" if d.weekday() == 4 else "weekday"


def _prior(d: date, h: float) -> float:
    v = sum(a * math.exp(-0.5 * ((h - c) / s) ** 2) for c, s, a in _PRIOR[_kind(d)])
    return 0.04 + v


def _load() -> Dict[str, Any]:
    try:
        return json.loads(CACHE.read_text("utf-8"))
    except Exception:
        return {}


def _save(d: Dict[str, Any]) -> None:
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), "utf-8")
    tmp.replace(CACHE)


# ── data: your audience + your posts (refreshed every 6 h, 2 Graph calls) ──────────────────────
def _account() -> Optional[Dict[str, Any]]:
    from app import rags
    from app.engagement import store
    want = os.getenv("SK_POST_ACCOUNT_ID", "").strip()
    accts = rags.list_accounts() or []
    if want:
        return rags.get_account(int(want), with_secret=True)
    best, best_n = None, 0
    for a in accts:                                       # the account that posts the affiliate carousels
        try:
            n = len(store.list_affiliate_posts(int(a["id"])))
        except Exception:
            n = 0
        if n > best_n:
            best, best_n = a, n
    return rags.get_account(int(best["id"]), with_secret=True) if best else None


def _refresh(acct: Dict[str, Any]) -> Dict[str, Any]:
    import requests
    from app.services.instagram import GRAPH
    ig, tok = acct.get("ig_business_id"), acct.get("ig_access_token")
    out: Dict[str, Any] = {"t": time.time(), "account_id": acct.get("id"), "online": None, "posts": []}
    if not (ig and tok):
        return out
    try:                                                  # hours your followers are online (≥100 followers)
        r = requests.get(f"{GRAPH}/{ig}/insights", params={"metric": "online_followers", "period": "lifetime",
                                                            "access_token": tok}, timeout=20).json()
        hours = [0.0] * 24
        days = 0
        for item in (r.get("data") or []):
            for v in item.get("values") or []:
                vals = v.get("value") or {}
                if not vals:
                    continue
                days += 1
                for k, n in vals.items():
                    hours[int(k) % 24] += float(n or 0)
        if days and max(hours) > 0:
            local = [0.0] * 24                            # Meta's hours → local hours
            ref = datetime.now(INSIGHTS_TZ).replace(minute=0, second=0, microsecond=0)
            for h, n in enumerate(hours):
                lh = ref.replace(hour=h).astimezone(TZ).hour
                local[lh] += n / days
            out["online"] = local
            out["online_days"] = days
    except Exception:
        pass
    try:                                                  # your own posts' engagement by posting hour
        r = requests.get(f"{GRAPH}/{ig}/media", params={"fields": "timestamp,like_count,comments_count",
                                                         "limit": 60, "access_token": tok}, timeout=20).json()
        now = datetime.now(timezone.utc)
        for m in r.get("data") or []:
            ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if (now - ts).total_seconds() < 24 * 3600:   # still collecting likes — not settled
                continue
            lt = ts.astimezone(TZ)
            out["posts"].append({"h": lt.hour + lt.minute / 60, "eng": int(m.get("like_count") or 0) + 3 * int(m.get("comments_count") or 0)})
    except Exception:
        pass
    return out


def data(force: bool = False) -> Dict[str, Any]:
    with _LOCK:
        d = _load()
        if force or time.time() - float(d.get("t") or 0) > CACHE_SECS:
            acct = _account()
            if acct:
                d = _refresh(acct)
                _save(d)
        return d


# ── the curve + the two slots ──────────────────────────────────────────────────────────────────
def curve(d: date, dat: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dat = dat if dat is not None else data()
    grid = [x / 2 for x in range(48)]                     # half hours
    pr = [_prior(d, h) for h in grid]
    pmax = max(pr)
    score = [p / pmax for p in pr]
    basis = ["India Instagram peak hours (IST)"]
    on = dat.get("online")
    if on and max(on) > 0:                                # your followers' online hours
        m = max(on)
        o = [(on[int(h)] * (1 - (h % 1)) + on[(int(h) + 1) % 24] * (h % 1)) / m for h in grid]
        score = [0.4 * s + 0.6 * x for s, x in zip(score, o)]
        basis.append(f"your followers' online hours ({dat.get('online_days', 0)} days)")
    posts = [p for p in (dat.get("posts") or []) if p.get("eng", 0) > 0]
    if len(posts) >= 5:                                   # your posts: engagement by hour, smoothed
        med = sorted(p["eng"] for p in posts)[len(posts) // 2] or 1
        num = [0.0] * 48
        den = [0.0] * 48
        for p in posts:
            for i, h in enumerate(grid):
                dist = min(abs(h - p["h"]), 24 - abs(h - p["h"]))
                w = math.exp(-0.5 * (dist / 1.0) ** 2)
                num[i] += w * (p["eng"] / med)
                den[i] += w
        perf = [n / dn if dn > 0.2 else 1.0 for n, dn in zip(num, den)]
        pm = max(perf) or 1
        perf = [x / pm for x in perf]
        w = min(0.4, len(posts) / 100)
        score = [(1 - w) * s + w * x for s, x in zip(score, perf)]
        basis.append(f"your {len(posts)} posts' likes & comments")
    return {"grid": grid, "score": score, "basis": basis}


def _fmt(h: float) -> str:
    hh, mm = int(h), int(round((h % 1) * 60))
    return f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"


def slots_for(d: date, dat: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    c = curve(d, dat)
    out = []
    for n, (lo, hi) in enumerate(WINDOWS, 1):
        cand = [(s, h) for h, s in zip(c["grid"], c["score"]) if lo <= h <= hi]
        s, h = max(cand)
        at = datetime(d.year, d.month, d.day, int(h), int(round((h % 1) * 60)), tzinfo=TZ)
        out.append({"id": f"post-{d.isoformat()}-{n}", "n": n, "at": int(at.timestamp()), "label": _fmt(h),
                    "day": d.isoformat(), "score": round(s, 3)})
    return out


# ── the 2-a-day rule ───────────────────────────────────────────────────────────────────────────
def _today() -> date:
    return datetime.now(TZ).date()


def posts_today(account_id: Optional[int]) -> int:
    """Affiliate carousels published today (local day) + ones being published right now."""
    if account_id is None:
        return 0
    from app.engagement import store
    from app.services import post_ledger
    start = datetime.combine(_today(), datetime.min.time(), tzinfo=TZ)
    n = store.affiliate_posts_since(int(account_id), start)       # one tiny COUNT
    return n + post_ledger.in_flight(int(account_id))


def plan(account_id: Optional[int] = None) -> Dict[str, Any]:
    """Today + the next 2 days' slots, today's count and the next slot — for the Studio and the phone.
    Cached 30 s: the phone asks every 20 s; a publish drops the cache."""
    from app import cache
    return cache.get(("post_plan", account_id), 30, lambda: _plan(account_id))


def _plan(account_id: Optional[int] = None) -> Dict[str, Any]:
    dat = data()
    if account_id is None:
        account_id = dat.get("account_id")
    today = _today()
    done = posts_today(account_id)
    now = time.time()
    slots = []
    for i in range(3):
        for s in slots_for(today + timedelta(days=i), dat):
            s = dict(s)
            s["state"] = ("done" if s["day"] == today.isoformat() and done >= s["n"] else
                          "missed" if s["at"] < now - 3600 else "upcoming")
            slots.append(s)
    nxt = next((s for s in slots if s["state"] == "upcoming" and not (s["day"] == today.isoformat() and done >= CAP)), None)
    return {"success": True, "tz": str(TZ), "cap": CAP, "posts_today": done, "left_today": max(0, CAP - done),
            "lead_min": LEAD_MIN, "slots": slots, "next": nxt, "basis": curve(today, dat)["basis"],
            "studio": os.getenv("SK_STUDIO_URL", "https://140-238-247-18.nip.io") + "/#sk-post"}
