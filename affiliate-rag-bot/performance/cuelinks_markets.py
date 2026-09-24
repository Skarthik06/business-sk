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
# ONLY the stores we can actually SCRAPE real products from (product photos + prices). Everything
# else was deals-only (no product photo) and has been removed — this panel is products-only now.
#   • flipkart → Flipkart product scrape (official JSON via premium proxy)
#   • boAt / Noise / Mamaearth → the merchant's PUBLIC Shopify feed (/products.json) — verified live
MARKETS: list[dict] = [
    # Free public Shopify product feeds (no proxy/ScraperAPI needed) — verified live.
    {"id": "boat",           "name": "boAt",            "category": "Electronics", "commission": 8.0,  "aov": "₹1k–3k",   "cookie": 30, "note": "Audio & wearables — free Shopify feed."},
    {"id": "noise",          "name": "Noise",           "category": "Electronics", "commission": 8.0,  "aov": "₹1.5k–4k", "cookie": 30, "note": "Smartwatches & buds — free Shopify feed."},
    {"id": "mamaearth",      "name": "Mamaearth",       "category": "Beauty",      "commission": 11.0, "aov": "₹600–1.5k","cookie": 30, "note": "D2C skincare — free Shopify feed."},
    {"id": "sugarcosmetics", "name": "SUGAR Cosmetics", "category": "Beauty",      "commission": 12.0, "aov": "₹600–1.5k","cookie": 30, "note": "Makeup — free Shopify feed."},
    {"id": "plum",           "name": "Plum",            "category": "Beauty",      "commission": 10.0, "aov": "₹500–1.4k","cookie": 30, "note": "Clean skincare — free Shopify feed."},
    {"id": "mcaffeine",      "name": "mCaffeine",       "category": "Beauty",      "commission": 10.0, "aov": "₹500–1.3k","cookie": 30, "note": "Caffeinated skincare — free Shopify feed."},
    {"id": "pilgrim",        "name": "Pilgrim",         "category": "Beauty",      "commission": 12.0, "aov": "₹500–1.5k","cookie": 30, "note": "Global beauty rituals — free Shopify feed."},
    {"id": "minimalist",     "name": "Minimalist",      "category": "Beauty",      "commission": 10.0, "aov": "₹500–1.5k","cookie": 30, "note": "Active-led skincare — free Shopify feed."},
    {"id": "juicychemistry", "name": "Juicy Chemistry", "category": "Beauty",      "commission": 10.0, "aov": "₹600–1.8k","cookie": 30, "note": "Organic skincare — free Shopify feed."},
    {"id": "sirona",         "name": "Sirona",          "category": "Beauty",      "commission": 10.0, "aov": "₹400–1.2k","cookie": 30, "note": "Hygiene & wellness — free Shopify feed."},
    {"id": "themancompany",  "name": "The Man Company", "category": "Grooming",    "commission": 12.0, "aov": "₹600–1.6k","cookie": 30, "note": "Men's grooming — free Shopify feed."},
    {"id": "beardo",         "name": "Beardo",          "category": "Grooming",    "commission": 12.0, "aov": "₹500–1.5k","cookie": 30, "note": "Men's grooming — free Shopify feed."},
    {"id": "bombayshaving",  "name": "Bombay Shaving Co","category": "Grooming",   "commission": 12.0, "aov": "₹500–1.5k","cookie": 30, "note": "Shaving & grooming — free Shopify feed."},
    {"id": "snitch",         "name": "Snitch",          "category": "Fashion",     "commission": 10.0, "aov": "₹900–2.5k","cookie": 30, "note": "Men's fast fashion — free Shopify feed."},
    {"id": "chumbak",        "name": "Chumbak",         "category": "Home",        "commission": 10.0, "aov": "₹700–2.5k","cookie": 30, "note": "Quirky decor & lifestyle — free Shopify feed."},
    {"id": "sleepycat",      "name": "SleepyCat",       "category": "Home",        "commission": 8.0,  "aov": "₹8k–25k",  "cookie": 30, "note": "Mattresses & sleep — free Shopify feed."},
    # Shopsy (Flipkart's app) — FREE direct scrape (no proxy); resells the Flipkart catalogue.
    {"id": "shopsy",         "name": "Shopsy",          "category": "Marketplace", "commission": 6.0,  "aov": "₹200–1.5k","cookie": 30, "note": "Flipkart's value marketplace — FREE direct scrape, Cuelinks-monetised."},
    # Amazon + Flipkart — fetched by your residential scrape worker (PC/phone), FREE, no proxy.
    {"id": "amazon",         "name": "Amazon",          "category": "Marketplace", "commission": 4.0,  "aov": "₹1k–5k",   "cookie": 1,  "note": "Widest catalogue — via your residential scrape worker (Amazon Associates tag)."},
    {"id": "flipkart",       "name": "Flipkart",        "category": "Marketplace", "commission": 6.0,  "aov": "₹1k–3k",   "cookie": 30, "note": "Widest catalogue — via your residential scrape worker (or ScraperAPI)."},
]
_CATEGORIES = sorted({m["category"] for m in MARKETS})
_IDS = {m["id"] for m in MARKETS}

