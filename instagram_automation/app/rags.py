"""The "rags" store — encapsulated credential & settings management.

Everything the UI Settings panel reads/writes goes through here:
  - Instagram accounts (label, niche, business id, access token, active)
  - App settings (News API key, GitHub hosting overrides, batch sizes)

The OpenAI key is intentionally NOT stored here — it lives in `.env`.

Secrets are masked when listed for the frontend; full values are only ever
read internally (e.g. by the Instagram publisher).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app import crypto, settings
from app.db import connect, init_db

VALID_NICHES = ("quotes", "news", "both")


# ===================== ACCOUNTS =====================

def _mask(secret: Optional[str]) -> str:
    if not secret:
        return ""
    s = secret.strip()
    if len(s) <= 8:
        return "•" * len(s)
    return f"{s[:4]}{'•' * 8}{s[-4:]}"


def _account_public(row: Dict[str, Any]) -> Dict[str, Any]:
    """Account dict safe to send to the frontend (token masked, never raw)."""
    return {
        "id": row["id"],
        "label": row["label"],
        "handle": (row["handle"] if "handle" in row.keys() else "") or "",
        "niche": row["niche"],
        "ig_business_id": row["ig_business_id"] or "",
        "ig_access_token_masked": _mask(crypto.decrypt(row["ig_access_token"])),
        "has_token": bool(row["ig_access_token"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


# ===================== AFFILIATE PROGRAM ACCOUNTS =====================
# Secrets (api_key/api_secret) are Fernet-encrypted at rest (.ragskey) and masked
# for the frontend. tracking_id/tag is semi-public (it appears in affiliate URLs).

def _affiliate_public(row: Dict[str, Any]) -> Dict[str, Any]:
    """Affiliate account dict safe for the frontend (secrets masked, never raw)."""
    return {
        "id": row["id"],
        "program": row["program"],
        "label": row["label"] or "",
        "tracking_id": row["tracking_id"] or "",
        "api_key_masked": _mask(crypto.decrypt(row["api_key"])),
        "has_api_key": bool(row["api_key"]),
        "has_api_secret": bool(row["api_secret"]),
        "link_template": row["link_template"] or "",
        "notes": row["notes"] or "",
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }


def list_affiliate_accounts(active_only: bool = False) -> List[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        q = "SELECT * FROM affiliate_accounts"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY id"
        cur.execute(q)
        return [_affiliate_public(dict(r)) for r in cur.fetchall()]


def get_affiliate_account(account_id: int, *, with_secret: bool = False) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM affiliate_accounts WHERE id = %s", (account_id,))
        row = cur.fetchone()
    if not row:
        return None
    if with_secret:
        d = dict(row)
        d["api_key"] = crypto.decrypt(d["api_key"])          # plaintext for internal use
        d["api_secret"] = crypto.decrypt(d["api_secret"])
        return d
    return _affiliate_public(dict(row))


def add_affiliate_account(*, program: str, label: str = "", tracking_id: str = "",
                          api_key: str = "", api_secret: str = "", link_template: str = "",
                          notes: str = "", is_active: bool = True) -> Dict[str, Any]:
    init_db()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO affiliate_accounts
               (program, label, tracking_id, api_key, api_secret, link_template, notes, is_active)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (program.strip(), label.strip(), tracking_id.strip(),
             crypto.encrypt(api_key.strip()), crypto.encrypt(api_secret.strip()),
             link_template.strip(), notes.strip(), int(is_active)),
        )
        new_id = cur.fetchone()["id"]
    return get_affiliate_account(new_id)


def update_affiliate_account(account_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {"program", "label", "tracking_id", "api_key", "api_secret",
               "link_template", "notes", "is_active"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in ("api_key", "api_secret"):
            if not str(value).strip():
                continue                                     # blank = keep existing secret
            value = crypto.encrypt(str(value).strip())
        elif key == "is_active":
            value = int(bool(value))
        sets.append(f"{key} = %s"); vals.append(value)
    if not sets:
        return get_affiliate_account(account_id)
    vals.append(account_id)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE affiliate_accounts SET {', '.join(sets)} WHERE id = %s", tuple(vals))
    return get_affiliate_account(account_id)


def delete_affiliate_account(account_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM affiliate_accounts WHERE id = %s", (account_id,))
        return cur.rowcount > 0


def active_amazon_tag() -> Optional[str]:
    """The tracking_id of the active Amazon Associates account, if any — so link
    building can prefer the stored tag over the .env default."""
    for a in list_affiliate_accounts(active_only=True):
        if a["program"] == "amazon_associates" and a["tracking_id"]:
            return a["tracking_id"]
    return None


def list_accounts(niche: Optional[str] = None, active_only: bool = False) -> List[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        clauses, params = [], []
        if niche:
            # 'both' accounts match any niche filter
            clauses.append("(niche = ? OR niche = 'both')")
            params.append(niche)
        if active_only:
            clauses.append("is_active = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(f"SELECT * FROM accounts{where} ORDER BY id", params)
        return [_account_public(dict(r)) for r in cur.fetchall()]


def get_account(account_id: int, *, with_secret: bool = False) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
    if not row:
        return None
    if with_secret:
        d = dict(row)
        d["ig_access_token"] = crypto.decrypt(d["ig_access_token"])  # plaintext for the publisher
        return d
    return _account_public(dict(row))


def _clean_handle(handle: str) -> str:
    """Normalise an IG handle: drop a leading @ and surrounding whitespace."""
    return (handle or "").strip().lstrip("@").strip()


def add_account(
    *, label: str, niche: str, ig_business_id: str, ig_access_token: str,
    handle: str = "", is_active: bool = True,
) -> Dict[str, Any]:
    # Any non-empty niche is accepted (custom niches like 'affiliate' are allowed);
    # quotes/news/both keep their special meaning for slide-overlay resolution.
    niche = (niche or "").strip().lower() or "quotes"
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO accounts (label, handle, niche, ig_business_id, ig_access_token, is_active)
               VALUES (?, ?, ?, ?, ?, ?)
               RETURNING id""",
            (label.strip(), _clean_handle(handle), niche, ig_business_id.strip(),
             crypto.encrypt(ig_access_token.strip()), int(is_active)),
        )
        new_id = int(cur.fetchone()["id"])
    return get_account(new_id)  # type: ignore[return-value]


