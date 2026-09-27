"""Remote worker pool (laptop, phone, any machine): heartbeat, pull-based jobs with leases, results.

Workers only make OUTBOUND requests (they work behind NAT / carrier CGNAT). Flow:
  worker → POST /v1/workers/heartbeat            (every ~20 s; also implied by every lease call)
  worker → POST /v1/workers/jobs/lease (long-poll) ← jobs targeted at this worker
  worker → POST /v1/workers/jobs/{attempt}/result
The engine pushes a job to scraper:queue:worker:{id} and waits (bounded) for the result; an
abandoned lease simply expires and the engine fails over to the next route."""
from __future__ import annotations

import base64
import gzip
import hmac
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import config, metrics, store

HB = "scraper:worker:heartbeat:"
QW = "scraper:queue:worker:"
LEASE = "scraper:worker:lease:"
RESULT = "scraper:result:"
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _num(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


ONLINE_SECS = _num("SCRAPER_WORKER_ONLINE_SECS", 60)
STALE_SECS = _num("SCRAPER_WORKER_STALE_SECS", 120)
PICKUP_SECS = _num("SCRAPER_WORKER_PICKUP_SECS", 25)     # a job not leased within this → failover


def tokens() -> List[str]:
    return [t.strip() for t in (os.getenv("SCRAPER_WORKER_TOKENS") or "").split(",") if t.strip()]


def token_ok(tok: str) -> bool:
    tok = (tok or "").strip()
    return bool(tok) and any(hmac.compare_digest(tok, t) for t in tokens())


def valid_id(worker_id: str) -> bool:
    return bool(_ID.match(worker_id or ""))


def state_of(age: float) -> str:
    return "online" if age < ONLINE_SECS else ("stale" if age < STALE_SECS else "offline")


_LAST_DB: Dict[str, float] = {}


async def heartbeat(worker_id: str, info: Dict[str, Any]) -> None:
    now = time.time()
    caps = [c for c in (info.get("capabilities") or ["http"]) if c in ("http", "browser")] or ["http"]
    rec = {"worker_id": worker_id, "t": now, "status": str(info.get("status") or "healthy")[:20],
           "capabilities": caps, "active_jobs": int(info.get("active_jobs") or 0),
           "cpu_percent": info.get("cpu_percent"), "memory_percent": info.get("memory_percent"),
           "kind": str(info.get("kind") or "remote")[:20], "version": str(info.get("version") or "")[:20]}
    await metrics.redis.set(HB + worker_id, json.dumps(rec), ex=STALE_SECS * 10)
    if now - _LAST_DB.get(worker_id, 0) > 60:              # persist at most once a minute
        _LAST_DB[worker_id] = now
        await store.q("""insert into workers (worker_id, kind, capabilities, last_heartbeat, info)
                         values (%s,%s,%s,now(),%s)
                         on conflict (worker_id) do update set kind=excluded.kind,
                           capabilities=excluded.capabilities, last_heartbeat=now(), info=excluded.info""",
                      (worker_id, rec["kind"], ",".join(caps), json.dumps(rec)))


async def list_workers() -> List[Dict[str, Any]]:
    out = []
    now = time.time()
    async for key in metrics.redis.scan_iter(HB + "*"):
        raw = await metrics.redis.get(key)
        if not raw:
            continue
        rec = json.loads(raw)
        age = now - float(rec.get("t") or 0)
        out.append({**rec, "age_s": int(age), "state": state_of(age)})
    return sorted(out, key=lambda r: r["worker_id"])


async def dispatch(worker_id: str, attempt_id: str, url: str, mode: str, timeout: int) -> Dict[str, Any]:
    """Queue a fetch for one worker and wait (bounded) for its result. Never raises."""
    job = {"attempt_id": attempt_id, "url": url, "mode": mode, "timeout": timeout, "queued": time.time()}
    qkey = QW + worker_id
    await metrics.redis.rpush(qkey, json.dumps(job))
    await metrics.redis.expire(qkey, 3600)
    deadline = time.time() + PICKUP_SECS
    picked = False
    while True:                                           # 1) wait for the lease (the worker is alive)
        if await metrics.redis.exists(LEASE + attempt_id):
            picked = True
            break
        if time.time() > deadline:
            break
        item = await metrics.redis.blpop([RESULT + attempt_id], timeout=1)
        if item:
            return json.loads(item[1])
    if not picked:
        await metrics.redis.lrem(qkey, 0, json.dumps(job))   # stale job must never run later
        return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {},
                "error": f"worker {worker_id} did not pick up the job in {PICKUP_SECS}s"}
    item = await metrics.redis.blpop([RESULT + attempt_id], timeout=timeout + 25)   # 2) wait for the result
                                                              # (the worker may retry a throttled page
                                                              #  within `timeout`, then uploads)
    if not item:
        return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {},
                "error": f"worker {worker_id} timed out"}
    return json.loads(item[1])


async def lease(worker_id: str, max_jobs: int, wait: float) -> List[Dict[str, Any]]:
    """Long-poll: hand this worker up to max_jobs of ITS queued jobs, each with a lease."""
    out: List[Dict[str, Any]] = []
    qkey = QW + worker_id
    item = await metrics.redis.blpop([qkey], timeout=max(1, int(wait)))
    while item and len(out) < max_jobs:
        job = json.loads(item[1])
        await metrics.redis.set(LEASE + job["attempt_id"], worker_id, ex=int(job.get("timeout", 30)) + 30)
        out.append({k: job[k] for k in ("attempt_id", "url", "mode", "timeout")})
        raw = await metrics.redis.lpop(qkey)
        item = (qkey, raw) if raw else None
    return out


async def submit(worker_id: str, attempt_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    owner = await metrics.redis.get(LEASE + attempt_id)
    if owner != worker_id:
        return {"ok": False, "error": "no active lease for this worker"}
    html = body.get("html") or ""
    if body.get("gz") and html:
        try:
            raw = gzip.decompress(base64.b64decode(html))
            html = raw[: config.MAX_BYTES].decode("utf-8", "ignore")
        except Exception:
            html, body["error"] = "", body.get("error") or "bad gzip payload"
    elif len(html) > config.MAX_BYTES:
        html = html[: config.MAX_BYTES]
    res = {"status": body.get("status_code"), "html": html, "final_url": body.get("final_url") or "",
           "content_type": body.get("content_type") or "text/html", "headers": {},
           "error": (str(body.get("error"))[:200] if body.get("error") else None)}
    await metrics.redis.rpush(RESULT + attempt_id, json.dumps(res))
    await metrics.redis.expire(RESULT + attempt_id, 120)
    await metrics.redis.delete(LEASE + attempt_id)
    return {"ok": True}
