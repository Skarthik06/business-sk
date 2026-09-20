"""
tools/shopify_scrape.py — REAL products (photos + prices) from any Shopify-based Cuelinks merchant.

Shopify exposes a PUBLIC, no-auth product endpoint — https://<store>/products.json — that returns
each product's title, vendor (brand), price, compare-at price, type/tags and hi-res CDN image URLs.
This is Shopify's own built-in feed (served by default), so it is NOT fragile scraping — no proxy,
no key, no bot-block for stores that keep it enabled (verified live: boAt, Noise, Mamaearth).

Products are normalised to the SAME shape as the Flipkart/Amazon scrape (asin/title/price/orig_price/
discount_pct/image/url/brand…), so they flow through the identical _finalize_pool → dedup → compose
→ render → publish pipeline. The product `url` is later converted to a tracked Cuelinks link.

The public feed is not search-ranked, so we pull several pages and rank locally by how well each
product matches the query terms (brand/attribute precision is then tightened by _finalize_pool).
"""
from __future__ import annotations

import re

import requests

from utils.logger import log

_TIMEOUT = 12
_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/html",
}


def _clean_domain(domain: str) -> str:
    return (domain or "").strip().lower().replace("https://", "").replace("http://", "").strip("/")


def _price_int(v) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return 0


def _img_hd(src: str) -> str:
    """Shopify CDN images accept a width param — request a large render for crisp slides."""
    if not src:
        return ""
    src = src.split("?")[0]
    if src.startswith("//"):
        src = "https:" + src
    return f"{src}?width=1200"


def _base(domain: str) -> str | None:
    """Resolve the base URL that serves this store's public Shopify product feed (www or apex)."""
    for base in (f"https://www.{domain}", f"https://{domain}"):
        try:
            r = requests.get(base + "/products.json", headers=_UA, timeout=_TIMEOUT, params={"limit": 1})
            if r.status_code == 200 and r.text.strip().startswith("{") and isinstance(r.json().get("products"), list):
                return base
        except Exception:
            continue
    return None


def products_available(domain: str) -> bool:
    """True when this store serves the public Shopify product feed (so it can generate PRODUCTS)."""
    return _base(_clean_domain(domain)) is not None


def _terms(query: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (query or "").lower()).split() if len(t) > 1]


def scrape_products(domain: str, query: str = "", count: int = 8, max_pages: int = 5) -> dict:
    """Pull REAL products from a Shopify store's public feed, ranked to the query.
    Same output shape as flipkart_scrape.scrape_products: {ok, count, items:[{asin,title,price,
    orig_price,discount_pct,rating,reviews,image,url,brand,category,source}]}. Never raises."""
    domain = _clean_domain(domain)
    if not domain:
        return {"ok": False, "error": "no domain", "items": []}
    base = _base(domain)
    if not base:
        return {"ok": False, "error": f"{domain} does not expose a public Shopify product feed", "items": []}

    terms = _terms(query)
    scored: list[tuple[int, dict]] = []
    seen: set = set()
    try:
        for pg in range(1, max(1, min(max_pages, 8)) + 1):
            r = requests.get(base + "/products.json", headers=_UA, timeout=_TIMEOUT,
                             params={"limit": 250, "page": pg})
            if not r.ok:
                break
            prods = r.json().get("products") or []
            if not prods:
                break
            for p in prods:
                handle = (p.get("handle") or "").strip()
                title = (p.get("title") or "").strip()
                if not handle or not title:
                    continue
                pid = str(p.get("id") or handle)
                if pid in seen:
                    continue
                variants = p.get("variants") or []
                v = variants[0] if variants else {}
                sell = _price_int(v.get("price"))
                if sell <= 0:
                    continue
                images = p.get("images") or []
                img = _img_hd(images[0].get("src")) if images and isinstance(images[0], dict) else ""
                if not img:                                  # a product with no photo is useless here
                    continue
                mrp = _price_int(v.get("compare_at_price"))
                disc = int(round((mrp - sell) / mrp * 100)) if mrp and mrp > sell else None
                tags = p.get("tags")
                tag_s = " ".join(tags) if isinstance(tags, list) else str(tags or "")
                hay = " ".join([title, p.get("vendor") or "", p.get("product_type") or "", tag_s]).lower()
                score = sum(1 for t in terms if t in hay)    # relevance to the query
                if terms and score == 0:                     # off-query product → skip when searching
                    continue
                seen.add(pid)
                scored.append((score, {
                    "asin": f"shp_{pid}", "category": (p.get("product_type") or "").strip(),
                    "title": title,
                    "price": f"₹{sell:,}", "orig_price": (f"₹{mrp:,}" if mrp and mrp > sell else ""),
                    "discount_pct": disc, "rating": None, "reviews": 0, "bought_past_month": "", "badge": "",
                    "image": img, "url": f"{base}/products/{handle}",
                    "brand": (p.get("vendor") or "").strip(), "source": "shopify",
                }))
            if len(prods) < 250 or len(scored) >= count * 8:
                break
        scored.sort(key=lambda x: x[0], reverse=True)        # best query match first
        items = [it for _s, it in scored]
        return {"ok": True, "count": len(items), "items": items[: max(count * 4, count)]}
    except Exception as e:
        log.warning(f"[shopify] scrape_products {domain!r} failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": [it for _s, it in scored][:count]}
