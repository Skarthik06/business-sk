"""Persistence for the engagement platform (Phase 1 data model).

Reuses the app's Postgres connection. Every row carries workspace_id + social_account_id
for strict isolation (Spec sections 4, 14, 43). Single-admin app -> workspace_id defaults
to 1; social_account_id is the connected Instagram account id from the rags store.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.business.store import Jsonb, connect
from app.engagement import rules as R

_DEFAULT_WS = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eng_posts (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1,
  social_account_id BIGINT NOT NULL,
  ig_media_id TEXT, media_type TEXT, caption TEXT, permalink TEXT, thumbnail_url TEXT,
  published_at TIMESTAMPTZ, status TEXT DEFAULT 'PUBLISHED', creation_id TEXT,
  carousel_parent_id TEXT, slide_count INT,
  UNIQUE(social_account_id, ig_media_id)
);
CREATE TABLE IF NOT EXISTS eng_insights (
  id BIGSERIAL PRIMARY KEY, post_id BIGINT REFERENCES eng_posts(id) ON DELETE CASCADE,
  likes INT, comments INT, saves INT, shares INT, reach INT, impressions INT, views INT,
  synced_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eng_events (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT NOT NULL,
  platform TEXT DEFAULT 'instagram', event_type TEXT NOT NULL,
  external_event_id TEXT NOT NULL, post_id TEXT, comment_id TEXT, conversation_id TEXT,
  payload JSONB, received_at TIMESTAMPTZ DEFAULT now(), processed_at TIMESTAMPTZ,
  status TEXT DEFAULT 'PENDING', error_message TEXT,
  UNIQUE(social_account_id, external_event_id)
);
CREATE TABLE IF NOT EXISTS eng_rules (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT NOT NULL,
  post_id TEXT, name TEXT NOT NULL, enabled BOOLEAN DEFAULT TRUE,
  trigger_type TEXT NOT NULL, match_mode TEXT DEFAULT 'all', priority INT DEFAULT 100,
  conditions JSONB NOT NULL DEFAULT '[]', actions JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eng_conversations (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT NOT NULL,
  ig_user_ref TEXT, source_post_id TEXT, first_message_at TIMESTAMPTZ,
  last_message_at TIMESTAMPTZ, status TEXT DEFAULT 'OPEN', tags JSONB DEFAULT '[]',
  automation_status TEXT
);
CREATE TABLE IF NOT EXISTS eng_messages (
  id BIGSERIAL PRIMARY KEY, conversation_id BIGINT REFERENCES eng_conversations(id) ON DELETE CASCADE,
  direction TEXT, message_type TEXT DEFAULT 'text', text TEXT, external_message_id TEXT,
  sent_at TIMESTAMPTZ DEFAULT now(), status TEXT, automation_rule_id BIGINT
);
CREATE TABLE IF NOT EXISTS eng_comments (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT NOT NULL,
  post_id TEXT, external_comment_id TEXT, username TEXT, user_id TEXT, text TEXT,
  parent_id TEXT, reply_status TEXT, automation_status TEXT, created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(social_account_id, external_comment_id)
);
CREATE TABLE IF NOT EXISTS eng_executions (
  id BIGSERIAL PRIMARY KEY,
  rule_id BIGINT, social_account_id BIGINT NOT NULL, post_id TEXT, event_id BIGINT,
  comment_id TEXT, conversation_id TEXT, action_type TEXT, status TEXT,
  request_reference TEXT, error_code TEXT, error_message TEXT, executed_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eng_audit (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT,
  action TEXT, entity TEXT, entity_id TEXT, detail JSONB, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eng_leads (
  id BIGSERIAL PRIMARY KEY,
  workspace_id BIGINT NOT NULL DEFAULT 1, social_account_id BIGINT NOT NULL,
  conversation_id BIGINT, source_post_id TEXT, username TEXT, label TEXT,
  status TEXT DEFAULT 'NEW', rule_id BIGINT,
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(social_account_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS eng_lead_events (
  id BIGSERIAL PRIMARY KEY, lead_id BIGINT REFERENCES eng_leads(id) ON DELETE CASCADE,
  from_status TEXT, to_status TEXT, note TEXT, created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE eng_executions ADD COLUMN IF NOT EXISTS retry_count INT DEFAULT 0;
ALTER TABLE eng_executions ADD COLUMN IF NOT EXISTS retryable BOOLEAN DEFAULT FALSE;
ALTER TABLE eng_conversations ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ;
ALTER TABLE eng_conversations ADD COLUMN IF NOT EXISTS assigned_to TEXT;
ALTER TABLE eng_comments ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;
-- Business-SK affiliate posts live in eng_posts too, tagged source='affiliate' so the
-- poller can sync their comments WITHOUT touching the real-estate (JK) flow, which reads
-- bstore.list_published_posts() directly. category/products ground the auto-DM link.
ALTER TABLE eng_posts ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'business';
ALTER TABLE eng_posts ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE eng_posts ADD COLUMN IF NOT EXISTS products JSONB DEFAULT '[]';
CREATE INDEX IF NOT EXISTS ix_eng_rules_acct ON eng_rules(social_account_id, enabled);
CREATE INDEX IF NOT EXISTS ix_eng_events_acct ON eng_events(social_account_id, status);
CREATE INDEX IF NOT EXISTS ix_eng_exec_rule ON eng_executions(rule_id, executed_at);
CREATE INDEX IF NOT EXISTS ix_eng_comments_post ON eng_comments(post_id);
CREATE INDEX IF NOT EXISTS ix_eng_leads_acct ON eng_leads(social_account_id, status);
"""

