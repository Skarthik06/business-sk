"""
chains/discovery.py — Product-discovery engine (taxonomy + scoring + collections).

Implements the strategy encoded in agents/product-scout, product-scorer,
collection-builder. Everything here is DETERMINISTIC and derived ONLY from real scraped
fields (rating, reviews, discount_pct, price, bought_past_month, badge, image). No product
fact is ever invented — a score is an internal ranking heuristic, not a claim shown to buyers.

Legend of scores (all 0–100):
  value_score, purchase_intent_score, instagram_score, content_potential_score,
  content_score (weighted master), + tier (S/A/B/C/D) + price_band.
"""
from __future__ import annotations

import math
import re
from typing import Optional

# ── Category taxonomy: base category → family + Instagram content angles ──────
# The 8 base categories map to families; angles power the Content-Potential score
# and give the content strategist ready homes for each product.
FAMILY = {
    "fashion": "fashion", "home": "room_desk", "kitchen": "everyday",
    "beauty": "beauty", "fitness": "fitness", "toys": "gifting",
    "books": "student", "electronics": "tech",
}

# Representative Amazon India search intents per base category (Product Scout S2).
SUBCATEGORIES = {
    "fashion":     ["oversized t-shirt", "graphic t-shirt", "shirt", "hoodie", "jacket",
                    "cargo pants", "baggy jeans", "co-ord set", "sneakers", "watch",
                    "sunglasses", "wallet", "backpack"],
    "electronics": ["wireless earbuds", "bluetooth speaker", "smartwatch", "power bank",
                    "fast charger", "phone stand", "laptop stand", "keyboard", "mouse",
                    "led strip lights", "neckband", "usb hub"],
    "home":        ["led lights", "desk lamp", "desk organizer", "cable organizer",
                    "wall shelf", "artificial plants", "posters", "desk mat", "wall clock"],
    "kitchen":     ["kitchen gadgets", "storage containers", "organizer", "cleaning gadget",
                    "water bottle", "lunch box"],
    "fitness":     ["resistance bands", "yoga mat", "gym bag", "shaker bottle",
                    "skipping rope", "dumbbells"],
    "beauty":      ["makeup organizer", "hair accessories", "grooming kit", "mirror",
                    "storage box"],
    "toys":        ["gift set", "board games", "hobby kit", "desk toy"],
    "books":       ["backpack", "study lamp", "stationery", "notebook", "laptop sleeve"],
}

# Content angles per base category (count drives Content-Potential; grounded, generic).
ANGLES = {
    "fashion":     ["Fashion finds under ₹2K", "Outfit combos under ₹3K", "Make a basic outfit look expensive",
                    "Best sneakers under ₹2.5K", "Watches that look expensive", "Minimalist fashion finds",
                    "Pieces that upgrade your wardrobe", "Amazon finds you'd actually wear"],
    "electronics": ["Tech finds under ₹1K", "Desk gadgets under ₹2K", "Useful tech you didn't know you needed",
                    "Best earbuds under ₹2K", "Gadgets that make your desk better", "Student gadgets",
                    "Clean desk setup under ₹5K", "Cheap gadgets that feel premium"],
    "home":        ["Make your desk look better under ₹2K", "Room setup finds under ₹1.5K",
                    "Products that improve your desk", "Minimalist room finds", "Clean desk finds",
                    "Room glow-up under ₹2K", "Things your desk is missing"],
    "kitchen":     ["Things under ₹500 that are useful", "Products that solve everyday problems",
                    "Amazon finds you'll use every day", "Things I wish I bought earlier",
                    "Small products that make life easier"],
    "fitness":     ["Beginner gym kit under ₹2K", "Fitness finds under ₹1K", "Useful gym accessories",
                    "Amazon finds for your workout", "Things to pack for your workout"],
    "beauty":      ["Grooming finds under ₹1K", "Products that upgrade your setup", "Useful beauty finds",
                    "Budget self-care essentials"],
    "toys":        ["Gifts under ₹1K", "Gift ideas under ₹2K", "Gifts that don't feel cheap",
                    "Useful gifts people actually want", "Budget birthday gifts"],
    "books":       ["College essentials under ₹2K", "Every student should have these",
                    "College setup under ₹3K", "Useful Amazon finds for students", "Budget desk setup for students"],
}

