"""
tools/flipkart_scrape.py — Flipkart product scraper (public search, via OUR scraper service).

Flipkart blocks datacenter IPs (the Oracle server), so every page is fetched by our own scraper
(tools/scrape_bus → the scraper container → your phone / laptop on a home IP). No paid scraping
API is used. Flipkart embeds every product in a `window.__INITIAL_STATE__` JSON blob, so we parse
that (robust) rather than fragile CSS selectors.

Products are normalised to the SAME shape as the Amazon scrape (asin/title/price/image/url/rating/
discount…), so they flow through the exact same dedup → compose → render → publish pipeline. The
product `url` is later converted to a tracked Cuelinks link (Flipkart is a Cuelinks market).
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote_plus

import requests

from utils.logger import log

_TIMEOUT = 60


def configured() -> bool:
    """Our own scraper service (phone/laptop on a home IP) can reach Flipkart right now."""
    from tools import scrape_bus
    return scrape_bus.online()


def _num_price(v) -> int:
    try:
        return int(re.sub(r"[^0-9]", "", str(v or "")) or 0)
    except Exception:
        return 0


def search_url(query: str, page: int = 1, sort: str = "") -> str:
    url = f"https://www.flipkart.com/search?q={quote_plus(query)}"
    if sort:                                          # e.g. price_asc (Flipkart's own sort)
        url += f"&sort={sort}"
    if page > 1:
        url += f"&page={page}"
    return url


def _fetch(query: str, page: int = 1, sort: str = "") -> str:
    """Fetch a Flipkart search page through OUR scraper service (tools/scrape_bus → the scraper
    container → your phone/laptop on a home IP). No paid scraping API. "" when it can't."""
    from tools import scrape_bus
    r = scrape_bus.fetch(search_url(query, page, sort), "flipkart", _TIMEOUT)
    if r.get("ok") and "__INITIAL_STATE__" in (r.get("html") or ""):
        return r["html"]
    log.warning(f"[flipkart] own scraper: {str(r.get('error') or 'no product JSON')[:90]}")
    return ""


def _img(u: str) -> str:
    """A valid, high-res Flipkart CDN image URL. Flipkart embeds templated placeholders
    ({@width}/{@height}/{@quality}) that MUST all be filled — a leftover {@quality} makes the CDN
    return HTTP 400 (blank product on the slide). Fills them at 1080px / q70 and upgrades any
    baked-in small size."""
    if not u:
        return ""
    u = (u.replace("{@width}", "1080").replace("{@height}", "1080")
          .replace("{@quality}", "70").replace("http://", "https://"))
    u = re.sub(r"/image/\d{2,4}/\d{2,4}/", "/image/1080/1080/", u)   # upscale a fixed small size
    u = re.sub(r"\{@[^}]+\}", "70", u)                               # any leftover placeholder → safe value
    return u


def _price(pricing: dict, names: set) -> int | None:
    """The price you PAY (or the MRP when "MRP" in names). Flipkart's own finalPrice / mrp fields
    first; then the prices list, where STRIKE-OFF is authoritative (struck = MRP). Never trust the
    labels alone — since 2026-10 Flipkart labels the struck-through MRP "Selling Price"/"FSP" and
    the real price "Special Price", which made us read every MRP as the selling price."""
    pr = pricing or {}
    want_mrp = "MRP" in names

    def _v(x):
        try:
            v = x.get("value") if x.get("value") is not None else x.get("decimalValue")
            return int(round(float(v))) if v is not None else None
        except Exception:
            return None
    top = pr.get("mrp") if want_mrp else pr.get("finalPrice")
    if isinstance(top, dict) and _v(top):
        return _v(top)
    prices = pr.get("prices", []) or []
    struck = [p for p in prices if "strikeOff" in p]
    if struck:
        for p in struck:
            if bool(p.get("strikeOff")) == want_mrp and _v(p):
                return _v(p)
        if not want_mrp:                              # no unstruck entry → the lowest is what you pay
            vals = [_v(p) for p in struck if _v(p)]
            return min(vals) if vals else None
        return None
    for p in prices:                                  # oldest layout: named, no strike-off flag
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
    try:                                                   # current layout: a page-level percentage
        return int((pricing or {}).get("totalDiscount")) or None
    except Exception:
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
    stars: dict = {}                                  # productId → (average rating, rating count)

    def walk(o):
        if isinstance(o, dict):
            if o.get("titles") and o.get("pricing") and o.get("baseUrl"):
                nodes.append(o)                       # a product-summary node (don't recurse in)
                return
            if o.get("productId") and o.get("averageRating") is not None:
                # Flipkart keeps ratings in a separate node, linked by productId (= the pid)
                stars[str(o["productId"])] = (o.get("averageRating"),
                                              o.get("totalRatingCount") or o.get("totalReviewCount"))
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
        if rating is None and pid in stars:
            try:
                rating = round(float(stars[pid][0]), 1)
                reviews = int(stars[pid][1] or 0)
            except Exception:
                pass
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