LEAD_STATUSES = ["NEW", "CONTACTED", "QUALIFIED", "SITE_VISIT", "CONVERTED", "LOST"]


def init_schema() -> None:
    with connect() as c:
        for stmt in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
            c.execute(stmt)


# ---- rules ---------------------------------------------------------------
def create_rule(account_id: int, data: Dict[str, Any]) -> int:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""INSERT INTO eng_rules
            (workspace_id, social_account_id, post_id, name, enabled, trigger_type,
             match_mode, priority, conditions, actions)
            VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (_DEFAULT_WS, account_id, data.get("post_id"), data.get("name", "Rule"),
             data.get("enabled", True), data.get("trigger_type", "COMMENT_RECEIVED"),
             data.get("match_mode", "all"), int(data.get("priority", 100)),
             Jsonb(data.get("conditions", [])), Jsonb(data.get("actions", []))))
        return int(cur.fetchone()["id"])


def list_rules(account_id: int) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_rules WHERE social_account_id = ? ORDER BY post_id NULLS LAST, priority, id", (account_id,))
        return [dict(r) for r in cur.fetchall()]


def get_rule(account_id: int, rule_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_rules WHERE id = ? AND social_account_id = ?", (rule_id, account_id))
        row = cur.fetchone()
        return dict(row) if row else None


def duplicate_rule(account_id: int, rule_id: int) -> Optional[int]:
    src = get_rule(account_id, rule_id)
    if not src:
        return None
    src["name"] = f"{src.get('name', 'Rule')} (copy)"
    src["enabled"] = False
    return create_rule(account_id, src)


def rule_executions(account_id: int, rule_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT id, action_type, status, post_id, comment_id, request_reference,
            error_code, error_message, executed_at FROM eng_executions
            WHERE social_account_id=? AND rule_id=? ORDER BY executed_at DESC LIMIT ?""",
            (account_id, rule_id, limit))
        return [dict(r) for r in cur.fetchall()]


def update_rule(account_id: int, rule_id: int, data: Dict[str, Any]) -> bool:
    cols, vals = [], []
    for k in ("name", "enabled", "trigger_type", "match_mode", "priority", "post_id"):
        if k in data:
            cols.append(f"{k} = ?"); vals.append(data[k])
    for k in ("conditions", "actions"):
        if k in data:
            cols.append(f"{k} = ?"); vals.append(Jsonb(data[k]))
    if not cols:
        return False
    cols.append("updated_at = now()")
    with connect() as c:
        cur = c.cursor()
        cur.execute(f"UPDATE eng_rules SET {', '.join(cols)} WHERE id = ? AND social_account_id = ?",
                    (*vals, rule_id, account_id))
        return cur.rowcount > 0


def delete_rule(account_id: int, rule_id: int) -> bool:
    with connect() as c:
        cur = c.cursor()
        cur.execute("DELETE FROM eng_rules WHERE id = ? AND social_account_id = ?", (rule_id, account_id))
        return cur.rowcount > 0


def load_engine_rules(account_id: int) -> List[R.Rule]:
    """Load a workspace's enabled rules as engine Rule objects (isolated per account)."""
    out: List[R.Rule] = []
    for row in list_rules(account_id):
        if not row.get("enabled"):
            continue
        conds = [R.Condition(**{k: v for k, v in c.items() if k in ("operator", "keywords", "case_sensitive", "strip_punct")})
                 for c in (row.get("conditions") or [])]
        acts = [R.Action(**{k: v for k, v in a.items() if k in ("type", "message", "tag", "ai")})
                for a in (row.get("actions") or [])]
        out.append(R.Rule(id=row["id"], name=row["name"], trigger_type=row["trigger_type"],
                          conditions=conds, actions=acts, enabled=True, post_id=row.get("post_id"),
                          priority=row.get("priority", 100), match_mode=row.get("match_mode", "all")))
    return out


# ---- events (idempotent) --------------------------------------------------
def store_event(account_id: int, event_type: str, external_event_id: str,
                payload: Dict[str, Any], *, post_id=None, comment_id=None,
                conversation_id=None) -> Dict[str, Any]:
    """Insert an inbound event. Returns {event_id, is_duplicate}. The UNIQUE
    constraint makes duplicate webhooks a no-op (Spec sections 14, 29)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""INSERT INTO eng_events
            (workspace_id, social_account_id, event_type, external_event_id, post_id,
             comment_id, conversation_id, payload)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT (social_account_id, external_event_id) DO NOTHING
            RETURNING id""",
            (_DEFAULT_WS, account_id, event_type, external_event_id, post_id,
             comment_id, conversation_id, Jsonb(payload)))
        row = cur.fetchone()
        if row:
            return {"event_id": int(row["id"]), "is_duplicate": False}
        cur.execute("SELECT id FROM eng_events WHERE social_account_id = ? AND external_event_id = ?",
                    (account_id, external_event_id))
        r = cur.fetchone()
        return {"event_id": int(r["id"]) if r else None, "is_duplicate": True}


def mark_event(event_id: int, status: str, error: Optional[str] = None) -> None:
    with connect() as c:
        c.execute("UPDATE eng_events SET status = ?, processed_at = now(), error_message = ? WHERE id = ?",
                  (status, error, event_id))


# ---- executions (idempotency + audit) -------------------------------------
def already_executed(rule_id: int, event_id: int) -> bool:
    """Has this rule already acted on this event successfully? (duplicate guard)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT 1 FROM eng_executions
            WHERE rule_id = ? AND event_id = ? AND status IN ('SUCCESS','PROCESSING') LIMIT 1""",
            (rule_id, event_id))
        return cur.fetchone() is not None