# Heuristic visual-appeal weight per base category (Instagram-Score proxy, 0–1). Honest:
# true visual appeal needs image analysis; this is a category prior, not a product claim.
VISUAL = {
    "fashion": 0.95, "beauty": 0.88, "home": 0.82, "electronics": 0.80,
    "toys": 0.75, "kitchen": 0.68, "fitness": 0.62, "books": 0.45,
}

# Documented Amazon India commission rates (also in server.CATEGORY_RATES).
COMMISSION = {"fashion": 9, "home": 8, "kitchen": 7, "beauty": 6,
              "fitness": 5, "toys": 5, "books": 4, "electronics": 4}

# Giftable / bundle-friendly families get a small content-potential boost.
GIFTABLE = {"fashion", "toys", "electronics", "beauty", "home"}

PRICE_BANDS = [
    (0, 499, "Under ₹500"), (500, 999, "Under ₹1K"), (1000, 1499, "Under ₹1.5K"),
    (1500, 2000, "Under ₹2K"), (2001, 3000, "Under ₹3K"), (3001, 5000, "Under ₹5K"),
    (5001, 10**9, "₹5K+"),
]

_DIGITS = re.compile(r"[\d,]+")


def _price_int(p: dict) -> Optional[int]:
    m = _DIGITS.search(str(p.get("price") or ""))
    return int(m.group().replace(",", "")) if m else None


def _reviews_int(p: dict) -> int:
    v = p.get("reviews")
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"([\d.]+)\s*([KkMm]?)", str(v or "").replace(",", ""))
    if not m:
        return 0
    n = float(m.group(1)); u = m.group(2).lower()
    return int(n * (1000 if u == "k" else 1_000_000 if u == "m" else 1))


def _bought_int(p: dict) -> int:
    m = re.search(r"([\d.]+)\s*([KkMm]?)", str(p.get("bought_past_month") or "").replace(",", ""))
    if not m:
        return 0
    n = float(m.group(1)); u = m.group(2).lower()
    return int(n * (1000 if u == "k" else 1_000_000 if u == "m" else 1))


def _clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


def price_band(price: Optional[int]) -> Optional[str]:
    if price is None:
        return None
    for lo, hi, label in PRICE_BANDS:
        if lo <= price <= hi:
            return label
    return None


def value_score(p: dict) -> int:
    """Quality-per-rupee from real data: rating, reviews, real discount, price accessibility."""
    price = _price_int(p) or 0
    rating = float(p.get("rating") or 0)
    reviews = _reviews_int(p)
    disc = int(p.get("discount_pct") or 0)
    s = (rating / 5) * 40                                    # up to 40
    s += min(math.log10(reviews + 1) / math.log10(20000), 1) * 20   # up to 20
    s += min(disc, 60) / 60 * 25                            # up to 25 (real discount only)
    s += (15 if price and price <= 500 else 11 if price <= 1000 else 7 if price <= 2000 else 3)
    return int(_clamp(s))


def purchase_intent_score(p: dict) -> int:
    rating = float(p.get("rating") or 0)
    reviews = _reviews_int(p)
    bought = _bought_int(p)
    price = _price_int(p) or 0
    badge = 1 if p.get("badge") else 0
    s = (rating / 5) * 30
    s += min(math.log10(reviews + 1) / math.log10(50000), 1) * 25
    s += min(math.log10(bought + 1) / math.log10(10000), 1) * 20
    s += (15 if price and price <= 999 else 10 if price <= 2000 else 5)
    s += badge * 10
    return int(_clamp(s))


