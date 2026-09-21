"""
tools/my_shopify.py — the OWNER's OWN Shopify store as a first-party product source.

For a store you're promoting on Instagram the storefront has to be PUBLIC anyway (customers must be
able to reach it to buy), and a public Shopify storefront serves its catalogue at the no-auth
`/products.json` endpoint — so by default this reads that public feed with NO token at all. Just set:

    SHOPIFY_STORE_DOMAIN = your-store.myshopify.com   (or a custom domain)

If you'd rather keep the storefront password-protected, add a Shopify ADMIN API token and it will use
the Admin API instead (works on a private store):

    SHOPIFY_ADMIN_TOKEN  = shpat_xxx                  (Admin API token, scope read_products)

Note: Shopify's NEW "Dev Dashboard" custom apps are OAuth-only and DON'T issue a static store token
(the "app automation token" is app-management only — it returns 401 on the store Admin API), so the
public-feed route is the practical one for most owners.

Products are normalised to the SAME shape as the other scrapers so they flow through the identical
_finalize_pool → dedup → compose → render → publish pipeline. Own-store links stay raw. Never raises.
"""
from __future__ import annotations

import os
import re

import requests

from utils.logger import log
from tools import shopify_scrape

# Affiliate-link curation: a My Store product can carry the DESTINATION affiliate link (e.g. a Nykaa/
# Myntra link for a product you can't scrape) — put it in a product TAG like `aff:https://…` (also
# accepts link:/buy:/url:), or just paste the URL into the product description. The post then links
# there instead of your store page. This turns your Shopify store into a curation + image warehouse
# for ANY unscrapable affiliate: upload the image once, set the link, and the engine posts it.
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_AFF_PREFIXES = ("aff:", "link:", "buy:", "url:", "go:")


def _affiliate_link(tags: str, descr: str) -> str:
    for t in re.split(r"[,\n]", tags or ""):
        t = t.strip()
        low = t.lower()
        for pre in _AFF_PREFIXES:
            if low.startswith(pre):
                u = t[len(pre):].strip()
                if u.startswith("http"):
                    return u
    m = _URL_RE.search(descr or "")
    return m.group(0) if m else ""


def _apply_aff(items: list) -> list:
    """If a product carries an affiliate link (tag `aff:…` or a URL in its description), post links
    there instead of the store page. Keeps the Shopify image + details."""
    for it in items or []:
        aff = _affiliate_link(it.get("tags", ""), it.get("descr", ""))
        if aff:
            it["url"] = aff
            it["curated"] = True
    return items

_API_VERSION = "2024-10"
_TIMEOUT = 15


def _domain() -> str:
    d = (os.getenv("SHOPIFY_STORE_DOMAIN") or "").strip().lower()
    return d.replace("https://", "").replace("http://", "").strip("/")


def _token() -> str:
    return (os.getenv("SHOPIFY_ADMIN_TOKEN") or "").strip()


def _has_token() -> bool:
    return bool(_token())


def configured() -> bool:
    """We can operate as long as a store domain is set (token is optional — public feed by default)."""
    return bool(_domain())


def _headers() -> dict:
    return {"X-Shopify-Access-Token": _token(), "Accept": "application/json"}


def _base() -> str:
    return f"https://{_domain()}/admin/api/{_API_VERSION}"


def status() -> dict:
    """Connection status for the panel. Never raises.
    {ok, connected, mode: 'public'|'admin', domain, shop?, product_count?, note?}."""
    dom = _domain()
    if not dom:
        return {"ok": True, "connected": False, "domain": "",
                "note": "Add SHOPIFY_STORE_DOMAIN to the server .env (your-store.myshopify.com)."}
    # Admin API path (private store) when a token is present.
    if _has_token():
        try:
            shop = requests.get(f"{_base()}/shop.json", headers=_headers(), timeout=_TIMEOUT)
            if shop.status_code == 401:
                return {"ok": False, "connected": False, "mode": "admin", "domain": dom, "error": "Invalid Admin API token (401)."}
            if not shop.ok:
                return {"ok": False, "connected": False, "mode": "admin", "domain": dom, "error": f"Shopify {shop.status_code}."}
            name = (shop.json().get("shop") or {}).get("name", dom)
            cnt = requests.get(f"{_base()}/products/count.json", headers=_headers(), timeout=_TIMEOUT)
            n = (cnt.json().get("count") if cnt.ok else None)
            return {"ok": True, "connected": True, "mode": "admin", "domain": dom, "shop": name, "product_count": n}
        except Exception as e:
            log.warning(f"[my_shopify] admin status failed: {e}")
            return {"ok": False, "connected": False, "mode": "admin", "domain": dom, "error": str(e)[:140]}
    # Public feed path (public storefront) — the default.
    try:
        base = shopify_scrape._base(dom)
        if base:
            r = requests.get(base + "/products.json", headers={"User-Agent": "Mozilla/5.0"}, timeout=_TIMEOUT, params={"limit": 1})
            n = len(r.json().get("products", [])) if r.ok and r.text.strip().startswith("{") else 0
            return {"ok": True, "connected": True, "mode": "public", "domain": dom, "shop": dom,
                    "product_count": None if n else 0,
                    "note": None if n else "Storefront is reachable but has no products yet — add products to start posting."}
        # not reachable → almost always a password-protected storefront
        return {"ok": True, "connected": False, "mode": "public", "domain": dom,
                "note": "Storefront isn't public. Remove the storefront password (Online store → Preferences), or add a SHOPIFY_ADMIN_TOKEN to read it privately."}
    except Exception as e:
        log.warning(f"[my_shopify] public status failed: {e}")
        return {"ok": False, "connected": False, "mode": "public", "domain": dom, "error": str(e)[:140]}