def action_already_succeeded(rule_id: int, event_id: int, action_type: str) -> bool:
    """Has THIS specific action for this (rule, event) already succeeded? Per-action guard so a
    FAILED SEND_DM retries next sync even though its REPLY_TO_COMMENT already succeeded."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT 1 FROM eng_executions
            WHERE rule_id = ? AND event_id = ? AND action_type = ? AND status IN ('SUCCESS','PROCESSING') LIMIT 1""",
            (rule_id, event_id, action_type))
        return cur.fetchone() is not None


def first_held_at(account_id: int, comment_id: Optional[str]):
    """When the follow gate first HELD the DM for this comment (None = never held)."""
    if not comment_id:
        return None
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT MIN(executed_at) AS t FROM eng_executions
            WHERE social_account_id=? AND comment_id=? AND action_type='SEND_DM' AND status='HELD'""",
            (account_id, comment_id))
        row = cur.fetchone()
        return row["t"] if row else None


def event_id_for(account_id: int, external_event_id: str) -> Optional[int]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT id FROM eng_events WHERE social_account_id=? AND external_event_id=?",
                    (account_id, external_event_id))
        row = cur.fetchone()
        return int(row["id"]) if row else None


def log_execution(rule_id, account_id: int, action_type: str, status: str, *,
                  post_id=None, event_id=None, comment_id=None, conversation_id=None,
                  request_reference=None, error_code=None, error_message=None) -> int:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""INSERT INTO eng_executions
            (rule_id, social_account_id, post_id, event_id, comment_id, conversation_id,
             action_type, status, request_reference, error_code, error_message)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (rule_id, account_id, post_id, event_id, comment_id, conversation_id,
             action_type, status, request_reference, error_code, error_message))
        return int(cur.fetchone()["id"])


def list_activity(account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Chronological feed of what automations did — the execution log joined to the
    triggering rule + event text. This is the real 'what got sent' inbox view."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT e.id, e.action_type, e.status, e.post_id, e.comment_id,
              e.conversation_id, e.request_reference, e.error_message, e.executed_at,
              r.name AS rule_name,
              ev.event_type, ev.payload
            FROM eng_executions e
            LEFT JOIN eng_rules r ON r.id = e.rule_id
            LEFT JOIN eng_events ev ON ev.id = e.event_id
            WHERE e.social_account_id = ?
            ORDER BY e.executed_at DESC LIMIT ?""", (account_id, limit))
        out = []
        for row in cur.fetchall():
            d = dict(row)
            pl = d.pop("payload", None) or {}
            val = pl.get("value") or pl            # Meta change payloads nest under "value"
            frm = val.get("from") or {}
            d["inbound_text"] = val.get("text") or (val.get("message") or {}).get("text") or ""
            d["username"] = val.get("username") or frm.get("username") or ""
            out.append(d)
        return out


def list_conversations(account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """DM inbox — one row per conversation with its latest message preview."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT co.id, co.ig_user_ref, co.source_post_id, co.status,
              co.automation_status, co.last_message_at, co.tags,
              (SELECT text FROM eng_messages m WHERE m.conversation_id = co.id
                 ORDER BY m.sent_at DESC LIMIT 1) AS last_text,
              (SELECT COUNT(*) FROM eng_messages m WHERE m.conversation_id = co.id) AS message_count
            FROM eng_conversations co
            WHERE co.social_account_id = ?
            ORDER BY co.last_message_at DESC NULLS LAST LIMIT ?""", (account_id, limit))
        return [dict(r) for r in cur.fetchall()]


