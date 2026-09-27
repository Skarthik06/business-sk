"""Response classification + bounded retry policy (exponential backoff with jitter)."""
from __future__ import annotations

import random
from enum import Enum
from typing import Optional, Tuple

from .strategies import Strategy


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    ACCESS_DENIED = "ACCESS_DENIED"         # 401 / 403
    RATE_LIMITED = "RATE_LIMITED"           # 429
    SERVER_ERROR = "SERVER_ERROR"           # 5xx
    NETWORK_ERROR = "NETWORK_ERROR"         # timeout / connection
    CHALLENGE = "CHALLENGE"                 # bot-check / block page / suspiciously short page
    NOT_FOUND = "NOT_FOUND"                 # 404 / 410 (permanent)
    CLIENT_ERROR = "CLIENT_ERROR"           # other 4xx (permanent)
    BAD_CONTENT = "BAD_CONTENT"             # content type / size not allowed (permanent)
    NEEDS_RESIDENTIAL = "NEEDS_RESIDENTIAL"  # site blocks datacenter IPs and no proxy is configured
    BLOCKED_TARGET = "BLOCKED_TARGET"       # SSRF guard / invalid URL (permanent)


PERMANENT = {Outcome.NOT_FOUND, Outcome.CLIENT_ERROR, Outcome.BAD_CONTENT, Outcome.NEEDS_RESIDENTIAL,
             Outcome.BLOCKED_TARGET}
# retried with the SAME route after a backoff
TRANSIENT = {Outcome.RATE_LIMITED, Outcome.SERVER_ERROR, Outcome.NETWORK_ERROR}
# retried only with a DIFFERENT route (browser instead of http, or another proxy) — never hammered
ROUTE_CHANGE = {Outcome.CHALLENGE, Outcome.ACCESS_DENIED}


def _looks_html(content_type: str, body: str) -> bool:
    ct = (content_type or "").lower()
    return "html" in ct or body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def classify(status: Optional[int], body: str, content_type: str, strategy: Strategy,
             error: Optional[str] = None) -> Tuple[Outcome, str]:
    if error:
        return Outcome.NETWORK_ERROR, error[:160]
    if status is None:
        return Outcome.NETWORK_ERROR, "no response"
    text = body or ""
    marker = next((m for m in strategy.challenge_markers if m and m in text), None)
    if status == 429:
        return Outcome.RATE_LIMITED, "429 Too Many Requests"
    if status in (401, 403):
        return (Outcome.CHALLENGE, f"{status} + challenge marker") if marker else (Outcome.ACCESS_DENIED, str(status))
    if status in (404, 410):
        return Outcome.NOT_FOUND, str(status)
    if 500 <= status:
        return (Outcome.CHALLENGE, f"{status} + challenge marker") if marker else (Outcome.SERVER_ERROR, str(status))
    if 400 <= status:
        return Outcome.CLIENT_ERROR, str(status)
    if marker:
        return Outcome.CHALLENGE, f"challenge marker: {marker[:40]}"
    if _looks_html(content_type, text) and len(text.encode("utf-8", "ignore")) < strategy.min_bytes:
        return Outcome.CHALLENGE, f"page too short ({len(text)} chars < {strategy.min_bytes})"
    return Outcome.SUCCESS, str(status)


def backoff(attempt: int, retry_after: Optional[float] = None, base: float = 1.0, cap: float = 20.0) -> float:
    """Seconds to wait before attempt `attempt + 1`. Honours Retry-After; else exponential + equal jitter."""
    if retry_after is not None and retry_after >= 0:
        return min(cap, float(retry_after))
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    return exp / 2 + random.uniform(0, exp / 2)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            import time
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except Exception:
            return None
