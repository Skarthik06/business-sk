"""
rag/store_base.py  —  Shared SQLAlchemy engine/session for the Autopilot stores.

One engine (pooled, pre-ping) over the project's PostgreSQL DB (DATABASE_URL), shared by
the Phase 4-10 relational stores so we don't open a new engine per module. Each store
defines its own DeclarativeBase and calls ensure(Base) once to create its tables.
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import cfg
from utils.logger import log

_engine = None
_SessionLocal = None
_ensured: set = set()


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(cfg.storage.sqlalchemy_url, pool_pre_ping=True, echo=False)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def ensure(base, label: str = "") -> None:
    """Create a Base's tables once per process (idempotent)."""
    key = id(base)
    if key in _ensured:
        return
    try:
        base.metadata.create_all(_get_engine())
        _ensured.add(key)
        if label:
            log.success(f"[{label}] tables ready ✓")
    except Exception as e:
        log.warning(f"[store_base] create_all failed ({label}): {e}")


@contextmanager
def session() -> Session:
    _get_engine()
    s = _SessionLocal()
    try:
        yield s
    finally:
        s.close()