# ── which SCRAPE ENGINE backs each store ─────────────────────────────────────────────────────
#   • flipkart → Flipkart product scrape (official JSON via premium proxy)
#   • shopify  → the merchant's PUBLIC Shopify feed (/products.json) — boAt, Noise, Mamaearth
_ENGINES: dict[str, tuple[str, str]] = {
    "amazon":         ("amazon",   "amazon.in"),
    "flipkart":       ("flipkart", "flipkart.com"),
    "shopsy":         ("shopsy",   "shopsy.in"),
    "boat":           ("shopify",  "boat-lifestyle.com"),
    "noise":          ("shopify",  "gonoise.com"),
    "mamaearth":      ("shopify",  "mamaearth.in"),
    "sugarcosmetics": ("shopify",  "sugarcosmetics.com"),
    "plum":           ("shopify",  "plumgoodness.com"),
    "mcaffeine":      ("shopify",  "mcaffeine.com"),
    "pilgrim":        ("shopify",  "discoverpilgrim.com"),
    "minimalist":     ("shopify",  "beminimalist.co"),
    "juicychemistry": ("shopify",  "juicychemistry.com"),
    "sirona":         ("shopify",  "thesirona.com"),
    "themancompany":  ("shopify",  "themancompany.com"),
    "beardo":         ("shopify",  "beardo.in"),
    "bombayshaving":  ("shopify",  "bombayshavingcompany.com"),
    "snitch":         ("shopify",  "snitch.co.in"),
    "chumbak":        ("shopify",  "chumbak.com"),
    "sleepycat":      ("shopify",  "sleepycat.in"),
}


def _market_engine(mid: str) -> str:
    return _ENGINES.get(mid, ("", ""))[0]


def _market_domain(mid: str) -> str:
    return _ENGINES.get(mid, ("", ""))[1]


# bake engine/domain/capability onto the catalog so the frontend can badge each store.
for _m in MARKETS:
    _m["engine"] = _market_engine(_m["id"])
    _m["domain"] = _market_domain(_m["id"])
    _m["can_products"] = _m["engine"] in ("flipkart", "shopify", "shopsy", "amazon")


def market_by_id(mid: str) -> dict | None:
    """The catalog entry for a market id, enriched with its engine + domain. None if unknown."""
    for m in list(get_live_markets() or []) + MARKETS:
        if m.get("id") == mid:
            return {**m, "engine": m.get("engine") or _market_engine(mid),
                    "domain": m.get("domain") or _market_domain(mid),
                    "can_products": (m.get("engine") or _market_engine(mid)) in ("flipkart", "shopify", "shopsy", "amazon")}
    return None

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


def set_live_markets(markets: list[dict]) -> list[dict]:
    """Store the LIVE Cuelinks campaign catalogue (from a /campaigns refresh) — top markets with
    real payout %/EPC. When present it replaces the curated list in the panel."""
    clean = [m for m in (markets or []) if m.get("id") and m.get("name")][:60]
    _write("live_markets", clean)
    return clean


def get_live_markets() -> list[dict]:
    return _read("live_markets", []) or []


def catalog() -> dict:
    """The full panel payload: markets (with active flag), categories, and current constraints —
    one clean JSON shape the frontend renders and the AI planner reasons over. Uses the LIVE
    Cuelinks catalogue when it has been synced, else the curated fallback list."""
    active = set(get_active())
    # Products-only: ALWAYS the curated scrapable stores (ignore any old live-sync catalogue, which
    # pulled the full deals-store list we no longer want).
    base = MARKETS
    markets = [{**m, "active": m.get("id") in active,
                "engine": m.get("engine") or _market_engine(m.get("id", "")),
                "domain": m.get("domain") or _market_domain(m.get("id", "")),
                "can_products": (m.get("engine") or _market_engine(m.get("id", ""))) in ("flipkart", "shopify", "shopsy", "amazon")}
               for m in base]
    cats = sorted({m.get("category", "") for m in base if m.get("category")}) or _CATEGORIES
    return {
        "markets": markets,
        "categories": cats,
        "constraints": get_constraints(),
        "active_count": len(active),
        "total": len(base),
        "live": bool(get_live_markets()),
    }
