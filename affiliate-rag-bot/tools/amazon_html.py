"""
tools/amazon_html.py — parse an Amazon SEARCH page's HTML into normalised products.

Used with the residential scrape worker: the server hands out the Amazon search URL, a worker on a
residential IP fetches the HTML, and this parses it (no Playwright, no login, no proxy). Output is
the SAME shape as the other scrapers, so products flow through the identical rank → dedup → compose
→ render → publish pipeline. Every product URL carries the associate tag. Never raises.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import quote_plus

# Amazon renders TWO <h2> blocks per card: a short BRAND line ("Generic", "BLUE TYGA") and the
# real product title. Collect every candidate and keep the LONGEST — the brand line is always the
# short one, so this never returns a brand as the title.
_TITLE_ANY = re.compile(r'<h2[^>]*>.*?<span[^>]*>([^<]{3,})</span>', re.S)
_TITLE_LINK = re.compile(r'<a[^>]*class="[^"]*a-link-normal[^"]*"[^>]*>\s*<span[^>]*>([^<]{8,})</span>', re.S)
_BRAND = re.compile(r'<h2[^>]*a-size-mini[^>]*>.*?<span[^>]*>([^<]{2,40})</span>', re.S)
_IMG = re.compile(r'class="s-image"[^>]*src="([^"]+)"')
_PRICE_WHOLE = re.compile(r'class="a-price-whole">([\d,]+)')
_OFFSCREEN = re.compile(r'class="a-offscreen">\s*₹?\s*([\d,]+)')
_RATING = re.compile(r'([\d.]+)\s+out of\s+5\s+stars')
# "54 ratings" in an aria-label, or the "(54)" next to the stars.
_REVIEWS = re.compile(r'aria-label="([\d,]+)\s+ratings?"')
_REVIEWS2 = re.compile(r's-underline-text"\s*>\(([\d,]+)\)')


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
            cands = _TITLE_ANY.findall(block) + _TITLE_LINK.findall(block)
            cands = [_html.unescape(re.sub(r"\s+", " ", c).strip()) for c in cands]
            title = max(cands, key=len) if cands else ""      # longest = the real product title
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
            rv = _REVIEWS.search(block) or _REVIEWS2.search(block)
            reviews = _int(rv.group(1)) if rv else None
            bm = _BRAND.search(block)
            brand = _html.unescape(bm.group(1).strip()) if bm else ""
            if brand and brand.lower() in ("generic", "unbranded"):
                brand = ""                                    # not a real brand — don't show it
            img = img_m.group(1).replace("&amp;", "&")
            seen.add(asin)
            items.append({
                "asin": asin, "category": "", "title": title,
                "price": f"₹{sell:,}", "orig_price": (f"₹{mrp:,}" if mrp and mrp > sell else ""),
                "discount_pct": disc, "rating": rating, "reviews": reviews,
                "bought_past_month": "", "badge": "",
                "image": img,
                "url": f"https://www.{marketplace}/dp/{asin}?tag={tag}",
                "brand": brand, "source": "amazon",
            })
            if len(items) >= count * 3:
                break
        return {"ok": True, "count": len(items), "items": items[: max(count * 3, count)]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160], "items": items[:count]}
