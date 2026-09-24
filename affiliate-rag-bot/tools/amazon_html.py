"""
tools/amazon_html.py — parse an Amazon SEARCH page's HTML into normalised products.

Used with the residential scrape worker: the server hands out the Amazon search URL, a worker on a
residential IP fetches the HTML, and this parses it (no Playwright, no login, no proxy). Output is
the SAME shape as the other scrapers, so products flow through the identical rank → dedup → compose
→ render → publish pipeline. Every product URL carries the associate tag. Never raises.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

_TITLE = re.compile(r'<h2[^>]*>.*?<span[^>]*>([^<]{4,})</span>', re.S)
_TITLE2 = re.compile(r'<a[^>]*class="[^"]*a-link-normal[^"]*"[^>]*>\s*<span[^>]*>([^<]{4,})</span>', re.S)
_IMG = re.compile(r'class="s-image"[^>]*src="([^"]+)"')
_PRICE_WHOLE = re.compile(r'class="a-price-whole">([\d,]+)')
_OFFSCREEN = re.compile(r'class="a-offscreen">\s*₹?\s*([\d,]+)')
_RATING = re.compile(r'([\d.]+)\s+out of\s+5\s+stars')
_REVIEWS = re.compile(r'aria-label="([\d,]+)"[^>]*>\s*<span[^>]*class="[^"]*s-underline-text')


def search_url(query: str, marketplace: str = "amazon.in") -> str:
    return f"https://www.{marketplace}/s?k={quote_plus(query)}&ref=nb_sb_noss"


def _int(s: str) -> int:
    try:
        return int(str(s).replace(",", ""))
    except Exception:
        return 0


def parse_search(html: str, count: int = 8, *, tag: str = "yourtag-21", marketplace: str = "amazon.in") -> dict:
    """Parse Amazon search HTML → {ok, count, items:[...]}. Never raises."""
    if not html or "data-asin" not in html:
        return {"ok": False, "error": "no Amazon products in HTML (blocked or empty page)", "items": []}
    # split into per-result blocks (each real result has a 10-char data-asin)
    marks = [(m.group(1), m.start()) for m in re.finditer(r'data-asin="([A-Z0-9]{10})"', html)]
    items: list[dict] = []
    seen: set = set()
    try:
        for i, (asin, pos) in enumerate(marks):
            if asin in seen:
                continue
            end = marks[i + 1][1] if i + 1 < len(marks) else min(pos + 6000, len(html))
            block = html[pos:end]
            img_m = _IMG.search(block)
            if not img_m:                                   # ads / non-product rows have no s-image
                continue
            tm = _TITLE.search(block) or _TITLE2.search(block)
            title = re.sub(r"\s+", " ", tm.group(1)).strip() if tm else ""
            if len(title) < 4:
                continue
            pw = _PRICE_WHOLE.search(block)
            sell = _int(pw.group(1)) if pw else 0
            if sell <= 0:
                continue
            offs = _OFFSCREEN.findall(block)
            # a-offscreen lists selling then (often) the struck MRP; take the largest > sell as MRP
            mrp = 0
            for o in offs:
                v = _int(o)
                if v > sell:
                    mrp = max(mrp, v)
            disc = int(round((mrp - sell) / mrp * 100)) if mrp and mrp > sell else None
            rt = _RATING.search(block)
            rating = float(rt.group(1)) if rt else None
            rv = _REVIEWS.search(block)
            reviews = _int(rv.group(1)) if rv else None
            img = img_m.group(1).replace("&amp;", "&")
            seen.add(asin)
            items.append({
                "asin": asin, "category": "", "title": title,
                "price": f"₹{sell:,}", "orig_price": (f"₹{mrp:,}" if mrp and mrp > sell else ""),
                "discount_pct": disc, "rating": rating, "reviews": reviews,
                "bought_past_month": "", "badge": "",
                "image": img,
                "url": f"https://www.{marketplace}/dp/{asin}?tag={tag}",
                "brand": "", "source": "amazon",
            })
            if len(items) >= count * 3:
                break
        return {"ok": True, "count": len(items), "items": items[: max(count * 3, count)]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160], "items": items[:count]}
