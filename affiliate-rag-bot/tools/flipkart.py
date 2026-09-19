"""
tools/flipkart.py — Flipkart Affiliate API client (official product data + direct affiliate links).

Cuelinks monetises Flipkart through a redirect, but the Flipkart Affiliate API gives us the real
thing: official, structured product data (title, price, MRP, discount, image, brand, specs) and a
DIRECT affiliate-tracked `productUrl` — stronger than scraping and stronger than a generic
redirect. This makes the Flipkart market a first-class product source alongside the Amazon scrape.

  Base:  https://affiliate-api.flipkart.net/affiliate/
  Auth:  headers  Fk-Affiliate-Id: <tracking id>,  Fk-Affiliate-Token: <token>
  Creds: FLIPKART_AFFILIATE_ID + FLIPKART_AFFILIATE_TOKEN (from affiliate.flipkart.com).

Products are normalised to the SAME shape as the Amazon scrape (asin/title/price/image/url/…), so
they flow through compose → render → publish unchanged. Never raises: a missing key or a bad row
degrades gracefully rather than breaking the pipeline.
"""
from __future__ import annotations

import os

import requests

from utils.logger import log

_BASE = os.getenv("FLIPKART_API_URL", "https://affiliate-api.flipkart.net/affiliate").rstrip("/")
_TIMEOUT = 30


def _aff_id() -> str:
    return (os.getenv("FLIPKART_AFFILIATE_ID") or "").strip()


def _token() -> str:
    return (os.getenv("FLIPKART_AFFILIATE_TOKEN") or "").strip()


def configured() -> bool:
    return bool(_aff_id() and _token())


def _headers() -> dict:
    return {"Fk-Affiliate-Id": _aff_id(), "Fk-Affiliate-Token": _token(), "Accept": "application/json"}


def _amount(v) -> int | None:
    """Flipkart prices are {amount, currency} objects (or a bare number)."""
    if isinstance(v, dict):
        v = v.get("amount")
    try:
        return int(round(float(v))) if v not in (None, "") else None
    except Exception:
        return None


def _image(p: dict) -> str:
    imgs = p.get("imageUrls") or p.get("productImageUrls") or {}
    if isinstance(imgs, dict):
        for size in ("800x800", "400x400", "unknown", "200x200"):
            if imgs.get(size):
                return imgs[size]
        for v in imgs.values():
            if v:
                return v
    return imgs if isinstance(imgs, str) else ""


def _normalize(item: dict) -> dict | None:
    """Map a Flipkart product to our internal product shape (matches the Amazon scrape)."""
    p = item.get("productBaseInfoV1") or item.get("productBaseInfo") or item
    if not isinstance(p, dict):
        return None
    pid = p.get("productId") or p.get("productdID") or p.get("id")
    title = (p.get("title") or "").strip()
    if not pid or len(title) < 3:
        return None
    sell = _amount(p.get("flipkartSpecialPrice")) or _amount(p.get("flipkartSellingPrice"))
    mrp = _amount(p.get("maximumRetailPrice"))
    if not sell:
        return None
    disc = p.get("discountPercentage")
    try:
        disc = int(round(float(disc))) if disc not in (None, "") else (int(round((mrp - sell) / mrp * 100)) if mrp and mrp > sell else None)
    except Exception:
        disc = None
    rating = p.get("productRating") or p.get("sellerAverageRating")
    try:
        rating = round(float(rating), 1) if rating not in (None, "", "0") else None
    except Exception:
        rating = None
    return {
        "asin": str(pid),                                 # productId as the unique key
        "category": "",
        "title": title,
        "price": f"₹{sell:,}",
        "orig_price": f"₹{mrp:,}" if mrp and mrp > sell else "",
        "discount_pct": disc,
        "rating": rating,
        "reviews": None,
        "bought_past_month": "",
        "badge": "Flipkart Assured" if p.get("productDescription") and p.get("fAssuredProduct") else "",
        "image": _image(p),
        "url": p.get("productUrl") or "",                 # already affiliate-tracked
        "brand": p.get("productBrand") or "",
        "in_stock": bool(p.get("inStock", True)),
        "source": "flipkart",
    }


def _records(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("products", "productInfoList", "productList", "data"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def ping() -> dict:
    """Verify the Flipkart affiliate credentials via the feed-listing endpoint. Never raises."""
    if not configured():
        return {"ok": False, "configured": False,
                "error": "FLIPKART_AFFILIATE_ID / FLIPKART_AFFILIATE_TOKEN not set."}
    try:
        r = requests.get(f"{_BASE}/api/{_aff_id()}.json", headers=_headers(), timeout=_TIMEOUT)
        if r.ok:
            d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            listings = (d.get("apiGroups", {}).get("affiliate", {}).get("apiListings")
                        if isinstance(d.get("apiGroups"), dict) else d.get("apiListings")) or {}
            return {"ok": True, "configured": True, "feeds": len(listings) if isinstance(listings, dict) else 0}
        return {"ok": False, "configured": True, "status": r.status_code, "error": r.text[:160]}
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)[:160]}


def search_products(query: str, count: int = 10) -> dict:
    """Keyword product search (GET /1.0/search.json) → normalised products. Never raises.
    Returns {ok, count, items:[...]} or an error dict. resultCount is capped at 10 by Flipkart."""
    if not configured():
        return {"ok": False, "error": "Flipkart affiliate credentials not set.", "items": []}
    q = (query or "").strip()
    if len(q) < 2:
        return {"ok": False, "error": "query too short", "items": []}
    try:
        r = requests.get(f"{_BASE}/1.0/search.json", headers=_headers(), timeout=_TIMEOUT,
                         params={"query": q, "resultCount": max(1, min(int(count or 10), 10))})
        if not r.ok:
            return {"ok": False, "error": f"flipkart {r.status_code}: {r.text[:160]}", "items": []}
        items = [m for m in (_normalize(x) for x in _records(r.json()) if isinstance(x, dict)) if m]
        return {"ok": True, "count": len(items), "items": items}
    except Exception as e:
        log.warning(f"[flipkart] search failed: {e}")
        return {"ok": False, "error": str(e)[:160], "items": []}