def update_account(account_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {"label", "handle", "niche", "ig_business_id", "ig_access_token", "is_active"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "niche":
            value = (str(value).strip().lower() or "quotes")   # accept any custom niche
        if key == "is_active":
            value = int(bool(value))
        # An empty token from the UI means "leave unchanged" (it was masked).
        if key == "ig_access_token" and not str(value).strip():
            continue
        sets.append(f"{key} = ?")
        if key == "ig_access_token":
            params.append(crypto.encrypt(str(value).strip()))
        elif key == "handle":
            params.append(_clean_handle(str(value)))
        else:
            params.append(value.strip() if isinstance(value, str) else value)
    if not sets:
        return get_account(account_id)
    params.append(account_id)
    with connect() as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params)
    return get_account(account_id)


def handle_for_niche(niche: str) -> Optional[str]:
    """The IG @handle to overlay on a niche's slides.

    Resolves to the handle of the first active account serving this niche
    (so news slides show the news page, quotes the quotes page). Falls back
    to that account's label, then to None — in which case the renderer uses
    the config.json / DEFAULT_HANDLE value.
    """
    accounts = list_accounts(niche=niche, active_only=True) or list_accounts(niche=niche)
    if not accounts:
        return None
    acct = accounts[0]
    return _clean_handle(acct.get("handle", "")) or _clean_handle(acct.get("label", "")) or None


def delete_account(account_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return cur.rowcount > 0


# ===================== APP SETTINGS =====================

# Keys whose values are secrets and must be masked when listed.
_SECRET_SETTINGS = {"news_api_key", "github_token"}

_DEFAULT_SETTINGS = {
    "news_api_key": "",
    "github_username": settings.GITHUB_USERNAME,
    "github_repo": settings.GITHUB_REPO,
    "github_branch": settings.GITHUB_BRANCH,
    "github_token": "",   # PAT with contents:write — enables in-container publishing
    "posts_per_batch": str(settings.DEFAULT_POSTS_PER_BATCH),
    "slides_per_post": str(settings.DEFAULT_SLIDES_PER_POST),
    # Brand hashtags appended to every caption (space/comma separated).
    # Overrides config.json hashtags.fixed when non-empty.
    "fixed_hashtags": "",
}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cur.fetchone()
    if row is not None:
        # Secret settings are stored Fernet-encrypted (.ragskey). crypto.decrypt
        # passes legacy plaintext through unchanged, so old rows still read.
        return crypto.decrypt(row["value"]) if key in _SECRET_SETTINGS else row["value"]
    return default if default is not None else _DEFAULT_SETTINGS.get(key)


def set_setting(key: str, value: str) -> None:
    # Empty value for a secret means "leave unchanged".
    if key in _SECRET_SETTINGS and not str(value).strip():
        return
    # Encrypt secrets at rest so a DB dump/leak never exposes them without .ragskey.
    stored = crypto.encrypt(str(value)) if key in _SECRET_SETTINGS else value
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, to_char(now(),'YYYY-MM-DD HH24:MI:SS'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = to_char(now(),'YYYY-MM-DD HH24:MI:SS')""",
            (key, stored),
        )


def get_public_settings() -> Dict[str, Any]:
    """All settings for the UI, secrets masked. Also reports LLM-key presence."""
    out: Dict[str, Any] = {}
    for key in _DEFAULT_SETTINGS:
        val = get_setting(key) or ""
        out[key] = _mask(val) if key in _SECRET_SETTINGS else val
        if key in _SECRET_SETTINGS:
            out[f"{key}_set"] = bool(val)
    out["openai_key_set"] = bool(settings.OPENAI_API_KEY)
    out["openai_model"] = settings.OPENAI_MODEL
    return out


def get_int_setting(key: str, fallback: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(get_setting(key) or fallback)))
    except (TypeError, ValueError):
        return fallback


# ===================== SEED / MIGRATION =====================

def encrypt_legacy_tokens() -> None:
    """Re-encrypt any plaintext tokens left from earlier runs / the .env seed."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, ig_access_token FROM accounts")
        for row in cur.fetchall():
            tok = row["ig_access_token"]
            if tok and not crypto.is_encrypted(tok):
                conn.execute(
                    "UPDATE accounts SET ig_access_token = ? WHERE id = ?",
                    (crypto.encrypt(tok), row["id"]),
                )


def encrypt_legacy_settings() -> None:
    """Re-encrypt any secret app_settings (github_token, news_api_key) still stored
    as plaintext — e.g. rows recovered from an older DB. Idempotent: already-encrypted
    (`enc:`) values are skipped."""
    with connect() as conn:
        cur = conn.cursor()
        for key in _SECRET_SETTINGS:
            cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            if row and row["value"] and not crypto.is_encrypted(row["value"]):
                conn.execute("UPDATE app_settings SET value = ? WHERE key = ?",
                             (crypto.encrypt(row["value"]), key))


def seed_from_env() -> None:
    """First-run convenience: migrate a single .env account into rags."""
    init_db()
    encrypt_legacy_settings()    # encrypt any plaintext secret settings at rest (.ragskey)
    if list_accounts():
        encrypt_legacy_tokens()  # secure any pre-existing plaintext token
        return
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    biz = os.getenv("INSTAGRAM_BUSINESS_ID", "").strip()
    if token and biz:
        add_account(
            label="Quotes (from .env)",
            niche="quotes",
            ig_business_id=biz,
            ig_access_token=token,
        )
