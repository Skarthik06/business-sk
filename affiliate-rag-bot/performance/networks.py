"""
performance/networks.py — Affiliate NETWORK attribution (the results panel's data layer).

The funnel/outcome layer (performance/store.py) answers "which POST performed?". This layer
answers "which NETWORK and which PRODUCT actually earned?" — the money side. It holds:

  • a NETWORK REGISTRY  — Amazon (direct), Cuelinks (aggregator, primary), and the expansion
    slots (Flipkart, Myntra, …) so the panel shows what's live and what's coming next; and
  • a network_earnings TABLE — imported/fetched report rows (per network, per day, optionally
    per product) carrying clicks / orders / earnings.

CRITICAL (G13): earnings are recorded ONLY from a real report (a manual import or a network
API sync). Nothing is fabricated — an un-connected network simply has no rows and reads as
"no data yet". This is the attribution foundation the Learner folds back into ranking.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, String, Integer, Float, DateTime
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from utils.logger import log


# ── the registry: what networks exist + how they monetise ─────────────────────
# kind:   direct = our own affiliate tag (we keep the full commission)
#         aggregator = a network that monetises many stores via one redirect (Cuelinks/EarnKaro)
# status: connected = links live + we can attribute; coming_soon = planned, not wired yet.
NETWORK_REGISTRY = [
    {"name": "amazon",   "label": "Amazon Associates", "kind": "direct",
     "status": "connected", "note": "Direct tag — full commission, no aggregator cut."},
    {"name": "cuelinks", "label": "Cuelinks",          "kind": "aggregator",
     "status": "connected", "note": "One redirect monetises 1000s of non-Amazon stores."},
    {"name": "flipkart", "label": "Flipkart Affiliate", "kind": "direct",
     "status": "coming_soon", "note": "Add a direct Flipkart tag to keep the full cut."},
    {"name": "myntra",   "label": "Myntra Partner",     "kind": "direct",
     "status": "coming_soon", "note": "Fashion-heavy — high AOV, strong for this niche."},
]
_KNOWN = {n["name"] for n in NETWORK_REGISTRY}


class Base(DeclarativeBase):
    pass


class NetworkEarnings(Base):
    __tablename__ = "network_earnings"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    network       = Column(String(40), nullable=False, index=True)
    period_date   = Column(String(10), nullable=False, index=True)   # 'YYYY-MM-DD'
    product_asin  = Column(String(64), nullable=True, index=True)     # optional per-product row
    product_title = Column(String(200), nullable=True)
    clicks        = Column(Integer, nullable=True)
    orders        = Column(Integer, nullable=True)
    earnings      = Column(Float, nullable=True)
    currency      = Column(String(8), nullable=False, default="INR")
    source        = Column(String(24), nullable=False, default="import")   # import | api
    captured_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def _init():
    ensure(Base, "network_earnings")


def _num(v, cast=float):
    try:
        return cast(v) if v is not None and str(v).strip() != "" else None
    except Exception:
        return None


class NetworkStore:
    def record_earnings(self, network: str, rows: list[dict], source: str = "import") -> dict:
        """Record report rows for one network. Each row: {period_date, [product_asin],
        [product_title], [clicks], [orders], [earnings], [currency]}. Idempotent-ish: we
        append snapshots (latest wins in reads). Returns how many rows were stored."""
        network = (network or "").strip().lower()
        if network not in _KNOWN:
            return {"ok": False, "error": f"unknown network '{network}'"}
        _init()
        stored = 0
        with session() as s:
            for r in (rows or []):
                pd = str(r.get("period_date") or r.get("date") or "").strip()[:10]
                if not pd:
                    continue
                s.add(NetworkEarnings(
                    network=network, period_date=pd,
                    product_asin=(r.get("product_asin") or r.get("asin") or None),
                    product_title=(r.get("product_title") or r.get("title") or None),
                    clicks=_num(r.get("clicks"), int), orders=_num(r.get("orders"), int),
                    earnings=_num(r.get("earnings")), currency=(r.get("currency") or "INR"),
                    source=source))
                stored += 1
            s.commit()
        return {"ok": True, "network": network, "rows_stored": stored}

    def _rows(self, days: int) -> list[NetworkEarnings]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with session() as s:
            return (s.query(NetworkEarnings)
                    .filter(NetworkEarnings.period_date >= since).all())

    def summary(self, days: int = 30) -> dict:
        """Per-network totals + an overall roll-up over the window. Networks with no rows are
        still listed (from the registry) so the panel shows the full expansion path."""
        _init()
        try:
            rows = self._rows(days)
        except Exception as e:
            log.warning(f"[networks] summary failed: {e}")
            rows = []
        agg: dict[str, dict] = {}
        for r in rows:
            a = agg.setdefault(r.network, {"clicks": 0, "orders": 0, "earnings": 0.0, "has_data": False})
            a["clicks"] += r.clicks or 0
            a["orders"] += r.orders or 0
            a["earnings"] += r.earnings or 0.0
            a["has_data"] = True
        nets = []
        for spec in NETWORK_REGISTRY:
            a = agg.get(spec["name"], {})
            nets.append({**spec,
                         "clicks": a.get("clicks", 0), "orders": a.get("orders", 0),
                         "earnings": round(a.get("earnings", 0.0), 2),
                         "epc": (round(a["earnings"] / a["clicks"], 2)
                                 if a.get("clicks") else None),
                         "has_data": a.get("has_data", False)})
        total = {
            "earnings": round(sum(n["earnings"] for n in nets), 2),
            "clicks":   sum(n["clicks"] for n in nets),
            "orders":   sum(n["orders"] for n in nets),
        }
        total["epc"] = round(total["earnings"] / total["clicks"], 2) if total["clicks"] else None
        total["conv"] = round(total["orders"] / total["clicks"], 4) if total["clicks"] else None
        return {"days": days, "connected": bool(rows), "total": total, "networks": nets}

    def top_products(self, days: int = 30, limit: int = 20) -> list[dict]:
        """Best-earning PRODUCTS across networks (rows that carry a product), for the panel."""
        _init()
        try:
            rows = [r for r in self._rows(days) if r.product_asin]
        except Exception:
            rows = []
        by: dict[str, dict] = {}
        for r in rows:
            b = by.setdefault(r.product_asin, {"product_asin": r.product_asin,
                                               "product_title": r.product_title,
                                               "network": r.network, "clicks": 0,
                                               "orders": 0, "earnings": 0.0})
            b["clicks"] += r.clicks or 0
            b["orders"] += r.orders or 0
            b["earnings"] += r.earnings or 0.0
            if r.product_title and not b["product_title"]:
                b["product_title"] = r.product_title
        out = sorted(by.values(), key=lambda x: x["earnings"], reverse=True)
        for b in out:
            b["earnings"] = round(b["earnings"], 2)
        return out[:limit]


network_store = NetworkStore()
