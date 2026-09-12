"""
tools/amazon.py  —  Async Playwright Amazon scraper.

Handles: login, best-sellers scraping, SiteStripe affiliate link generation.
"""
from __future__ import annotations
import asyncio
import re
from typing import Optional
from urllib.parse import quote_plus

from playwright.async_api import Page
from utils.logger import log
from utils.alerts import find_element


async def _delay(min_ms: int, max_ms: int = 0) -> None:
    import random
    ms = random.randint(min_ms, max_ms or min_ms)
    await asyncio.sleep(ms / 1000)


async def _human_type(page: Page, selector: str, text: str) -> None:
    """Type text with realistic human-speed keystroke delays."""
    import random
    await page.click(selector)
    await _delay(150, 300)
    for char in text:
        await page.type(selector, char, delay=random.randint(40, 110))


def _extract_asin(url: str) -> Optional[str]:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# ─── Login ───────────────────────────────────────────────────────────────────

async def amazon_login(page: Page, email: str, password: str, marketplace: str) -> None:
    base = f"https://www.{marketplace}"
    log.step("Logging into Amazon...")

    await page.goto(f"{base}/gp/bestsellers", wait_until="domcontentloaded")
    await _delay(2000, 3000)

    # Already logged in?
    if await page.query_selector("#nav-link-accountList"):
        content = await page.text_content("#nav-link-accountList")
        if content and "sign in" not in content.lower():
            log.success("Already logged into Amazon ✓")
            return

    await page.goto(
        f"{base}/ap/signin?openid.return_to={base}/gp/bestsellers",
        wait_until="domcontentloaded",
    )
    await _delay(1500, 2500)

    await _human_type(page, "#ap_email", email)
    await page.click("#continue")
    await page.wait_for_selector("#ap_password", timeout=12000)
    await _delay(700, 1200)

    await _human_type(page, "#ap_password", password)
    await _delay(400, 700)
    await page.click("#signInSubmit")
    await page.wait_for_load_state("domcontentloaded")
    await _delay(2000, 3000)

    log.success("Logged into Amazon ✓")


# ─── Product Scrape (Amazon Search — server-rendered, reliable) ──────────────
# Best Sellers pages render their grid client-side, and Amazon serves a JS
# skeleton to automated/headless browsers (0 products). Search results are
# server-rendered — product data (data-asin, title, price, image) is in the
# initial HTML — so they scrape reliably in headless and non-headless alike.

CATEGORY_SEARCH = {
    "home":        "home decor",
    "kitchen":     "kitchen gadgets",
    "fashion":     "fashion clothing",
    "beauty":      "beauty skincare",
    "electronics": "electronics gadgets",
    "fitness":     "fitness equipment",
    "toys":        "toys for kids",
    "books":       "bestselling books",
}


# ─── Quality gate + attractiveness ranking (deterministic, pre-AI) ───────────

_DIGITS = re.compile(r"[\d,]+")


def _parse_price(s: Optional[str]) -> Optional[int]:
    m = _DIGITS.search(s or "")
    return int(m.group().replace(",", "")) if m else None


def _hi_res_image(url: Optional[str]) -> str:
    """Upgrade an Amazon thumbnail URL to the FULL-RESOLUTION original.

    Amazon encodes the size in the filename, e.g.
      .../images/I/71AbCdEf._AC_UL320_.jpg   (320px thumbnail)
    Stripping the `._…_` size token returns the original full-size image:
      .../images/I/71AbCdEf.jpg
    So carousels/storefront use crisp images instead of the tiny search thumb."""
    if not url:
        return ""
    url = url.split("?")[0]
    if "._" not in url:
        return url
    base = url.split("._")[0]
    ext = url.rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    return f"{base}.{ext}"


def _parse_count(s: Optional[str]) -> int:
    """'6K+' -> 6000 · '1.8K' -> 1800 · '907' -> 907 · '' -> 0."""
    if not s:
        return 0
    m = re.search(r"([\d.]+)\s*([KkMm]?)", str(s).replace(",", ""))
    if not m:
        return 0
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        n *= 1_000
    elif unit == "m":
        n *= 1_000_000
    return int(n)


