"""The scrape engine (Intelligent Multi-Route): strategy → cache → STRATEGY ENGINE picks the healthiest
eligible route (direct http/browser · laptop/phone/remote workers · proxies) → classify → record route
health + circuit → bounded failover (attempt AND time budget) → extract → cache. Never raises."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from . import cache, config, metrics, routing, security, store, workers
from .browser import pool as browser_pool
from .classifier import PERMANENT, ROUTE_CHANGE, Outcome, backoff, classify, parse_retry_after
from .proxies import Proxy, manager as proxies
from .routing import Route
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


async def build_routes(s: Strategy, render: bool, use_proxy: Optional[bool], host: str = "") -> List[Route]:
    """Every route that could serve this request right now (health/circuits are applied by the ranker)."""
    modes = ["browser"] if (render or s.mode == "browser") else (["http"] if s.mode == "http" else ["http", "browser"])
    routes: List[Route] = []
    if use_proxy is not True:
        routes += [Route(f"direct_{m}", "direct", m) for m in modes]
        for w in await workers.list_workers():
            if w.get("status") == "paused" or not workers.allows(w, host):
                continue                              # paused (data cap / battery) or site not allowed
            avail = {"online": 1.0, "stale": 0.5}.get(w["state"], 0.0)
            routes += [Route(f"worker_{m}:{w['worker_id']}", "worker", m, avail, w)
                       for m in modes if m in (w.get("capabilities") or [])]
    if use_proxy is not False:
        for px in proxies.usable():
            routes += [Route(f"proxy_{m}:{px.id}", "proxy", m, 1.0, px) for m in modes]
    return routes


async def execute(route: Route, url: str, timeout: int, s: Strategy, attempt_id: str) -> Dict[str, Any]:
    if route.kind == "worker":
        return await workers.dispatch(route.ref["worker_id"], attempt_id, url, route.mode, timeout)
    px: Optional[Proxy] = route.ref if route.kind == "proxy" else None
    if px:
        await proxies.acquire(px)
    t0 = time.monotonic()
    raw: Dict[str, Any] = {}
    try:
        if route.mode == "browser":
            raw = await browser_pool.fetch(url, timeout, px.playwright() if px else None, s.wait_for)
        else:
            raw = await http_fetch(url, timeout, px)
        return raw
    finally:
        if px:
            await proxies.release(px, bool(raw) and raw.get("status") is not None and not raw.get("error")
                                  and int(raw.get("status") or 0) < 500, int((time.monotonic() - t0) * 1000))


def prefer_kind(ranked: List[tuple], prefer: Optional[str]) -> List[tuple]:
    """Put the preferred device kind's worker routes first (the phone when you work on the phone,
    the laptop on the laptop); everything else keeps its score order as the fallback."""
    if not prefer:
        return ranked
    return sorted(ranked, key=lambda sr: 0 if (sr[1].kind == "worker" and (sr[1].ref or {}).get("kind") == prefer) else 1)


async def scrape(url: str, *, api_key_id: int, render: bool = False, fields: Optional[List[str]] = None,
                 cache_ttl: Optional[int] = None, timeout: Optional[int] = None, country: Optional[str] = None,
                 use_proxy: Optional[bool] = None, job_id: Optional[str] = None,
                 request_id: Optional[str] = None, prefer: Optional[str] = None) -> Dict[str, Any]:
    t_start = time.monotonic()
    rid = request_id or new_request_id()
    fields = [f.strip().lower() for f in (fields or ["html", "title", "images", "metadata", "jsonld", "price"])]
    host = urlsplit(url).hostname or ""
    s = strategies.for_host(host)
    ttl = s.cache_ttl if cache_ttl is None else max(0, int(cache_ttl))
    tmo = max(3, min(int(timeout or s.timeout), 120))
    base = {"request_id": rid, "url": url, "domain": s.domain, "cached": False}
    tried: List[Dict[str, Any]] = []

    def done(outcome: Outcome, reason: str, status=None, final_url=None, route=None, data=None, ctype="") -> Dict[str, Any]:
        return {**base, "final_url": final_url or url, "status_code": status, "content_type": ctype,
                "strategy": (route.mode if route else None), "route": (route.id if route else None),
                "outcome": outcome.value, "reason": reason, "attempts": len(tried), "routes_tried": tried,
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
                                     "attempt": 0, "worker": config.WORKER_ID, "mode": "cache", "route": "cache",
                                     "outcome": "SUCCESS", "cache_hit": True, "duration_ms": 0, "response_bytes": 0})
            return {**hit, "request_id": rid, "cached": True,
                    "duration_ms": int((time.monotonic() - t_start) * 1000)}

    priors = s.priors()
    routes = await build_routes(s, render, use_proxy, host)
    deadline = t_start + s.max_time
    exclude: set = set()
    retried: Dict[str, int] = {}
    last: Dict[str, Any] = {"outcome": Outcome.NO_ROUTE, "reason": "no eligible route", "raw": None, "route": None}
    async with _GLOBAL, _domain_sem(s):
        for attempt in range(1, s.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining < 3:
                last["reason"] = (last["reason"] + " · time budget exhausted") if tried else "time budget exhausted"
                break
            ranked = prefer_kind(routing.book.rank(s.domain, routes, priors, exclude), prefer)
            if not ranked:
                if not tried:
                    avail = ", ".join(r.id for r in routes) or "none"
                    last = {"outcome": Outcome.NO_ROUTE, "raw": None, "route": None,
                            "reason": f"no eligible route (known routes: {avail}; circuits open or offline)"}
                break
            score, route = ranked[0]
            routing.book.claim_probe(s.domain, route)
            attempt_id = f"{rid}_{attempt}"
            t0 = time.monotonic()
            raw = await execute(route, url, max(3, min(tmo, int(remaining))), s, attempt_id)
            ms = int((time.monotonic() - t0) * 1000)
            if raw.get("blocked"):
                outcome, reason = Outcome.BLOCKED_TARGET, raw["blocked"]
            elif raw.get("bad_content"):
                outcome, reason = Outcome.BAD_CONTENT, raw["bad_content"]
            else:
                outcome, reason = classify(raw.get("status"), raw.get("html", ""), raw.get("content_type", ""), s,
                                           raw.get("error"))
            nbytes = len((raw.get("html") or "").encode("utf-8", "ignore"))
            tried.append({"route": route.id, "score": round(score, 3), "outcome": outcome.value, "ms": ms})
            metrics.requests.labels(metrics.dom(s.domain), route.kind + "_" + route.mode, outcome.value).inc()
            metrics.latency.labels(route.mode).observe(ms / 1000)
            metrics.bandwidth.inc(nbytes)
            await routing.book.record(s.domain, route, outcome, ms, priors.get(route.family, {}))
            await store.attempt_log({"request_id": rid, "job_id": job_id, "api_key_id": api_key_id, "domain": s.domain,
                                     "attempt": attempt, "worker": (route.ref or {}).get("worker_id") if route.kind == "worker"
                                     else config.WORKER_ID, "mode": route.mode, "route": route.id,
                                     "proxy_id": route.ref.id if route.kind == "proxy" else None,
                                     "status_code": raw.get("status"), "outcome": outcome.value,
                                     "error": reason if outcome != Outcome.SUCCESS else None, "duration_ms": ms,
                                     "response_bytes": nbytes, "cache_hit": False,
                                     "worker_ip": raw.get("worker_ip") or ((route.ref or {}).get("ip")
                                                                           if route.kind == "worker" else None)})
            await store.usage_add(api_key_id, bandwidth=nbytes, browser=(route.mode == "browser"))
            last = {"outcome": outcome, "reason": reason, "raw": raw, "route": route}
            if outcome == Outcome.SUCCESS or outcome in PERMANENT:
                break
            # bounded failover: a block / rate limit / dead worker → never the same route again;
            # a network / server error → at most one retry on the same route
            retried[route.id] = retried.get(route.id, 0) + 1
            if outcome in ROUTE_CHANGE or outcome == Outcome.RATE_LIMITED or route.kind == "worker" \
                    or retried[route.id] >= 2:
                exclude.add(route.id)
            wait = backoff(attempt, parse_retry_after((raw.get("headers") or {}).get("retry-after")))
            await asyncio.sleep(max(0.0, min(wait, deadline - time.monotonic() - 3)))

    raw = last["raw"] or {}
    outcome: Outcome = last["outcome"]
    route: Optional[Route] = last["route"]
    if outcome != Outcome.SUCCESS:
        return done(outcome, last["reason"], raw.get("status"), raw.get("final_url"), route)
    from .extractors import extract
    data = await asyncio.to_thread(extract, raw.get("html", ""), raw.get("final_url") or url, fields)
    res = done(outcome, last["reason"], raw.get("status"), raw.get("final_url"), route, data, raw.get("content_type", ""))
    await cache.put(metrics.redis, ck, {k: v for k, v in res.items() if k not in ("request_id", "duration_ms")}, ttl)
    return res
