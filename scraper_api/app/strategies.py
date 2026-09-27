"""Domain strategy lookup (strategies/domains.yaml). The engine never hard-codes a website."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

import yaml

from . import config

_MODES = ("http", "browser", "auto")


@dataclass(frozen=True)
class Strategy:
    domain: str = "*"
    mode: str = "auto"
    timeout: int = 30
    max_attempts: int = 3
    concurrency: int = 4
    cache_ttl: int = 1800
    min_bytes: int = 512
    residential: bool = False
    wait_for: str = ""                              # CSS selector the browser waits for (optional)
    extract: List[str] = field(default_factory=lambda: ["title", "images", "metadata", "jsonld", "price"])
    challenge_markers: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _coerce(domain: str, raw: Dict[str, Any], base: Strategy) -> Strategy:
    raw = dict(raw or {})
    kw: Dict[str, Any] = {"domain": domain}
    for name, typ in (("mode", str), ("timeout", int), ("max_attempts", int), ("concurrency", int),
                      ("cache_ttl", int), ("min_bytes", int), ("residential", bool), ("wait_for", str)):
        if name in raw and raw[name] is not None:
            kw[name] = typ(raw[name])
    for name in ("extract", "challenge_markers"):
        if isinstance(raw.get(name), list):
            kw[name] = [str(x) for x in raw[name]]
    s = replace(base, **kw)
    if s.mode not in _MODES:
        s = replace(s, mode="auto")
    return replace(s, timeout=max(3, min(s.timeout, 120)), max_attempts=max(1, min(s.max_attempts, 5)),
                   concurrency=max(1, min(s.concurrency, 16)))


class Strategies:
    def __init__(self, path=config.STRATEGIES_FILE):
        self.path = path
        self.default = Strategy()
        self.domains: Dict[str, Strategy] = {}
        self.reload()

    def reload(self) -> None:
        try:
            data = yaml.safe_load(open(self.path, encoding="utf-8")) or {}
        except FileNotFoundError:
            data = {}
        self.default = _coerce("*", data.get("defaults") or {}, Strategy())
        self.domains = {d.lower(): _coerce(d.lower(), cfg, self.default)
                        for d, cfg in (data.get("domains") or {}).items()}

    def for_host(self, host: str) -> Strategy:
        """Exact domain or any parent domain (www.amazon.in → amazon.in; x.myshopify.com → myshopify.com)."""
        h = (host or "").lower().rstrip(".")
        parts = h.split(".")
        for i in range(len(parts) - 1):
            cand = ".".join(parts[i:])
            if cand in self.domains:
                return self.domains[cand]
        return replace(self.default, domain=h or "*")

    def get(self, domain: str) -> Optional[Strategy]:
        return self.domains.get((domain or "").lower())
