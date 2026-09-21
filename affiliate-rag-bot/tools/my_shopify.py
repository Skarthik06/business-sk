"""
tools/my_shopify.py — the OWNER's OWN Shopify store as a first-party product source.

Unlike the third-party merchant feeds (which we read from the public /products.json), this uses the
Shopify ADMIN API with the owner's read-only token — so it works even on a password-protected or
in-development store and can read every published product. It's the owner's store, so the generated
"affiliate link" is simply the product's own URL (a direct sale — 100% margin, no commission split).

Config (server .env):
    SHOPIFY_STORE_DOMAIN = your-store.myshopify.com   (or a custom domain)
    SHOPIFY_ADMIN_TOKEN  = shpat_xxx                  (Admin API token, scope read_products)

Products are normalised to the SAME shape as the Flipkart/Shopify scrape so they flow through the
identical _finalize_pool → dedup → compose → render → publish pipeline. Never raises.
"""
from __future__ import annotations

import os
import re

import requests

from utils.logger import log

_API_VERSION = "2024-10"
_TIMEOUT = 15


def _domain() -> str:
    d = (os.getenv("SHOPIFY_STORE_DOMAIN") or "").strip().lower()
    return d.replace("https://", "").replace("http://", "").strip("/")


def _token() -> str:
    return (os.getenv("SHOPIFY_ADMIN_TOKEN") or "").strip()


def configured() -> bool:
    return bool(_domain() and _token())


def _headers() -> dict:
    return {"X-Shopify-Access-Token": _token(), "Accept": "application/json"}


def _base() -> str:
    return f"https://{_domain()}/admin/api/{_API_VERSION}"


def _price_int(v) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return 0


def _img_hd(src: str) -> str:
    if not src:
        return ""
    src = src.split("?")[0]
    if src.startswith("//"):
        src = "https:" + src
    return f"{src}?width=1200"


def status() -> dict:
    """Connection status for the panel: {ok, connected, domain, shop, product_count}. Never raises."""
    if not configured():
        return {"ok": True, "connected": False, "domain": _domain(),
                "note": "Add SHOPIFY_STORE_DOMAIN + SHOPIFY_ADMIN_TOKEN to the server .env to connect."}
    try:
        shop = requests.get(f"{_base()}/shop.json", headers=_headers(), timeout=_TIMEOUT)
        if shop.status_code == 401:
            return {"ok": False, "connected": False, "domain": _domain(), "error": "Invalid Admin API token (401)."}
        if not shop.ok:
            return {"ok": False, "connected": False, "domain": _domain(), "error": f"Shopify {shop.status_code}: {shop.text[:120]}"}
        name = (shop.json().get("shop") or {}).get("name", _domain())
        cnt = requests.get(f"{_base()}/products/count.json", headers=_headers(), timeout=_TIMEOUT)
        n = (cnt.json().get("count") if cnt.ok else None)
        return {"ok": True, "connected": True, "domain": _domain(), "shop": name, "product_count": n}
    except Exception as e:
        log.warning(f"[my_shopify] status failed: {e}")
        return {"ok": False, "connected": False, "domain": _domain(), "error": str(e)[:140]}


def collections() -> dict:
    """Custom + smart collections (for the panel dropdown). {ok, collections:[{id,title,handle}]}."""
    if not configured():
        return {"ok": False, "collections": []}
    out: list[dict] = []
    try:
        for kind in ("custom_collections", "smart_collections"):
            r = requests.get(f"{_base()}/{kind}.json", headers=_headers(), timeout=_TIMEOUT, params={"limit": 250})
            if r.ok:
                for c in (r.json().get(kind) or []):
                    out.append({"id": str(c.get("id")), "title": c.get("title", ""), "handle": c.get("handle", "")})
        return {"ok": True, "collections": out}
    except Exception as e:
        log.warning(f"[my_shopify] collections failed: {e}")
        return {"ok": False, "collections": []}


def _terms(query: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (query or "").lower()).split() if len(t) > 1]


def scrape_products(query: str = "", count: int = 8, collection_id: str = "", max_pages: int = 4) -> dict:
    """Pull the owner's OWN products via the Admin API, ranked to the query (and optionally scoped to
    one collection). Same output shape as the scrapers. source='mystore'. Never raises."""
    if not configured():
        return {"ok": False, "error": "SHOPIFY_STORE_DOMAIN + SHOPIFY_ADMIN_TOKEN not set.", "items": []}
    terms = _terms(query)
    scored: list[tuple[int, dict]] = []
    seen: set = set()
    dom = _domain()
    try:
        for pg in range(1, max(1, min(max_pages, 8)) + 1):
            params = {"limit": 250, "page": pg, "status": "active",
                      "fields": "id,title,handle,vendor,product_type,tags,images,variants"}
            if collection_id:
                params["collection_id"] = collection_id
            r = requests.get(f"{_base()}/products.json", headers=_headers(), timeout=_TIMEOUT, params=params)
            if not r.ok:
                if pg == 1:
                    return {"ok": False, "error": f"Shopify {r.status_code}: {r.text[:120]}", "items": []}
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
                if not img:
                    continue
                mrp = _price_int(v.get("compare_at_price"))
                disc = int(round((mrp - sell) / mrp * 100)) if mrp and mrp > sell else None
                tags = p.get("tags")
                tag_s = " ".join(tags) if isinstance(tags, list) else str(tags or "")
                hay = " ".join([title, p.get("vendor") or "", p.get("product_type") or "", tag_s]).lower()
                score = sum(1 for t in terms if t in hay)
                if terms and score == 0:
                    continue
                seen.add(pid)
                scored.append((score, {
                    "asin": f"my_{pid}", "category": (p.get("product_type") or "").strip(), "title": title,
                    "price": f"₹{sell:,}", "orig_price": (f"₹{mrp:,}" if mrp and mrp > sell else ""),
                    "discount_pct": disc, "rating": None, "reviews": 0, "bought_past_month": "", "badge": "",
                    "image": img, "url": f"https://{dom}/products/{handle}",
                    "brand": (p.get("vendor") or "").strip(), "source": "mystore",
                }))
            if len(prods) < 250 or len(scored) >= count * 8:
                break
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [it for _s, it in scored]
        return {"ok": True, "count": len(items), "items": items[: max(count * 4, count)]}
    except Exception as e:
        log.warning(f"[my_shopify] scrape_products failed: {e}")
        return {"ok": False, "error": str(e)[:140], "items": [it for _s, it in scored][:count]}
