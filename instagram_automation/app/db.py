"""PostgreSQL data layer: connection helper, schema, and published-post history.

Migrated from SQLite to PostgreSQL for the containerized ("Instagram_Business")
deployment. A thin compatibility shim keeps the rest of the codebase
(`rags.py`, `generator.py`) using the same cursor API it always had:

  * ``?`` placeholders are rewritten to psycopg's ``%s``
  * rows come back as dicts (so ``row["col"]``, ``dict(row)`` and ``row.keys()``
    all keep working exactly as they did with ``sqlite3.Row``)

A single Postgres database holds the same three concerns as before:
  - `accounts`        : Instagram accounts + their Graph API creds  (rags)
  - `app_settings`    : misc keys (News API key, hosting overrides) (rags)
  - `published_posts` : history of what was actually posted
  - `used_quotes`     : posted quote bodies (de-duplication)

On first boot a one-time migration copies any existing local `posts.db`
(SQLite) into Postgres so linked accounts and history are preserved.
"""
from __future__ import annotations

import json
import re as _re
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import psycopg
from psycopg.rows import dict_row

from app.settings import DATABASE_URL, LEGACY_SQLITE_DB

# A Postgres string expression that reproduces SQLite's `datetime('now')`
# (e.g. "2026-08-22 10:20:30"), so `created_at` / `updated_at` stay plain
# strings for the frontend just like before.
_NOW = "to_char(now(),'YYYY-MM-DD HH24:MI:SS')"


# ===================== CONNECTION SHIM =====================

def _q(sql: str) -> str:
    """Translate SQLite-style ``?`` placeholders to psycopg's ``%s``.

    None of the SQL in this codebase contains a literal ``?`` inside a string,
    so a plain replace is safe.
    """
    return sql.replace("?", "%s")


class _Cursor:
    """Wraps a psycopg cursor to accept ``?`` placeholders and dict rows."""

    def __init__(self, cur: psycopg.Cursor) -> None:
        self._cur = cur

    def execute(self, sql: str, params: Any = ()) -> "_Cursor":
        self._cur.execute(_q(sql), params)
        return self

    def executemany(self, sql: str, seq: Any) -> "_Cursor":
        self._cur.executemany(_q(sql), seq)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def __iter__(self):
        return iter(self._cur)


