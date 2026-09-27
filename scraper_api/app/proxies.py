"""Proxy manager: registry (encrypted credentials), health checks, lease + cooldown, metrics.
The scraper works without any proxy; proxies only add routes. Credentials are never logged/returned."""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from . import config, security, store


@dataclass
class Proxy:
    id: int
    scheme: str
    host: str
    port: int
    username: str
    password: str
    country: Optional[str]
    provider: str
    status: str
    success_count: int
    failure_count: int
    consecutive_failures: int
    latency_ms: Optional[int]
    cooldown_until: Optional[dt.datetime]

    def url(self) -> str:
        auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@" if self.username else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def playwright(self) -> Dict[str, str]:
        d = {"server": f"{self.scheme}://{self.host}:{self.port}"}
        if self.username:
            d.update(username=self.username, password=self.password)
        return d

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "endpoint": f"{self.scheme}://{self.host}:{self.port}", "has_auth": bool(self.username),
                "country": self.country, "provider": self.provider, "status": self.status,
                "success": self.success_count, "failure": self.failure_count, "latency_ms": self.latency_ms,
                "cooling_down": bool(self.cooldown_until and self.cooldown_until > store.now())}


def parse_proxy_url(url: str) -> Dict[str, Any]:
    raw = url if "://" in url else "http://" + url
    p = urlsplit(raw)
    if p.scheme not in ("http", "https", "socks5") or not p.hostname or not p.port:
        raise ValueError("proxy must look like scheme://[user:pass@]host:port")
    from urllib.parse import unquote
    return {"scheme": p.scheme, "host": p.hostname, "port": p.port,
            "username": unquote(p.username or ""), "password": unquote(p.password or "")}


