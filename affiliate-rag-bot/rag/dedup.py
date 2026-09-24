"""
rag/dedup.py  —  Product deduplication via PostgreSQL (same DB as the pgvector store).

Maintains a `seen_products` table that tracks every ASIN ever pinned.
Before any product enters the pipeline, it's checked here.
Already-pinned products are filtered out entirely.

Table: seen_products
  asin        VARCHAR PRIMARY KEY  — Amazon product identifier
  title       TEXT                 — product title (for logging)
  category    VARCHAR              — product category
  pin_count   INT                  — how many times pinned (incremented on re-pin)
  first_seen  TIMESTAMP            — when first pinned
  last_seen   TIMESTAMP            — when most recently pinned

Why a relational table (not pgvector) for dedup?
  Deduplication is a relational lookup (exact ASIN match), not a vector
  similarity search. An indexed primary-key lookup is O(log n) and far
  faster than embedding + cosine similarity. It lives in the SAME PostgreSQL
  database as the vector store — one connection string (DATABASE_URL) for both.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, Column, String, Integer, DateTime, Text,
    inspect, text
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import cfg
from utils.logger import log


# ─── ORM Model ───────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class SeenProduct(Base):
    __tablename__ = "seen_products"

    asin       = Column(String(20),  primary_key=True)
    title      = Column(Text,        nullable=False, default="")
    category   = Column(String(60),  nullable=False, default="general")
    pin_count  = Column(Integer,     nullable=False, default=1)
    first_seen = Column(DateTime,    nullable=False, default=datetime.utcnow)
    last_seen  = Column(DateTime,    nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─── Engine & Session ─────────────────────────────────────────────────────────

def _make_engine():
    # PostgreSQL via psycopg (v3). The same DATABASE_URL powers the pgvector
    # store. pool_pre_ping keeps pooled connections healthy across the long
    # 20-minute idle waits between pins.
    engine = create_engine(
        cfg.storage.sqlalchemy_url,
        pool_pre_ping=True,
        echo=False,
    )
    return engine


_engine = None
_SessionLocal = None


def _get_session() -> Session:
    global _engine, _SessionLocal
    # Guard on the SESSION FACTORY so a transient DB outage during init is RETRIED (guarding on
    # _engine would leave _SessionLocal None forever → "'NoneType' object is not callable").
    if _SessionLocal is None:
        eng = _make_engine()
        # Auto-create the table if it doesn't exist
        Base.metadata.create_all(eng)
        _engine = eng                                  # publish only after a successful init
        _SessionLocal = sessionmaker(bind=eng, expire_on_commit=False)
        log.success("[Dedup] seen_products table ready ✓")
    return _SessionLocal()


# ─── Public API ───────────────────────────────────────────────────────────────

class DedupStore:
    """
    Checks and records which ASINs have already been pinned.

    Usage in the pipeline:
        1. Call filter_unseen(products) to remove already-pinned products
        2. Call mark_as_pinned(products) after successful posting
    """

    def filter_unseen(self, products: list[dict]) -> tuple[list[dict], list[str]]:
        """
        Splits products into new (never pinned) and duplicates (already pinned).

        Returns:
            (new_products, duplicate_asins)
        """
        if not products:
            return [], []

        asins = [p.get("asin", "") for p in products if p.get("asin")]

        with _get_session() as session:
            seen_rows = session.query(SeenProduct.asin).filter(
                SeenProduct.asin.in_(asins)
            ).all()
            seen_asins = {row.asin for row in seen_rows}

        new_products   = [p for p in products if p.get("asin") not in seen_asins]
        duplicate_asins = list(seen_asins & set(asins))

        if duplicate_asins:
            log.warn(
                f"[Dedup] Filtered {len(duplicate_asins)} already-pinned product(s): "
                f"{', '.join(duplicate_asins[:5])}"
            )
        log.success(f"[Dedup] {len(new_products)} new / {len(duplicate_asins)} duplicate products")

        return new_products, duplicate_asins

    def mark_as_pinned(self, products: list[dict]) -> None:
        """
        Record a list of products as pinned.
        If an ASIN already exists (shouldn't happen after filter), increments pin_count.
        """
        if not products:
            return

        with _get_session() as session:
            for product in products:
                asin = product.get("asin", "")
                if not asin:
                    continue

                existing = session.get(SeenProduct, asin)
                if existing:
                    existing.pin_count += 1
                    existing.last_seen  = datetime.utcnow()
                else:
                    session.add(SeenProduct(
                        asin=      asin,
                        title=     product.get("title", "")[:500],
                        category=  product.get("category", "general"),
                        pin_count= 1,
                        first_seen=datetime.utcnow(),
                        last_seen= datetime.utcnow(),
                    ))

            session.commit()
            log.success(f"[Dedup] Recorded {len(products)} ASINs as pinned")

    def is_seen(self, asin: str) -> bool:
        """Quick single-ASIN check."""
        with _get_session() as session:
            return session.get(SeenProduct, asin) is not None

    def stats(self) -> dict:
        """Return basic stats about the dedup table."""
        with _get_session() as session:
            total   = session.query(SeenProduct).count()
            by_cat  = {}
            rows    = session.query(SeenProduct.category, SeenProduct.asin).all()
            for row in rows:
                by_cat[row.category] = by_cat.get(row.category, 0) + 1
            return {"total_seen": total, "by_category": by_cat}

    def history(self, limit: int = 20) -> list[dict]:
        """Return most recently pinned products."""
        with _get_session() as session:
            rows = (
                session.query(SeenProduct)
                .order_by(SeenProduct.last_seen.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "asin":       r.asin,
                    "title":      r.title,
                    "category":   r.category,
                    "pin_count":  r.pin_count,
                    "last_seen":  r.last_seen.isoformat() if r.last_seen else "",
                }
                for r in rows
            ]


# Singleton
dedup_store = DedupStore()
