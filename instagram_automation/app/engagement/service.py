"""Instagram Graph API client for engagement (comments, DMs, insights).

Uses the connected account's token (server-side only, never logged). Each call maps
to a permission the app already holds:
  reply_to_comment / hide_comment  -> instagram_manage_comments
  send_dm                          -> instagram_manage_messages
  get_comments                     -> instagram_manage_comments
  get_post_insights                -> instagram_manage_insights

Follows the Meta docs: verify eligibility/windows before assuming an action is
permitted (Spec sections 22, 51-52). Every call returns a normalized result and
never raises a raw provider payload to callers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from app import settings

GRAPH = f"https://graph.facebook.com/{settings.GRAPH_API_VERSION}"


class GraphError(Exception):
    def __init__(self, status: int, code: Any, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(f"[{status}/{code}] {message}")


# ── Live rate-limit awareness (Meta returns usage % on EVERY response) ────────
# X-App-Usage / X-Business-Use-Case-Usage tell us how close we are to Meta's throttle.
# We read them and self-back-off BEFORE hitting the wall (which is what causes #4 / bot flags).
import json as _json_mod
import time as _time_mod

_USAGE = {"pct": 0.0, "ts": 0.0}          # highest usage % seen recently


def _track_usage(resp) -> None:
    try:
        pcts = [0.0]
        au = resp.headers.get("x-app-usage")
        if au:
            d = _json_mod.loads(au)
            pcts += [float(d.get(k, 0)) for k in ("call_count", "total_cputime", "total_time")]
        bu = resp.headers.get("x-business-use-case-usage")
        if bu:
            for items in _json_mod.loads(bu).values():
                for it in items:
                    pcts += [float(it.get(k, 0)) for k in ("call_count", "total_cputime", "total_time")]
        _USAGE["pct"] = max(pcts)
        _USAGE["ts"] = _time_mod.time()
    except Exception:
        pass


def app_usage_pct() -> float:
    """Highest Meta rate-limit usage % from the last call (decays to 0 after ~5 min idle)."""
    if _time_mod.time() - _USAGE["ts"] > 300:
        return 0.0
    return _USAGE["pct"]


def _post(url: str, token: str, data: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(url, data={**data, "access_token": token}, timeout=30)
    _track_usage(r)
    body = r.json() if r.content else {}
    if r.status_code not in (200, 201):
        err = (body or {}).get("error", {})
        raise GraphError(r.status_code, err.get("code"), err.get("message", "request failed"))
    return body


def _get(url: str, token: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(url, params={**params, "access_token": token}, timeout=30)
    _track_usage(r)
    body = r.json() if r.content else {}
    if r.status_code != 200:
        err = (body or {}).get("error", {})
        raise GraphError(r.status_code, err.get("code"), err.get("message", "request failed"))
    return body


def user_profile(token: str, igsid: str,
                 fields: str = "username,is_user_follow_business,is_business_follow_user,follower_count") -> Dict[str, Any]:
    """Instagram Messaging User Profile API. For an IGSID your app can message, returns the
    OFFICIAL `is_user_follow_business` field (does this user follow the business) — compliant,
    no scraping. Needs instagram_manage_messages. Raises GraphError if the IGSID isn't readable."""
    return _get(f"{GRAPH}/{igsid}", token, {"fields": fields})


def check_user_follows_business(token: str, igsid: str) -> Optional[bool]:
    """True/False whether the user follows the business; None when it can't be determined (e.g. the
    IGSID is not yet a messaging participant, or the field is unavailable). Never raises."""
    if not igsid:
        return None
    try:
        d = _get(f"{GRAPH}/{igsid}", token, {"fields": "is_user_follow_business"})
        v = d.get("is_user_follow_business")
        return bool(v) if v is not None else None
    except Exception:
        return None


def reply_to_comment(token: str, comment_id: str, message: str) -> Dict[str, Any]:
    """Public reply to a comment. Needs instagram_manage_comments."""
    return _post(f"{GRAPH}/{comment_id}/replies", token, {"message": message})


# ── webhook subscription self-heal ────────────────────────────────────────────
_PAGE_WEBHOOK_FIELDS = ("feed,messages,messaging_postbacks,message_reactions,"
                        "mention,messaging_referrals,messaging_handovers")


