"""
performance/cuelinks.py — Cuelinks v3 API client (earnings sync + live campaigns + link convert).

Cuelinks is the primary aggregator (every non-Amazon store monetises through one redirect). This
talks to the Cuelinks **Public API v3** (`/pub_api/v3`, the v2 report API retires 31 Oct 2026):

  • ping()            — verify the key + identity (@ping).
  • sync()            — GET /reports/performance → record daily clicks/orders/commission (earnings).
  • fetch_campaigns() — GET /campaigns → the LIVE market catalogue (payout %, EPC) for the panel.
  • convert_link()    — POST /links/convert → turn any product URL into a tracked clnk.in link.

Auth: header `Authorization: Token <key>`, key from CUELINKS_API_TOKEN (or CUELINKS_API_KEY, the
name the Cuelinks MCP uses — both accepted). Defensive by design: the exact report/campaign schema
can vary, so we map a range of field names and skip anything unparseable — a bad row never breaks a
sync, and a missing key degrades to "manual import" rather than an error.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import requests

from performance.networks import network_store
from utils.logger import log

_API_BASE = os.getenv("CUELINKS_API_URL", "https://developers.cuelinks.com/pub_api/v3").rstrip("/")
_TIMEOUT = 30


def _token() -> str:
    return (os.getenv("CUELINKS_API_TOKEN") or os.getenv("CUELINKS_API_KEY") or "").strip()


def configured() -> bool:
    return bool(_token())


def _headers() -> dict:
    return {"Authorization": f"Token {_token()}", "Accept": "application/json"}


def _pick(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _records(data) -> list:
    """Pull the row array out of whatever wrapper the v3 endpoint uses (defensive)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "reports", "report", "results", "rows", "transactions", "campaigns", "performance"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        # some responses nest under {"data": {"rows": [...]}}
        inner = data.get("data")
        if isinstance(inner, dict):
            for k in ("rows", "results", "items", "performance"):
                if isinstance(inner.get(k), list):
                    return inner[k]
    return []


# ── health ───────────────────────────────────────────────────────────────────
def ping() -> dict:
    """Verify the key + identity (v3 @ping). Never raises."""
    if not configured():
        return {"ok": False, "configured": False, "error": "CUELINKS_API_TOKEN not set."}
    try:
        r = requests.get(f"{_API_BASE}/ping", headers=_headers(), timeout=_TIMEOUT)
        if r.ok:
            return {"ok": True, "configured": True, "identity": (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}
        return {"ok": False, "configured": True, "status": r.status_code, "error": r.text[:160]}
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)[:160]}


# ── earnings sync (reports/performance) ───────────────────────────────────────
def _map_report_row(r: dict) -> dict | None:
    date = _pick(r, "date", "day", "period_date", "transaction_date", "click_date")
    if not date:
        return None
    return {
        "period_date":   str(date)[:10],
        "product_asin":  _pick(r, "product_id", "asin", "sku", "campaign_id"),
        "product_title": _pick(r, "campaign", "campaign_name", "merchant", "product_name", "title"),
        "clicks":        _pick(r, "clicks", "click_count"),
        "orders":        _pick(r, "transactions", "orders", "sales", "conversions", "transaction_count"),
        "earnings":      _pick(r, "commission", "earnings", "amount", "payout", "cashback", "revenue"),
        "currency":      _pick(r, "currency", "payout_currency") or "INR",
    }


def sync(days: int = 30) -> dict:
    """Fetch the last `days` of Cuelinks performance (GET /reports/performance) and record it.
    Returns a status dict; never raises (the panel stays usable even if Cuelinks is down)."""
    if not configured():
        return {"ok": False, "manual": True,
                "error": "CUELINKS_API_TOKEN not set — use manual import instead."}
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    try:
        resp = requests.get(f"{_API_BASE}/reports/performance", headers=_headers(), timeout=_TIMEOUT,
                            params={"from_date": start.isoformat(), "to_date": end.isoformat(), "per_page": 500})
        if not resp.ok:
            return {"ok": False, "error": f"cuelinks v3 {resp.status_code}: {resp.text[:160]}"}
        records = _records(resp.json())
        rows = [m for m in (_map_report_row(r) for r in records if isinstance(r, dict)) if m]
        res = network_store.record_earnings("cuelinks", rows, source="api")
        return {"ok": True, "fetched": len(records), **res}
    except Exception as e:
        log.warning(f"[cuelinks] sync failed: {e}")
        return {"ok": False, "error": str(e)[:160]}


# ── live campaigns (GET /campaigns) ───────────────────────────────────────────
# The catalogue has 28k+ campaigns and its generic listing is alphabetical CPC noise, so a raw
# dump is useless. Instead we ENRICH the curated market list: query each market by name, match the
# right campaign, and pull its LIVE payout %, EPC and join status. Best of both — a clean, curated
# grid carrying real Cuelinks numbers.
import re as _re