def list_messages(conversation_id: int) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT direction, message_type, text, status, sent_at, automation_rule_id
            FROM eng_messages WHERE conversation_id = ? ORDER BY sent_at ASC""", (conversation_id,))
        return [dict(r) for r in cur.fetchall()]


def list_comments(account_id: int, post_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Comments tab — inbound comments with their automation/reply status."""
    with connect() as c:
        cur = c.cursor()
        if post_id:
            cur.execute("""SELECT id, post_id, username, text, reply_status, automation_status, created_at
                FROM eng_comments WHERE social_account_id = ? AND post_id = ?
                ORDER BY created_at DESC LIMIT ?""", (account_id, post_id, limit))
        else:
            cur.execute("""SELECT id, post_id, username, text, reply_status, automation_status, created_at
                FROM eng_comments WHERE social_account_id = ?
                ORDER BY created_at DESC LIMIT ?""", (account_id, limit))
        return [dict(r) for r in cur.fetchall()]


def upsert_comment(account_id: int, post_id: Optional[str], external_comment_id: str,
                   username: Optional[str] = None, user_id: Optional[str] = None,
                   text: str = "", parent_id: Optional[str] = None) -> Dict[str, Any]:
    """Store an inbound comment idempotently (UNIQUE social_account_id, external_comment_id).
    Returns {id, is_new}. is_new distinguishes first-seen comments (to auto-respond once)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""INSERT INTO eng_comments
            (social_account_id, post_id, external_comment_id, username, user_id, text, parent_id)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (social_account_id, external_comment_id)
            DO UPDATE SET text = EXCLUDED.text
            RETURNING id, (xmax = 0) AS is_new""",
            (account_id, post_id, external_comment_id, username, user_id, text, parent_id))
        row = cur.fetchone()
        return {"id": int(row["id"]), "is_new": bool(row["is_new"])}


def set_comment_reply_status(account_id: int, external_comment_id: str, status: str) -> None:
    with connect() as c:
        c.cursor().execute(
            "UPDATE eng_comments SET reply_status = ? WHERE social_account_id = ? AND external_comment_id = ?",
            (status, account_id, external_comment_id))


def record_dm(account_id: int, user_ref: str, text: str, external_message_id: Optional[str],
              direction: str = "in", source_post_id: Optional[str] = None,
              rule_id: Optional[int] = None) -> int:
    """Append a DM to its conversation (find-or-create by ig_user_ref). direction
    'in' = received, 'out' = sent by an automation. Returns conversation id."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT id FROM eng_conversations WHERE social_account_id = ? AND ig_user_ref = ?",
                    (account_id, user_ref))
        row = cur.fetchone()
        if row:
            conv_id = int(row["id"])
            cur.execute("UPDATE eng_conversations SET last_message_at = now() WHERE id = ?", (conv_id,))
        else:
            cur.execute("""INSERT INTO eng_conversations
                (social_account_id, ig_user_ref, source_post_id, first_message_at, last_message_at, status)
                VALUES (?,?,?,now(),now(),'OPEN') RETURNING id""", (account_id, user_ref, source_post_id))
            conv_id = int(cur.fetchone()["id"])
        cur.execute("""INSERT INTO eng_messages
            (conversation_id, direction, text, external_message_id, status, automation_rule_id)
            VALUES (?,?,?,?,?,?)""", (conv_id, direction, text, external_message_id, "SENT", rule_id))
        return conv_id


def dm_message_exists(external_message_id: Optional[str]) -> bool:
    """True if a DM with this external id is already stored (dedupe for pull sync)."""
    if not external_message_id:
        return False
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT 1 FROM eng_messages WHERE external_message_id = ? LIMIT 1",
                    (external_message_id,))
        return cur.fetchone() is not None


def post_activity(account_id: int, post_id: str, limit: int = 100) -> Dict[str, Any]:
    """Automation executions + comment counts for one published post (media id)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT action_type, status, COUNT(*) AS n
            FROM eng_executions WHERE social_account_id = ? AND post_id = ?
            GROUP BY action_type, status""", (account_id, post_id))
        by_action = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT e.action_type, e.status, e.executed_at, r.name AS rule_name
            FROM eng_executions e LEFT JOIN eng_rules r ON r.id = e.rule_id
            WHERE e.social_account_id = ? AND e.post_id = ?
            ORDER BY e.executed_at DESC LIMIT ?""", (account_id, post_id, limit))
        recent = [dict(r) for r in cur.fetchall()]
        return {"by_action": by_action, "recent": recent}