def instagram_score(p: dict) -> int:
    """Heuristic scroll-stop proxy — category visual prior + deal appeal + image + price."""
    cat = (p.get("category") or "").lower()
    vis = VISUAL.get(cat, 0.6)
    disc = int(p.get("discount_pct") or 0)
    price = _price_int(p) or 0
    s = vis * 35
    s += 15 if p.get("image") or p.get("image_url") else 0
    s += min(disc, 50) / 50 * 20
    s += (15 if price and price <= 1500 else 9 if price <= 3000 else 4)
    s += 15 if p.get("badge") else 0
    return int(_clamp(s))


def content_potential_score(p: dict) -> int:
    cat = (p.get("category") or "").lower()
    angles = len(ANGLES.get(cat, []))
    s = min(angles / 8, 1) * 70
    s += 15 if price_band(_price_int(p)) and (_price_int(p) or 0) <= 5000 else 0
    s += 15 if cat in GIFTABLE else 0
    return int(_clamp(s))


def _quality_subscore(p: dict) -> float:
    rating = float(p.get("rating") or 0)
    reviews = _reviews_int(p)
    return _clamp((rating / 5) * 60 + min(math.log10(reviews + 1) / math.log10(50000), 1) * 40)


def _commercial_subscore(p: dict) -> float:
    return _clamp(COMMISSION.get((p.get("category") or "").lower(), 4) / 9 * 100)


# Master weights (Product-Scorer SC1) — configurable via env if desired.
WEIGHTS = {"instagram": 0.30, "purchase_intent": 0.20, "value": 0.15,
           "content_potential": 0.15, "quality": 0.10, "commercial": 0.10}


def content_score(p: dict, sub: dict) -> int:
    s = (WEIGHTS["instagram"] * sub["instagram_score"]
         + WEIGHTS["purchase_intent"] * sub["purchase_intent_score"]
         + WEIGHTS["value"] * sub["value_score"]
         + WEIGHTS["content_potential"] * sub["content_potential_score"]
         + WEIGHTS["quality"] * _quality_subscore(p)
         + WEIGHTS["commercial"] * _commercial_subscore(p))
    return int(_clamp(s))


def tier(score: int) -> str:
    return "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D"


