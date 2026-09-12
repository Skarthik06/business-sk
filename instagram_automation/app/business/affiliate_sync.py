"""
affiliate_sync.py  —  IG insights → affiliate performance loop (fully wired).

For each published affiliate carousel (recorded in the affiliate service with an IG
media id), this pulls the real Instagram insights via the Graph API (using the account
that owns the media) and posts them to the affiliate service's /api/performance/ingest,
which feeds the Learning agent + winner prediction.

Only REAL Graph metrics are sent (reach/saves/likes/comments/shares); link_clicks,
orders and commission come from affiliate networks and stay absent here (never faked).
Fully fail-open: a media that errors is skipped, the rest still sync.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import requests

from app import rags
from app.business import analytics

AFFILIATE_URL = os.getenv("AFFILIATE_URL", "http://affiliate_backend:8100")


def _accounts_with_tokens() -> List[Dict[str, Any]]:
    out = []
    for a in rags.list_accounts():
        full = rags.get_account(a["id"], with_secret=True)
        if full and full.get("ig_access_token"):
            out.append(full)
    return out


def sync_affiliate_performance(limit: int = 50) -> Dict[str, Any]:
    """Pull IG insights for recent affiliate posts and ingest them. Returns a summary."""
    # 1) recent affiliate posts (id + media_id) from the affiliate service
    try:
        r = requests.get(f"{AFFILIATE_URL}/api/posts", params={"limit": limit}, timeout=20)
        posts = (r.json() or {}).get("posts", [])
    except Exception as e:
        return {"ok": False, "error": f"could not read affiliate posts: {e}", "synced": 0}

    accounts = _accounts_with_tokens()
    if not accounts:
        return {"ok": False, "error": "no Instagram account with a token connected", "synced": 0}

    synced, skipped, errors = 0, 0, []
    for p in posts:
        media_id = p.get("media_id")
        post_id = p.get("id")
        if not media_id or p.get("status") != "posted":
            skipped += 1
            continue
        metrics = None
        for acc in accounts:                     # find the account that owns this media
            try:
                ins = analytics.fetch_media_insights(acc, media_id)
                if ins:
                    metrics = ins
                    break
            except Exception:
                continue
        if not metrics:
            skipped += 1
            continue
        payload = {
            "post_id": str(post_id), "source": "instagram_insights",
            "metrics": {
                "reach": metrics.get("reach"),
                "saves": metrics.get("saved"),
                "likes": metrics.get("likes"),
                "comments": metrics.get("comments"),
                "shares": metrics.get("shares"),
            },
        }
        try:
            requests.post(f"{AFFILIATE_URL}/api/performance/ingest", json=payload, timeout=20)
            synced += 1
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    return {"ok": True, "synced": synced, "skipped": skipped, "errors": errors,
            "posts_seen": len(posts)}