def subscribe_page_webhooks(token: str, subscribed_fields: str = _PAGE_WEBHOOK_FIELDS) -> Dict[str, Any]:
    """Re-assert this account's Page→App webhook subscription (idempotent) so a token refresh
    never silently stops comment/DM delivery. Uses the account's own token; resolves the Page id."""
    page_id = _page_id(token)
    if not page_id:
        raise GraphError(400, None, "could not resolve page id")
    return _post(f"{GRAPH}/{page_id}/subscribed_apps", token, {"subscribed_fields": subscribed_fields})


def subscribe_app_instagram(app_id: str, app_secret: str, callback_url: str, verify_token: str,
                            fields: str = "comments,messages") -> Dict[str, Any]:
    """Re-assert the APP-level Instagram webhook subscription (idempotent). Needs the app id +
    secret (an app access token) — used only by the self-heal when those are configured."""
    apptok = f"{app_id}|{app_secret}"
    r = requests.post(f"{GRAPH}/{app_id}/subscriptions",
                      data={"object": "instagram", "callback_url": callback_url, "fields": fields,
                            "verify_token": verify_token, "include_values": "true", "access_token": apptok},
                      timeout=30)
    body = r.json() if r.content else {}
    if r.status_code not in (200, 201):
        err = (body or {}).get("error", {})
        raise GraphError(r.status_code, err.get("code"), err.get("message", "app subscribe failed"))
    return body


def hide_comment(token: str, comment_id: str, hide: bool = True) -> Dict[str, Any]:
    return _post(f"{GRAPH}/{comment_id}", token, {"hide": "true" if hide else "false"})


_PAGE_ID_CACHE: Dict[str, str] = {}   # page/user token → the Page id (resolved via /me)


def _page_id(token: str) -> Optional[str]:
    """The Facebook Page id for a PAGE token (GET /me → the Page). Cached per token."""
    if not token:
        return None
    if token in _PAGE_ID_CACHE:
        return _PAGE_ID_CACHE[token]
    pid = (get_self(token) or {}).get("id")
    if pid:
        _PAGE_ID_CACHE[token] = str(pid)
    return pid


