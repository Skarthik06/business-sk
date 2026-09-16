"""
performance/cuelinks.py — Cuelinks reporting client (the attribution data source).

Cuelinks is the primary aggregator (every non-Amazon store monetises through it). This pulls
its earnings report so the panel shows REAL money, not predictions. Two modes:

  • API SYNC   — when CUELINKS_API_TOKEN is set, fetch the report from the Cuelinks reports API
                 and record it. (URL overridable via CUELINKS_API_URL.)
  • MANUAL     — otherwise, the panel accepts a report export (CSV/JSON) via /api/networks/import.

Defensive by design: the exact report schema can vary, so we map a range of common field names
and skip anything unparseable — a bad row never breaks the sync.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import requests

from performance.networks import network_store
from utils.logger import log

_API_URL = os.getenv("CUELINKS_API_URL", "https://www.cuelinks.com/api/v2/reports.json").strip()


def _token() -> str:
    return os.getenv("CUELINKS_API_TOKEN", "").strip()


def configured() -> bool:
    return bool(_token())


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _map_row(r: dict) -> dict | None:
    """Map one Cuelinks report record to our network_earnings row shape (best-effort)."""
    date = _pick(r, "date", "transaction_date", "click_date", "period_date")
    if not date:
        return None
    return {
        "period_date":   str(date)[:10],
        "product_asin":  _pick(r, "product_id", "asin", "sku"),
        "product_title": _pick(r, "product_name", "product", "title"),
        "clicks":        _pick(r, "clicks", "click_count"),
        "orders":        _pick(r, "orders", "sales", "transactions", "order_count"),
        "earnings":      _pick(r, "earnings", "commission", "amount", "payout", "cashback"),
        "currency":      _pick(r, "currency") or "INR",
    }


def sync(days: int = 30) -> dict:
    """Fetch the last `days` of Cuelinks earnings and record them. Returns a status dict; never
    raises (the panel stays usable even if Cuelinks is down or the token is missing)."""
    if not configured():
        return {"ok": False, "manual": True,
                "error": "CUELINKS_API_TOKEN not set — use manual import instead."}
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    try:
        resp = requests.get(_API_URL, timeout=30,
                            headers={"Authorization": f"Token token={_token()}"},
                            params={"fromDate": start.isoformat(), "toDate": end.isoformat()})
        if not resp.ok:
            return {"ok": False, "error": f"cuelinks API {resp.status_code}: {resp.text[:120]}"}
        data = resp.json()
        # the report array lives under a few possible keys depending on the account/plan
        records = (data.get("reports") or data.get("data") or data.get("transactions")
                   or (data if isinstance(data, list) else [])) or []
        rows = [m for m in (_map_row(r) for r in records if isinstance(r, dict)) if m]
        res = network_store.record_earnings("cuelinks", rows, source="api")
        return {"ok": True, "fetched": len(records), **res}
    except Exception as e:
        log.warning(f"[cuelinks] sync failed: {e}")
        return {"ok": False, "error": str(e)[:160]}
