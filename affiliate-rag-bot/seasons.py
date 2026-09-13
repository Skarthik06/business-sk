"""
seasons.py — Festival & seasonal-deal calendar (India).

A curated, offline calendar of Indian festivals + seasonal windows, each mapped to the
product categories, search keywords, and caption angle that sell best around it. No external
API needed — the dates are curated (best-effort) and seasons are month-based. The engine uses
this to (a) suggest what to push right now, and (b) let captions lean into the occasion
("Diwali gifts", "winter essentials"). Dates are approximate; update FESTIVALS yearly.

Consumed by: server.py (/api/seasons), graph/nodes.compose_pins (caption angle),
agents/seasonal-planner.agents.md.
"""
from __future__ import annotations

from datetime import date, datetime

# Festivals & big Indian sale events (approximate dates — refresh each year).
FESTIVALS = [
    {"name": "Ganesh Chaturthi", "date": "2026-09-14", "emoji": "🐘",
     "categories": ["home", "beauty", "toys"], "keywords": ["ganesh decor", "puja items", "festive gift"],
     "angle": "Ganesh Chaturthi decor & gifting"},
    {"name": "Navratri", "date": "2026-10-11", "emoji": "🪔",
     "categories": ["fashion", "beauty", "home"], "keywords": ["navratri outfit", "ethnic wear", "garba dress", "chaniya choli", "festive jewellery"],
     "angle": "Navratri festive looks & garba-ready outfits"},
    {"name": "Dussehra", "date": "2026-10-20", "emoji": "🏹",
     "categories": ["fashion", "home", "electronics"], "keywords": ["festive outfit", "home decor", "dussehra deal"],
     "angle": "Dussehra festive finds"},
    {"name": "Amazon Great Indian Festival", "date": "2026-10-25", "emoji": "🛍️",
     "categories": ["electronics", "home", "fashion", "kitchen"], "keywords": ["great indian festival", "big billion deal", "festive sale"],
     "angle": "Amazon festive-sale steals"},
    {"name": "Dhanteras", "date": "2026-11-06", "emoji": "🪙",
     "categories": ["kitchen", "home", "electronics"], "keywords": ["dhanteras utensils", "steel", "appliances deal"],
     "angle": "Dhanteras buys (utensils & appliances)"},
    {"name": "Diwali", "date": "2026-11-08", "emoji": "🪔",
     "categories": ["home", "electronics", "fashion", "kitchen", "toys"], "keywords": ["diwali gift", "diwali decor", "led lights", "diya", "festive hamper"],
     "angle": "Diwali gifts & home glow-up"},
    {"name": "Christmas", "date": "2026-12-25", "emoji": "🎄",
     "categories": ["toys", "home", "fashion", "electronics"], "keywords": ["christmas gift", "xmas decor", "secret santa", "gift under"],
     "angle": "Christmas gifts & decor"},
    {"name": "New Year", "date": "2027-01-01", "emoji": "🎉",
     "categories": ["fashion", "electronics", "beauty"], "keywords": ["new year outfit", "party wear", "new year gift"],
     "angle": "New-Year party looks & gadgets"},
    {"name": "Republic Day Sale", "date": "2027-01-26", "emoji": "🇮🇳",
     "categories": ["electronics", "home", "fashion"], "keywords": ["republic day sale", "big savings"],
     "angle": "Republic-Day-sale picks"},
    {"name": "Valentine's Day", "date": "2027-02-14", "emoji": "❤️",
     "categories": ["beauty", "fashion", "toys"], "keywords": ["valentine gift", "gift for her", "gift for him", "couple gift"],
     "angle": "Valentine's gifts they'll love"},
    {"name": "Holi", "date": "2027-03-14", "emoji": "🎨",
     "categories": ["fashion", "home", "beauty"], "keywords": ["holi outfit", "white kurta", "holi colors", "waterproof"],
     "angle": "Holi-ready outfits & essentials"},
    {"name": "Raksha Bandhan", "date": "2027-08-28", "emoji": "🧵",
     "categories": ["beauty", "fashion", "electronics", "toys"], "keywords": ["rakhi gift", "gift for brother", "gift for sister"],
     "angle": "Rakhi gifts for siblings"},
    {"name": "Independence Day Sale", "date": "2027-08-15", "emoji": "🇮🇳",
     "categories": ["electronics", "home", "fashion"], "keywords": ["independence day sale", "freedom sale"],
     "angle": "Freedom-sale deals"},
]

# Month-based seasons (stable year to year).
SEASONS = [
    {"name": "Winter", "months": [11, 12, 1, 2], "emoji": "❄️",
     "categories": ["fashion", "home"], "keywords": ["winter wear", "hoodie", "jacket", "sweatshirt", "room heater", "warm"],
     "angle": "Winter essentials to stay cosy"},
    {"name": "Summer", "months": [3, 4, 5, 6], "emoji": "☀️",
     "categories": ["fashion", "kitchen", "home", "beauty"], "keywords": ["summer cotton", "sunglasses", "water bottle", "cooler", "sunscreen"],
     "angle": "Summer must-haves to beat the heat"},
    {"name": "Monsoon", "months": [6, 7, 8, 9], "emoji": "🌧️",
     "categories": ["fashion", "home"], "keywords": ["raincoat", "umbrella", "waterproof", "quick dry", "monsoon"],
     "angle": "Monsoon-ready picks"},
]


def _days_until(iso: str, today: date) -> int:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return 9999
    return (d - today).days


def current_season(today: date | None = None) -> dict | None:
    today = today or date.today()
    for s in SEASONS:
        if today.month in s["months"]:
            return {k: s[k] for k in ("name", "emoji", "categories", "keywords", "angle")}
    return None


def upcoming_festivals(within_days: int = 45, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for f in FESTIVALS:
        d = _days_until(f["date"], today)
        if -1 <= d <= within_days:                       # include today + the window ahead
            out.append({**f, "days_until": d})
    out.sort(key=lambda x: x["days_until"])
    return out


def context(today: date | None = None) -> dict:
    """The active seasonal context: current season + nearest festival + merged suggestions."""
    today = today or date.today()
    season = current_season(today)
    fests = upcoming_festivals(45, today)
    nearest = fests[0] if fests else None

    cats: list[str] = []
    kws: list[str] = []
    parts: list[str] = []
    if nearest:
        cats += nearest["categories"]; kws += nearest["keywords"]
        when = "today" if nearest["days_until"] <= 0 else f"in {nearest['days_until']} days"
        parts.append(f"{nearest['emoji']} {nearest['name']} {when}")
    if season:
        cats += season["categories"]; kws += season["keywords"]
        parts.append(f"{season['emoji']} {season['name']} season")

    seen = set(); ucats = [c for c in cats if not (c in seen or seen.add(c))]
    seen = set(); ukws = [k for k in kws if not (k in seen or seen.add(k))]
    angle = (nearest["angle"] if nearest else (season["angle"] if season else ""))

    return {
        "season": season,
        "nearest_festival": nearest,
        "upcoming": fests,
        "suggested_categories": ucats[:8],
        "suggested_keywords": ukws[:10],
        "angle": angle,
        "headline": " · ".join(parts) if parts else "No festival nearby",
    }
