"""Tiny in-process TTL cache for hot, rarely-changing database reads.

Why: the database is Supabase (paid by EGRESS — every byte a query returns). The comment poller,
the phone's 20-second check-in and every comment's rule run re-read the same rows (accounts,
rules, the affiliate posts list, a post's products) thousands of times a day — ~12 GB in a month
on a 5.5 GB plan. These change only when YOU change them, so they're cached for a short time and
dropped immediately when the code that writes them runs (invalidate).
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable, Dict, Tuple

_DATA: Dict[Tuple, Tuple[float, Any]] = {}
_LOCK = threading.Lock()


def get(key: Tuple, ttl: float, load: Callable[[], Any]) -> Any:
    now = time.time()
    with _LOCK:
        hit = _DATA.get(key)
        if hit and hit[0] > now:
            return copy.deepcopy(hit[1])           # callers may mutate what they get
    val = load()
    with _LOCK:
        if len(_DATA) > 5000:
            _DATA.clear()
        _DATA[key] = (now + ttl, val)
    return copy.deepcopy(val)


def invalidate(prefix: str) -> None:
    """Drop every entry whose key starts with `prefix` (e.g. 'rules', 'accounts')."""
    with _LOCK:
        for k in [k for k in _DATA if k and k[0] == prefix]:
            _DATA.pop(k, None)
