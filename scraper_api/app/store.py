"""PostgreSQL persistence: API keys, jobs, every attempt, proxies, daily usage. Creates its own DB."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from . import config

SCHEMA = """
create table if not exists api_keys (
  id serial primary key, key_hash text unique not null, owner text, quota_per_day int,
  status text not null default 'active', created_at timestamptz not null default now());
create table if not exists scrape_jobs (
  id text primary key, api_key_id int, url text not null, domain text, mode text, status text not null,
  attempts int not null default 0, options jsonb, outcome text, error text,
  created_at timestamptz not null default now(), finished_at timestamptz);
create table if not exists scrape_attempts (
  id bigserial primary key, request_id text not null, job_id text, api_key_id int, domain text,
  attempt int, worker text, mode text, proxy_id int, status_code int, outcome text, error text,
  duration_ms int, response_bytes int, cache_hit boolean not null default false,
  created_at timestamptz not null default now());
create index if not exists scrape_attempts_created on scrape_attempts (created_at);
create index if not exists scrape_attempts_domain on scrape_attempts (domain, created_at);
create table if not exists proxies (
  id serial primary key, scheme text not null, host text not null, port int not null,
  username_enc text, password_enc text, country text, provider text not null default 'manual',
  status text not null default 'active', success_count int not null default 0,
  failure_count int not null default 0, consecutive_failures int not null default 0, latency_ms int,
  last_checked timestamptz, cooldown_until timestamptz, created_at timestamptz not null default now(),
  unique (scheme, host, port, provider));
create table if not exists workers (
  worker_id text primary key, kind text, capabilities text, last_heartbeat timestamptz, info jsonb,
  created_at timestamptz not null default now());
create table if not exists route_health (
  domain text not null, route text not null, attempts int not null default 0,
  successes int not null default 0, recent text, ewma_ms double precision,
  last_success double precision, last_failure double precision,
  consecutive_failures int not null default 0, updated_at timestamptz not null default now(),
  primary key (domain, route));
create table if not exists circuit_breakers (
  domain text not null, route text not null, state text not null default 'CLOSED',
  open_until double precision, opens int not null default 0, reason text,
  updated_at timestamptz not null default now(), primary key (domain, route));
alter table scrape_attempts add column if not exists route text;
create table if not exists usage_daily (
  api_key_id int not null, day date not null, requests int not null default 0,
  bandwidth bigint not null default 0, browser_requests int not null default 0,
  cache_hits int not null default 0, primary key (api_key_id, day));
"""

pool: Optional[AsyncConnectionPool] = None


async def ensure_database() -> None:
    """CREATE DATABASE <scraper db> if it doesn't exist (connects to the default 'postgres' db)."""
    import psycopg
    p = urlsplit(config.DATABASE_URL)
    name = p.path.lstrip("/") or "scraper_api"
    admin = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
    async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
        cur = await conn.execute("select 1 from pg_database where datname = %s", (name,))
        if not await cur.fetchone():
            await conn.execute(f'create database "{name}"')


async def open_pool() -> None:
    global pool
    await ensure_database()
    pool = AsyncConnectionPool(config.DATABASE_URL, min_size=1, max_size=6, open=False,
                               kwargs={"row_factory": dict_row, "autocommit": True})
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)


async def close_pool() -> None:
    if pool:
        await pool.close()


async def q(sql: str, args: tuple = (), one: bool = False):
    async with pool.connection() as conn:
        cur = await conn.execute(sql, args)
        if cur.description is None:
            return None
        return await (cur.fetchone() if one else cur.fetchall())


# ── api keys ──────────────────────────────────────────────────────────────────
async def upsert_key(key_hash: str, owner: str = "env") -> None:
    await q("insert into api_keys (key_hash, owner) values (%s, %s) on conflict (key_hash) do nothing",
            (key_hash, owner))