def chart_series(account_id: int, days: int = 7) -> Dict[str, Any]:
    """Per-day engagement time series for the overview chart: comments received, DMs sent,
    comment replies, and conversations started. Real data only — zero-filled per day."""
    from datetime import datetime, timedelta, timezone
    days = max(1, min(days, 90))
    start_day = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
    start_ts = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)

    def per_day(sql: str) -> Dict[str, int]:
        with connect() as c:
            cur = c.cursor()
            cur.execute(sql, (account_id, start_ts))
            return {str(r["d"]): int(r["n"]) for r in cur.fetchall()}

    comments = per_day("SELECT to_char(created_at::date,'YYYY-MM-DD') d, COUNT(*) n "
                       "FROM eng_comments WHERE social_account_id=? AND created_at>=? GROUP BY 1")
    dms = per_day("SELECT to_char(executed_at::date,'YYYY-MM-DD') d, COUNT(*) n FROM eng_executions "
                  "WHERE social_account_id=? AND executed_at>=? AND action_type='SEND_DM' AND status='SUCCESS' GROUP BY 1")
    replies = per_day("SELECT to_char(executed_at::date,'YYYY-MM-DD') d, COUNT(*) n FROM eng_executions "
                      "WHERE social_account_id=? AND executed_at>=? AND action_type='REPLY_TO_COMMENT' AND status='SUCCESS' GROUP BY 1")
    convos = per_day("SELECT to_char(first_message_at::date,'YYYY-MM-DD') d, COUNT(*) n "
                     "FROM eng_conversations WHERE social_account_id=? AND first_message_at>=? GROUP BY 1")
    labels, C, D, Rr, V = [], [], [], [], []
    for i in range(days):
        day = (start_day + timedelta(days=i)).isoformat()
        labels.append(day)
        C.append(comments.get(day, 0)); D.append(dms.get(day, 0))
        Rr.append(replies.get(day, 0)); V.append(convos.get(day, 0))
    return {"labels": labels, "comments": C, "dms": D, "replies": Rr, "conversations": V}


