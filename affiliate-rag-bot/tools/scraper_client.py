"""
tools/scraper_client.py — client for the Business-SK Scraper API (scraper_api service).

Env: SCRAPER_API_URL (e.g. http://scraper_api:8200) + SCRAPER_API_KEY. Unset ⇒ not used.
Never raises; never logs the key.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

_HEALTH = {"t": 0.0, "data": {}}


def _base() -> str:
    return (os.getenv("SCRAPER_API_URL") or "").strip().rstrip("/")


def _key() -> str:
    return (os.getenv("SCRAPER_API_KEY") or "").strip()


def available() -> bool:
    return bool(_base() and _key())


def _get(path: str, timeout: float) -> dict:
    req = urllib.request.Request(_base() + path, headers={"X-API-Key": _key(), "User-Agent": "sk-affiliate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health(max_age: float = 30.0) -> dict:
    """Cached /v1/health (30 s)."""
    if not available():
        return {}
    if time.time() - _HEALTH["t"] < max_age:
        return _HEALTH["data"]
    try:
        data = _get("/v1/health", 5)
    except Exception:
        data = {}
    _HEALTH.update(t=time.time(), data=data)
    return data


def residential_ready() -> bool:
    """True when the Scraper API has a route that reaches sites rejecting the server's IP:
    an online worker (laptop / phone / remote) or a healthy proxy."""
    return bool(health().get("residential_ready"))


def scrape_html(url: str, timeout: int = 45) -> dict:
    """{"ok", "html", "outcome", "reason", "cached"} for one page. Never raises."""
    if not available():
        return {"ok": False, "outcome": "UNAVAILABLE", "reason": "scraper API not configured"}
    q = urllib.parse.urlencode({"url": url, "extract": "html", "timeout": min(120, max(3, timeout))})
    try:
        r = _get("/v1/scrape?" + q, 150)            # the engine may fail over across routes (≤ max_time)
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "outcome": "UNAVAILABLE", "reason": f"{type(e).__name__}"}
    ok = r.get("outcome") == "SUCCESS" and bool((r.get("data") or {}).get("html"))
    return {"ok": ok, "html": (r.get("data") or {}).get("html", "") if ok else "",
            "outcome": r.get("outcome"), "reason": r.get("reason"), "cached": r.get("cached", False),
            "via": "scraper_api"}