def rating_from_page(html: str) -> tuple:
    """(average rating, rating count) from a product page's star histogram
    ("individualRatingsCount": [{ratingValue, ratingCount}, …]) — exact, e.g. (4.2, 97)."""
    m = re.search(r'"individualRatingsCount":(\[[^\]]*\])', html or "")
    if not m:
        return None, None
    try:
        hist = json.loads(m.group(1))
        n = sum(int(x.get("ratingCount") or 0) for x in hist)
        if not n:
            return None, 0
        return round(sum(float(x["ratingValue"]) * int(x.get("ratingCount") or 0) for x in hist) / n, 1), n
    except Exception:
        return None, None


def enrich_ratings(items: list[dict], fetch, workers: int = 4) -> int:
    """Flipkart's fashion grid shows ratings for only a few products — fill the rest in place from
    each product's own page (our scraper, ~2 s a page, `workers` at a time). Returns how many."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [p for p in items if p.get("rating") is None and p.get("url")]

    def one(p):
        try:
            avg, n = rating_from_page(fetch(p["url"]) or "")
            if avg is not None:
                p["rating"], p["reviews"] = avg, n
                return 1
        except Exception:
            pass
        return 0
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return sum(ex.map(one, todo))


def parse_products(html: str, count: int = 8) -> dict:
    """Parse already-fetched Flipkart search HTML → products. Used with the residential worker
    (server fetches the HTML from a residential IP, then hands it here). Never raises."""
    items: list[dict] = []
    seen: set = set()
    try:
        for p in _parse(html or ""):
            if p["asin"] not in seen and p.get("image"):
                seen.add(p["asin"]); items.append(p)
        items = items if price_max else items[:count]
        return {"ok": True, "count": len(items), "items": items}
    except Exception as e:
        log.warning(f"[flipkart_scrape] parse failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": items[:count]}


def scrape_products(query: str, count: int = 8, max_pages: int = 2, fetch=None,
                    price_max: int | None = None) -> dict:
    """Search Flipkart → normalised products (same shape as the Amazon scrape). Never raises.
    `fetch(url)->html` optionally overrides the fetch (e.g. the residential worker); default uses
    ScraperAPI. Returns {ok, count, items:[...]}. Paginates until it has `count` products."""
    q = (query or "").strip()
    if len(q) < 2:
        return {"ok": False, "error": "query too short", "items": []}
    if fetch is None and not configured():
        return {"ok": False, "error": "Our scraper isn't online — open SK Helper on the phone or start the laptop worker.", "items": []}
    items: list[dict] = []
    seen: set = set()
    try:
        pages = [(pg, "") for pg in range(1, max(1, min(max_pages, 4)) + 1)]
        if price_max:                                 # a price cap → also Flipkart's cheapest-first
            # pages, going deeper until there are enough products UNDER the cap (count guarantee)
            pages = [(1, ""), (1, "price_asc"), (2, "price_asc"), (3, "price_asc"), (2, ""), (4, "price_asc")]
        under = 0
        for pg, sort in pages:
            html = fetch(search_url(q, pg, sort)) if fetch else _fetch(q, pg, sort)
            for p in _parse(html or ""):
                if p["asin"] not in seen and p.get("image"):
                    seen.add(p["asin"]); items.append(p)
                    if price_max and 0 < _num_price(p.get("price")) <= price_max:
                        under += 1
            if (len(items) if not price_max else under) >= count:
                break
        log.success(f"[flipkart] scraped {len(items)} products for '{q}'")
        items = items if price_max else items[:count]
        return {"ok": True, "count": len(items), "items": items}
    except Exception as e:
        log.warning(f"[flipkart_scrape] failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": items[:count]}