def summary_deltas(account_id: int, days: int = 7) -> Dict[str, Any]:
    """Current 5 KPIs plus a % change vs the previous equal window. delta is None when
    there's no prior data to compare against (never fabricate a trend)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cur0 = now - timedelta(days=days)
    prev0 = now - timedelta(days=2 * days)

    def win(sql_win: str, a, b) -> int:
        with connect() as c:
            cur = c.cursor(); cur.execute(sql_win, (account_id, a, b)); return int(cur.fetchone()["n"])

    def metric(sql_win: str):
        cur_n = win(sql_win, cur0, now)
        prev_n = win(sql_win, prev0, cur0)
        delta = round((cur_n - prev_n) / prev_n * 100) if prev_n else None
        return {"value": cur_n, "prev": prev_n, "delta_pct": delta}

    exec_win = ("SELECT COUNT(*) n FROM eng_executions WHERE social_account_id=? "
                "AND executed_at>=? AND executed_at<? AND status='SUCCESS' AND action_type=")
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) n FROM eng_rules WHERE social_account_id=? AND enabled", (account_id,))
        active_rules = int(cur.fetchone()["n"])
    return {
        "active_rules": {"value": active_rules, "delta_pct": None},
        "dms_sent": metric(exec_win + "'SEND_DM'"),
        "replies_sent": metric(exec_win + "'REPLY_TO_COMMENT'"),
        "conversations": metric("SELECT COUNT(*) n FROM eng_conversations WHERE social_account_id=? "
                                "AND first_message_at>=? AND first_message_at<?"),
        "comments": metric("SELECT COUNT(*) n FROM eng_comments WHERE social_account_id=? "
                           "AND created_at>=? AND created_at<?"),
    }


def activity_summary(account_id: int) -> Dict[str, Any]:
    """Top-line counters for the Engagement dashboard header."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT
              (SELECT COUNT(*) FROM eng_rules WHERE social_account_id = ? AND enabled) AS active_rules,
              (SELECT COUNT(*) FROM eng_executions WHERE social_account_id = ? AND action_type='SEND_DM' AND status='SUCCESS') AS dms_sent,
              (SELECT COUNT(*) FROM eng_executions WHERE social_account_id = ? AND action_type='REPLY_TO_COMMENT' AND status='SUCCESS') AS replies_sent,
              (SELECT COUNT(*) FROM eng_conversations WHERE social_account_id = ?) AS conversations,
              (SELECT COUNT(*) FROM eng_comments WHERE social_account_id = ?) AS comments
        """, (account_id, account_id, account_id, account_id, account_id))
        return dict(cur.fetchone() or {})


# ---- Business-SK affiliate posts (isolated from real-estate) --------------
def register_affiliate_post(account_id: int, ig_media_id: str, *, category: str = "",
                            caption: str = "", permalink: Optional[str] = None,
                            media_type: str = "CAROUSEL_ALBUM",
                            products: Optional[List[Dict[str, Any]]] = None) -> int:
    """Record an affiliate carousel in eng_posts (source='affiliate') so the poller
    syncs its comments. Idempotent per (account, media). Keeps affiliate posts in their
    own lane — the real-estate sync reads bstore, never these."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""INSERT INTO eng_posts
            (workspace_id, social_account_id, ig_media_id, media_type, caption, permalink,
             published_at, status, source, category, products)
            VALUES (?,?,?,?,?,?, now(), 'PUBLISHED', 'affiliate', ?, ?)
            ON CONFLICT (social_account_id, ig_media_id)
            DO UPDATE SET caption=EXCLUDED.caption, permalink=EXCLUDED.permalink,
                          category=EXCLUDED.category, products=EXCLUDED.products,
                          source='affiliate'
            RETURNING id""",
            (_DEFAULT_WS, account_id, ig_media_id, media_type, caption, permalink,
             category, Jsonb(products or [])))
        return int(cur.fetchone()["id"])


def list_affiliate_posts(account_id: int) -> List[Dict[str, Any]]:
    """Affiliate posts for this account (for the poller + Posts view). Real-estate
    posts (source='business' or NULL) are deliberately excluded."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT id, ig_media_id, media_type, caption, permalink, category,
                products, published_at
            FROM eng_posts WHERE social_account_id=? AND source='affiliate'
            ORDER BY id DESC""", (account_id,))
        return [dict(r) for r in cur.fetchall()]


def affiliate_products_for_media(account_id: int, ig_media_id: str) -> List[Dict[str, Any]]:
    """The stored products for an affiliate post (by media id) — used to build the
    product-card DM. [] if the post isn't ours/affiliate."""
    if not ig_media_id:
        return []
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT products FROM eng_posts
            WHERE social_account_id=? AND ig_media_id=? AND source='affiliate' LIMIT 1""",
            (account_id, ig_media_id))
        row = cur.fetchone()
        if not row:
            return []
        prods = row["products"]
        return prods if isinstance(prods, list) else (json.loads(prods) if prods else [])


def affiliate_rule_for_post(account_id: int, ig_media_id: str) -> Optional[int]:
    """Return the id of the existing per-post affiliate rule for this media, if any."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT id FROM eng_rules
            WHERE social_account_id=? AND post_id=? ORDER BY id LIMIT 1""",
            (account_id, ig_media_id))
        row = cur.fetchone()
        return int(row["id"]) if row else None


def prune_to_live_posts(account_id: int, live_media_ids: List[str], *,
                        dry_run: bool = True) -> Dict[str, Any]:
    """Drop every engagement row tied to an affiliate post that no longer exists on Instagram:
    its per-post automation, runs, comments, comment events, DM threads (+messages, leads) and
    the post itself. DM events / account-wide threads older than the oldest LIVE post go too.
    Real-estate posts (source<>'affiliate') and account-wide rules are never touched.
    dry_run=True only counts. Leaves a PRUNE row in eng_audit — its time is the inbox cutoff."""
    keep = [str(m) for m in live_media_ids if m]
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT ig_media_id, published_at FROM eng_posts
                       WHERE social_account_id=? AND source='affiliate'""", (account_id,))
        rows = [dict(r) for r in cur.fetchall()]
        gone = [r["ig_media_id"] for r in rows if str(r["ig_media_id"]) not in keep]
        live_times = [r["published_at"] for r in rows if str(r["ig_media_id"]) in keep and r["published_at"]]
        since = min(live_times) if live_times else None
        convs: List[int] = []
        for m in gone:
            cur.execute("SELECT id FROM eng_conversations WHERE social_account_id=? AND source_post_id=?",
                        (account_id, m))
            convs += [int(r["id"]) for r in cur.fetchall()]
        if since is not None:                      # account-wide threads that predate the live post
            cur.execute("""SELECT id FROM eng_conversations WHERE social_account_id=?
                           AND source_post_id IS NULL AND first_message_at < ?""", (account_id, since))
            convs += [int(r["id"]) for r in cur.fetchall()]
        # one statement per id — no empty-array / NULL params reach the driver
        plan = []
        for m in gone:
            plan += [
                ("automations", "DELETE FROM eng_rules WHERE social_account_id=? AND post_id=?", (account_id, m)),
                ("runs", "DELETE FROM eng_executions WHERE social_account_id=? AND post_id=?", (account_id, m)),
                ("comments", "DELETE FROM eng_comments WHERE social_account_id=? AND post_id=?", (account_id, m)),
                ("comment_events", "DELETE FROM eng_events WHERE social_account_id=? AND post_id=?", (account_id, m)),
                ("leads", "DELETE FROM eng_leads WHERE social_account_id=? AND source_post_id=?", (account_id, m)),
            ]
        if since is not None:
            plan.append(("dm_events", """DELETE FROM eng_events WHERE social_account_id=? AND post_id IS NULL
                                         AND received_at < ?""", (account_id, since)))
        for cid in convs:
            plan += [
                ("leads", "DELETE FROM eng_leads WHERE social_account_id=? AND conversation_id=?", (account_id, cid)),
                ("dm_threads", "DELETE FROM eng_conversations WHERE social_account_id=? AND id=?", (account_id, cid)),
            ]
        for m in gone:
            plan.append(("posts", """DELETE FROM eng_posts WHERE social_account_id=? AND source='affiliate'
                                     AND ig_media_id=?""", (account_id, m)))
        counts: Dict[str, int] = {k: 0 for k in ("automations", "runs", "comments", "comment_events",
                                                 "dm_events", "leads", "dm_threads", "posts")}
        for name, sql, params in plan:
            if dry_run:
                cur.execute("SELECT COUNT(*) AS n FROM " + sql.split("FROM ", 1)[1], params)
                counts[name] += int(cur.fetchone()["n"])
            else:
                cur.execute(sql, params)
                counts[name] += max(cur.rowcount, 0)
        if not dry_run:
            cur.execute("""INSERT INTO eng_audit (workspace_id, social_account_id, action, entity, detail)
                           VALUES (?,?,'PRUNE','eng_posts',?)""",
                        (_DEFAULT_WS, account_id, Jsonb({"kept": keep, "removed_posts": gone, "counts": counts})))
        return {"dry_run": dry_run, "kept_posts": keep, "removed_posts": gone,
                "dm_threads": convs, "counts": counts}


