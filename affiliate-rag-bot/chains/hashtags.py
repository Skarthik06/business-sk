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


def bank_for(category: str, n: int = 8) -> list[str]:
    cat = (category or "").lower().strip()
    tags = BANK.get(cat, [])[:]
    for g in GENERIC:
        if g not in tags:
            tags.append(g)
    return tags[:n]


def merge_hashtags(category: str, llm_tags: list[str], use_bank: bool = True,
                   cap: int = 25) -> list[str]:
    """Blend LLM tags with the category bank; lowercase, strip '#', dedupe, cap, force 'ad'.
    LLM tags come first (they reference the actual products), then the bank fills reach."""
    out: list[str] = []
    seen: set = set()

    def add(t: str):
        t = (t or "").lstrip("#").strip().lower().replace(" ", "")
        if t and t not in seen:
            seen.add(t); out.append(t)

    for t in (llm_tags or []):
        add(t)
    if use_bank:
        for t in bank_for(category):
            if len(out) >= cap:
                break
            add(t)
    if not any(t == "ad" for t in out):          # FTC disclosure (G3) — always present
        out.insert(0, "ad")
    return out[:cap]