def _norm(s: str) -> str:
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_percent(payout_type: str) -> bool:
    pt = (payout_type or "").lower()
    return "%" in pt or "sale" in pt or "order" in pt


def _best_campaign_match(name: str, results: list[dict]) -> dict | None:
    """Pick the campaign that best matches a curated market name (a fuzzy `q` search returns many —
    e.g. 'boAt' also matches 'Boattrader'). Requires a solid name match; prefers % payouts."""
    nn = _norm(name)
    best, best_score = None, -1
    for c in results:
        if not isinstance(c, dict):
            continue
        ncn = _norm(c.get("name") or "")
        if not ncn:
            continue
        if ncn == nn:
            score = 100
        elif ncn.startswith(nn) or nn.startswith(ncn):
            score = 60
        elif nn in ncn or ncn in nn:
            score = 30
        else:
            score = 0
        if _is_percent(c.get("payout_type") or ""):
            score += 20
        if score > best_score:
            best_score, best = score, c
    return best if best_score >= 30 else None


def fetch_campaigns(q: str = "", limit: int = 20) -> dict:
    """GET /campaigns — raw search by name (for a live merchant lookup). Returns {ok, markets:[...]}."""
    if not configured():
        return {"ok": False, "error": "CUELINKS_API_TOKEN not set."}
    try:
        params = {"per_page": max(1, min(limit, 100))}
        if q:
            params["q"] = q
        resp = requests.get(f"{_API_BASE}/campaigns", headers=_headers(), timeout=_TIMEOUT, params=params)
        if not resp.ok:
            return {"ok": False, "error": f"cuelinks v3 {resp.status_code}: {resp.text[:160]}"}
        out = []
        for c in _records(resp.json()):
            if not isinstance(c, dict):
                continue
            cats = c.get("categories")
            out.append({"id": str(c.get("id") or "").lower(), "name": (c.get("name") or "")[:40],
                        "category": (cats[0].get("name") if isinstance(cats, list) and cats and isinstance(cats[0], dict) else ""),
                        "payout": c.get("payout"), "payout_type": c.get("payout_type"),
                        "epc": c.get("epc_7d"), "status": c.get("access_status")})
        return {"ok": True, "count": len(out), "markets": out}
    except Exception as e:
        log.warning(f"[cuelinks] fetch_campaigns failed: {e}")
        return {"ok": False, "error": str(e)[:160]}


def enrich_markets(markets: list[dict]) -> dict:
    """Enrich curated markets with LIVE Cuelinks data (payout %, EPC, join status) by matching each
    by name. Percentage payouts override the curated commission; category/AOV/note are kept. Returns
    {ok, markets, matched}. Never raises — a market with no confident match keeps its curated values."""
    if not configured():
        return {"ok": False, "error": "CUELINKS_API_TOKEN not set."}
    out, matched = [], 0
    for m in markets:
        merged = dict(m)
        try:
            resp = requests.get(f"{_API_BASE}/campaigns", headers=_headers(), timeout=_TIMEOUT,
                                params={"q": m.get("name", ""), "per_page": 6})
            if resp.ok:
                c = _best_campaign_match(m.get("name", ""), _records(resp.json()))
                if c:
                    matched += 1
                    merged["live_matched"] = True
                    merged["join_status"] = c.get("access_status") or "open"
                    merged["campaign_id"] = c.get("id")
                    if c.get("epc_7d") not in (None, "", "0.0"):
                        merged["epc"] = c.get("epc_7d")
                    if _is_percent(c.get("payout_type") or "") and c.get("payout") not in (None, ""):
                        try:
                            merged["commission"] = round(float(str(c["payout"]).replace("%", "").strip()), 1)
                        except Exception:
                            pass
        except Exception:
            pass
        out.append(merged)
    return {"ok": True, "markets": out, "matched": matched}


# ── live offers / deals (the Cuelinks post source) ────────────────────────────
def _map_offer(r: dict) -> dict | None:
    """Map a Cuelinks offer to a normalised DEAL shape (also usable as a pipeline 'product')."""
    if not isinstance(r, dict) or not r.get("title"):
        return None
    cats = [c.get("name") for c in (r.get("categories") or []) if isinstance(c, dict) and c.get("name")]
    return {
        "id":          str(r.get("id") or ""),
        "title":       (r.get("title") or "").strip(),
        "merchant":    (r.get("campaign_name") or "").strip(),
        "category":    cats[0] if cats else "",
        "categories":  cats,
        "discount":    r.get("percent_off"),
        "code":        (r.get("coupon_code") or "").strip(),
        "offer_type":  r.get("offer_type") or "deal",
        "url":         r.get("tracking_url") or "",
        "description": (r.get("description") or "").strip(),
        "ends":        r.get("end_date"),
        "source":      "cuelinks",
    }


