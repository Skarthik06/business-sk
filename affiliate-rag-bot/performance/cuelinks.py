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


# ── live campaigns → market catalogue overlay ─────────────────────────────────
def _aov_band(v) -> str:
    """Turn an average-order-value number into the panel's band label."""
    try:
        n = float(v)
    except Exception:
        return ""
    lo, hi = int(n * 0.6), int(n * 1.6)
    def k(x):
        return f"₹{round(x/1000,1)}k" if x >= 1000 else f"₹{x}"
    return f"{k(lo)}–{k(hi)}"


def _map_campaign(r: dict) -> dict | None:
    name = _pick(r, "name", "merchant", "campaign", "title")
    if not name:
        return None
    cid = _pick(r, "id", "campaign_id", "slug") or name.lower().replace(" ", "")
    payout = _pick(r, "payout", "commission", "commission_rate", "max_payout", "epc")
    try:
        commission = round(float(str(payout).replace("%", "").strip()), 1) if payout is not None else None
    except Exception:
        commission = None
    return {
        "id": str(cid).lower(),
        "name": str(name)[:40],
        "category": str(_pick(r, "category", "category_name", "vertical") or "Marketplace"),
        "commission": commission,
        "epc": _pick(r, "epc", "epc_7d", "epc_30d"),
        "aov": _aov_band(_pick(r, "aov", "average_order_value")) or "",
        "cookie": _pick(r, "cookie_days", "cookie_duration", "cookie") or 30,
        "status": _pick(r, "status", "access_status") or "available",
        "note": (str(_pick(r, "description", "note") or "")[:80]),
        "source": "cuelinks",
    }


def fetch_campaigns(q: str = "", sort: str = "epc", limit: int = 60) -> dict:
    """GET /campaigns — the LIVE Cuelinks market catalogue (normalised to the panel's shape).
    Returns {ok, markets:[...]} or a status dict. Never raises."""
    if not configured():
        return {"ok": False, "error": "CUELINKS_API_TOKEN not set."}
    try:
        params = {"sort": sort, "per_page": max(1, min(limit, 500))}
        if q:
            params["q"] = q
        resp = requests.get(f"{_API_BASE}/campaigns", headers=_headers(), timeout=_TIMEOUT, params=params)
        if not resp.ok:
            return {"ok": False, "error": f"cuelinks v3 {resp.status_code}: {resp.text[:160]}"}
        records = _records(resp.json())
        markets = [m for m in (_map_campaign(r) for r in records if isinstance(r, dict)) if m and m.get("commission") is not None]
        return {"ok": True, "count": len(markets), "markets": markets}
    except Exception as e:
        log.warning(f"[cuelinks] fetch_campaigns failed: {e}")
        return {"ok": False, "error": str(e)[:160]}


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