def _passes_quality(p: dict, overrides: Optional[dict] = None) -> bool:
    """Only products that ATTRACT customers: real price+image, impulse price
    range, and (when present) a solid rating + enough reviews for social proof.
    Per-request `overrides` (min_rating/min_reviews/price_min/price_max) win over
    the config defaults so the UI can tune the constraints."""
    from config import cfg
    import runtime
    o = overrides or {}
    # Per-request overrides win; else the runtime overlay (Agents panel); else config default.
    min_rating  = o.get("min_rating")  if o.get("min_rating")  is not None else runtime.get("QUALITY_MIN_RATING", cfg.bot.min_rating, "float")
    min_reviews = o.get("min_reviews") if o.get("min_reviews") is not None else runtime.get("QUALITY_MIN_REVIEWS", cfg.bot.min_reviews, "int")
    price_min   = o.get("price_min")   if o.get("price_min")   is not None else cfg.bot.price_min
    price_max   = o.get("price_max")   if o.get("price_max")   is not None else runtime.get("QUALITY_PRICE_MAX", cfg.bot.price_max, "int")

    price = _parse_price(p.get("price"))
    if not p.get("image") or not price:
        return False
    if not (price_min <= price <= price_max):
        return False
    rating = p.get("rating")
    if rating is not None and rating < min_rating:
        return False
    reviews = p.get("reviews")
    if reviews is not None and reviews < min_reviews:
        return False
    return True


def _attractiveness(p: dict) -> float:
    """Composite pull score: rating, review volume, discount, recent demand, badge."""
    import math
    rating  = float(p.get("rating") or 0)
    reviews = int(p.get("reviews") or 0)
    disc    = int(p.get("discount_pct") or 0)
    bought  = _parse_count(p.get("bought_past_month"))
    badge   = 6.0 if p.get("badge") else 0.0
    return (rating * 20) + (math.log10(reviews + 1) * 10) + (disc * 0.4) + (math.log10(bought + 1) * 8) + badge


_SCRAPE_JS = r"""
(args) => {
  const { marketplace, category } = args;
  const items = Array.from(document.querySelectorAll('[data-component-type="s-search-result"]'))
    .filter(el => (el.getAttribute('data-asin') || '').length === 10).slice(0, 60);
  const out = [];
  for (const item of items) {
    const g = (s) => item.querySelector(s);
    const t = (s) => (g(s)?.textContent || '').trim();
    const asin  = item.getAttribute('data-asin');
    const title = t('h2 a span, h2 span, .a-size-base-plus, .a-size-medium');
    if (!asin || title.length < 4) continue;
    const full = (item.innerText || '').replace(/\s+/g, ' ');
    const rM   = (t('.a-icon-alt').match(/([\d.]+)\s*out of 5/) || full.match(/([\d.]+)\s*out of 5 stars/));
    const revM = full.match(/out of 5 stars\s*\(([\d.,]+[KkMm]?)\)/i);
    const dM   = full.match(/\((\d+)%\s*off\)/i);
    const bM   = full.match(/([\d.,]+[KkMm]?\+?)\s*bought in past/i);
    const mM   = full.match(/M\.?R\.?P\.?:?\s*₹\s*([\d,]+)/i);   // strikethrough list price
    // Badge: the "Amazon's Choice" badge splits across TWO .a-badge-text spans ("Amazon's" +
    // "Choice"), so reading the first span alone truncates it to "Amazon's". Canonicalise from
    // the card's full visible text instead (reliable), then fall back to the joined spans.
    let badge = '';
    const bm = full.match(/Amazon['’]s\s+Choice|#1\s*Best\s*Seller|Best\s*Seller|Limited time deal/i);
    if (bm) {
      const s = bm[0];
      if (/amazon/i.test(s))            badge = "Amazon's Choice";
      else if (/best\s*seller/i.test(s)) badge = /#1/.test(s) ? '#1 Best Seller' : 'Best Seller';
      else                               badge = 'Limited time deal';
    } else {
      badge = Array.from(item.querySelectorAll('.a-badge-text'))
                .map(x => (x.textContent || '').trim()).filter(Boolean).join(' ')
                .replace(/\s+/g, ' ').trim();
      if (/^amazon['’]s$/i.test(badge)) badge = "Amazon's Choice";   // repair a split badge
    }
    const imgEl = g('img.s-image');
    out.push({
      asin: asin, category: category, title: title,
      price:      t('.a-price .a-offscreen'),
      orig_price: mM ? ('₹' + mM[1]) : '',
      discount_pct:      dM ? parseInt(dM[1]) : null,
      rating:            rM ? parseFloat(rM[1]) : null,
      reviews:           revM ? revM[1] : null,          // string; parsed to int in Python
      bought_past_month: bM ? bM[1] : '',
      badge:             (badge || '').trim(),
      sponsored:         /^Sponsored/i.test(full),
      image: imgEl?.getAttribute('src') || '',
      url:   'https://www.' + marketplace + '/dp/' + asin,
    });
  }
  return out;
};
"""