class _Connection:
    """Minimal sqlite3-Connection-compatible wrapper over psycopg."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor())

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        cur = self._conn.cursor()
        cur.execute(_q(sql), params)
        return _Cursor(cur)

    def executemany(self, sql: str, seq: Any) -> _Cursor:
        cur = self._conn.cursor()
        cur.executemany(_q(sql), seq)
        return _Cursor(cur)

    def commit(self) -> None:
        self._conn.commit()


# Connection reuse. The DB is Supabase over the internet: a NEW connection per query cost ~29 ms
# and one Postgres slot each (the DB allows 60, shared with the other services) — a burst of
# comments, each doing a few queries from parallel worker threads, could exhaust the slots and
# fail events. Reused connections cost ~2.5 ms. Idle connections wait in a small stack; at most
# _DB_MAX_ACTIVE are open at once (others wait briefly instead of overloading the database).
import queue as _queue
import threading as _threading
import time as _time

_DB_IDLE_MAX = 8
_DB_MAX_ACTIVE = 20
_IDLE: "_queue.LifoQueue" = _queue.LifoQueue(maxsize=_DB_IDLE_MAX)
_ACTIVE = _threading.BoundedSemaphore(_DB_MAX_ACTIVE)


def _fresh() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _take() -> psycopg.Connection:
    while True:
        try:
            conn, idle_since = _IDLE.get_nowait()
        except _queue.Empty:
            return _fresh()
        if conn.closed or conn.broken:
            continue
        if _time.time() - idle_since > 20:                 # the pooler may have dropped it — check
            try:
                conn.execute("SELECT 1")
                conn.rollback()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                continue
        return conn


def _give(conn: psycopg.Connection) -> None:
    try:
        if (not conn.closed and not conn.broken
                and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE):
            _IDLE.put_nowait((conn, _time.time()))
            return
    except _queue.Full:
        pass
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def connect() -> Iterator[_Connection]:
    # nested connect() calls in one thread can't deadlock: after 15 s we go ahead regardless
    slot = _ACTIVE.acquire(timeout=15)
    try:
        conn = _take()
        ok = False
        try:
            yield _Connection(conn)
            conn.commit()
            ok = True
        finally:
            if not ok:
                try:
                    conn.rollback()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            _give(conn)
    finally:
        if slot:
            _ACTIVE.release()


# ===================== SCHEMA =====================

def init_db() -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS accounts (
                id              SERIAL PRIMARY KEY,
                label           TEXT NOT NULL,
                handle          TEXT DEFAULT '',
                niche           TEXT NOT NULL DEFAULT 'quotes',
                ig_business_id  TEXT,
                ig_access_token TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT DEFAULT ({_NOW})
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT ({_NOW})
            )
            """
        )
        # Affiliate program accounts (Amazon Associates, EarnKaro, Cuelinks, …).
        # Secret fields (api_key/api_secret) are Fernet-encrypted at rest (.ragskey);
        # the tracking_id/tag is semi-public (it appears in affiliate URLs) so it is
        # stored plaintext. Managed from the studio's Accounts panel.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS affiliate_accounts (
                id            SERIAL PRIMARY KEY,
                program       TEXT NOT NULL,               -- amazon_associates | earnkaro | cuelinks | …
                label         TEXT NOT NULL DEFAULT '',
                tracking_id   TEXT DEFAULT '',             -- associate tag / publisher id (semi-public)
                api_key       TEXT DEFAULT '',             -- encrypted at rest
                api_secret    TEXT DEFAULT '',             -- encrypted at rest
                link_template TEXT DEFAULT '',             -- optional network redirect template
                notes         TEXT DEFAULT '',
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT DEFAULT ({_NOW})
            )
            """
        )
        # Posted quote bodies, normalized, to avoid repeating quotes over time.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS used_quotes (
                norm       TEXT PRIMARY KEY,
                quote      TEXT,
                created_at TEXT DEFAULT ({_NOW})
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS published_posts (
                id            SERIAL PRIMARY KEY,
                account_id    INTEGER,
                account_label TEXT,
                niche         TEXT,
                caption       TEXT,
                media_type    TEXT,
                ig_media_id   TEXT,
                permalink     TEXT,
                cover_url     TEXT,
                slide_urls    TEXT,
                created_at    TEXT DEFAULT ({_NOW})
            )
            """
        )
    migrate_legacy_sqlite()


# ===================== ONE-TIME SQLITE -> POSTGRES MIGRATION =====================

def _pg_is_empty() -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM accounts")
        accounts = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM published_posts")
        posts = int(cur.fetchone()["n"])
    return accounts == 0 and posts == 0


