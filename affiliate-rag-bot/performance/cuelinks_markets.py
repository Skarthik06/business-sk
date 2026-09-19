"""
performance/cuelinks_markets.py — the Cuelinks AFFILIATE MARKET catalog + control config.

Cuelinks monetises 1000s of non-Amazon Indian stores through ONE redirect, so the moment the
Cuelinks token is connected every market here is monetisable. This module is the panel's data
layer for that:

  • MARKETS   — a curated catalog of the highest-value Cuelinks markets (merchant, category,
                typical commission %, average-order-value band, cookie window). Optionally
                refreshed from the live Cuelinks campaigns API when a token is present.
  • CONFIG    — the CONSTRAINTS the AI planner obeys (focus categories, commission floor, min
                AOV, max active markets, goal, audience, content style) + the ACTIVE market
                picks. Persisted in a tiny key/value table so the panel is durable.

Everything in / out is plain JSON — no noise — so the frontend and the AI agent share one shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from utils.logger import log


# ── the catalog: the markets a shopper-facing affiliate actually wants, via Cuelinks ──────────
# commission = typical payout % (indicative); aov = average-order-value band (₹); cookie = days.
MARKETS: list[dict] = [
    {"id": "flipkart",   "name": "Flipkart",         "category": "Marketplace", "commission": 6.0,  "aov": "₹1k–3k",  "cookie": 30, "note": "Widest catalogue — electronics, home, fashion."},
    {"id": "myntra",     "name": "Myntra",           "category": "Fashion",     "commission": 7.0,  "aov": "₹1.2k–3k","cookie": 30, "note": "Fashion-first, high AOV, strong for this niche."},
    {"id": "ajio",       "name": "AJIO",             "category": "Fashion",     "commission": 8.0,  "aov": "₹1k–2.5k","cookie": 30, "note": "Trend-led fashion, frequent deep deals."},
    {"id": "nykaa",      "name": "Nykaa",            "category": "Beauty",      "commission": 7.0,  "aov": "₹800–2k", "cookie": 30, "note": "Beauty & cosmetics leader; loyal repeat buyers."},
    {"id": "tatacliq",   "name": "Tata CLiQ",        "category": "Marketplace", "commission": 5.0,  "aov": "₹1.5k–4k","cookie": 30, "note": "Premium electronics + luxury fashion."},
    {"id": "meesho",     "name": "Meesho",           "category": "Marketplace", "commission": 5.0,  "aov": "₹300–900", "cookie": 30, "note": "Value shoppers, huge volume, low AOV."},
    {"id": "lenskart",   "name": "Lenskart",         "category": "Eyewear",     "commission": 10.0, "aov": "₹1.2k–3k","cookie": 30, "note": "Eyewear — very high commission %."},
    {"id": "mamaearth",  "name": "Mamaearth",        "category": "Beauty",      "commission": 11.0, "aov": "₹600–1.5k","cookie": 30,"note": "D2C skincare — top payout, content-friendly."},
    {"id": "boat",       "name": "boAt",             "category": "Electronics", "commission": 8.0,  "aov": "₹1k–3k",  "cookie": 30, "note": "Audio & wearables — young audience."},
    {"id": "noise",      "name": "Noise",            "category": "Electronics", "commission": 8.0,  "aov": "₹1.5k–4k","cookie": 30, "note": "Smartwatches & buds — strong Reels fit."},
    {"id": "croma",      "name": "Croma",            "category": "Electronics", "commission": 2.5,  "aov": "₹3k–30k", "cookie": 30, "note": "Big-ticket electronics; low % but high AOV."},
    {"id": "reliancedigital","name":"Reliance Digital","category":"Electronics","commission": 2.5,  "aov": "₹3k–40k", "cookie": 30, "note": "Appliances + electronics, large baskets."},
    {"id": "firstcry",   "name": "FirstCry",         "category": "Baby",        "commission": 8.0,  "aov": "₹800–2k", "cookie": 30, "note": "Baby & kids — high repeat rate."},
    {"id": "pharmeasy",  "name": "PharmEasy",        "category": "Pharmacy",    "commission": 6.0,  "aov": "₹700–1.8k","cookie": 30,"note": "Health & wellness essentials."},
    {"id": "pepperfry",  "name": "Pepperfry",        "category": "Home",        "commission": 8.0,  "aov": "₹3k–15k", "cookie": 30, "note": "Furniture & decor — high AOV, high payout."},
    {"id": "decathlon",  "name": "Decathlon",        "category": "Sports",      "commission": 6.0,  "aov": "₹900–3k", "cookie": 30, "note": "Sports & fitness gear — broad appeal."},
    {"id": "bigbasket",  "name": "BigBasket",        "category": "Grocery",     "commission": 4.0,  "aov": "₹800–2k", "cookie": 30, "note": "Grocery — frequent, habitual purchases."},
    {"id": "wow",        "name": "WOW Skin Science", "category": "Beauty",      "commission": 10.0, "aov": "₹600–1.4k","cookie": 30,"note": "D2C skincare — strong influencer pull."},
    {"id": "snapdeal",   "name": "Snapdeal",         "category": "Marketplace", "commission": 6.0,  "aov": "₹400–1k", "cookie": 30, "note": "Value marketplace, budget audience."},
    {"id": "makemytrip", "name": "MakeMyTrip",       "category": "Travel",      "commission": 4.0,  "aov": "₹5k–25k", "cookie": 30, "note": "Travel — per-booking payout, huge AOV."},
    {"id": "adidas",     "name": "adidas",           "category": "Fashion",     "commission": 9.0,  "aov": "₹2k–6k",  "cookie": 30, "note": "Brand store — premium sportswear."},
    {"id": "puma",       "name": "PUMA",             "category": "Fashion",     "commission": 9.0,  "aov": "₹1.5k–5k","cookie": 30, "note": "Brand store — sneakers & apparel."},
    {"id": "urbanic",    "name": "Urbanic",          "category": "Fashion",     "commission": 10.0, "aov": "₹900–2.2k","cookie": 30,"note": "Gen-Z fast fashion — Reels native."},
    {"id": "swiggy",     "name": "Swiggy Instamart", "category": "Grocery",     "commission": 4.0,  "aov": "₹400–1.2k","cookie": 30,"note": "Quick-commerce essentials."},
]
_CATEGORIES = sorted({m["category"] for m in MARKETS})
_IDS = {m["id"] for m in MARKETS}

# The default constraints the AI planner starts from (all overridable from the panel).
DEFAULT_CONSTRAINTS: dict = {
    "focus_categories": [],          # [] = all; else e.g. ["Fashion","Beauty"]
    "commission_floor": 5.0,         # ignore markets paying below this %
    "min_aov": 0,                    # ₹ average-order-value floor (0 = any)
    "max_active": 8,                 # cap on how many markets the plan activates
    "goal": "balanced",             # balanced | commission | volume
    "audience": "",                 # '' everyone | men | women | kids
    "content_style": "auto",
}


class Base(DeclarativeBase):
    pass


class CuelinksConfig(Base):
    __tablename__ = "cuelinks_config"
    key        = Column(String(40), primary_key=True)
    value      = Column(Text, nullable=False)          # JSON blob
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def _init():
    ensure(Base, "cuelinks_config")


def _read(key: str, default):
    try:
        _init()
        with session() as s:
            row = s.get(CuelinksConfig, key)
            return json.loads(row.value) if row else default
    except Exception as e:
        log.warning(f"[cuelinks_markets] read {key} failed: {e}")
        return default


def _write(key: str, value) -> None:
    _init()
    with session() as s:
        row = s.get(CuelinksConfig, key)
        blob = json.dumps(value)
        if row:
            row.value = blob
            row.updated_at = datetime.now(timezone.utc)
        else:
            s.add(CuelinksConfig(key=key, value=blob))
        s.commit()


def get_constraints() -> dict:
    """The current planner constraints (defaults merged with saved overrides)."""
    saved = _read("constraints", {}) or {}
    return {**DEFAULT_CONSTRAINTS, **{k: v for k, v in saved.items() if k in DEFAULT_CONSTRAINTS}}


def set_constraints(patch: dict) -> dict:
    """Persist a partial constraints update (only known keys, validated)."""
    cur = get_constraints()
    for k, v in (patch or {}).items():
        if k not in DEFAULT_CONSTRAINTS:
            continue
        if k == "focus_categories":
            cur[k] = [c for c in (v or []) if c in _CATEGORIES][:len(_CATEGORIES)]
        elif k in ("commission_floor", "min_aov"):
            try:
                cur[k] = max(0.0, float(v))
            except Exception:
                pass
        elif k == "max_active":
            try:
                cur[k] = max(1, min(int(v), len(MARKETS)))
            except Exception:
                pass
        else:
            cur[k] = str(v)[:40]
    _write("constraints", cur)
    return cur


def get_active() -> list[str]:
    return [m for m in (_read("active", []) or []) if m in _IDS]


def set_active(ids: list[str]) -> list[str]:
    clean = [m for m in (ids or []) if m in _IDS]
    _write("active", clean)
    return clean


def toggle_active(market_id: str) -> list[str]:
    if market_id not in _IDS:
        return get_active()
    cur = get_active()
    cur = [m for m in cur if m != market_id] if market_id in cur else (cur + [market_id])
    _write("active", cur)
    return cur


def catalog() -> dict:
    """The full panel payload: markets (with active flag), categories, and current constraints —
    one clean JSON shape the frontend renders and the AI planner reasons over."""
    active = set(get_active())
    markets = [{**m, "active": m["id"] in active} for m in MARKETS]
    return {
        "markets": markets,
        "categories": _CATEGORIES,
        "constraints": get_constraints(),
        "active_count": len(active),
        "total": len(MARKETS),
    }