async def _scrape_page(page: Page, category: str, marketplace: str,
                       term: str, page_num: int = 1) -> list[dict]:
    """Scrape ONE search-results page for `term` and return RAW normalized products
    (no quality filter / dedup yet — the caller pools + filters across pages)."""
    url = f"https://www.{marketplace}/s?k={quote_plus(term)}&ref=nb_sb_noss"
    if page_num > 1:
        url += f"&page={page_num}"

    log.step(f"Scraping Amazon search: {category} ('{term}' · page {page_num})...")
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        # "Download is starting" / net::ERR = Amazon's Akamai bot-wall served the datacenter
        # IP a challenge instead of the page. The fix is a residential proxy / scraping API.
        from config import cfg
        hint = "" if cfg.scraper_proxy.enabled else " — set SCRAPER_PROXY_* (residential proxy/scraping API) so Amazon serves real results from the cloud IP"
        log.error(f"Amazon blocked the request for '{term}' ({e}){hint}")
        return []
    try:
        await page.wait_for_selector('[data-component-type="s-search-result"]', timeout=15000)
    except Exception:
        log.warning(f"no results rendered for '{term}' p{page_num} (possible bot-wall){'' if __import__('config').cfg.scraper_proxy.enabled else ' — set SCRAPER_PROXY_*'}")
        return []
    await _delay(600, 1200)

    products = await page.evaluate(_SCRAPE_JS, {"marketplace": marketplace, "category": category})

    # Normalize the review-count string ("1.8K") to an int, upgrade the tiny search
    # thumbnail to the full-resolution product image, and stamp the source query/page
    # (used by discovery query-yield stats + the ProductCandidate contract).
    for p in products:
        rv = p.get("reviews")
        p["reviews"] = _parse_count(rv) if rv else None
        p["image"] = _hi_res_image(p.get("image"))
        p["source_query"] = term
        p["source_page"] = page_num
    return products


def _finalize_pool(raw: list[dict], quality: Optional[dict], cap: int) -> list[dict]:
    """Quality-gate → attractiveness sort → within-run dedup → top `cap`. Shared by
    the single-query and multi-query paths so both apply the SAME uniqueness rules."""
    kept = [p for p in raw if _passes_quality(p, quality)]
    if not kept:  # relaxed fallback so a strict filter never empties a category
        kept = [p for p in raw if p.get("image") and _parse_price(p.get("price"))]
    kept.sort(key=_attractiveness, reverse=True)
    kept = _dedup_products(kept)                 # drop variant/duplicate listings
    return kept[:cap]