def inbox_cutoff(account_id: int):
    """Time of the last PRUNE — the inbox pull never re-imports DMs older than this."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT MAX(created_at) AS t FROM eng_audit
                       WHERE social_account_id=? AND action='PRUNE'""", (account_id,))
        row = cur.fetchone()
        return row["t"] if row else None


# ---- leads (Spec 31) ------------------------------------------------------
def upsert_lead(account_id: int, conversation_id: Optional[int] = None,
                source_post_id: Optional[str] = None, username: Optional[str] = None,
                label: str = "Potential Lead", rule_id: Optional[int] = None) -> int:
    """Create/refresh a lead from an automation. Idempotent per conversation."""
    with connect() as c:
        cur = c.cursor()
        if conversation_id:
            cur.execute("""INSERT INTO eng_leads
                (social_account_id, conversation_id, source_post_id, username, label, rule_id)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT (social_account_id, conversation_id)
                DO UPDATE SET label=EXCLUDED.label, updated_at=now()
                RETURNING id, (xmax=0) AS is_new""",
                (account_id, conversation_id, source_post_id, username, label, rule_id))
        else:
            cur.execute("""INSERT INTO eng_leads
                (social_account_id, source_post_id, username, label, rule_id)
                VALUES (?,?,?,?,?) RETURNING id, TRUE AS is_new""",
                (account_id, source_post_id, username, label, rule_id))
        row = cur.fetchone(); lid = int(row["id"])
        if row["is_new"]:
            cur.execute("INSERT INTO eng_lead_events (lead_id, from_status, to_status, note) VALUES (?,?,?,?)",
                        (lid, None, "NEW", "created by automation"))
        return lid


