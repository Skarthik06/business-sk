"""
tools/flipkart_scrape.py — Flipkart product scraper (public search, via ScraperAPI premium).

Flipkart's own affiliate API is closed, and its site is a "protected domain" that the standard
proxy tier can't reach — but ScraperAPI's API mode with premium=true DOES return the search HTML.
Flipkart embeds every product in a `window.__INITIAL_STATE__` JSON blob, so we parse that (robust)
rather than fragile CSS selectors.

Products are normalised to the SAME shape as the Amazon scrape (asin/title/price/image/url/rating/
discount…), so they flow through the exact same dedup → compose → render → publish pipeline. The
product `url` is later converted to a tracked Cuelinks link (Flipkart is a Cuelinks market).

Cost note: premium requests cost ~10 ScraperAPI credits each (vs 1 for Amazon), so scrape only when
the user asks — never on a schedule.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote_plus

import requests

from utils.logger import log

_API = "http://api.scraperapi.com/"
_TIMEOUT = 70
_ATTEMPTS = 3


def _key() -> str:
    return (os.getenv("SCRAPER_PROXY_PASS") or os.getenv("SCRAPERAPI_KEY") or "").strip()


def configured() -> bool:
    return bool(_key())


def _fetch(query: str, page: int = 1) -> str:
    """Fetch a Flipkart search page via ScraperAPI premium. Premium is slow/flaky, so retry a few
    times (a fresh attempt usually lands on a faster backend); succeed as soon as the page carries
    the product JSON."""
    import time as _t
    url = f"https://www.flipkart.com/search?q={quote_plus(query)}"
    if page > 1:
        url += f"&page={page}"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            r = requests.get(_API, timeout=_TIMEOUT, params={
                "api_key": _key(), "url": url, "country_code": "in", "premium": "true"})
            if r.ok and "__INITIAL_STATE__" in r.text:
                return r.text
            log.warning(f"[flipkart] attempt {attempt}/{_ATTEMPTS}: status {r.status_code}, no product JSON")
        except Exception as e:
            log.warning(f"[flipkart] attempt {attempt}/{_ATTEMPTS} failed: {str(e)[:70]}")
        if attempt < _ATTEMPTS:
            _t.sleep(2)
    return ""


def _img(u: str) -> str:
    """Highest-resolution Flipkart CDN image (fills the {@width}/{@height} placeholders at 1080,
    and upgrades any baked-in small size to 1080) — crisp on a 1080px Instagram slide."""
    if not u:
        return ""
    u = u.replace("{@width}", "1080").replace("{@height}", "1080").replace("http://", "https://")
    # some URLs carry a fixed small size in the path (…/image/612/612/…) — upscale it
    u = re.sub(r"/image/\d{2,4}/\d{2,4}/", "/image/1080/1080/", u)
    return u


def _price(pricing: dict, names: set) -> int | None:
    for p in (pricing or {}).get("prices", []) or []:
        if p.get("name") in names or p.get("priceType") in names:
            try:
                return int(round(float(p.get("decimalValue"))))
            except Exception:
                pass
    return None


def _discount(pricing: dict) -> int | None:
    for p in (pricing or {}).get("prices", []) or []:
        if p.get("discount"):
            try:
                return int(p["discount"])
            except Exception:
                pass
    return None


def _parse(html: str) -> list[dict]:
    """Extract normalised products from Flipkart's __INITIAL_STATE__ JSON."""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        return []
    try:
        st = json.loads(m.group(1))
    except Exception:
        return []
    nodes: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("titles") and o.get("pricing") and o.get("baseUrl"):
                nodes.append(o)                       # a product-summary node (don't recurse in)
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(st)
    out, seen = [], set()
    for info in nodes:
        base = info.get("baseUrl") or ""
        pm = re.search(r"pid=([A-Z0-9]+)", base)
        pid = pm.group(1) if pm else str(info.get("id") or "")
        if not pid or pid in seen:
            continue
        titles = info.get("titles") or {}
        title = (titles.get("title") or titles.get("newTitle") or "").strip()
        if len(title) < 4:
            continue
        sell = _price(info.get("pricing"), {"Selling Price", "FSP", "Final Price"})
        if not sell:
            continue
        seen.add(pid)
        mrp = _price(info.get("pricing"), {"MRP"})
        disc = _discount(info.get("pricing"))
        if (not mrp or mrp <= sell) and disc and 0 < disc < 95:   # derive MRP from the discount
            mrp = int(round(sell / (1 - disc / 100.0)))
        imgs = (info.get("media") or {}).get("images") or []
        img = _img(imgs[0].get("url")) if imgs and isinstance(imgs[0], dict) else ""
        rating = reviews = None
        rt = info.get("rating")
        if isinstance(rt, dict):
            try:
                rating = round(float(rt.get("average") or rt.get("rating")), 1)
            except Exception:
                rating = None
            reviews = rt.get("count") or rt.get("reviewCount") or rt.get("ratingCount")
        clean_url = base.split("&")[0]
        out.append({
            "asin": str(pid), "category": "", "title": title,
            "price": f"₹{sell:,}", "orig_price": (f"₹{mrp:,}" if mrp and mrp > sell else ""),
            "discount_pct": disc,
            "rating": rating, "reviews": reviews, "bought_past_month": "", "badge": "",
            "image": img,
            "url": ("https://www.flipkart.com" + clean_url) if clean_url.startswith("/") else clean_url,
            "brand": (titles.get("superTitle") or "").strip(), "source": "flipkart",
        })
    return out


def scrape_products(query: str, count: int = 8, max_pages: int = 2) -> dict:
    """Search Flipkart → normalised products (same shape as the Amazon scrape). Never raises.
    Returns {ok, count, items:[...]}. Paginates until it has `count` renderable products."""
    if not configured():
        return {"ok": False, "error": "SCRAPER_PROXY_PASS (ScraperAPI key) not set.", "items": []}
    q = (query or "").strip()
    if len(q) < 2:
        return {"ok": False, "error": "query too short", "items": []}
    items: list[dict] = []
    seen: set = set()
    try:
        for pg in range(1, max(1, min(max_pages, 4)) + 1):
            for p in _parse(_fetch(q, pg)):
                if p["asin"] not in seen and p.get("image"):
                    seen.add(p["asin"]); items.append(p)
            if len(items) >= count:
                break
        log.success(f"[flipkart] scraped {len(items)} products for '{q}'")
        return {"ok": True, "count": len(items), "items": items[:count]}
    except Exception as e:
        log.warning(f"[flipkart_scrape] failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": items[:count]}
