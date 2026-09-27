"""Playwright browser worker: one lazily-started Chromium, a context per request (own proxy/cookies),
heavy resources blocked (we only need HTML), bounded concurrency, closed again when idle."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from . import config, security

_BLOCK = {"image", "media", "font", "stylesheet"}


class BrowserPool:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(config.BROWSER_CONCURRENCY)
        self.last_used = 0.0
        self.active = 0

    @property
    def started(self) -> bool:
        return self._browser is not None

    async def _ensure(self):
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
        return self._browser

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._browser:
                    await self._browser.close()
                if self._pw:
                    await self._pw.stop()
            finally:
                self._browser = self._pw = None

    async def close_if_idle(self) -> None:
        if self._browser and self.active == 0 and time.time() - self.last_used > config.BROWSER_IDLE_CLOSE_SECS:
            await self.close()

    async def fetch(self, url: str, timeout: int, proxy: Optional[Dict[str, str]] = None,
                    wait_for: str = "") -> Dict[str, Any]:
        async with self.sem:
            self.active += 1
            self.last_used = time.time()
            ctx = None
            try:
                browser = await self._ensure()
                ctx = await browser.new_context(user_agent=config.UA, locale="en-IN",
                                                extra_http_headers={"Accept-Language": config.HEADERS["Accept-Language"]},
                                                proxy=proxy or None)
                page = await ctx.new_page()

                async def _route(route):
                    req = route.request
                    host = urlsplit(req.url).hostname or ""
                    if req.resource_type in _BLOCK:
                        return await route.abort()
                    if req.url.startswith(("http://", "https://")) and \
                            await asyncio.to_thread(security.host_blocked, host):
                        return await route.abort()            # SSRF: no internal hops from the browser
                    return await route.continue_()
                await page.route("**/*", _route)
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=min(15, timeout) * 1000)
                    except Exception:
                        pass
                html = await page.content()
                if len(html.encode("utf-8", "ignore")) > config.MAX_BYTES:
                    html = html[: config.MAX_BYTES]
                return {"status": resp.status if resp else None, "html": html, "final_url": page.url,
                        "content_type": (resp.headers.get("content-type") if resp else "") or "text/html",
                        "headers": dict(resp.headers) if resp else {}, "error": None}
            except Exception as e:                      # noqa: BLE001
                return {"status": None, "html": "", "final_url": url, "content_type": "", "headers": {},
                        "error": f"browser: {type(e).__name__}: {str(e)[:140]}"}
            finally:
                if ctx:
                    try:
                        await ctx.close()
                    except Exception:
                        pass
                self.active -= 1
                self.last_used = time.time()


pool = BrowserPool()
