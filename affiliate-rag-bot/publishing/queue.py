"""
publishing/queue.py  —  Publishing queue + safe state machine (Phase 5).

The intelligence layer decides WHAT to publish and stages a job here; the IG automation
service (instagram_automation/) decides HOW and executes it. This module owns the durable
queue, the state machine, retries, a global EMERGENCY STOP, and account-health signals.
It performs NO Instagram actions itself (separation of concerns, blueprint §39).

Tables (via rag.store_base shared engine):
  publish_jobs   — one row per queued carousel
  system_flags   — key/value flags (holds the global emergency_stop)

All I/O is JSON-serializable dicts. Never publishes on its own; dry-run and emergency stop
are honoured by the consumer.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.orm import DeclarativeBase

from rag.store_base import ensure, session
from config import cfg
from utils.logger import log


class Base(DeclarativeBase):
    pass


# Safe state machine. DRAFT→QUEUED→SCHEDULED→RUNNING→PUBLISHED; failures branch off.
STATUSES = ["DRAFT", "QUEUED", "SCHEDULED", "RUNNING", "PUBLISHED",
            "FAILED", "NEEDS_REVIEW", "CANCELLED"]
_ALLOWED = {
    "DRAFT": {"QUEUED", "SCHEDULED", "CANCELLED"},
    "QUEUED": {"SCHEDULED", "RUNNING", "CANCELLED"},
    "SCHEDULED": {"QUEUED", "RUNNING", "CANCELLED"},
    "RUNNING": {"PUBLISHED", "FAILED"},
    "FAILED": {"QUEUED", "NEEDS_REVIEW", "CANCELLED"},
    "NEEDS_REVIEW": {"QUEUED", "CANCELLED"},
    "PUBLISHED": set(),
    "CANCELLED": set(),
}


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    job_id        = Column(String(40), primary_key=True)
    account_id    = Column(String(80), nullable=False, default="default")
    category      = Column(String(80), nullable=False, default="")
    payload       = Column(Text, nullable=False, default="{}")   # the draft carousel JSON
    status        = Column(String(20), nullable=False, default="DRAFT")
    scheduled_for = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error    = Column(Text, nullable=True)
    created_at    = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class SystemFlag(Base):
    __tablename__ = "system_flags"
    key   = Column(String(60), primary_key=True)
    value = Column(String(200), nullable=False, default="")


def _init():
    ensure(Base, "publishing")


def _to_dict(r: PublishJob) -> dict:
    return {
        "job_id": r.job_id, "account_id": r.account_id, "category": r.category,
        "payload": json.loads(r.payload or "{}"), "status": r.status,
        "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
        "attempt_count": r.attempt_count, "last_error": r.last_error,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }


class PublishQueue:
    """Durable publishing queue with a safe state machine + emergency stop."""

    # ── flags ────────────────────────────────────────────────────────────
    def emergency_stop(self, on: bool = True) -> dict:
        _init()
        with session() as s:
            f = s.get(SystemFlag, "emergency_stop") or SystemFlag(key="emergency_stop")
            f.value = "1" if on else "0"
            s.merge(f); s.commit()
        log.warning(f"[publishing] EMERGENCY STOP {'ENABLED' if on else 'cleared'}")
        return {"emergency_stop": on}

    def is_stopped(self) -> bool:
        _init()
        try:
            with session() as s:
                f = s.get(SystemFlag, "emergency_stop")
                return bool(f and f.value == "1")
        except Exception:
            return False

    # ── queue ops ────────────────────────────────────────────────────────
    def enqueue(self, category: str, payload: dict, account_id: str = "default",
                scheduled_for: datetime | None = None, status: str = "QUEUED") -> dict:
        """Stage a carousel draft as a job. Honours the global min posting interval by
        auto-scheduling after the latest pending job when no explicit time is given."""
        _init()
        if status not in ("DRAFT", "QUEUED", "SCHEDULED"):
            status = "QUEUED"
        with session() as s:
            if scheduled_for is None and status != "DRAFT":
                # space jobs by the configured minimum interval (anti-shadowban, G4)
                latest = (s.query(PublishJob)
                          .filter(PublishJob.status.in_(["QUEUED", "SCHEDULED"]))
                          .order_by(PublishJob.scheduled_for.desc().nullslast()).first())
                base = datetime.now(timezone.utc)
                if latest and latest.scheduled_for and latest.scheduled_for > base:
                    base = latest.scheduled_for
                scheduled_for = base + timedelta(seconds=cfg.publishing.min_interval_seconds)
            row = PublishJob(job_id=uuid.uuid4().hex[:16], account_id=account_id,
                             category=category, payload=json.dumps(payload),
                             status=status, scheduled_for=scheduled_for)
            s.add(row); s.commit()
            return _to_dict(row)

    def list(self, status: str | None = None, account_id: str | None = None,
             limit: int = 100) -> list[dict]:
        _init()
        with session() as s:
            q = s.query(PublishJob)
            if status:
                q = q.filter(PublishJob.status == status.upper())
            if account_id:
                q = q.filter(PublishJob.account_id == account_id)
            rows = q.order_by(PublishJob.created_at.desc()).limit(max(1, min(limit, 500))).all()
            return [_to_dict(r) for r in rows]

    def transition(self, job_id: str, to: str, error: str | None = None) -> dict:
        """Move a job to a new state IF the transition is allowed. Returns the job or an error."""
        _init()
        to = (to or "").upper()
        if to not in STATUSES:
            return {"ok": False, "error": f"unknown status {to}"}
        with session() as s:
            r = s.get(PublishJob, job_id)
            if not r:
                return {"ok": False, "error": "job not found"}
            if to not in _ALLOWED.get(r.status, set()):
                return {"ok": False, "error": f"illegal transition {r.status}->{to}"}
            if to == "RUNNING" and self.is_stopped():
                return {"ok": False, "error": "emergency stop active — publishing halted"}
            if to == "RUNNING":
                r.attempt_count += 1
            if to == "FAILED":
                r.last_error = error or "unknown"
                # auto-route: retry until max_retries, then needs review
                to = "NEEDS_REVIEW" if r.attempt_count >= cfg.publishing.max_retries else "FAILED"
            r.status = to
            r.updated_at = datetime.now(timezone.utc)
            s.commit()
            return {"ok": True, "job": _to_dict(r)}

    def cancel(self, job_id: str) -> dict:
        return self.transition(job_id, "CANCELLED")

    def next_due(self, account_id: str = "default") -> dict | None:
        """The next job ready to publish (QUEUED/SCHEDULED, due, not emergency-stopped).
        The IG automation service calls this; returns None when nothing is due."""
        _init()
        if self.is_stopped():
            return None
        now = datetime.now(timezone.utc)
        with session() as s:
            r = (s.query(PublishJob)
                 .filter(PublishJob.account_id == account_id,
                         PublishJob.status.in_(["QUEUED", "SCHEDULED"]))
                 .order_by(PublishJob.scheduled_for.asc().nullsfirst())
                 .first())
            if not r:
                return None
            if r.scheduled_for and r.scheduled_for > now:
                return None
            return _to_dict(r)

    def account_health(self, account_id: str = "default") -> dict:
        """Simple health signal from queue state + recent failures + emergency stop."""
        _init()
        with session() as s:
            recent_fail = (s.query(PublishJob)
                           .filter(PublishJob.account_id == account_id,
                                   PublishJob.status.in_(["FAILED", "NEEDS_REVIEW"])).count())
            pending = (s.query(PublishJob)
                       .filter(PublishJob.account_id == account_id,
                               PublishJob.status.in_(["QUEUED", "SCHEDULED"])).count())
        stopped = self.is_stopped()
        if stopped:
            status = "STOPPED"
        elif recent_fail >= 3:
            status = "ATTENTION"
        else:
            status = "HEALTHY"
        return {"account_id": account_id, "status": status, "emergency_stop": stopped,
                "pending_jobs": pending, "failed_or_review": recent_fail}


publish_queue = PublishQueue()