async def scrape_products(page: Page, category: str, marketplace: str,
                          query: Optional[str] = None,
                          quality: Optional[dict] = None) -> list[dict]:
    """Scrape Amazon SEARCH results (server-rendered → works headless) and return
    the top quality-ranked products. Each product carries grounded, customer-pull
    signals: rating, review count, discount %, M.R.P, 'bought in past month', badge.

    `query`   — free-text search term (overrides the category→keyword mapping).
    `quality` — per-request min_rating/min_reviews/price_min/price_max overrides.

    Single-query path (keyword mode, or discovery disabled). Extract ~60 → keep only
    those passing quality → rank by attractiveness → dedup → top 40. Links/details
    are matched by construction (the affiliate URL is built from the exact ASIN).
    """
    term = (query or CATEGORY_SEARCH.get(category, category) or category).strip()
    raw = await _scrape_page(page, category, marketplace, term, 1)
    result = _finalize_pool(raw, quality, cap=40)
    log.success(f"Scraped {len(raw)} → top {len(result)} unique quality products ('{term}')")
    return result


async def scrape_products_multi(page: Page, category: str, marketplace: str,
                                queries: list[str], quality: Optional[dict] = None,
                                max_pages: int = 2, target_pool: int = 60) -> tuple[list[dict], dict]:
    """Phase 1 discovery: mine SEVERAL search intents (+ pagination) for one category,
    pool them, and return the top unique quality products — plus a per-query yield map
    for adaptive rotation. Stops early once the unique quality pool reaches
    `target_pool` (adaptive stopping keeps runtime bounded).

    Returns (products, yields) where yields = {query: {"raw": n, "quality_unique": n}}.
    """
    raw_all: list[dict] = []
    yields: dict = {}
    for term in queries:
        term = (term or "").strip()
        if not term:
            continue
        before_unique = len(_finalize_pool(raw_all, quality, cap=10**6))
        q_raw = 0
        for pg in range(1, max_pages + 1):
            page_raw = await _scrape_page(page, category, marketplace, term, pg)
            if not page_raw:
                break                                    # no more pages for this term
            raw_all.extend(page_raw)
            q_raw += len(page_raw)
        after_unique = len(_finalize_pool(raw_all, quality, cap=10**6))
        yields[term] = {"raw": q_raw, "quality_unique": max(0, after_unique - before_unique)}
        # Adaptive stopping — enough unique quality candidates pooled.
        if after_unique >= target_pool:
            log.info(f"[discovery] target pool {target_pool} reached after '{term}' — stopping early")
            break

    result = _finalize_pool(raw_all, quality, cap=max(target_pool, 40))
    log.success(
        f"[discovery] {len(queries)} intents · {len(raw_all)} raw → {len(result)} unique quality products"
    )
    return result, yields


def _dedup_products(products: list[dict]) -> list[dict]:
    """Collapse duplicate/variant listings so a carousel never shows the same product twice.
    Amazon lists one product under many ASINs (colours/sizes/sponsored+organic) that share the
    SAME product photo and title. We key on: exact ASIN, the base image id (the part of the
    Amazon image URL before '._' — identical for the same photo), and a normalized title prefix.
    Products are already sorted best-first, so the strongest of each duplicate group is kept."""
    seen_asin: set = set()
    seen_img: set = set()
    seen_title: set = set()
    out: list[dict] = []
    for p in products:
        asin = p.get("asin") or ""
        img_base = (p.get("image") or "").split("._")[0].split("?")[0]
        title_key = re.sub(r"[^a-z0-9]", "", (p.get("title") or "").lower())[:40]
        if (asin and asin in seen_asin) or (img_base and img_base in seen_img) or (title_key and title_key in seen_title):
            continue
        if asin:
            seen_asin.add(asin)
        if img_base:
            seen_img.add(img_base)
        if title_key:
            seen_title.add(title_key)
        out.append(p)
    return out


# ─── Affiliate tag resolution (stored account → env fallback) ─────────────────
# Prefer the Amazon Associates tag saved in the IG backend's encrypted account store
# (managed from the studio Accounts panel) over the static .env tag. Cached ~5 min;
# any failure falls back to the configured tag so link-building never breaks.
import os as _os
import time as _time
import urllib.request as _urlreq
import json as _json

_tag_cache = {"tag": None, "at": 0.0}
_IG_URL = _os.getenv("IG_BACKEND_URL", "http://backend:8000")