def score_product(p: dict) -> dict:
    """Return the full score bundle for one product (all derived from real fields)."""
    sub = {
        "value_score": value_score(p),
        "purchase_intent_score": purchase_intent_score(p),
        "instagram_score": instagram_score(p),
        "content_potential_score": content_potential_score(p),
    }
    cs = content_score(p, sub)
    price = _price_int(p)
    return {
        **sub,
        "content_score": cs,
        "tier": tier(cs),
        "price_band": price_band(price),
        "family": FAMILY.get((p.get("category") or "").lower()),
        "retailer": "amazon_in",
        "product_type": "AFFILIATE_PRODUCT",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Novelty Analyst + Winner Engine (see agents/novelty-analyst,
# agents/winner-engine). All deterministic; novelty is supplied by the caller
# (rag.store.novelty_scores) so this module stays free of DB/embedding deps.
# ══════════════════════════════════════════════════════════════════════════════

def confidence_score(p: dict) -> float:
    """0.55–1.0 — how complete/reliable a candidate's evidence is. A strong product
    with a couple of missing fields is only gently penalised, never zeroed, so we
    never *select* on incomplete evidence but also don't discard good products."""
    from config import cfg
    floor = cfg.novelty.min_confidence
    have = 0.0
    if _price_int(p) is not None:                 have += 0.30    # real price
    if p.get("image") or p.get("image_url"):      have += 0.20    # valid image
    if p.get("rating"):                           have += 0.20    # rating present
    if _reviews_int(p) > 0:                        have += 0.15    # review volume
    if p.get("asin"):                             have += 0.15    # stable identity
    return round(floor + (1.0 - floor) * have, 3)  # map [0,1]→[floor,1]


def product_intelligence(p: dict, sub: dict, novelty: Optional[int]) -> int:
    """Master pick score = content_score blended with novelty (weight NOVELTY_WEIGHT).
    Novelty rewards products UNLIKE what we've already posted, so the feed stays fresh."""
    from config import cfg
    cs = content_score(p, sub)
    if not cfg.novelty.enabled or novelty is None:
        return cs
    w = max(0.0, min(cfg.novelty.weight, 1.0))
    return int(_clamp(cs * (1 - w) + float(novelty) * w))


def winner_score(intelligence: int, confidence: float, freshness: float = 1.0) -> int:
    """winner_score = product_intelligence × confidence × freshness (§20 of the
    blueprint). Freshness defaults to 1.0 until product first/last-seen data exists."""
    return int(_clamp(intelligence * confidence * freshness))


def evidence(p: dict) -> list[str]:
    """Grounded, human-readable reasons — ONLY things the real data supports (G13).
    Shown in the studio's 'Why this product?' panel. Never a fabricated claim."""
    out: list[str] = []
    if _price_int(p) is not None:            out.append(f"real price available (₹{_price_int(p):,})")
    if p.get("rating"):                      out.append(f"strong rating ({p.get('rating')}★)")
    rv = _reviews_int(p)
    if rv:                                   out.append(f"{rv:,} reviews (social proof)")
    if int(p.get("discount_pct") or 0) > 0:  out.append(f"real discount ({int(p['discount_pct'])}% off)")
    if (p.get("bought_past_month") or ""):   out.append(f"recent demand ({p['bought_past_month']} bought/mo)")
    if (p.get("badge") or ""):               out.append(f"badge: {p['badge']}")
    if p.get("image") or p.get("image_url"): out.append("valid product image")
    if (p.get("category") or ""):            out.append(f"relevant category ({p['category']})")
    return out


def winner_bundle(p: dict, novelty: Optional[int] = None) -> dict:
    """Full Phase-2 annotation for one product: novelty + confidence + intelligence +
    winner score + evidence. Additive — layers on top of score_product()."""
    sub = {
        "value_score": value_score(p),
        "purchase_intent_score": purchase_intent_score(p),
        "instagram_score": instagram_score(p),
        "content_potential_score": content_potential_score(p),
    }
    conf = confidence_score(p)
    intel = product_intelligence(p, sub, novelty)
    return {
        "novelty_score":     novelty,                       # None when disabled/cold-start-unknown
        "confidence":        conf,
        "intelligence_score": intel,
        "winner_score":      winner_score(intel, conf),
        "winner_tier":       tier(winner_score(intel, conf)),
        "evidence":          evidence(p),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Discovery query planning (see agents/discovery-planner).
# ══════════════════════════════════════════════════════════════════════════════

def category_queries(category: str) -> list[str]:
    """All candidate search intents for a base category (subcategories + the base term)."""
    cat = (category or "").lower()
    subs = list(SUBCATEGORIES.get(cat, []))
    base = cat.strip()
    return ([base] + subs) if base and base not in subs else (subs or [base])


# ── Collections & bundles (Collection-Builder) ───────────────────────────────
def build_price_bands(products: list[dict]) -> list[dict]:
    """Group scored products into truthful price-band collections (real prices only)."""
    out = []
    for lo, hi, label in PRICE_BANDS:
        items = [p for p in products if (pr := _price_int(p)) is not None and lo <= pr <= hi]
        if not items:
            continue
        items.sort(key=lambda x: x.get("content_score", 0), reverse=True)
        out.append({"band": label, "count": len(items), "products": items})
    return out


def build_bundles(products: list[dict], budget: int, size: int = 3) -> list[dict]:
    """Detect complementary sets whose COMBINED REAL PRICE fits `budget` (budget-claim
    truth, G15). Picks the highest-scored products from DISTINCT categories that sum ≤ budget."""
    pool = sorted([p for p in products if _price_int(p)], key=lambda x: x.get("content_score", 0), reverse=True)
    chosen, total, used_cats = [], 0, set()
    for p in pool:
        pr = _price_int(p)
        cat = (p.get("category") or "").lower()
        if cat in used_cats:                    # complementary = spread across categories
            continue
        if total + pr <= budget and len(chosen) < size:
            chosen.append(p); total += pr; used_cats.add(cat)
    if len(chosen) < 2:
        return []
    return [{"title": f"Setup under ₹{budget:,}", "combined_price": total,
             "count": len(chosen), "products": chosen}]
