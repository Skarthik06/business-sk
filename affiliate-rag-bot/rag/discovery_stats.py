"""
rag/discovery_stats.py  —  Adaptive discovery-query memory (Phase 1, Discovery Planner).

Tracks how productive each search intent has been so the Discovery Planner rotates
toward queries that yield fresh, quality products and away from ones that mostly
return duplicates. Lives in the SAME PostgreSQL DB as the dedup ledger + pgvector
store (one DATABASE_URL). Purely additive — if it fails, discovery falls back to the
static subcategory order (fail-open, G2).

Table: discovery_queries
  category      VARCHAR   — base category
  query         VARCHAR   — search intent (subcategory term)     (PK with category)
  usage_count   INT       — times this query was run
  raw_total     INT       — cumulative raw products scraped
  fresh_total   INT       — cumulative unique quality products it contributed
  last_used_at  TIMESTAMP — most recent run
  priority      FLOAT     — fresh_total / max(usage_count,1)  (higher = pick sooner)

Constraint tuning: this is an AGENT — its selection rule is documented in
agents/discovery-planner.agents.md and its bounds in config.DiscoveryConfig.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import cfg
from utils.logger import log


class Base(DeclarativeBase):
    pass


class DiscoveryQuery(Base):
    __tablename__ = "discovery_queries"
    category     = Column(String(60), primary_key=True)
    query        = Column(String(120), primary_key=True)
    usage_count  = Column(Integer, nullable=False, default=0)
    raw_total    = Column(Integer, nullable=False, default=0)
    fresh_total  = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)
    priority     = Column(Float, nullable=False, default=0.0)


_engine = None
_SessionLocal = None


def _get_session() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(cfg.storage.sqlalchemy_url, pool_pre_ping=True, echo=False)
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        log.success("[discovery] discovery_queries table ready ✓")
    return _SessionLocal()


class DiscoveryStats:
    """Pick the next search intents to mine, and record what each one yielded."""

    def pick_queries(self, category: str, candidates: list[str], n: int) -> list[str]:
        """Choose up to `n` of `candidates` to run this cycle. Order: highest priority
        (best historical fresh-yield) first, then least-recently-used, then any unseen
        query (so new intents get explored). Fail-open to the static order on error."""
        if n <= 0 or not candidates:
            return candidates[: max(n, 0)]
        try:
            with _get_session() as s:
                rows = {
                    r.query: r for r in
                    s.query(DiscoveryQuery).filter(DiscoveryQuery.category == category).all()
                }
        except Exception as e:
            log.warning(f"[discovery] pick_queries fell back to static order: {e}")
            return candidates[:n]

        def sort_key(q: str):
            r = rows.get(q)
            if r is None:
                return (1, 0.0, 0.0)                  # unseen → explore first
            lru = r.last_used_at.timestamp() if r.last_used_at else 0.0
            return (0, r.priority, -lru)              # seen → highest priority, oldest first

        # Unseen (tier 1) and high-priority seen interleave via the tuple sort (desc).
        ordered = sorted(candidates, key=sort_key, reverse=True)
        return ordered[:n]

    def record_yields(self, category: str, yields: dict) -> None:
        """Persist per-query outcomes: yields = {query: {"raw": n, "quality_unique": n}}."""
        if not yields:
            return
        try:
            now = datetime.now(timezone.utc)
            with _get_session() as s:
                for q, y in yields.items():
                    raw = int(y.get("raw", 0)); fresh = int(y.get("quality_unique", 0))
                    r = s.get(DiscoveryQuery, {"category": category, "query": q})
                    if r is None:
                        r = DiscoveryQuery(category=category, query=q, usage_count=0,
                                           raw_total=0, fresh_total=0)
                        s.add(r)
                    r.usage_count += 1
                    r.raw_total += raw
                    r.fresh_total += fresh
                    r.last_used_at = now
                    r.priority = round(r.fresh_total / max(r.usage_count, 1), 3)
                s.commit()
        except Exception as e:
            log.warning(f"[discovery] record_yields skipped: {e}")

    def top(self, category: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Most productive queries (for the dashboard / Intelligence panel)."""
        try:
            with _get_session() as s:
                q = s.query(DiscoveryQuery)
                if category:
                    q = q.filter(DiscoveryQuery.category == category)
                rows = q.order_by(DiscoveryQuery.priority.desc()).limit(limit).all()
                return [{"category": r.category, "query": r.query, "usage_count": r.usage_count,
                         "raw_total": r.raw_total, "fresh_total": r.fresh_total,
                         "priority": r.priority,
                         "last_used_at": r.last_used_at.isoformat() if r.last_used_at else ""}
                        for r in rows]
        except Exception:
            return []


discovery_stats = DiscoveryStats()
