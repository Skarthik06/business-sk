"""Scraper API — FastAPI gateway (blueprint MVP).

  GET  /v1/scrape          synchronous scrape
  POST /v1/jobs            async job  →  GET /v1/jobs/{id}
  GET  /v1/health          service health (db, redis, browser, proxies, queue)
  GET  /v1/usage           this key's usage (7 days)
  GET  /v1/domains/{d}     effective domain strategy
  GET/POST/DELETE /v1/admin/proxies   proxy registry (credentials never returned)
  GET  /v1/stats           per-domain success / latency (24 h)
  GET  /v1/dashboard       route health, circuits, workers, throughput (the ops dashboard's data)
  POST /v1/workers/heartbeat · /v1/workers/jobs/lease · /v1/workers/jobs/{id}/result
                           remote workers (laptop / phone / any machine) — X-Worker-Token auth;
                           the ONLY paths exposed publicly (Caddy /scraper-worker/*)
  GET  /metrics            Prometheus (internal network only)
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import cache, config, engine, metrics, routing, security, store, workers
from .browser import pool as browser_pool
from .proxies import manager as proxies

Q_HTTP, Q_BROWSER = "scraper:queue:http", "scraper:queue:browser"
_KEYS: Dict[str, Dict[str, Any]] = {}          # key_hash → row (cache of api_keys)
_tasks: list = []


async def _seed_keys() -> None:
    for k in config.API_KEYS:
        await store.upsert_key(security.key_hash(k), "env")


async def _job_worker(n: int) -> None:
    while True:
        try:
            item = await metrics.redis.blpop([Q_BROWSER, Q_HTTP], timeout=5)
            if not item:
                continue
            job = json.loads(item[1])
            await store.job_update(job["id"], status="running")
            res = await engine.scrape(job["url"], api_key_id=job["api_key_id"], render=job.get("render", False),
                                      fields=job.get("extract"), cache_ttl=job.get("cache_ttl"),
                                      timeout=job.get("timeout"), country=job.get("country"), job_id=job["id"])
            await metrics.redis.set(f"scraper:job:{job['id']}", json.dumps(res), ex=config.JOB_RESULT_TTL)
            await store.job_update(job["id"], status="done" if res["outcome"] == "SUCCESS" else "failed",
                                   attempts=res.get("attempts") or 0, outcome=res["outcome"],
                                   error=None if res["outcome"] == "SUCCESS" else res.get("reason"),
                                   finished_at=store.now())
        except asyncio.CancelledError:
            raise
        except Exception:                            # a bad job never kills the worker
            await asyncio.sleep(1)


async def _housekeeping() -> None:
    last_proxy = last_prune = 0.0
    while True:
        try:
            now = time.time()
            if now - last_proxy > config.PROXY_HEALTH_SECS and proxies.proxies:
                last_proxy = now
                await proxies.health_check_all()
            if now - last_prune > 3600:
                last_prune = now
                await asyncio.to_thread(cache.prune)
                await store.prune_attempts(30)
            await browser_pool.close_if_idle()
            metrics.proxies_healthy.set(proxies.healthy_count())
            metrics.browser_active.set(browser_pool.active)
            metrics.queue_depth.labels("http").set(await metrics.redis.llen(Q_HTTP))
            metrics.queue_depth.labels("browser").set(await metrics.redis.llen(Q_BROWSER))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    metrics.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    await store.open_pool()
    await _seed_keys()
    await proxies.load()
    await proxies.seed_from_env()
    await routing.book.load()
    _tasks.extend(asyncio.create_task(_job_worker(i)) for i in range(config.JOB_WORKERS))
    _tasks.append(asyncio.create_task(_housekeeping()))
    yield
    for t in _tasks:
        t.cancel()
    await browser_pool.close()
    await store.close_pool()
    await metrics.redis.aclose()


app = FastAPI(title="Business-SK Scraper API", version="1.0.0", lifespan=lifespan)


async def api_key(x_api_key: str = Header("", alias="X-API-Key")) -> Dict[str, Any]:
    if not x_api_key:
        raise HTTPException(401, "missing X-API-Key")
    h = security.key_hash(x_api_key)
    row = _KEYS.get(h) or await store.key_by_hash(h)
    if not row or row.get("status") != "active":
        raise HTTPException(401, "invalid API key")
    _KEYS[h] = row
    rk = f"scraper:ratelimit:{row['id']}:{int(time.time() // 60)}"
    n = await metrics.redis.incr(rk)
    if n == 1:
        await metrics.redis.expire(rk, 70)
    if n > config.RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})
    return row


def _fields(extract: str) -> list:
    return [f for f in (extract or "").split(",") if f.strip()]


@app.get("/v1/scrape")
async def scrape(url: str = Query(..., max_length=config.MAX_URL_LEN), render: bool = False,
                 extract: str = "html,title,images,metadata,jsonld,price", cache_ttl: Optional[int] = None,
                 timeout: Optional[int] = Query(None, ge=3, le=120), country: Optional[str] = None,
                 proxy: Optional[bool] = None, key: Dict[str, Any] = Depends(api_key)):
    return await engine.scrape(url, api_key_id=key["id"], render=render, fields=_fields(extract),
                               cache_ttl=cache_ttl, timeout=timeout, country=country, use_proxy=proxy)


class JobReq(BaseModel):
    url: str = Field(..., max_length=config.MAX_URL_LEN)
    render: bool = False
    extract: str = "html,title,images,metadata,jsonld,price"
    cache_ttl: Optional[int] = None
    timeout: Optional[int] = Field(None, ge=3, le=120)
    country: Optional[str] = None


@app.post("/v1/jobs", status_code=202)
async def create_job(body: JobReq, key: Dict[str, Any] = Depends(api_key)):
    try:
        await asyncio.to_thread(security.validate_url, body.url)
    except security.UrlRejected as e:
        raise HTTPException(400, str(e))
    jid = "job_" + engine.new_request_id()[4:]
    host = (body.url.split("/")[2] if "://" in body.url else "").split(":")[0]
    s = engine.strategies.for_host(host)
    mode = "browser" if (body.render or s.mode == "browser") else "http"
    await store.job_create(jid, key["id"], body.url, s.domain, mode, body.model_dump())
    await metrics.redis.rpush(Q_BROWSER if mode == "browser" else Q_HTTP,
                              json.dumps({"id": jid, "api_key_id": key["id"], **body.model_dump(),
                                          "extract": _fields(body.extract)}))
    return {"job_id": jid, "status": "queued"}


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str, key: Dict[str, Any] = Depends(api_key)):
    job = await store.job_get(job_id)
    if not job or job["api_key_id"] != key["id"]:
        raise HTTPException(404, "job not found")
    raw = await metrics.redis.get(f"scraper:job:{job_id}")
    return {"job_id": job_id, "status": job["status"], "url": job["url"], "outcome": job["outcome"],
            "error": job["error"], "attempts": job["attempts"],
            "created_at": job["created_at"].isoformat() if job["created_at"] else None,
            "finished_at": job["finished_at"].isoformat() if job["finished_at"] else None,
            "result": json.loads(raw) if raw else None}


@app.get("/v1/health")
async def health():
    out: Dict[str, Any] = {"ok": True}
    try:
        await store.q("select 1", one=True)
        out["db"] = "ok"
    except Exception:
        out["db"], out["ok"] = "down", False
    try:
        await metrics.redis.ping()
        out["redis"] = "ok"
        out["queue"] = {"http": await metrics.redis.llen(Q_HTTP), "browser": await metrics.redis.llen(Q_BROWSER)}
    except Exception:
        out["redis"], out["ok"] = "down", False
    out["browser"] = {"started": browser_pool.started, "active_pages": browser_pool.active}
    out["proxies"] = {"total": len(proxies.proxies), "healthy": proxies.healthy_count()}
    try:
        ws = await workers.list_workers()
    except Exception:
        ws = []
    out["workers"] = {w["worker_id"]: w["state"] for w in ws}
    # a route that can reach sites which reject the server's own IP (a worker or a proxy) is up
    out["residential_ready"] = proxies.healthy_count() > 0 or any(w["state"] == "online" for w in ws)
    return out


@app.get("/v1/usage")
async def usage(key: Dict[str, Any] = Depends(api_key)):
    return {"api_key_id": key["id"], "days": await store.usage_get(key["id"], 7)}


@app.get("/v1/domains/{domain}")
async def domain(domain: str, key: Dict[str, Any] = Depends(api_key)):
    s = engine.strategies.get(domain) or engine.strategies.for_host(domain)
    return {"domain": domain, "configured": engine.strategies.get(domain) is not None, "strategy": s.public()}


@app.get("/v1/stats")
async def stats(hours: int = Query(24, ge=1, le=720), key: Dict[str, Any] = Depends(api_key)):
    rows = await store.domain_stats(hours)
    return {"hours": hours, "domains": [{**r, "p50_ms": int(r["p50_ms"] or 0)} for r in rows]}


class ProxyReq(BaseModel):
    url: str = Field(..., max_length=500)            # scheme://user:pass@host:port — never echoed back
    country: Optional[str] = Field(None, max_length=4)
    provider: str = Field("manual", max_length=40)


@app.get("/v1/admin/proxies")
async def list_proxies(key: Dict[str, Any] = Depends(api_key)):
    return {"proxies": [p.public() for p in proxies.proxies.values()]}


@app.post("/v1/admin/proxies", status_code=201)
async def add_proxy(body: ProxyReq, key: Dict[str, Any] = Depends(api_key)):
    try:
        pid = await proxies.add(body.url, body.country, body.provider)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": pid, "proxy": proxies.proxies[pid].public()}


@app.delete("/v1/admin/proxies/{pid}")
async def delete_proxy(pid: int, key: Dict[str, Any] = Depends(api_key)):
    if pid not in proxies.proxies:
        raise HTTPException(404, "proxy not found")
    await proxies.delete(pid)
    return {"deleted": pid}


@app.get("/metrics", response_class=PlainTextResponse)
async def prom():
    from prometheus_client import generate_latest
    return PlainTextResponse(generate_latest(metrics.registry).decode(), media_type="text/plain; version=0.0.4")


# ── remote workers (laptop / phone / any machine) ─────────────────────────────────────────────
async def worker_auth(x_worker_token: str = Header("", alias="X-Worker-Token")) -> None:
    if not workers.token_ok(x_worker_token):
        raise HTTPException(401, "bad worker token")


class HeartbeatReq(BaseModel):
    worker_id: str = Field(..., max_length=40)
    status: str = Field("healthy", max_length=20)
    capabilities: list = Field(default_factory=lambda: ["http"])
    active_jobs: int = Field(0, ge=0, le=1000)
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    kind: str = Field("remote", max_length=20)
    version: str = Field("", max_length=20)


class LeaseReq(HeartbeatReq):
    max: int = Field(1, ge=1, le=4)
    wait: float = Field(20, ge=0, le=25)


class ResultReq(BaseModel):
    worker_id: str = Field(..., max_length=40)
    status_code: Optional[int] = None
    content_type: str = Field("", max_length=200)
    final_url: str = Field("", max_length=config.MAX_URL_LEN)
    html: str = ""
    gz: bool = False
    error: Optional[str] = Field(None, max_length=500)
    duration_ms: Optional[int] = None


@app.post("/v1/workers/heartbeat", dependencies=[Depends(worker_auth)])
async def worker_heartbeat(body: HeartbeatReq):
    if not workers.valid_id(body.worker_id):
        raise HTTPException(400, "worker_id: letters, digits, . _ - (max 40)")
    await workers.heartbeat(body.worker_id, body.model_dump())
    return {"ok": True, "online_secs": workers.ONLINE_SECS}


@app.post("/v1/workers/jobs/lease", dependencies=[Depends(worker_auth)])
async def worker_lease(body: LeaseReq):
    if not workers.valid_id(body.worker_id):
        raise HTTPException(400, "bad worker_id")
    await workers.heartbeat(body.worker_id, body.model_dump())
    return {"jobs": await workers.lease(body.worker_id, body.max, body.wait)}


@app.post("/v1/workers/jobs/{attempt_id}/result", dependencies=[Depends(worker_auth)])
async def worker_result(body: ResultReq, attempt_id: str = Path(..., max_length=80, pattern=r"^[A-Za-z0-9_]+$")):
    res = await workers.submit(body.worker_id, attempt_id, body.model_dump())
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "rejected"))
    return res


# ── ops dashboard data ─────────────────────────────────────────────────────────────────────────
@app.get("/v1/dashboard")
async def dashboard(key: Dict[str, Any] = Depends(api_key)):
    n = await store.dashboard_numbers()
    req24 = int(n.get("req_24h") or 0)
    ws = await workers.list_workers()
    return {
        "requests_per_min": int(n.get("req_1m") or 0),
        "requests_24h": req24,
        "success_rate_24h": round(int(n.get("ok_24h") or 0) / req24, 3) if req24 else None,
        "browser_requests_24h": int(n.get("browser_24h") or 0),
        "cache_hits_24h": int(n.get("cache_24h") or 0),
        "queue_depth": {"http": await metrics.redis.llen(Q_HTTP), "browser": await metrics.redis.llen(Q_BROWSER)},
        "route_success_24h": [{"route": r["route"], "attempts": r["attempts"],
                               "success_rate": round(int(r["success"] or 0) / r["attempts"], 3) if r["attempts"] else None}
                              for r in n.get("by_route") or []],
        "workers": [{k: w.get(k) for k in ("worker_id", "kind", "state", "age_s", "capabilities", "active_jobs",
                                           "cpu_percent", "memory_percent", "version")} for w in ws],
        "proxies": [p.public() for p in proxies.proxies.values()],
        **routing.book.snapshot(),
        "weights": routing.weights(),
    }