class ProxyManager:
    def __init__(self):
        self.proxies: Dict[int, Proxy] = {}
        self.leases: Dict[int, int] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        rows = await store.q("select * from proxies where status <> 'deleted' order by id")
        out = {}
        for r in rows or []:
            try:
                user, pw = security.decrypt(r["username_enc"] or ""), security.decrypt(r["password_enc"] or "")
            except Exception:
                user = pw = ""                    # key changed → unusable until re-added
            out[r["id"]] = Proxy(r["id"], r["scheme"], r["host"], r["port"], user, pw, r["country"], r["provider"],
                                 r["status"], r["success_count"], r["failure_count"], r["consecutive_failures"],
                                 r["latency_ms"], r["cooldown_until"])
        self.proxies = out

    async def add(self, url: str, country: Optional[str] = None, provider: str = "manual") -> int:
        p = parse_proxy_url(url)
        row = await store.q(
            """insert into proxies (scheme, host, port, username_enc, password_enc, country, provider, status)
               values (%s,%s,%s,%s,%s,%s,%s,'active')
               on conflict (scheme, host, port, provider) do update set username_enc = excluded.username_enc,
                 password_enc = excluded.password_enc, country = excluded.country, status = 'active',
                 consecutive_failures = 0, cooldown_until = null
               returning id""",
            (p["scheme"], p["host"], p["port"], security.encrypt(p["username"]), security.encrypt(p["password"]),
             (country or "").upper() or None, provider), one=True)
        await self.load()
        return row["id"]

    async def delete(self, pid: int) -> bool:
        await store.q("update proxies set status = 'deleted' where id = %s", (pid,))
        await self.load()
        return True

    async def seed_from_env(self) -> None:
        """SCRAPER_PROXY_SERVER / _USER / _PASS (same names the affiliate engine used) → provider 'env'."""
        server = (os.getenv("SCRAPER_PROXY_SERVER") or "").strip()
        if not server:
            return
        user = (os.getenv("SCRAPER_PROXY_USER") or "").strip()
        pw = (os.getenv("SCRAPER_PROXY_PASS") or "").strip()
        p = parse_proxy_url(server)
        url = f"{p['scheme']}://" + (f"{quote(user, safe='')}:{quote(pw, safe='')}@" if user else "") + f"{p['host']}:{p['port']}"
        await self.add(url, os.getenv("SCRAPER_PROXY_COUNTRY") or "IN", provider="env")

    def _usable(self, country: Optional[str]) -> List[Proxy]:
        now = store.now()
        c = [p for p in self.proxies.values() if p.status == "active"
             and not (p.cooldown_until and p.cooldown_until > now)
             and self.leases.get(p.id, 0) < config.PROXY_MAX_LEASES]
        if country:
            c = [p for p in c if (p.country or "").upper() == country.upper()] or c
        return c

    def healthy_count(self) -> int:
        return len(self._usable(None))

    def usable(self, country: Optional[str] = None) -> List[Proxy]:
        """Proxies the Strategy Engine may consider (active, not cooling down, lease capacity left)."""
        return self._usable(country)

    async def acquire(self, proxy: Proxy) -> None:
        async with self.lock:
            self.leases[proxy.id] = self.leases.get(proxy.id, 0) + 1

    async def lease(self, country: Optional[str] = None, exclude: Optional[set] = None) -> Optional[Proxy]:
        """Healthy + not cooling down + geography → lowest failure ratio, then lowest latency."""
        async with self.lock:
            cands = [p for p in self._usable(country) if not exclude or p.id not in exclude]
            if not cands:
                return None
            best = min(cands, key=lambda p: (p.failure_count / max(1, p.success_count + p.failure_count),
                                             p.latency_ms or 10_000))
            self.leases[best.id] = self.leases.get(best.id, 0) + 1
            return best

    async def release(self, proxy: Proxy, ok: bool, latency_ms: Optional[int] = None) -> None:
        async with self.lock:
            self.leases[proxy.id] = max(0, self.leases.get(proxy.id, 1) - 1)
        if ok:
            proxy.success_count += 1
            proxy.consecutive_failures = 0
            proxy.cooldown_until = None
            if latency_ms:
                proxy.latency_ms = int(latency_ms if not proxy.latency_ms else 0.7 * proxy.latency_ms + 0.3 * latency_ms)
        else:
            proxy.failure_count += 1
            proxy.consecutive_failures += 1
            if proxy.consecutive_failures >= 3:     # 5 min, 10, 20 … capped at 1 h
                mins = min(60, 5 * 2 ** (proxy.consecutive_failures - 3))
                proxy.cooldown_until = store.now() + dt.timedelta(minutes=mins)
        await store.q("""update proxies set success_count=%s, failure_count=%s, consecutive_failures=%s,
                         latency_ms=%s, cooldown_until=%s where id=%s""",
                      (proxy.success_count, proxy.failure_count, proxy.consecutive_failures, proxy.latency_ms,
                       proxy.cooldown_until, proxy.id))

    async def health_check_all(self) -> None:
        import httpx
        for p in list(self.proxies.values()):
            if p.status != "active":
                continue
            t0 = time.monotonic()
            ok = False
            try:
                async with httpx.AsyncClient(proxy=p.url(), timeout=15) as c:
                    r = await c.get(config.PROXY_HEALTH_URL)
                    ok = r.status_code < 500
            except Exception:
                ok = False
            ms = int((time.monotonic() - t0) * 1000)
            await self.release_checked(p, ok, ms)

    async def release_checked(self, p: Proxy, ok: bool, ms: int) -> None:
        if ok:
            p.latency_ms = ms
            p.consecutive_failures = 0
        else:
            p.consecutive_failures += 1
            if p.consecutive_failures >= 3:
                p.cooldown_until = store.now() + dt.timedelta(minutes=min(60, 5 * 2 ** (p.consecutive_failures - 3)))
        await store.q("update proxies set latency_ms=%s, consecutive_failures=%s, cooldown_until=%s, last_checked=now() where id=%s",
                      (p.latency_ms, p.consecutive_failures, p.cooldown_until, p.id))


manager = ProxyManager()