def migrate_legacy_sqlite() -> None:
    """Copy an existing local `posts.db` (SQLite) into Postgres, once.

    Runs only when the Postgres store is still empty and a legacy SQLite file
    is present. Preserves primary keys for `accounts` / `published_posts` and
    fixes the id sequences afterwards so future inserts don't collide.
    Encrypted IG tokens carry over unchanged (they decrypt with the same
    `.ragskey`, which is mounted into the container).
    """
    if not LEGACY_SQLITE_DB or not LEGACY_SQLITE_DB.exists():
        return
    if not _pg_is_empty():
        return

    sq = sqlite3.connect(str(LEGACY_SQLITE_DB))
    sq.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in sq.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        with connect() as conn:
            cur = conn.cursor()

            if "accounts" in tables:
                for row in sq.execute("SELECT * FROM accounts").fetchall():
                    keys = row.keys()
                    cur.execute(
                        """INSERT INTO accounts
                             (id, label, handle, niche, ig_business_id,
                              ig_access_token, is_active, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT (id) DO NOTHING""",
                        (
                            row["id"], row["label"],
                            (row["handle"] if "handle" in keys else "") or "",
                            row["niche"], row["ig_business_id"],
                            row["ig_access_token"], row["is_active"],
                            row["created_at"],
                        ),
                    )

            if "app_settings" in tables:
                for row in sq.execute("SELECT * FROM app_settings").fetchall():
                    cur.execute(
                        """INSERT INTO app_settings (key, value, updated_at)
                           VALUES (?, ?, ?)
                           ON CONFLICT (key) DO NOTHING""",
                        (row["key"], row["value"], row["updated_at"]),
                    )

            if "used_quotes" in tables:
                for row in sq.execute("SELECT * FROM used_quotes").fetchall():
                    cur.execute(
                        """INSERT INTO used_quotes (norm, quote, created_at)
                           VALUES (?, ?, ?)
                           ON CONFLICT (norm) DO NOTHING""",
                        (row["norm"], row["quote"], row["created_at"]),
                    )

            if "published_posts" in tables:
                for row in sq.execute("SELECT * FROM published_posts").fetchall():
                    cur.execute(
                        """INSERT INTO published_posts
                             (id, account_id, account_label, niche, caption,
                              media_type, ig_media_id, permalink, cover_url,
                              slide_urls, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT (id) DO NOTHING""",
                        (
                            row["id"], row["account_id"], row["account_label"],
                            row["niche"], row["caption"], row["media_type"],
                            row["ig_media_id"], row["permalink"], row["cover_url"],
                            row["slide_urls"], row["created_at"],
                        ),
                    )

            # Re-align auto-increment sequences to the max id we just inserted.
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('accounts','id'), "
                "GREATEST((SELECT COALESCE(MAX(id),0) FROM accounts), 1))"
            )
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('published_posts','id'), "
                "GREATEST((SELECT COALESCE(MAX(id),0) FROM published_posts), 1))"
            )
        print(f"[db] migrated legacy SQLite store from {LEGACY_SQLITE_DB}")
    finally:
        sq.close()


# ===================== PUBLISHED POSTS =====================

def save_published_post(
    *,
    account_id: Optional[int],
    account_label: str,
    niche: str,
    caption: str,
    media_type: str,
    ig_media_id: Optional[str],
    permalink: Optional[str],
    cover_url: Optional[str],
    slide_urls: List[str],
) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO published_posts
                (account_id, account_label, niche, caption, media_type,
                 ig_media_id, permalink, cover_url, slide_urls)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                account_id,
                account_label,
                niche,
                caption,
                media_type,
                ig_media_id,
                permalink,
                cover_url,
                json.dumps(slide_urls),
            ),
        )
        return int(cur.fetchone()["id"])


def _row_to_post(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["slide_urls"] = json.loads(d.get("slide_urls") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["slide_urls"] = []
    return d


def get_published_posts(limit: int = 100, niche: Optional[str] = None) -> List[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        if niche:
            cur.execute(
                "SELECT * FROM published_posts WHERE niche = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (niche, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM published_posts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_post(r) for r in cur.fetchall()]


def count_published_posts() -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM published_posts")
        return int(cur.fetchone()["n"])


# ===================== USED QUOTES (de-duplication) =====================

def normalize_quote(text: str) -> str:
    """Lowercase, strip punctuation/whitespace — for duplicate comparison."""
    return _re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def add_used_quotes(quotes: List[str]) -> None:
    rows = [(normalize_quote(q), q.strip()) for q in quotes if q and q.strip()]
    rows = [(n, q) for n, q in rows if n]
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            "INSERT INTO used_quotes (norm, quote) VALUES (?, ?) "
            "ON CONFLICT (norm) DO NOTHING",
            rows,
        )


def get_recent_quote_texts(limit: int = 20) -> List[str]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT quote FROM used_quotes ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [r["quote"] for r in cur.fetchall()]


def get_used_quote_norms() -> set:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT norm FROM used_quotes")
        return {r["norm"] for r in cur.fetchall()}