def collections() -> dict:
    """Store collections for the panel dropdown (Admin API only — public feed has no collection filter)."""
    if not (_domain() and _has_token()):
        return {"ok": True, "collections": []}
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
        return {"ok": True, "collections": []}


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


def _admin_products(query: str, count: int, collection_id: str, max_pages: int) -> dict:
    """Private-store path: pull the owner's products via the Admin API. Same shape as the scrapers."""
    from tools.shopify_scrape import _terms
    terms = _terms(query)
    scored: list[tuple[int, dict]] = []
    seen: set = set()
    dom = _domain()
    try:
        for pg in range(1, max(1, min(max_pages, 8)) + 1):
            params = {"limit": 250, "page": pg, "status": "active",
                      "fields": "id,title,handle,vendor,product_type,tags,body_html,images,variants"}
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
                v = (p.get("variants") or [{}])[0]
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
                    "tags": tag_s, "descr": (p.get("body_html") or "")[:2000],
                }))
            if len(prods) < 250 or len(scored) >= count * 8:
                break
        scored.sort(key=lambda x: x[0], reverse=True)
        items = _apply_aff([it for _s, it in scored])
        return {"ok": True, "count": len(items), "items": items[: max(count * 4, count)]}
    except Exception as e:
        log.warning(f"[my_shopify] admin products failed: {e}")
        return {"ok": False, "error": str(e)[:140], "items": [it for _s, it in scored][:count]}


def _price_num(s) -> float:
    d = re.sub(r"[^\d.]", "", str(s or ""))
    try:
        return float(d) if d else 0.0
    except Exception:
        return 0.0


def create_product(p: dict) -> dict:
    """Create a product in the owner's Shopify store from a generated product dict (Studio / Flipkart /
    boAt / curated). The DESTINATION affiliate link is stored as a tag `aff:<link>` and a body button,
    so the store page can link out to it. Requires a write token. Returns {ok,id,handle,url,admin_url}."""
    if not (_domain() and _has_token()):
        return {"ok": False, "error": "My Store write not configured (need SHOPIFY_ADMIN_TOKEN)."}
    title = (p.get("product_title") or p.get("title") or "").strip()[:255]
    if not title:
        return {"ok": False, "error": "no title"}
    aff = (p.get("affiliate_link") or p.get("url") or "").strip()
    img = (p.get("image_url") or p.get("image") or "").strip()
    price = _price_num(p.get("price"))
    mrp = _price_num(p.get("orig_price"))
    tags = ["sk-pipeline", "source:" + (p.get("source") or "sk")]
    if aff:
        tags.append("aff:" + aff)
    body = str(p.get("summary") or p.get("hook") or "")
    if aff:
        body += f'<p><a href="{aff}" target="_blank" rel="nofollow sponsored">Buy now →</a></p>'
    variant = {"price": f"{price:.2f}" if price else "0.00"}
    if mrp and mrp > price:
        variant["compare_at_price"] = f"{mrp:.2f}"
    payload = {"product": {
        "title": title, "body_html": body,
        "vendor": str(p.get("brand") or "")[:80],
        "product_type": str(p.get("category") or "")[:80],
        "status": "active", "tags": ", ".join(tags),
        "variants": [variant],
    }}
    if img:
        payload["product"]["images"] = [{"src": img}]
    try:
        r = requests.post(f"{_base()}/products.json", headers={**_headers(), "Content-Type": "application/json"},
                          json=payload, timeout=30)
        if not r.ok:
            return {"ok": False, "error": f"Shopify {r.status_code}: {r.text[:160]}"}
        prod = r.json().get("product", {}) or {}
        handle = prod.get("handle", "")
        store = _domain().split(".")[0]
        return {"ok": True, "id": str(prod.get("id", "")), "handle": handle,
                "url": f"https://{_domain()}/products/{handle}" if handle else "",
                "admin_url": f"https://admin.shopify.com/store/{store}/products/{prod.get('id')}"}
    except Exception as e:
        log.warning(f"[my_shopify] create_product failed: {e}")
        return {"ok": False, "error": str(e)[:160]}


def scrape_products(query: str = "", count: int = 8, collection_id: str = "", max_pages: int = 5) -> dict:
    """Pull the owner's OWN products. Uses the Admin API when a token is set (private store), else the
    public /products.json feed. source='mystore'. Same shape as the scrapers. Never raises."""
    if not _domain():
        return {"ok": False, "error": "SHOPIFY_STORE_DOMAIN not set.", "items": []}
    if _has_token():
        return _admin_products(query, count, collection_id, max_pages)
    # Public feed — reuse the generic Shopify scraper, then relabel as own-store products.
    res = shopify_scrape.scrape_products(_domain(), query=query, count=count, max_pages=max_pages)
    for it in res.get("items", []):
        it["source"] = "mystore"
        it["asin"] = "my_" + str(it.get("asin", "")).replace("shp_", "")
    _apply_aff(res.get("items", []))
    return res
