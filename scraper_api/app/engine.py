"""The scrape engine: strategy → cache → route plan (http / browser, direct / proxy) → classify →
bounded retry → extract → cache + record every attempt. Never raises to the API layer."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from . import cache, config, metrics, security, store
from .browser import pool as browser_pool
from .classifier import PERMANENT, ROUTE_CHANGE, TRANSIENT, Outcome, backoff, classify, parse_retry_after
from .proxies import Proxy, manager as proxies
from .strategies import Strategies, Strategy

strategies = Strategies()
_GLOBAL = asyncio.Semaphore(config.GLOBAL_CONCURRENCY)
_DOMAIN_SEMS: Dict[str, asyncio.Semaphore] = {}


def new_request_id() -> str:
    return "req_" + format(int(time.time() * 1000), "x") + uuid.uuid4().hex[:10]


def _domain_sem(s: Strategy) -> asyncio.Semaphore:
    if s.domain not in _DOMAIN_SEMS:
        _DOMAIN_SEMS[s.domain] = asyncio.Semaphore(s.concurrency)
    return _DOMAIN_SEMS[s.domain]


async def _check_redirect(request: httpx.Request) -> None:
    why = await asyncio.to_thread(security.host_blocked, request.url.host, request.url.port or 443)
    if why:
        raise security.UrlRejected(f"redirect to blocked target: {why}")


async def http_fetch(url: str, timeout: int, proxy: Optional[Proxy]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(proxy=proxy.url() if proxy else None, headers=config.HEADERS,
                                     timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                                     follow_redirects=True, max_redirects=config.MAX_REDIRECTS,
                                     event_hooks={"request": [_check_redirect]}) as c:
            async with c.stream("GET", url) as r:
                ctype = r.headers.get("content-type", "")
                if ctype and not any(t in ctype.lower() for t in config.ALLOWED_CONTENT_TYPES):
                    return {"status": r.status_code, "html": "", "final_url": str(r.url), "content_type": ctype,
                            "headers": dict(r.headers), "error": None, "bad_content": f"content type {ctype[:60]}"}
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > config.MAX_BYTES:
                        break
                enc = r.encoding or "utf-8"
                return {"status": r.status_code, "html": bytes(buf).decode(enc, "ignore"), "final_url": str(r.url),
                        "content_type": ctype, "headers": dict(r.headers), "error": None}
    except security.UrlRejected as e:
        return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {},
                "error": None, "blocked": str(e)}
    except Exception as e:                            # noqa: BLE001 — timeouts, connect errors …
        msg = f"http: {type(e).__name__}"
        if proxy is None and str(e):
            msg += f": {str(e)[:120]}"
        return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {}, "error": msg}


async def scrape(url: str, *, api_key_id: int, render: bool = False, fields: Optional[List[str]] = None,
                 cache_ttl: Optional[int] = None, timeout: Optional[int] = None, country: Optional[str] = None,
                 use_proxy: Optional[bool] = None, job_id: Optional[str] = None,
                 request_id: Optional[str] = None) -> Dict[str, Any]:
    t_start = time.monotonic()
    rid = request_id or new_request_id()
    fields = [f.strip().lower() for f in (fields or ["html", "title", "images", "metadata", "jsonld", "price"])]
    host = urlsplit(url).hostname or ""
    s = strategies.for_host(host)
    ttl = s.cache_ttl if cache_ttl is None else max(0, int(cache_ttl))
    tmo = max(3, min(int(timeout or s.timeout), 120))
    base = {"request_id": rid, "url": url, "domain": s.domain, "cached": False}

    def done(outcome: Outcome, reason: str, status=None, final_url=None, mode=None, attempts=0, data=None,
             ctype="") -> Dict[str, Any]:
        return {**base, "final_url": final_url or url, "status_code": status, "content_type": ctype,
                "strategy": mode, "outcome": outcome.value, "reason": reason, "attempts": attempts,
                "duration_ms": int((time.monotonic() - t_start) * 1000), "data": data}

    try:
        await asyncio.to_thread(security.validate_url, url)
    except security.UrlRejected as e:
        metrics.requests.labels(metrics.dom(s.domain), "none", Outcome.BLOCKED_TARGET.value).inc()
        return done(Outcome.BLOCKED_TARGET, str(e))

    ck = cache.cache_key(url, render, fields)
    if ttl > 0:
        hit = await cache.get(metrics.redis, ck)
        if hit:
            metrics.cache_hits.inc()
            await store.usage_add(api_key_id, cache_hit=True)
            await store.attempt_log({"request_id": rid, "job_id": job_id, "api_key_id": api_key_id, "domain": s.domain,
                                     "attempt": 0, "worker": config.WORKER_ID, "mode": "cache", "outcome": "SUCCESS",
                                     "cache_hit": True, "duration_ms": 0, "response_bytes": 0})
            return {**hit, "request_id": rid, "cached": True,
                    "duration_ms": int((time.monotonic() - t_start) * 1000)}

    want_proxy = s.residential if use_proxy is None else bool(use_proxy)
    if s.residential and proxies.healthy_count() == 0:
        # this site blocks datacenter IPs: without a proxy every hit is wasted → answer at once
        metrics.requests.labels(metrics.dom(s.domain), "none", Outcome.NEEDS_RESIDENTIAL.value).inc()
        return done(Outcome.NEEDS_RESIDENTIAL, "site blocks datacenter IPs and no healthy proxy is configured")

    mode = "browser" if (render or s.mode == "browser") else "http"
    tried_proxies: set = set()
    proxy: Optional[Proxy] = None
    last = {"outcome": Outcome.NETWORK_ERROR, "reason": "not attempted", "raw": None}
    async with _GLOBAL, _domain_sem(s):
        for attempt in range(1, s.max_attempts + 1):
            if want_proxy and proxy is None:
                proxy = await proxies.lease(country, exclude=tried_proxies)
                if proxy is None and s.residential:
                    if last["raw"] is None:
                        last = {"outcome": Outcome.NEEDS_RESIDENTIAL, "reason": "no healthy proxy available", "raw": None}
                    break
            t0 = time.monotonic()
            if mode == "browser":
                raw = await browser_pool.fetch(url, tmo, proxy.playwright() if proxy else None, s.wait_for)
            else:
                raw = await http_fetch(url, tmo, proxy)
            ms = int((time.monotonic() - t0) * 1000)
            if raw.get("blocked"):
                outcome, reason = Outcome.BLOCKED_TARGET, raw["blocked"]
            elif raw.get("bad_content"):
                outcome, reason = Outcome.BAD_CONTENT, raw["bad_content"]
            else:
                outcome, reason = classify(raw.get("status"), raw.get("html", ""), raw.get("content_type", ""), s,
                                           raw.get("error"))
            nbytes = len((raw.get("html") or "").encode("utf-8", "ignore"))
            metrics.requests.labels(metrics.dom(s.domain), mode, outcome.value).inc()
            metrics.latency.labels(mode).observe(ms / 1000)
            metrics.bandwidth.inc(nbytes)
            await store.attempt_log({"request_id": rid, "job_id": job_id, "api_key_id": api_key_id, "domain": s.domain,
                                     "attempt": attempt, "worker": config.WORKER_ID, "mode": mode,
                                     "proxy_id": proxy.id if proxy else None, "status_code": raw.get("status"),
                                     "outcome": outcome.value, "error": reason if outcome != Outcome.SUCCESS else None,
                                     "duration_ms": ms, "response_bytes": nbytes, "cache_hit": False})
            await store.usage_add(api_key_id, bandwidth=nbytes, browser=(mode == "browser"))
            if proxy:
                await proxies.release(proxy, outcome == Outcome.SUCCESS, ms)
            last = {"outcome": outcome, "reason": reason, "raw": raw}
            if outcome == Outcome.SUCCESS or outcome in PERMANENT or attempt == s.max_attempts:
                break
            if outcome in ROUTE_CHANGE:
                # never hammer a block: change the route or stop
                if mode == "http" and s.mode == "auto" and not render:
                    mode = "browser"
                elif proxy is not None:
                    tried_proxies.add(proxy.id)
                    proxy = None
                else:
                    break
            elif proxy is not None and outcome == Outcome.NETWORK_ERROR:
                tried_proxies.add(proxy.id)
                proxy = None
            elif proxy is not None:
                proxy = None                          # lease again (may pick the same healthy proxy)
            await asyncio.sleep(backoff(attempt, parse_retry_after((raw.get("headers") or {}).get("retry-after"))))

    raw = last["raw"] or {}
    outcome: Outcome = last["outcome"]
    if outcome != Outcome.SUCCESS:
        return done(outcome, last["reason"], raw.get("status"), raw.get("final_url"), mode, attempt if raw else 0)
    from .extractors import extract
    data = await asyncio.to_thread(extract, raw.get("html", ""), raw.get("final_url") or url, fields)
    res = done(outcome, last["reason"], raw.get("status"), raw.get("final_url"), mode, attempt, data,
               raw.get("content_type", ""))
    await cache.put(metrics.redis, ck, {k: v for k, v in res.items() if k not in ("request_id", "duration_ms")}, ttl)
    return res
