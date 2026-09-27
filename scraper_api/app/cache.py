"""Response cache. Bodies live on DISK (gzip JSON); Redis only holds a tiny pointer with the TTL.
(The shared Redis is small and LRU — big HTML there would evict other services' keys.)"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from typing import Any, Dict, Iterable, Optional

from . import config

PREFIX = "scraper:cache:"


def cache_key(url: str, render: bool, fields: Iterable[str]) -> str:
    sig = json.dumps([url.strip(), bool(render), sorted({f.strip().lower() for f in fields})])
    return hashlib.sha256(sig.encode("utf-8")).hexdigest()


def _path(key: str):
    return config.CACHE_DIR / key[:2] / f"{key}.json.gz"


async def get(redis, key: str) -> Optional[Dict[str, Any]]:
    try:
        if not await redis.exists(PREFIX + key):
            return None
        with gzip.open(_path(key), "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


async def put(redis, key: str, value: Dict[str, Any], ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        p = _path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(value, f)
        tmp.replace(p)
        await redis.set(PREFIX + key, "1", ex=int(ttl))
    except Exception:
        pass


def prune(max_age: int = config.CACHE_MAX_AGE) -> int:
    """Delete cache files older than max_age seconds. Returns the number removed."""
    cutoff, n = time.time() - max_age, 0
    if not config.CACHE_DIR.exists():
        return 0
    for f in config.CACHE_DIR.glob("*/*.json.gz"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n
