"""
competitor/store.py  —  Competitor watchlist (Phase 9).

An ISOLATED, opt-in module (COMPETITOR_ENABLED, default off). It maintains a watchlist of
public creator accounts as a DISCOVERY SIGNAL — to detect market patterns (recurring product
types, categories), never to copy anyone's content (blueprint §66). Public-metadata collection
needs a data source; until one is connected, observation returns "not connected" (no fabrication).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from config import cfg


class Base(DeclarativeBase):
    pass


class Competitor(Base):
    __tablename__ = "competitor_watchlist"
    id       = Column(Integer, primary_key=True, autoincrement=True)
    handle   = Column(String(120), nullable=False, unique=True)
    note     = Column(Text, nullable=True)
    added_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def _init():
    ensure(Base, "competitor")


class CompetitorStore:
    def enabled(self) -> bool:
        return cfg.competitor.enabled

    def add(self, handle: str, note: str = "") -> dict:
        _init()
        h = (handle or "").strip().lstrip("@").lower()
        if not h:
            return {"ok": False, "error": "empty handle"}
        with session() as s:
            existing = s.query(Competitor).filter(Competitor.handle == h).first()
            if existing:
                existing.note = note or existing.note
            else:
                s.add(Competitor(handle=h, note=note))
            s.commit()
        return {"ok": True, "handle": h}

    def list(self) -> list[dict]:
        _init()
        with session() as s:
            rows = s.query(Competitor).order_by(Competitor.added_at.desc()).all()
            return [{"id": r.id, "handle": r.handle, "note": r.note or "",
                     "added_at": r.added_at.isoformat() if r.added_at else ""} for r in rows]

    def remove(self, handle: str) -> dict:
        _init()
        h = (handle or "").strip().lstrip("@").lower()
        with session() as s:
            r = s.query(Competitor).filter(Competitor.handle == h).first()
            if r:
                s.delete(r); s.commit()
                return {"ok": True, "removed": h}
        return {"ok": False, "error": "not found"}

    def observations(self, handle: str) -> dict:
        """Public-metadata signals for a watched account. Needs a connected collector;
        until then, honestly reports not-connected (never fabricated engagement, G13)."""
        return {"handle": handle, "connected": False,
                "note": "public-metadata collection not connected — watchlist stored as a discovery signal only"}


competitor_store = CompetitorStore()
