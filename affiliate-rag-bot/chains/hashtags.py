"""
chains/hashtags.py  —  Curated hashtag bank + merge (Phase 4, Content Intelligence).

The LLM writes 15-25 tags; this bank guarantees a consistent spine of broad + category +
audience + branded reach tags per category so hashtag strategy stays coherent run-to-run.
merge_hashtags() blends the LLM's tags with the bank, dedupes, lowercases, strips '#',
guarantees the '#ad' disclosure (G3), and caps the count.
"""
from __future__ import annotations

# Per-category tag spine (broad reach + category + audience). Kept lowercase, no '#'.
BANK: dict[str, list[str]] = {
    "fashion":     ["fashion", "ootd", "fashionfinds", "amazonfashion", "styleinspo",
                    "outfitideas", "budgetfashion", "amazonfinds", "fashionindia"],
    "electronics": ["gadgets", "techfinds", "amazonfinds", "gadgetsindia", "techdeals",
                    "coolgadgets", "smartgadgets", "techie", "amazonelectronics"],
    "home":        ["homedecor", "homefinds", "roomdecor", "desksetup", "amazonhome",
                    "homeimprovement", "aesthetichome", "amazonfinds", "interiordecor"],
    "kitchen":     ["kitchengadgets", "kitchenfinds", "amazonkitchen", "kitchenhacks",
                    "cookingtools", "homeandkitchen", "amazonfinds", "kitchenessentials"],
    "fitness":     ["fitness", "fitnessgear", "homeworkout", "gymessentials", "fitindia",
                    "workoutgear", "amazonfinds", "fitnessmotivation"],
    "beauty":      ["beauty", "skincare", "beautyfinds", "selfcare", "amazonbeauty",
                    "grooming", "beautytips", "amazonfinds"],
    "toys":        ["gifts", "giftideas", "giftguide", "amazonfinds", "giftsforhim",
                    "giftsforher", "toys", "giftinspo"],
    "books":       ["books", "studentessentials", "collegelife", "studygram", "amazonfinds",
                    "backtoschool", "studysetup", "stationery"],
}

GENERIC = ["amazonindia", "amazonfinds", "musthaves", "shopnow", "dealsoftheday",
           "founditonamazon", "trending"]

# Per-store reach tags. The bank above is written for Amazon; for another store every
# store-named tag is swapped for that store's own (a Flipkart post never says #amazonfinds).
STORE_GENERIC = {
    "amazon":   GENERIC,
    "flipkart": ["flipkart", "flipkartfinds", "flipkartsale", "musthaves", "shopnow",
                 "dealsoftheday", "trending"],
    "shopsy":   ["shopsy", "shopsyfinds", "flipkartfinds", "budgetfinds", "shopnow",
                 "dealsoftheday", "trending"],
}
_STORES = ("amazon", "flipkart", "shopsy", "myntra", "ajio", "meesho", "nykaa")


def _store_key(store: str) -> str:
    s = (store or "amazon").strip().lower()
    return s if s in STORE_GENERIC else "amazon"


def _for_store(tag: str, store: str) -> str:
    """Re-point a store-named tag at `store` (amazonfinds → flipkartfinds); drop it ("") when it
    names another store and has no sensible swap."""
    t = (tag or "").lower()
    other = next((x for x in _STORES if x in t and x != store), None)
    if not other:
        return t
    if other == "amazon" and store in STORE_GENERIC:
        swapped = t.replace("founditonamazon", f"foundon{store}").replace("amazon", store)
        return swapped
    return ""


def bank_for(category: str, n: int = 8, store: str = "amazon") -> list[str]:
    st = _store_key(store)
    cat = (category or "").lower().strip()
    tags = [x for x in (_for_store(t, st) for t in BANK.get(cat, [])) if x]
    for g in STORE_GENERIC[st]:
        if g not in tags:
            tags.append(g)
    return tags[:n]


def merge_hashtags(category: str, llm_tags: list[str], use_bank: bool = True,
                   cap: int = 25, store: str = "amazon") -> list[str]:
    """Blend LLM tags with the category bank; lowercase, strip '#', dedupe, cap, force 'ad'.
    LLM tags come first (they reference the actual products), then the bank fills reach.
    Every tag matches the post's REAL store (`store` = the products' source)."""
    st = _store_key(store)
    out: list[str] = []
    seen: set = set()

    def add(t: str):
        t = _for_store((t or "").lstrip("#").strip().lower().replace(" ", ""), st)
        if t and t not in seen:
            seen.add(t); out.append(t)

    for t in (llm_tags or []):
        add(t)
    if use_bank:
        for t in bank_for(category, store=st):
            if len(out) >= cap:
                break
            add(t)
    if not any(t == "ad" for t in out):          # FTC disclosure (G3) — always present
        out.insert(0, "ad")
    return out[:cap]