def list_leads(account_id: int, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        if status:
            cur.execute("SELECT * FROM eng_leads WHERE social_account_id=? AND status=? ORDER BY updated_at DESC LIMIT ?",
                        (account_id, status, limit))
        else:
            cur.execute("SELECT * FROM eng_leads WHERE social_account_id=? ORDER BY updated_at DESC LIMIT ?",
                        (account_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_lead(account_id: int, lead_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_leads WHERE id=? AND social_account_id=?", (lead_id, account_id))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        cur.execute("SELECT from_status, to_status, note, created_at FROM eng_lead_events WHERE lead_id=? ORDER BY id", (lead_id,))
        d["events"] = [dict(r) for r in cur.fetchall()]
        return d


def set_lead_status(account_id: int, lead_id: int, status: str, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if status not in LEAD_STATUSES:
        return None
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT status FROM eng_leads WHERE id=? AND social_account_id=?", (lead_id, account_id))
        row = cur.fetchone()
        if not row:
            return None
        old = row["status"]
        cur.execute("UPDATE eng_leads SET status=?, updated_at=now() WHERE id=?", (status, lead_id))
        cur.execute("INSERT INTO eng_lead_events (lead_id, from_status, to_status, note) VALUES (?,?,?,?)",
                    (lead_id, old, status, note))
        return {"id": lead_id, "status": status}


def lead_for_conversation(account_id: int, conversation_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_leads WHERE social_account_id=? AND conversation_id=?", (account_id, conversation_id))
        row = cur.fetchone()
        return dict(row) if row else None


def lead_stats(account_id: int) -> Dict[str, int]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT status, COUNT(*) n FROM eng_leads WHERE social_account_id=? GROUP BY status", (account_id,))
        return {r["status"]: int(r["n"]) for r in cur.fetchall()}


# ---- conversation management (Spec 20) ------------------------------------
def set_conversation_status(account_id: int, conv_id: int, status: str) -> bool:
    with connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE eng_conversations SET status=? WHERE id=? AND social_account_id=?",
                    (status, conv_id, account_id))
        return cur.rowcount > 0


def mark_conversation_read(account_id: int, conv_id: int) -> bool:
    with connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE eng_conversations SET last_read_at=now() WHERE id=? AND social_account_id=?",
                    (conv_id, account_id))
        return cur.rowcount > 0


def assign_conversation(account_id: int, conv_id: int, assignee: Optional[str]) -> bool:
    with connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE eng_conversations SET assigned_to=? WHERE id=? AND social_account_id=?",
                    (assignee, conv_id, account_id))
        return cur.rowcount > 0


def unread_conversation_count(account_id: int) -> int:
    """Conversations with an inbound message newer than last_read_at (or never read)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT COUNT(DISTINCT co.id) n FROM eng_conversations co
            JOIN eng_messages m ON m.conversation_id=co.id AND m.direction='in'
            WHERE co.social_account_id=? AND (co.last_read_at IS NULL OR m.sent_at > co.last_read_at)""",
            (account_id,))
        return int(cur.fetchone()["n"])


def get_conversation(account_id: int, conv_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_conversations WHERE id=? AND social_account_id=?", (conv_id, account_id))
        row = cur.fetchone()
        return dict(row) if row else None


# ---- comment moderation (Spec 17) -----------------------------------------
def mark_comment_read(account_id: int, comment_pk: int) -> bool:
    with connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE eng_comments SET read_at=now() WHERE id=? AND social_account_id=?", (comment_pk, account_id))
        return cur.rowcount > 0


def set_comment_hidden(account_id: int, comment_pk: int, hidden: bool) -> Optional[str]:
    """Flip local reply_status to HIDDEN/visible and return the external comment id (for Graph)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT external_comment_id FROM eng_comments WHERE id=? AND social_account_id=?", (comment_pk, account_id))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("UPDATE eng_comments SET reply_status=? WHERE id=?",
                    ("HIDDEN" if hidden else "VISIBLE", comment_pk))
        return row["external_comment_id"]


def get_comment(account_id: int, comment_pk: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_comments WHERE id=? AND social_account_id=?", (comment_pk, account_id))
        row = cur.fetchone()
        return dict(row) if row else None


# ---- events admin (Spec 28) -----------------------------------------------
def list_events(account_id: int, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        if status:
            cur.execute("""SELECT id, event_type, external_event_id, post_id, comment_id, status,
                received_at, processed_at, error_message FROM eng_events
                WHERE social_account_id=? AND status=? ORDER BY id DESC LIMIT ?""", (account_id, status, limit))
        else:
            cur.execute("""SELECT id, event_type, external_event_id, post_id, comment_id, status,
                received_at, processed_at, error_message FROM eng_events
                WHERE social_account_id=? ORDER BY id DESC LIMIT ?""", (account_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM eng_events WHERE id=?", (event_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# ---- execution retry / dead-letter (Spec 30) ------------------------------
def delete_executions_for_event(event_id: int) -> int:
    """Clear prior executions for an event so it can be force-reprocessed (Spec 28)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("DELETE FROM eng_executions WHERE event_id=?", (event_id,))
        return cur.rowcount


def list_failed_executions(account_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT * FROM eng_executions
            WHERE social_account_id=? AND status='FAILED' ORDER BY executed_at DESC LIMIT ?""",
            (account_id, limit))
        return [dict(r) for r in cur.fetchall()]


def bump_execution(execution_id: int, status: str, retryable: bool = False,
                   request_reference: Optional[str] = None, error_message: Optional[str] = None) -> None:
    with connect() as c:
        cur = c.cursor()
        cur.execute("""UPDATE eng_executions
            SET status=?, retryable=?, retry_count=COALESCE(retry_count,0)+1,
                request_reference=COALESCE(?, request_reference), error_message=?
            WHERE id=?""", (status, retryable, request_reference, error_message, execution_id))


def rule_stats(account_id: int) -> List[Dict[str, Any]]:
    """Per-rule execution counters by status (Spec section 33)."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT r.id, r.name, r.enabled,
            COUNT(e.id) AS executions,
            COUNT(*) FILTER (WHERE e.status='SUCCESS') AS success,
            COUNT(*) FILTER (WHERE e.status='FAILED') AS failed,
            COUNT(*) FILTER (WHERE e.status='SKIPPED') AS skipped,
            COUNT(*) FILTER (WHERE e.status='DUPLICATE') AS duplicate
            FROM eng_rules r LEFT JOIN eng_executions e ON e.rule_id = r.id
            WHERE r.social_account_id = ?
            GROUP BY r.id, r.name, r.enabled ORDER BY executions DESC""", (account_id,))
        return [dict(r) for r in cur.fetchall()]