def resolve_associate_tag(default_tag: str) -> str:
    now = _time.time()
    if _tag_cache["tag"] is not None and now - _tag_cache["at"] < 300:
        return _tag_cache["tag"] or default_tag
    tag = None
    try:
        with _urlreq.urlopen(_IG_URL + "/api/v1/integrations/affiliate/active-tag", timeout=4) as r:
            body = _json.loads(r.read().decode())
            tag = ((body.get("data") or {}).get("tag") or "").strip() or None
    except Exception:
        tag = None
    _tag_cache["tag"] = tag; _tag_cache["at"] = now
    return tag or default_tag


# ─── Affiliate Link ───────────────────────────────────────────────────────────

def _deep_link(asin: str, associate_tag: str, marketplace: str) -> str:
    """Official, always-valid affiliate URL — the ?tag= parameter is what Amazon
    tracks. No login and no browser required."""
    return f"https://www.{marketplace}/dp/{asin}?tag={associate_tag}"


def _from_template(template: str, product_url: str, asin: str, associate_tag: str) -> str:
    """Build a link via a network-agnostic template (aggregator redirect, etc.)."""
    from urllib.parse import quote
    return (
        template
        .replace("{url_encoded}", quote(product_url, safe=""))
        .replace("{url}", product_url)
        .replace("{asin}", asin)
        .replace("{tag}", associate_tag)
    )


async def get_affiliate_link(
    page:          Page,
    product:       dict,
    associate_tag: str,
    marketplace:   str,
) -> str:
    from config import cfg

    asin = product.get("asin") or _extract_asin(product.get("url", ""))
    if not asin:
        log.warning(f"No ASIN found for: {product['title'][:40]}")
        return product.get("url", "")

    # Prefer the tag from the stored (encrypted) Amazon account over the .env default.
    associate_tag = resolve_associate_tag(associate_tag)
    product_url = product.get("url") or _deep_link(asin, associate_tag, marketplace)

    # 1) Network-agnostic template (EarnKaro / Cuelinks / INRDeals, etc.) — wins if set.
    if cfg.affiliate_link_template:
        link = _from_template(cfg.affiliate_link_template, product_url, asin, associate_tag)
        log.info(f"Affiliate link via template: {link}")
        return link

    # 2) Official Amazon deep link (default) — no login, no browser, tracks correctly.
    if cfg.amazon.link_method != "sitestripe":
        link = _deep_link(asin, associate_tag, marketplace)
        log.info(f"Affiliate deep link: {link}")
        return link

    # 3) Legacy SiteStripe method (requires a logged-in Amazon session) ───────
    log.step(f"Getting affiliate link via SiteStripe — ASIN: {asin}")
    await page.goto(product["url"], wait_until="domcontentloaded")
    await _delay(2000, 3000)

    # Method 1: SiteStripe JSON API (fastest when logged in)
    try:
        short_url = await page.evaluate(
            """async (args) => {
                const res = await fetch(
                    `https://www.${args.marketplace}/associates/sitestripe/getShortUrl?asin=${args.asin}&associateTag=${args.tag}`,
                    { credentials: 'include' }
                );
                if (!res.ok) throw new Error('SiteStripe API ' + res.status);
                const data = await res.json();
                return data.shortUrl || null;
            }""",
            {"marketplace": marketplace, "asin": asin, "tag": associate_tag},
        )
        if short_url and short_url.startswith("http"):
            log.success(f"Affiliate link via SiteStripe API: {short_url}")
            return short_url
    except Exception:
        log.info("SiteStripe API unavailable — trying toolbar...")

    # Method 2: Click SiteStripe "Text" button
    try:
        btn = await page.query_selector("#sw-gtc, [data-sitestripe-feature='text']")
        if btn:
            await btn.click()
            await _delay(1000, 1500)
            inp = await page.query_selector(".sw-short-url-text, input[id*='shortUrl']")
            if inp:
                link = await inp.input_value()
                if link:
                    return link
    except Exception:
        pass

    # Method 3: Manual tag fallback
    fallback = f"https://www.{marketplace}/dp/{asin}?tag={associate_tag}"
    log.info(f"Fallback affiliate URL: {fallback}")
    return fallback