def fetch_offers(categories: list[str] | None = None, merchants: list[str] | None = None,
                 limit: int = 40, pages: int = 6) -> dict:
    """GET /offers — the LIVE Cuelinks deals/coupons. Optionally keep only offers in the given
    category names AND/OR from the given MERCHANTS (store names — normalised substring match, so
    'Nykaa' matches 'Nykaa Beauty'/'Nykaa Fashion', 'AJIO' matches 'Ajio Gram', etc.). Paginates.
    Never raises. Returns {ok, count, deals:[...]}."""
    if not configured():
        return {"ok": False, "error": "CUELINKS_API_TOKEN not set.", "deals": []}
    want_cat = {c.strip().lower() for c in (categories or []) if c and c.strip()}
    want_mer = [_norm(m) for m in (merchants or []) if m and m.strip()]
    out: list[dict] = []
    try:
        for pg in range(1, max(1, min(pages, 10)) + 1):
            resp = requests.get(f"{_API_BASE}/offers", headers=_headers(), timeout=_TIMEOUT,
                                params={"per_page": 100, "page": pg, "status": "live"})
            if not resp.ok:
                if pg == 1:
                    return {"ok": False, "error": f"cuelinks v3 {resp.status_code}: {resp.text[:160]}", "deals": []}
                break
            recs = _records(resp.json())
            if not recs:
                break
            for r in recs:
                d = _map_offer(r)
                if not d:
                    continue
                if want_cat and not ({c.lower() for c in d["categories"]} & want_cat):
                    continue
                if want_mer:
                    nm = _norm(d.get("merchant", ""))
                    if not any(w and (w in nm or nm in w) for w in want_mer):
                        continue
                out.append(d)
            if len(out) >= limit or len(recs) < 100:
                break
        return {"ok": True, "count": len(out), "deals": out[:limit]}
    except Exception as e:
        log.warning(f"[cuelinks] fetch_offers failed: {e}")
        return {"ok": False, "error": str(e)[:160], "deals": []}


def available_merchants(pages: int = 6) -> dict:
    """The distinct merchants that currently have LIVE offers in the Cuelinks feed — so the panel
    can show which selected stores can actually generate a deals post right now. Never raises."""
    if not configured():
        return {"ok": False, "merchants": []}
    seen: dict[str, int] = {}
    try:
        for pg in range(1, max(1, min(pages, 10)) + 1):
            resp = requests.get(f"{_API_BASE}/offers", headers=_headers(), timeout=_TIMEOUT,
                                params={"per_page": 100, "page": pg, "status": "live"})
            if not resp.ok:
                break
            recs = _records(resp.json())
            if not recs:
                break
            for r in recs:
                m = (r.get("campaign_name") or "").strip()
                if m:
                    seen[m] = seen.get(m, 0) + 1
            if len(recs) < 100:
                break
        return {"ok": True, "merchants": sorted(seen.keys()), "counts": seen}
    except Exception as e:
        log.warning(f"[cuelinks] available_merchants failed: {e}")
        return {"ok": False, "merchants": []}


# ── link conversion (monetise any URL) ────────────────────────────────────────
def convert_link(url: str, subids: list[str] | None = None, channel_id: str | None = None) -> dict:
    """POST /links/convert — turn a product URL into a tracked clnk.in affiliate link. Never raises."""
    if not configured():
        return {"ok": False, "error": "CUELINKS_API_TOKEN not set."}
    if not url:
        return {"ok": False, "error": "url required"}
    body: dict = {"url": url}
    for i, s in enumerate((subids or [])[:5], start=1):
        if s:
            body[f"subid{i}"] = s
    if channel_id:
        body["channel_id"] = channel_id
    try:
        resp = requests.post(f"{_API_BASE}/links/convert", headers={**_headers(), "Content-Type": "application/json"},
                             json=body, timeout=_TIMEOUT)
        if not resp.ok:
            return {"ok": False, "error": f"cuelinks v3 {resp.status_code}: {resp.text[:160]}"}
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        link = _pick(data, "tracking_url", "clnk_url", "affiliate_url", "url", "short_url") or _pick(data.get("data", {}) if isinstance(data.get("data"), dict) else {}, "tracking_url", "url")
        return {"ok": bool(link), "tracking_url": link, "raw": data}
    except Exception as e:
        log.warning(f"[cuelinks] convert_link failed: {e}")
        return {"ok": False, "error": str(e)[:160]}