async def key_by_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    return await q("select id, owner, quota_per_day, status from api_keys where key_hash = %s", (key_hash,), one=True)


# ── jobs / attempts / usage ───────────────────────────────────────────────────
async def job_create(job_id: str, api_key_id: int, url: str, domain: str, mode: str, options: Dict[str, Any]) -> None:
    await q("insert into scrape_jobs (id, api_key_id, url, domain, mode, status, options) values (%s,%s,%s,%s,%s,'queued',%s)",
            (job_id, api_key_id, url, domain, mode, json.dumps(options)))


async def job_update(job_id: str, **kv: Any) -> None:
    if not kv:
        return
    cols = ", ".join(f"{k} = %s" for k in kv)
    await q(f"update scrape_jobs set {cols} where id = %s", (*kv.values(), job_id))


async def job_get(job_id: str) -> Optional[Dict[str, Any]]:
    return await q("select * from scrape_jobs where id = %s", (job_id,), one=True)


async def attempt_log(row: Dict[str, Any]) -> None:
    cols = ("request_id", "job_id", "api_key_id", "domain", "attempt", "worker", "mode", "route", "proxy_id",
            "status_code", "outcome", "error", "duration_ms", "response_bytes", "cache_hit")
    await q(f"insert into scrape_attempts ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
            tuple(row.get(c) for c in cols))


async def usage_add(api_key_id: int, *, bandwidth: int = 0, browser: bool = False, cache_hit: bool = False) -> None:
    await q("""insert into usage_daily (api_key_id, day, requests, bandwidth, browser_requests, cache_hits)
               values (%s, current_date, 1, %s, %s, %s)
               on conflict (api_key_id, day) do update set requests = usage_daily.requests + 1,
                 bandwidth = usage_daily.bandwidth + excluded.bandwidth,
                 browser_requests = usage_daily.browser_requests + excluded.browser_requests,
                 cache_hits = usage_daily.cache_hits + excluded.cache_hits""",
            (api_key_id, int(bandwidth), int(browser), int(cache_hit)))


async def usage_get(api_key_id: int, days: int = 7) -> List[Dict[str, Any]]:
    rows = await q("select day, requests, bandwidth, browser_requests, cache_hits from usage_daily "
                   "where api_key_id = %s and day > current_date - %s order by day desc", (api_key_id, days))
    return [{**r, "day": r["day"].isoformat()} for r in rows or []]


async def domain_stats(hours: int = 24) -> List[Dict[str, Any]]:
    return await q("""select domain, count(*) as attempts,
                        sum(case when outcome = 'SUCCESS' then 1 else 0 end) as success,
                        percentile_cont(0.5) within group (order by duration_ms) as p50_ms
                      from scrape_attempts where created_at > now() - make_interval(hours => %s)
                      group by domain order by attempts desc limit 50""", (hours,)) or []


async def dashboard_numbers() -> Dict[str, Any]:
    r = await q("""select
          count(*) filter (where created_at > now() - interval '1 minute') as req_1m,
          count(*) filter (where created_at > now() - interval '24 hours') as req_24h,
          count(*) filter (where created_at > now() - interval '24 hours' and outcome = 'SUCCESS') as ok_24h,
          count(*) filter (where created_at > now() - interval '24 hours' and mode = 'browser') as browser_24h,
          count(*) filter (where created_at > now() - interval '24 hours' and cache_hit) as cache_24h
        from scrape_attempts""", one=True) or {}
    by_route = await q("""select coalesce(route, mode) as route, count(*) as attempts,
          sum(case when outcome = 'SUCCESS' then 1 else 0 end) as success
        from scrape_attempts where created_at > now() - interval '24 hours' and not cache_hit
        group by 1 order by 2 desc""") or []
    return {**r, "by_route": by_route}


async def prune_attempts(days: int = 30) -> None:
    await q("delete from scrape_attempts where created_at < now() - make_interval(days => %s)", (days,))


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
