"""
tools/shopsy_scrape.py — Shopsy (Flipkart's social-commerce app) product scraper.

Unlike flipkart.com (which hard-blocks datacenter IPs and needs a premium proxy), shopsy.in serves
its search results directly to a normal request — so we can scrape it FREE, no ScraperAPI. Shopsy
resells the Flipkart catalogue (products are `marketplace=FLIPKART`), and the product links are
Cuelinks-monetisable, so this is a zero-cost product + income source.

The search page is server-rendered React (emotion CSS classes, no INITIAL_STATE JSON). We split the
HTML by product anchors (`/…slug…/p/itm…`) and, per card, pull the rukminim CDN image, the ₹ price,
and a title from the slug. Output is the SAME shape as the other scrapers, so it flows through the
identical rank → dedup → compose → render → publish pipeline. Never raises.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

import requests

from utils.logger import log

_BASE = "https://www.shopsy.in"
_TIMEOUT = 30
_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}
_ANCHOR = re.compile(r'href="(/[^"]*?/p/itm[a-z0-9]+[^"]*)"')
_IMG = re.compile(r'https://rukminim\d?\.flixcart\.com/image/[^"\'\s]+')
_PRICE = re.compile(r'₹\s?([\d,]{2,})')


def _title_from_slug(href: str) -> str:
    slug = href.split("/p/")[0].strip("/").split("/")[-1]
    words = [w for w in slug.replace("-", " ").split() if w]
    t = " ".join(words).title()
    return t[:120]


def _img_hd(u: str) -> str:
    u = u.replace("&amp;", "&")
    u = re.sub(r"/image/\d{2,4}/\d{2,4}/", "/image/832/832/", u)
    return u


def scrape_products(query: str, count: int = 8, max_pages: int = 2) -> dict:
    """Scrape Shopsy search for `query`. FREE (no proxy). Returns {ok,count,items:[...]}. Never raises."""
    if not query or len(query.strip()) < 2:
        return {"ok": False, "error": "query too short", "items": []}
    out: list[dict] = []
    seen: set = set()
    try:
        for pg in range(1, max(1, min(max_pages, 4)) + 1):
            url = f"{_BASE}/search?q={quote_plus(query.strip())}" + (f"&page={pg}" if pg > 1 else "")
            r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
            if not r.ok:
                if pg == 1:
                    return {"ok": False, "error": f"shopsy {r.status_code}", "items": []}
                break
            html = r.text
            anchors = list(_ANCHOR.finditer(html))
            if not anchors:
                break
            for idx, m in enumerate(anchors):
                href = m.group(1)
                pid_m = re.search(r"/p/(itm[a-z0-9]+)", href)
                pid = pid_m.group(1) if pid_m else ""
                if not pid or pid in seen:
                    continue
                # card slice = from this anchor to the next one
                nxt = anchors[idx + 1].start() if idx + 1 < len(anchors) else min(m.start() + 4000, len(html))
                card = html[m.start():nxt]
                img_m = _IMG.search(card)
                price_m = _PRICE.search(card)
                if not img_m:
                    continue
                sell = int(price_m.group(1).replace(",", "")) if price_m else 0
                seen.add(pid)
                clean_href = href.replace("&amp;", "&")
                out.append({
                    "asin": f"sy_{pid}", "category": "", "title": _title_from_slug(href),
                    "price": (f"₹{sell:,}" if sell else ""), "orig_price": "", "discount_pct": None,
                    "rating": None, "reviews": 0, "bought_past_month": "", "badge": "",
                    "image": _img_hd(img_m.group(0)),
                    "url": _BASE + clean_href if clean_href.startswith("/") else clean_href,
                    "brand": "", "source": "shopsy",
                })
            if len(out) >= count * 4:
                break
        return {"ok": True, "count": len(out), "items": out[: max(count * 4, count)]}
    except Exception as e:
        log.warning(f"[shopsy] scrape_products failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": out[:count]}