def _send_raw(token: str, ig_user_id: str, recipient: Dict[str, Any], message_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Send ANY Instagram message object (text OR a template attachment). Must go through the
    connected Facebook PAGE: 'POST /{page-id}/messages' with the Page token. Resolves the page
    id and tries it first, then '/me', then the ig-business-id — robust across token setups."""
    import json as _json
    payload = {"recipient": _json.dumps(recipient), "message": _json.dumps(message_obj)}
    targets = [t for t in (_page_id(token), "me", str(ig_user_id or "").strip()) if t]
    last_err: Optional[GraphError] = None
    for target in targets:
        try:
            return _post(f"{GRAPH}/{target}/messages", token, payload)
        except GraphError as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise GraphError(400, None, "no valid message target")


def _send_message(token: str, ig_user_id: str, recipient: Dict[str, Any], message: str) -> Dict[str, Any]:
    return _send_raw(token, ig_user_id, recipient, {"text": message})


def send_product_cards(token: str, ig_user_id: str, recipient: Dict[str, Any],
                       products: List[Dict[str, Any]], storefront_url: Optional[str] = None,
                       intro: Optional[str] = None) -> Dict[str, Any]:
    """Send a HaulPack-style DM: a horizontal carousel of PRODUCT CARDS (image, name, price)
    each with a 'Shop Now' button (the Amazon affiliate link) and a 'See All Products' button
    (your storefront). Uses Instagram's generic message template. `recipient` = {comment_id}
    for a comment-triggered DM, or {id} for a direct DM."""
    elements: List[Dict[str, Any]] = []
    for p in (products or [])[:10]:                      # generic template: max 10 cards
        title = (p.get("product_title") or p.get("title") or "Product").strip()[:80]
        price = str(p.get("price") or "").strip()
        disc = p.get("discount_pct")
        subtitle = (f"{price}" + (f"  ·  {int(disc)}% off" if disc else "")).strip()[:80] or "Tap to shop"
        link = (p.get("affiliate_link") or p.get("link") or storefront_url or "").strip()
        img = (p.get("image_url") or p.get("image") or "").strip()
        buttons = []
        if link:
            buttons.append({"type": "web_url", "url": link, "title": "Shop Now"})
        if storefront_url:
            buttons.append({"type": "web_url", "url": storefront_url, "title": "See All Products"})
        el: Dict[str, Any] = {"title": title, "subtitle": subtitle}
        if img:
            el["image_url"] = img
        if link:
            el["default_action"] = {"type": "web_url", "url": link}
        if buttons:
            el["buttons"] = buttons[:3]
        elements.append(el)
    if not elements:
        raise GraphError(400, None, "no products to send")
    message_obj = {"attachment": {"type": "template",
                                  "payload": {"template_type": "generic", "elements": elements}}}
    return _send_raw(token, ig_user_id, recipient, message_obj)


def private_reply(token: str, ig_user_id: str, comment_id: str, message: str) -> Dict[str, Any]:
    """DM a commenter FROM their comment — the Instagram way. recipient={comment_id}.
    Allowed once per comment, within 7 days; needs instagram_manage_messages."""
    return _send_message(token, ig_user_id, {"comment_id": comment_id}, message)


def private_reply_cards(token: str, ig_user_id: str, comment_id: str,
                        products: List[Dict[str, Any]], storefront_url: Optional[str] = None) -> Dict[str, Any]:
    """Comment→DM with PRODUCT CARDS + buttons (the HaulPack look)."""
    return send_product_cards(token, ig_user_id, {"comment_id": comment_id}, products, storefront_url)


def send_dm(token: str, ig_user_id: str, recipient_id: str, message: str) -> Dict[str, Any]:
    """Send an Instagram DM to a user id (inside the Meta messaging window). Tries the
    account's IG-business-id endpoint first, then '/me'."""
    return _send_message(token, ig_user_id, {"id": recipient_id}, message)


def get_comments(token: str, ig_media_id: str) -> List[Dict[str, Any]]:
    body = _get(f"{GRAPH}/{ig_media_id}/comments", token,
                {"fields": "id,text,username,timestamp,parent_id,from{id,username}", "limit": 50})
    return body.get("data", [])


def get_self(token: str) -> Dict[str, Any]:
    """Resolve the identity this Page token belongs to (id + name)."""
    try:
        return _get(f"{GRAPH}/me", token, {"fields": "id,name"})
    except GraphError:
        return {}


def get_conversations(token: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Pull recent Instagram DM threads (pull model, no webhook). Needs
    instagram_manage_messages + pages_messaging. IMPORTANT: the conversations edge lives
    on the PAGE the token belongs to (`/me`), NOT the IG user id — querying the IG user id
    returns '(#3) Application does not have the capability'. Each thread includes its most
    recent messages so the inbox can be reconstructed."""
    body = _get(f"{GRAPH}/me/conversations", token, {
        "platform": "instagram", "limit": limit,
        "fields": "id,updated_time,participants{id,username},messages.limit(15){id,message,from,created_time}",
    })
    return body.get("data", [])


# Map Meta insight names -> our normalized fields (null = unavailable, not zero).
_INSIGHT_MAP = {"likes": "likes", "comments": "comments", "saved": "saves",
                "shares": "shares", "reach": "reach", "impressions": "impressions",
                "views": "views", "plays": "views"}


def get_post_insights(token: str, ig_media_id: str,
                      metrics: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return normalized insights; unavailable metrics stay None (Spec sections 10, 37).

    Robust to Meta's metric deprecations: `impressions` is dropped (removed for most
    media types in recent API versions — including it fails the WHOLE insights call).
    likes/comments come straight off the media object (always available), and the rest
    from /insights. A metric that a given media type doesn't expose just stays None."""
    out: Dict[str, Any] = {k: None for k in ("likes", "comments", "saves", "shares", "reach", "impressions", "views")}
    # 1) like_count / comments_count are plain media fields — reliable, no insights perm needed.
    try:
        media = _get(f"{GRAPH}/{ig_media_id}", token,
                     {"fields": "like_count,comments_count"})
        out["likes"] = media.get("like_count")
        out["comments"] = media.get("comments_count")
    except GraphError:
        pass
    # 2) reach/saved/shares/views from /insights (NO impressions — deprecated).
    metrics = metrics or ["reach", "saved", "shares", "views"]
    try:
        body = _get(f"{GRAPH}/{ig_media_id}/insights", token, {"metric": ",".join(metrics)})
        for row in body.get("data", []):
            field = _INSIGHT_MAP.get(row.get("name"))
            vals = row.get("values") or [{}]
            if field is not None and vals:
                out[field] = vals[0].get("value")
    except GraphError:
        pass                                    # partial insights are fine; keep media counts
    return out


def validate_token(token: str, ig_user_id: str) -> Dict[str, Any]:
    """Read-only check the token can reach the account."""
    try:
        body = _get(f"{GRAPH}/{ig_user_id}", token, {"fields": "id,username"})
        return {"valid": True, "username": body.get("username")}
    except GraphError as e:
        return {"valid": False, "reason": e.message}
