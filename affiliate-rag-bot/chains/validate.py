"""
chains/validate.py  —  Caption fact-check (Phase 4, Content Intelligence).

A deterministic safety net over the LLM caption: extract every numeric CLAIM (₹ prices and
"X% off") and verify it against the REAL scraped product rows. Anything not backed by source
data is reported as a warning (G13 — no fabricated numbers). This is stronger than trusting
the prompt alone. It does NOT rewrite the caption (that would risk mangling copy) — it flags,
so the studio can surface/hold a post whose numbers don't check out.
"""
from __future__ import annotations

import re

_PRICE = re.compile(r"₹\s?([\d,]+)")
_OFF = re.compile(r"(\d{1,2})\s?%\s?off", re.I)


def _int(s) -> int:
    m = re.search(r"[\d,]+", str(s or ""))
    return int(m.group().replace(",", "")) if m else 0


def audit_caption(caption: str, products: list[dict]) -> list[str]:
    """Return a list of warnings for numeric claims not supported by any product row.
    Empty list = every number in the caption is grounded in real data."""
    if not caption:
        return []
    real_prices = {_int(p.get("price")) for p in products if _int(p.get("price"))}
    # tolerate rounded prices (₹1,299 written as ₹1300) within ±2%
    def price_ok(v: int) -> bool:
        return any(abs(v - rp) <= max(2, rp * 0.02) for rp in real_prices)

    real_discounts = {int(p.get("discount_pct") or 0) for p in products if p.get("discount_pct")}

    warnings: list[str] = []
    for m in _PRICE.finditer(caption):
        v = _int(m.group(1))
        if v and not price_ok(v):
            warnings.append(f"caption price ₹{v:,} not found in any product (possible fabrication)")
    for m in _OFF.finditer(caption):
        v = int(m.group(1))
        if v and v not in real_discounts and not any(abs(v - d) <= 2 for d in real_discounts):
            warnings.append(f"caption '{v}% off' not found in any product (possible fabrication)")
    return warnings
