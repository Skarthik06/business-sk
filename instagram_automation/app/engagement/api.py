"""Engagement REST API + Meta webhook receiver.

- /api/engagement/automations  : deterministic rule CRUD + stats
- /api/engagement/simulate     : run the full pipeline WITHOUT Meta (test the engine)
- /api/webhooks/meta           : Meta webhook verify (GET) + receive (POST)

The event pipeline is deterministic: store -> dedupe -> evaluate rules -> dispatch
actions -> log executions. Sending real replies/DMs is opt-in (ENGAGEMENT_LIVE);
by default actions are recorded as SKIPPED (dry) so nothing posts until you enable it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import rags, settings
from app.engagement import rules as R
from app.engagement import service, store

router = APIRouter(prefix="/api/engagement", tags=["engagement"])
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_LIVE = os.getenv("ENGAGEMENT_LIVE", "0").strip() not in ("0", "false", "")
_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "").strip()
_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
# Background poller: pull comments/DMs + auto-reply on a timer (no webhook needed).
# Defaults ON at 60s so auto-commenting works while the project runs continuously.
_AUTO_SYNC = os.getenv("ENGAGEMENT_AUTO_SYNC", "1").strip() not in ("0", "false", "")
# Poll interval. 5s hammered Instagram's Graph API and triggered app-level rate limits
# (#4 'Application request limit reached'), which broke token generation + messaging. 30s is
# near-real-time AND safe. Lower via ENGAGEMENT_SYNC_INTERVAL only if you know the limits.
_SYNC_INTERVAL = max(10, int(os.getenv("ENGAGEMENT_SYNC_INTERVAL", "30")))

store.init_schema()


# ---- automations CRUD -----------------------------------------------------
class RuleIn(BaseModel):
    name: str = "Rule"
    trigger_type: str = "COMMENT_RECEIVED"     # COMMENT_RECEIVED | DM_RECEIVED
    post_id: Optional[str] = None              # None = account-wide
    enabled: bool = True
    match_mode: str = "all"                    # all | any
    priority: int = 100
    conditions: List[dict] = []                # [{operator, keywords, case_sensitive}]
    actions: List[dict] = []                   # [{type, message, tag}]


@router.get("/automations")
def list_automations(account_id: int):
    return {"account_id": account_id, "automations": store.list_rules(account_id)}


@router.post("/automations")
def create_automation(account_id: int, body: RuleIn):
    rid = store.create_rule(account_id, body.model_dump())
    return {"id": rid, "created": True}


@router.patch("/automations/{rule_id}")
def update_automation(account_id: int, rule_id: int, body: RuleIn):
    if not store.update_rule(account_id, rule_id, body.model_dump(exclude_unset=True)):
        raise HTTPException(404, "Rule not found")
    return {"id": rule_id, "updated": True}


@router.delete("/automations/{rule_id}")
def delete_automation(account_id: int, rule_id: int):
    if not store.delete_rule(account_id, rule_id):
        raise HTTPException(404, "Rule not found")
    return {"id": rule_id, "deleted": True}


@router.get("/automations/stats")
def automations_stats(account_id: int):
    return {"account_id": account_id, "rules": store.rule_stats(account_id)}


# ---- automation detail actions (Spec 11) ----------------------------------
class TestReq(BaseModel):
    text: str
    trigger_type: str = "COMMENT_RECEIVED"
    username: Optional[str] = "tester"
    post_id: Optional[str] = None


@router.get("/automations/{rule_id}")
def get_automation(account_id: int, rule_id: int):
    r = store.get_rule(account_id, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    return r


@router.put("/automations/{rule_id}")
def replace_automation(account_id: int, rule_id: int, body: RuleIn):
    if not store.update_rule(account_id, rule_id, body.model_dump()):
        raise HTTPException(404, "Rule not found")
    return {"id": rule_id, "updated": True}


@router.patch("/automations/{rule_id}/toggle")
def toggle_automation(account_id: int, rule_id: int):
    r = store.get_rule(account_id, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    store.update_rule(account_id, rule_id, {"enabled": not r.get("enabled")})
    return {"id": rule_id, "enabled": not r.get("enabled")}


@router.post("/automations/{rule_id}/duplicate")
def duplicate_automation(account_id: int, rule_id: int):
    new_id = store.duplicate_rule(account_id, rule_id)
    if not new_id:
        raise HTTPException(404, "Rule not found")
    return {"id": new_id, "duplicated_from": rule_id}


@router.get("/automations/{rule_id}/executions")
def automation_executions(account_id: int, rule_id: int, limit: int = 100):
    return {"rule_id": rule_id, "executions": store.rule_executions(account_id, rule_id, limit)}


@router.post("/automations/{rule_id}/test")
def test_automation(account_id: int, rule_id: int, body: TestReq):
    """Simulate ONE rule against sample input — no side effects, never posts."""
    r = store.get_rule(account_id, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    event = R.InboundEvent(trigger_type=body.trigger_type, text=body.text,
                           post_id=body.post_id or r.get("post_id"), comment_id="sim-comment",
                           user_id="sim-user", username=body.username, external_event_id=None)
    single = [store_rule_to_engine(r)]
    matched = R.evaluate(single, event)
    provider = R.default_provider("template")
    account = rags.get_account(account_id) or {}
    out = []
    for rule in matched:
        for action in rule.actions:
            out.append({"action": action.type,
                        "message": provider.build(action, event, _context(event, account)),
                        "would_execute": True})
    return {"rule_id": rule_id, "matched": len(matched), "actions": out}


def store_rule_to_engine(row: Dict[str, Any]) -> R.Rule:
    """Convert a stored rule row to an engine Rule (single-rule test path)."""
    conds = [R.Condition(**{k: v for k, v in c.items()
                            if k in ("operator", "keywords", "case_sensitive", "strip_punct")})
             for c in (row.get("conditions") or [])]
    acts = [R.Action(**{k: v for k, v in a.items() if k in ("type", "message", "tag", "ai")})
            for a in (row.get("actions") or [])]
    return R.Rule(id=row["id"], name=row["name"], trigger_type=row["trigger_type"],
                  conditions=conds, actions=acts, enabled=True, post_id=row.get("post_id"),
                  priority=row.get("priority", 100), match_mode=row.get("match_mode", "all"))


# ---- leads (Spec 31) ------------------------------------------------------
class LeadStatusReq(BaseModel):
    status: str
    note: Optional[str] = None


@router.get("/leads")
def leads_list(account_id: int, status: Optional[str] = None):
    return {"account_id": account_id, "leads": store.list_leads(account_id, status),
            "stats": store.lead_stats(account_id), "statuses": store.LEAD_STATUSES}


@router.get("/leads/{lead_id}")
def lead_detail(account_id: int, lead_id: int):
    lead = store.get_lead(account_id, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@router.patch("/leads/{lead_id}/status")
def lead_status(account_id: int, lead_id: int, body: LeadStatusReq):
    res = store.set_lead_status(account_id, lead_id, body.status, body.note)
    if not res:
        raise HTTPException(400, f"Invalid lead or status (allowed: {store.LEAD_STATUSES})")
    return res


# ---- conversation management (Spec 20) ------------------------------------
class ConvStatusReq(BaseModel):
    status: str


class AssignReq(BaseModel):
    assignee: Optional[str] = None


class SendMsgReq(BaseModel):
    account_id: int
    message: str


@router.get("/conversations/unread/count")
def conversations_unread(account_id: int):
    return {"account_id": account_id, "unread": store.unread_conversation_count(account_id)}


@router.patch("/conversations/{conversation_id}/status")
def conversation_status(account_id: int, conversation_id: int, body: ConvStatusReq):
    if not store.set_conversation_status(account_id, conversation_id, body.status):
        raise HTTPException(404, "Conversation not found")
    return {"id": conversation_id, "status": body.status}


@router.patch("/conversations/{conversation_id}/read")
def conversation_read(account_id: int, conversation_id: int):
    store.mark_conversation_read(account_id, conversation_id)
    return {"id": conversation_id, "read": True}


@router.patch("/conversations/{conversation_id}/assign")
def conversation_assign(account_id: int, conversation_id: int, body: AssignReq):
    store.assign_conversation(account_id, conversation_id, body.assignee)
    return {"id": conversation_id, "assigned_to": body.assignee}


@router.get("/conversations/{conversation_id}/lead")
def conversation_lead(account_id: int, conversation_id: int):
    return {"conversation_id": conversation_id, "lead": store.lead_for_conversation(account_id, conversation_id)}


@router.patch("/conversations/{conversation_id}/lead")
def conversation_lead_update(account_id: int, conversation_id: int, body: LeadStatusReq):
    lead = store.lead_for_conversation(account_id, conversation_id)
    if not lead:
        lid = store.upsert_lead(account_id, conversation_id=conversation_id)
    else:
        lid = lead["id"]
    res = store.set_lead_status(account_id, lid, body.status, body.note)
    if not res:
        raise HTTPException(400, f"Invalid status (allowed: {store.LEAD_STATUSES})")
    return res


@router.post("/conversations/{conversation_id}/messages")
def conversation_send(conversation_id: int, body: SendMsgReq):
    """Send a DM into a conversation via Graph (backend only). The recipient must be a
    Meta-messageable id and inside the messaging window."""
    conv = store.get_conversation(body.account_id, conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    recipient = conv.get("ig_user_ref")
    if not (recipient and str(recipient).isdigit()):
        raise HTTPException(400, "Recipient id unavailable for direct send (username-only thread).")
    if not _LIVE:
        raise HTTPException(400, "Live sending is off (ENGAGEMENT_LIVE=0).")
    account = rags.get_account(body.account_id, with_secret=True) or {}
    token = account.get("ig_access_token"); ig_id = account.get("ig_business_id")
    if not token:
        raise HTTPException(400, "Account has no access token.")
    try:
        r = service.send_dm(token, ig_id, recipient, body.message)
    except service.GraphError as e:
        raise HTTPException(400, f"Send failed: {e.message}")
    store.record_dm(body.account_id, str(recipient), body.message, str(r.get("id") or ""), direction="out")
    return {"conversation_id": conversation_id, "sent": True, "reference": r.get("id")}


# ---- comment moderation (Spec 17) -----------------------------------------
class ReplyReq(BaseModel):
    account_id: int
    message: str


class HideReq(BaseModel):
    hidden: bool = True


@router.post("/comments/{comment_pk}/reply")
def comment_reply(comment_pk: int, body: ReplyReq):
    cm = store.get_comment(body.account_id, comment_pk)
    if not cm:
        raise HTTPException(404, "Comment not found")
    if not _LIVE:
        raise HTTPException(400, "Live sending is off (ENGAGEMENT_LIVE=0).")
    account = rags.get_account(body.account_id, with_secret=True) or {}
    token = account.get("ig_access_token")
    if not token:
        raise HTTPException(400, "Account has no access token.")
    try:
        r = service.reply_to_comment(token, cm["external_comment_id"], body.message)
    except service.GraphError as e:
        raise HTTPException(400, f"Reply failed: {e.message}")
    store.set_comment_reply_status(body.account_id, cm["external_comment_id"], "REPLIED")
    return {"comment_id": comment_pk, "replied": True, "reference": r.get("id")}


@router.patch("/comments/{comment_pk}/hide")
def comment_hide(account_id: int, comment_pk: int, body: HideReq):
    ext = store.set_comment_hidden(account_id, comment_pk, body.hidden)
    if not ext:
        raise HTTPException(404, "Comment not found")
    if _LIVE:
        account = rags.get_account(account_id, with_secret=True) or {}
        token = account.get("ig_access_token")
        if token:
            try:
                service.hide_comment(token, ext, body.hidden)
            except service.GraphError as e:
                raise HTTPException(400, f"Hide failed on Instagram: {e.message}")
    return {"comment_id": comment_pk, "hidden": body.hidden}


@router.patch("/comments/{comment_pk}/read")
def comment_read(account_id: int, comment_pk: int):
    store.mark_comment_read(account_id, comment_pk)
    return {"comment_id": comment_pk, "read": True}


# ---- events admin (Spec 28) -----------------------------------------------
@router.get("/events")
def events_list(account_id: int, status: Optional[str] = None, limit: int = 100):
    return {"account_id": account_id, "events": store.list_events(account_id, status, limit)}


@router.get("/events/failed")
def events_failed(account_id: int, limit: int = 100):
    return {"account_id": account_id, "failed": store.list_events(account_id, "FAILED", limit),
            "failed_executions": store.list_failed_executions(account_id, limit)}


@router.get("/events/{event_id}")
def event_detail(event_id: int):
    ev = store.get_event(event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    return ev


@router.post("/events/{event_id}/ignore")
def event_ignore(event_id: int):
    store.mark_event(event_id, "IGNORED")
    return {"event_id": event_id, "ignored": True}


@router.post("/events/{event_id}/reprocess")
def event_reprocess(event_id: int):
    """Force-reprocess a stored event: clear its executions and re-run the rule engine."""
    ev = store.get_event(event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    parsed = _parse_change(ev.get("payload") or {})
    if not parsed:
        raise HTTPException(400, "Event payload is not reprocessable.")
    store.delete_executions_for_event(event_id)
    out = process_event(ev["social_account_id"], parsed["event"], event_id)
    store.mark_event(event_id, "SUCCESS")
    return {"event_id": event_id, "reprocessed": True, **out}


@router.post("/events/failed/retry")
def events_retry(account_id: int):
    """Retry failed/rate-limited events by reprocessing them (Spec 30)."""
    retried = 0
    for ev in store.list_events(account_id, "FAILED", 100):
        parsed = _parse_change(ev.get("payload") or {})
        if not parsed:
            continue
        store.delete_executions_for_event(ev["id"])
        process_event(account_id, parsed["event"], ev["id"])
        store.mark_event(ev["id"], "SUCCESS")
        retried += 1
    return {"account_id": account_id, "retried": retried}


# ---- webhook admin (Spec 55) ----------------------------------------------
@router.get("/webhook-status")
def webhook_status(account_id: int):
    account = rags.get_account(account_id, with_secret=True) or {}
    token = account.get("ig_access_token")
    token_ok = None
    if token and account.get("ig_business_id"):
        token_ok = service.validate_token(token, account["ig_business_id"]).get("valid")
    events = store.list_events(account_id, None, 5)
    return {
        "account_id": account_id,
        "webhook_verify_configured": bool(_VERIFY_TOKEN),
        "signature_check_configured": bool(_APP_SECRET),
        "live_sending": _LIVE, "auto_sync": _AUTO_SYNC, "interval_seconds": _SYNC_INTERVAL,
        "token_valid": token_ok,
        "subscribed_fields": ["comments", "messages"],
        "recent_events": events,
        "last_event_at": (events[0].get("received_at") if events else None),
    }


@router.get("/summary")
def engagement_summary(account_id: int):
    """Top-line counters + 7-day deltas + live-mode flag for the Engagement header."""
    return {"account_id": account_id, "live": _LIVE,
            **store.activity_summary(account_id),
            "deltas": store.summary_deltas(account_id, 7)}


@router.get("/charts")
def engagement_charts(account_id: int, days: int = 7):
    """Per-day engagement time series (comments, DMs, replies, conversations)."""
    return {"account_id": account_id, "days": days, **store.chart_series(account_id, days)}


@router.get("/top-posts")
def engagement_top_posts(account_id: int, limit: int = 5):
    """Top-performing published posts ranked by engagement (comments + DMs + replies).
    Reach comes from the latest stored insights; unavailable metrics stay null (N/A)."""
    from app.business import store as bstore
    rows = []
    for p in bstore.list_published_posts():
        if p.get("account_id") != account_id:      # per-account isolation
            continue
        mid = p.get("ig_media_id")
        if not mid:
            continue
        act = store.post_activity(account_id, mid)
        comments = len(store.list_comments(account_id, mid))
        dms = sum(a["n"] for a in act["by_action"] if a["action_type"] == "SEND_DM" and a["status"] == "SUCCESS")
        replies = sum(a["n"] for a in act["by_action"] if a["action_type"] == "REPLY_TO_COMMENT" and a["status"] == "SUCCESS")
        reach = None
        try:
            an = bstore.get_analytics_for_campaign(p["campaign_id"])
            if an:
                reach = (an[0].get("metrics") or {}).get("reach")
        except Exception:
            pass
        name = None
        if p.get("property_id"):
            m = bstore.get_property_model(p["property_id"]) or {}
            name = _clean((m.get("property") or {}).get("project_name"))
        rows.append({"campaign_id": p["campaign_id"], "label": p["label"],
                     "project_name": name, "permalink": p.get("permalink"), "cover": p.get("cover"),
                     "comments": comments, "dms": dms, "replies": replies, "reach": reach,
                     "engagement": comments + dms + replies})
    rows.sort(key=lambda r: r["engagement"], reverse=True)
    return {"account_id": account_id, "posts": rows[:limit]}


@router.get("/activity")
def engagement_activity(account_id: int, limit: int = 50):
    """Chronological feed of automation actions (the real 'what got sent' log)."""
    return {"account_id": account_id, "activity": store.list_activity(account_id, limit)}


@router.get("/conversations")
def engagement_conversations(account_id: int, limit: int = 50):
    """DM inbox — conversations with latest-message preview."""
    return {"account_id": account_id, "conversations": store.list_conversations(account_id, limit)}


@router.get("/conversations/{conversation_id}/messages")
def engagement_messages(conversation_id: int):
    return {"conversation_id": conversation_id, "messages": store.list_messages(conversation_id)}


@router.get("/comments")
def engagement_comments(account_id: int, post_id: Optional[str] = None, limit: int = 100):
    """Comments tab — inbound comments with reply/automation status."""
    return {"account_id": account_id, "comments": store.list_comments(account_id, post_id, limit)}


@router.get("/posts")
def engagement_posts(account_id: int):
    """Published posts ('Post #N') with per-post engagement counters, so each post's
    comments & insights can be tracked by its Post number."""
    from app.business import store as bstore

    def _counts(mid: str):
        act = store.post_activity(account_id, mid) if mid else {"by_action": []}
        comments = store.list_comments(account_id, mid) if mid else []
        dms = sum(a["n"] for a in act["by_action"] if a["action_type"] == "SEND_DM" and a["status"] == "SUCCESS")
        replies = sum(a["n"] for a in act["by_action"] if a["action_type"] == "REPLY_TO_COMMENT" and a["status"] == "SUCCESS")
        return len(comments), dms, replies

    out = []
    # Real-estate posts published BY THIS account only (per-account isolation).
    for p in bstore.list_published_posts():
        if p.get("account_id") != account_id:
            continue
        name = None
        if p.get("property_id"):
            m = bstore.get_property_model(p["property_id"]) or {}
            name = _clean((m.get("property") or {}).get("project_name"))
        c, dms, replies = _counts(p.get("ig_media_id"))
        out.append({**p, "project_name": name, "comment_count": c, "dms_sent": dms, "replies_sent": replies})
    # Business-SK affiliate posts for this account (so switching to the affiliate account
    # shows ITS posts + lets you scope rules to them). Synthetic negative campaign_id.
    for ap in store.list_affiliate_posts(account_id):
        mid = ap.get("ig_media_id")
        if not mid:
            continue
        prods = ap.get("products") or []
        cover = (prods[0].get("image_url") or prods[0].get("image")) if prods else None
        c, dms, replies = _counts(mid)
        out.append({
            "campaign_id": -int(ap["id"]), "label": f"Affiliate · {ap.get('category') or 'post'}",
            "ig_media_id": mid, "permalink": ap.get("permalink"),
            "project_name": ap.get("category"), "cover": cover, "affiliate": True,
            "comment_count": c, "dms_sent": dms, "replies_sent": replies,
        })
    return {"account_id": account_id, "posts": out}


@router.get("/posts/{campaign_id}")
def engagement_post_detail(account_id: int, campaign_id: int, live: bool = True):
    """One post's full engagement view: live Instagram insights + comments (best-effort),
    stored comments, and the automation activity for this post."""
    from app.business import store as bstore
    # Affiliate posts use a synthetic NEGATIVE campaign_id (= -eng_post id).
    if campaign_id < 0:
        aff = next((a for a in store.list_affiliate_posts(account_id) if int(a["id"]) == -campaign_id), None)
        if not aff:
            raise HTTPException(404, "Post not found")
        media_id = aff.get("ig_media_id")
        account = rags.get_account(account_id, with_secret=True) or {}
        token = account.get("ig_access_token")
        insights: Dict[str, Any] = {}
        live_comments: List[Dict[str, Any]] = []
        warnings = []
        if live and media_id and token:
            try: insights = service.get_post_insights(token, media_id)
            except service.GraphError as e: warnings.append(f"Insights unavailable: {e.message}")
            try: live_comments = service.get_comments(token, media_id)
            except service.GraphError as e: warnings.append(f"Comments unavailable: {e.message}")
        return {
            "campaign_id": campaign_id, "label": f"Affiliate · {aff.get('category') or 'post'}",
            "ig_media_id": media_id, "permalink": aff.get("permalink"), "insights": insights,
            "live_comments": live_comments,
            "stored_comments": store.list_comments(account_id, media_id) if media_id else [],
            "activity": store.post_activity(account_id, media_id) if media_id else {"by_action": [], "recent": []},
            "warnings": warnings,
        }
    camp = bstore.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(404, "Post not found")
    pub = (camp.get("contract") or {}).get("published") or {}
    media_id = pub.get("ig_media_id")
    insights: Dict[str, Any] = {}
    live_comments: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if live and media_id:
        account = rags.get_account(account_id, with_secret=True) or {}
        token = account.get("ig_access_token")
        if not token:
            warnings.append("No access token on this account — showing stored data only.")
        else:
            try:
                insights = service.get_post_insights(token, media_id)
            except service.GraphError as e:
                warnings.append(f"Insights unavailable: {e.message}")
            try:
                live_comments = service.get_comments(token, media_id)
            except service.GraphError as e:
                warnings.append(f"Comments unavailable: {e.message}")
    elif not media_id:
        warnings.append("This post is not published to Instagram yet.")
    return {
        "campaign_id": campaign_id, "label": f"Post #{campaign_id}",
        "ig_media_id": media_id, "permalink": pub.get("permalink"),
        "insights": insights,
        "live_comments": live_comments,
        "stored_comments": store.list_comments(account_id, media_id) if media_id else [],
        "activity": store.post_activity(account_id, media_id) if media_id else {"by_action": [], "recent": []},
        "warnings": warnings,
    }


class SyncReq(BaseModel):
    account_id: int
    run_rules: bool = True     # evaluate automations against newly-seen comments (default on)


# In-memory record of the most recent sync per account (for the panel's status line).
_LAST_SYNC: Dict[int, Dict[str, Any]] = {}


def _sync_one_post(account_id: int, media_id: str, token: str, run_rules: bool,
                   warnings: List[str], with_insights: bool = True) -> Dict[str, Any]:
    """Pull one post's comments (+ run rules on new ones). Insights are OPTIONAL:
    the background poller skips them (with_insights=False) because polling insights every
    tick was the #1 rate-limit consumer and comment→DM never needs them — analytics are
    fetched only on a manual sync or the post-detail view."""
    new_count = fired = 0
    try:
        comments = service.get_comments(token, media_id)
    except service.GraphError as e:
        comments = []
        # Persistent permission / "object does not exist" errors mean this account's token
        # simply can't read this post (e.g. a JK real-estate post under a user token) — don't
        # surface it as a scary sync error; it's expected and fail-open.
        m = (e.message or "").lower()
        if "does not exist" not in m and "permission" not in m:
            warnings.append(f"Comments unavailable: {e.message}")
    for cm in comments:
        cid = cm.get("id")
        if not cid:
            continue
        res = store.upsert_comment(account_id, media_id, cid,
                                   username=cm.get("username"), text=cm.get("text", ""))
        if res["is_new"]:
            new_count += 1
        if run_rules:
            # Evaluate rules on EVERY pulled comment (not just brand-new ones) so a rule
            # you create/fix later still fires on comments already stored. store_event is
            # idempotent (one event per comment) and process_event dedupes per (rule,event),
            # so a given rule acts on a given comment exactly once.
            ev = R.InboundEvent(trigger_type="COMMENT_RECEIVED", text=cm.get("text", ""),
                                post_id=media_id, comment_id=cid, username=cm.get("username"),
                                user_id=(cm.get("from") or {}).get("id"),
                                external_event_id=f"comment:{cid}")
            rec = store.store_event(account_id, "COMMENT_RECEIVED", f"comment:{cid}",
                                    cm, post_id=media_id, comment_id=cid)
            out = process_event(account_id, ev, rec["event_id"])
            store.mark_event(rec["event_id"], "SUCCESS")
            fired += sum(1 for e in out.get("executions", []) if e.get("status") not in ("DUPLICATE",))
    insights: Dict[str, Any] = {}
    if with_insights:                                     # skipped by the poller (saves the most calls)
        try:
            insights = service.get_post_insights(token, media_id)
        except service.GraphError as e:
            warnings.append(f"Insights unavailable: {e.message}")
    return {"ig_media_id": media_id, "synced_comments": len(comments),
            "new_comments": new_count, "rules_fired": fired, "insights": insights}


def _sync_dms(account_id: int, token: str, account: Dict[str, Any], warnings: List[str]) -> int:
    """Best-effort pull of DM threads into the inbox (no webhook). Returns new-message count.
    Conversations are read off /me (the Page the token belongs to). Direction is decided
    by whether a message's sender id is one of OUR ids (Page id or IG business id)."""
    self_ids = {str(account.get("ig_business_id") or "")}
    me = service.get_self(token)
    if me.get("id"):
        self_ids.add(str(me["id"]))
    try:
        convos = service.get_conversations(token)
    except service.GraphError as e:
        # (#298) read_page_mailboxes / messaging-window permission errors are PERSISTENT (need
        # App Review), so don't spam the warning every tick — reading the inbox is optional and
        # never affects SENDING auto-DMs. Only surface genuinely transient errors.
        if str(e.code) not in ("298", "10", "200") and "permission" not in (e.message or "").lower():
            warnings.append(f"DMs unavailable: {e.message}")
        return 0
    new_msgs = 0
    for cv in convos:
        parts = (cv.get("participants") or {}).get("data") or []
        cust = next((p for p in parts if str(p.get("id")) not in self_ids), (parts[0] if parts else {}))
        user_ref = cust.get("username") or cust.get("id") or "unknown"
        for m in reversed(((cv.get("messages") or {}).get("data") or [])):
            mid = m.get("id")
            frm = str((m.get("from") or {}).get("id"))
            direction = "out" if frm in self_ids else "in"
            if not store.dm_message_exists(mid):     # dedupe on external_message_id
                store.record_dm(account_id, str(user_ref), m.get("message", ""), mid,
                                direction=direction)
                new_msgs += 1
    return new_msgs


def _affiliate_dm_text(category: str, products: List[Dict[str, Any]]) -> str:
    """Build the grounded auto-DM for an affiliate post — the ACTUAL Amazon links for the
    products in THAT post (trust = a real amazon.in/dp link with your tag). {{username}} is
    filled by the template provider at send time."""
    lines = [f"Hi {{{{username}}}}! 🛍️ Here's everything from this post on Amazon 👇", ""]
    for i, p in enumerate(products[:5], 1):
        title = (p.get("product_title") or p.get("title") or "").strip()[:70]
        price = (p.get("price") or "").strip()
        link = (p.get("affiliate_link") or p.get("link") or "").strip()
        if not link:
            continue
        head = f"{i}. {title}" + (f" — {price}" if price else "")
        lines += [head, f"🔗 {link}", ""]
    lines.append("💚 These are Amazon affiliate links — I may earn a small commission at "
                 "no extra cost to you. Happy shopping!")
    return "\n".join(lines)


def ensure_affiliate_automation(account_id: int, ig_media_id: str, *, category: str = "",
                                caption: str = "", permalink: Optional[str] = None,
                                products: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Register a freshly-published affiliate carousel for engagement and attach a
    POST-SPECIFIC comment→DM rule grounded on that post's products. Post-specific rules
    suppress the account-wide real-estate rules (see rules.evaluate), so the two businesses
    never clash on the same account. Idempotent — re-posting refreshes the same rule."""
    products = products or []
    post_pk = store.register_affiliate_post(
        account_id, ig_media_id, category=category, caption=caption,
        permalink=permalink, products=products)
    actions = [
        {"type": "REPLY_TO_COMMENT", "message": "🔗 Sent to your DM — check your inbox! 📩"},
        {"type": "SEND_DM", "message": _affiliate_dm_text(category, products)},
        {"type": "MARK_LEAD", "tag": f"Affiliate · {category}" if category else "Affiliate"},
    ]
    rule_data = {
        "post_id": ig_media_id,
        "name": f"Affiliate DM · {category or 'post'}",
        "enabled": True,
        "trigger_type": "COMMENT_RECEIVED",
        "match_mode": "any",
        "priority": 50,
        # Catch-all: ANY comment on this affiliate post gets the product links.
        "conditions": [{"operator": "any", "keywords": []}],
        "actions": actions,
    }
    existing = store.affiliate_rule_for_post(account_id, ig_media_id)
    if existing:
        store.update_rule(account_id, existing, rule_data)
        rule_id = existing
    else:
        rule_id = store.create_rule(account_id, rule_data)
    return {"post_pk": post_pk, "rule_id": rule_id, "ig_media_id": ig_media_id,
            "products": len(products)}


def run_account_sync(account_id: int, run_rules: bool = True, light: bool = False) -> Dict[str, Any]:
    """Universal sync for one account: every published post's comments (+ optional insights),
    plus optional DM-inbox pull. Auto-replies/DMs fire when a rule matches. Safe on a timer.

    `light=True` (the background poller) fetches ONLY comments — the piece the comment→DM
    automation needs — and SKIPS insights + the DM-inbox pull. Those were the biggest
    rate-limit consumers (insights alone was ~324 calls/day) and are unnecessary on every tick;
    they run only on a manual sync. Covers BOTH businesses (real-estate + affiliate), isolated
    per account (post-specific rules suppress account-wide ones)."""
    from app.business import store as bstore
    account = rags.get_account(account_id, with_secret=True) or {}
    token = account.get("ig_access_token")
    if not token:
        return {"account_id": account_id, "error": "no access token", "posts": 0}
    ig_user_id = account.get("ig_business_id")
    warnings: List[str] = []
    want_insights = not light
    # PER-ACCOUNT ISOLATION: only sync real-estate posts published BY THIS account, so the
    # affiliate account never touches real-estate media (no clash, no cross-account errors).
    posts = [p for p in bstore.list_published_posts() if p.get("account_id") == account_id]
    per_post, totals = [], {"new_comments": 0, "rules_fired": 0, "posts": 0}
    for p in posts:
        mid = p.get("ig_media_id")
        if not mid:
            continue
        r = _sync_one_post(account_id, mid, token, run_rules, warnings, with_insights=want_insights)
        if want_insights:                                 # persist analytics only on a full sync
            try:
                bstore.save_analytics(p["campaign_id"], p.get("property_id"), mid,
                                      p.get("permalink"), account.get("label"), r["insights"], 0.0)
            except Exception:
                pass
        per_post.append({"campaign_id": p["campaign_id"], "label": p["label"], **r})
        totals["new_comments"] += r["new_comments"]
        totals["rules_fired"] += r["rules_fired"]
        totals["posts"] += 1
    # Business-SK affiliate posts (separate lane — never overlaps the real-estate media ids).
    for ap in store.list_affiliate_posts(account_id):
        mid = ap.get("ig_media_id")
        if not mid:
            continue
        r = _sync_one_post(account_id, mid, token, run_rules, warnings, with_insights=want_insights)
        per_post.append({"campaign_id": None, "label": f"affiliate#{ap.get('category') or ''}", **r})
        totals["new_comments"] += r["new_comments"]
        totals["rules_fired"] += r["rules_fired"]
        totals["posts"] += 1
    new_dms = 0 if light else _sync_dms(account_id, token, account, warnings)
    import time as _t
    summary = {"account_id": account_id, "ran_at": _t.time(), "run_rules": run_rules,
               "posts": totals["posts"], "new_comments": totals["new_comments"],
               "rules_fired": totals["rules_fired"], "new_dms": new_dms,
               "warnings": warnings, "per_post": per_post}
    _LAST_SYNC[account_id] = {k: summary[k] for k in
                              ("ran_at", "posts", "new_comments", "rules_fired", "new_dms", "warnings")}
    return summary


@router.post("/posts/{campaign_id}/sync")
def sync_post(campaign_id: int, body: SyncReq):
    """Pull one published post's live comments + insights and store them (pull model —
    no webhook). Runs automations against newly-seen comments unless run_rules=false."""
    from app.business import store as bstore
    camp = bstore.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(404, "Post not found")
    media_id = ((camp.get("contract") or {}).get("published") or {}).get("ig_media_id")
    if not media_id:
        raise HTTPException(400, "This post is not published to Instagram yet.")
    account = rags.get_account(body.account_id, with_secret=True) or {}
    token = account.get("ig_access_token")
    if not token:
        raise HTTPException(400, "This account has no Instagram access token.")
    warnings: List[str] = []
    r = _sync_one_post(body.account_id, media_id, token, body.run_rules, warnings)
    return {"campaign_id": campaign_id, **r, "warnings": warnings}


@router.post("/sync-all")
def sync_all(body: SyncReq):
    """Universal one-click sync — all posts' comments + insights + DM threads for the
    account, auto-replying to any new comments that match a rule."""
    return run_account_sync(body.account_id, body.run_rules)


@router.get("/sync-status")
def sync_status(account_id: int):
    """Auto-sync config + the last run's result (for the panel's status line)."""
    return {"account_id": account_id, "auto_sync": _AUTO_SYNC,
            "interval_seconds": _SYNC_INTERVAL, "live": _LIVE,
            "last_sync": _LAST_SYNC.get(account_id)}


def _clean(v: Any) -> str:
    s = str(v or "").strip()
    return "" if s in ("", "NOT_AVAILABLE") else s


@router.get("/suggest-message")
def suggest_message(property_id: str, kind: str = "dm"):
    """Build a ready-made, eye-catchy but concise auto-reply / auto-DM from a property's
    REAL verified data: config, top facilities, a Google-Maps link, and contact numbers.
    Fits in one readable message; {{username}} is filled when the rule fires."""
    from app.business import store as bstore
    import urllib.parse
    model = bstore.get_property_model(property_id)
    if not model:
        raise HTTPException(404, "Property not found. Generate a post first.")
    p = model.get("property", {}); loc = model.get("location", {})
    cfg = (model.get("configuration") or [{}])[0]
    name = _clean(p.get("project_name")) or "our project"
    locality = _clean(loc.get("locality")); city = _clean(loc.get("city"))
    place = ", ".join([x for x in (locality, city) if x])
    amenities = [_clean(a) for a in (model.get("amenities") or []) if _clean(a)][:5]
    contacts = [c for c in (model.get("contacts") or []) if _clean(c.get("phone"))][:3]
    phones = " · ".join(f"{_clean(c.get('name')) or 'Sales'} {c['phone']}" for c in contacts)
    config = " · ".join([x for x in (_clean(cfg.get("bhk")),
                         (f"{_clean(cfg.get('area_sqft'))} sq ft" if _clean(cfg.get("area_sqft")) else "")) if x])
    map_url = "https://maps.google.com/?q=" + urllib.parse.quote(" ".join([name, place]).strip())

    if kind == "reply":
        text = f"Thanks for your interest in {name}! 🏡 Just sent you all the details in your DMs — do check 📩"
    else:
        lines = [f"Hi {{{{username}}}}! 👋 Thanks for asking about {name} 🏡", ""]
        if config:
            lines.append(f"✨ {config}")
        if _clean(p.get("builder")):
            lines.append(f"🏢 By {_clean(p.get('builder'))}")
        if amenities:
            lines.append(f"🌟 {', '.join(amenities)}")
        if place:
            lines.append(f"📍 {place}")
        lines.append(f"🗺️ Map: {map_url}")
        if phones:
            lines.append(f"📞 {phones}")
        lines += ["", "Book a FREE site visit today! 🔑"]
        text = "\n".join(lines)
    return {"property_id": property_id, "kind": kind, "message": text, "length": len(text)}


# ---- the deterministic event pipeline -------------------------------------
def _context(event: R.InboundEvent, account: Dict[str, Any]) -> Dict[str, Any]:
    return {"username": event.username, "post_id": event.post_id,
            "account_name": (account or {}).get("label"),
            "post_url": None, "post_title": None}


def _storefront_url() -> Optional[str]:
    """Public GitHub-Pages storefront URL — the 'See All Products' / universal shop link."""
    try:
        from app.services import hosting
        user, repo, _ = hosting._git_cfg()
        if user and repo:
            return f"https://{user.lower()}.github.io/{repo}/storefront/"
    except Exception:
        pass
    return None


def _dispatch(account_id: int, action: R.Action, event: R.InboundEvent, text: str, dry: bool):
    """Execute one action. Internal actions never touch Meta; reply/DM go to Graph
    only when live + eligible. Returns (status, request_ref, error_code, error_msg)."""
    if action.type in ("LOG_EVENT", "ADD_TAG", "MARK_LEAD"):
        return "SUCCESS", None, None, None
    if dry or not _LIVE:
        return "SKIPPED", "dry-run", None, None
    account = rags.get_account(account_id, with_secret=True)
    token = (account or {}).get("ig_access_token")
    ig_id = (account or {}).get("ig_business_id")
    if not token:
        return "FAILED", None, "NO_TOKEN", "account has no access token"
    try:
        if action.type == "REPLY_TO_COMMENT" and event.comment_id:
            r = service.reply_to_comment(token, event.comment_id, text)
        elif action.type == "SEND_DM" and event.comment_id:
            # If this is an affiliate post, DM PRODUCT CARDS (image + Shop Now + See All Products)
            # — the HaulPack look — instead of a plain text link. Falls back to text otherwise.
            products = store.affiliate_products_for_media(account_id, event.post_id) if event.post_id else []
            if products:
                r = service.private_reply_cards(token, ig_id, event.comment_id, products, _storefront_url())
            else:
                r = service.private_reply(token, ig_id, event.comment_id, text)
        elif action.type == "SEND_DM" and event.user_id:
            products = store.affiliate_products_for_media(account_id, event.post_id) if event.post_id else []
            if products:
                r = service.send_product_cards(token, ig_id, {"id": event.user_id}, products, _storefront_url())
            else:
                r = service.send_dm(token, ig_id, event.user_id, text)
        else:
            return "SKIPPED", "no-target", None, None
        return "SUCCESS", str(r.get("id") or ""), None, None
    except service.GraphError as e:
        # Meta rate-limit codes → retryable (Spec 30). Everything else is permanent.
        retryable = str(e.code) in ("4", "17", "32", "613", "-1")
        return ("RATE_LIMITED" if retryable else "FAILED"), None, str(e.code), e.message


def process_event(account_id: int, event: R.InboundEvent, event_id: Optional[int],
                  dry: bool = False, persist: bool = True) -> Dict[str, Any]:
    """Evaluate rules for one event and dispatch actions. Idempotent + logged.
    persist=False (used by /simulate) previews without writing execution rows."""
    account = rags.get_account(account_id) or {}
    engine_rules = store.load_engine_rules(account_id)
    # HARD ISOLATION: a comment/DM on an AFFILIATE (Business-SK) post fires ONLY that post's
    # own post-scoped rule — never an account-wide rule (e.g. the Business-JK real-estate
    # "site visit" auto-reply). This holds even if the affiliate rule is disabled/missing, so
    # the two businesses can NEVER clash. Automations are strictly per-post.
    if event.post_id and store.affiliate_products_for_media(account_id, event.post_id):
        engine_rules = [r for r in engine_rules if r.post_id == event.post_id]
    matched = R.evaluate(engine_rules, event)
    from app.engagement import ai as eng_ai
    provider = R.default_provider(generate=eng_ai.draft_reply)   # AI only runs for actions with ai=true
    results = []
    for rule in matched:
        conv_id: Optional[int] = None            # a DM in this rule may open a conversation
        for action in rule.actions:
            # PER-ACTION idempotency: skip an action only if IT already succeeded, so a FAILED
            # SEND_DM retries next sync even though the REPLY_TO_COMMENT already went out.
            if event_id is not None and store.action_already_succeeded(rule.id, event_id, action.type):
                results.append({"rule_id": rule.id, "action": action.type, "status": "DUPLICATE"})
                continue
            text = provider.build(action, event, _context(event, account))
            status, ref, code, err = _dispatch(account_id, action, event, text, dry)
            if persist:
                store.log_execution(rule.id, account_id, action.type, status, post_id=event.post_id,
                                    event_id=event_id, comment_id=event.comment_id,
                                    conversation_id=event.conversation_id, request_reference=ref,
                                    error_code=code, error_message=err)
                # Reflect a real send in the inbox / comments so the UI shows the thread.
                if status == "SUCCESS":
                    if action.type == "REPLY_TO_COMMENT" and event.comment_id:
                        store.set_comment_reply_status(account_id, event.comment_id, "REPLIED")
                    if action.type == "SEND_DM" and (event.user_id or event.conversation_id):
                        conv_id = store.record_dm(account_id, event.user_id or event.conversation_id, text, ref,
                                                  direction="out", source_post_id=event.post_id, rule_id=rule.id)
                # MARK_LEAD → create/refresh a lead (Spec 31), attributed to the source post
                # and linked to the conversation the DM opened (if any).
                if action.type == "MARK_LEAD" and status == "SUCCESS":
                    store.upsert_lead(account_id, conversation_id=conv_id, source_post_id=event.post_id,
                                      username=event.username, label=(action.tag or "Potential Lead"),
                                      rule_id=rule.id)
            results.append({"rule_id": rule.id, "rule": rule.name, "action": action.type,
                            "status": status, "message": text, "ai": getattr(action, "ai", False),
                            "error": err})
    return {"matched_rules": len(matched), "executions": results}


class SimulateIn(BaseModel):
    account_id: int
    trigger_type: str = "COMMENT_RECEIVED"
    text: str
    post_id: Optional[str] = None
    username: Optional[str] = None


@router.post("/simulate")
def simulate(body: SimulateIn):
    """Run the full deterministic pipeline for a fake comment/DM — WITHOUT Meta.
    Shows exactly which rules match and what would be sent (dry). No AI, no posting."""
    event = R.InboundEvent(trigger_type=body.trigger_type, text=body.text, post_id=body.post_id,
                           comment_id="sim-comment", user_id="sim-user", username=body.username,
                           external_event_id=None)
    return process_event(body.account_id, event, event_id=None, dry=True, persist=False)


# ---- Meta webhook receiver ------------------------------------------------
@webhook_router.get("/meta")
def verify_webhook(request: Request):
    """Meta webhook verification handshake (Spec section 12)."""
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == _VERIFY_TOKEN and _VERIFY_TOKEN:
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "verification failed")


def _valid_signature(raw: bytes, header: Optional[str]) -> bool:
    if not _APP_SECRET:                         # signature check optional until configured
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


@webhook_router.post("/meta")
async def receive_webhook(request: Request):
    """Receive a Meta webhook: validate signature, ACK 200 in <100ms, then process the events
    in a background worker thread. Returning fast is REQUIRED: Meta gives the webhook ~5s and
    RETRIES (eventually disabling it) if we're slow — and the DM/Graph calls in _ingest are
    blocking. Processing inline was the cause of the ~5-min lag + duplicate deliveries. Now the
    200 is instant and the DM fires a beat later. Dedupe still protects against any Meta retry
    via the unique (account, external_event_id) constraint in store_event."""
    raw = await request.body()
    if not _valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    import json, asyncio
    payload = json.loads(raw or b"{}")
    # Fire-and-forget in a thread so blocking Graph calls never hold up the 200 ACK to Meta.
    asyncio.get_running_loop().run_in_executor(None, _process_payload, payload)
    return {"received": True}


def _process_payload(payload: Dict[str, Any]) -> int:
    """Process every entry of a Meta webhook payload (runs in a worker thread). Each event's
    own errors are swallowed inside _ingest, so one bad event never stops the rest."""
    processed = 0
    for entry in payload.get("entry", []):
        acct = _account_for_ig(str(entry.get("id", "")))
        if not acct:
            continue
        # Comments (and messages delivered as change objects) arrive in `changes`.
        for change in entry.get("changes", []):
            processed += _ingest(acct, _parse_change(change), change)
        # Instagram DMs arrive in the Messenger-style `messaging` array.
        for msg in entry.get("messaging", []):
            processed += _ingest(acct, _parse_messaging(msg), msg)
    return processed


def _ingest(acct: Dict[str, Any], ev: Optional[Dict[str, Any]], raw_obj: Dict[str, Any]) -> int:
    """Store → dedupe → persist inbound → run rules for one parsed event. Returns 1 if
    processed, 0 if skipped/duplicate."""
    if not ev:
        return 0
    rec = store.store_event(acct["id"], ev["event_type"], ev["external_event_id"],
                            raw_obj, post_id=ev.get("post_id"), comment_id=ev.get("comment_id"))
    if rec.get("is_duplicate"):
        return 0
    iev = ev["event"]
    if ev["event_type"] == "COMMENT_RECEIVED" and iev.comment_id:
        store.upsert_comment(acct["id"], iev.post_id, iev.comment_id,
                             username=iev.username, user_id=iev.user_id, text=iev.text)
    elif ev["event_type"] == "DM_RECEIVED" and (iev.user_id or iev.conversation_id):
        store.record_dm(acct["id"], iev.user_id or iev.conversation_id, iev.text,
                        iev.external_event_id, direction="in")
    try:
        process_event(acct["id"], iev, rec["event_id"])
        store.mark_event(rec["event_id"], "SUCCESS")
    except Exception as exc:  # noqa: BLE001 — never let one bad event 500 the webhook
        store.mark_event(rec["event_id"], "FAILED", str(exc))
    return 1


def _parse_messaging(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a Messenger-style Instagram DM (`entry[].messaging[]`) to an InboundEvent."""
    msg = item.get("message") or {}
    if msg.get("is_echo"):                       # skip our own outbound echoes
        return None
    mid = msg.get("mid") or item.get("timestamp")
    sender = str((item.get("sender") or {}).get("id") or "")
    text = msg.get("text", "")
    if not sender:
        return None
    return {"event_type": "DM_RECEIVED", "external_event_id": f"dm:{mid}",
            "conversation_id": sender,
            "event": R.InboundEvent(trigger_type="DM_RECEIVED", text=text, conversation_id=sender,
                                    user_id=sender, username=None, external_event_id=f"dm:{mid}")}


# ---- background poller ----------------------------------------------------
_poller_started = False


async def _auto_sync_loop():
    """Every _SYNC_INTERVAL seconds, pull comments/DMs for every account and auto-reply.
    This is what keeps auto-commenting live while the project runs continuously — no
    public webhook required. Each blocking sync runs in a worker thread so the event
    loop stays responsive; any per-account error is swallowed so the loop never dies."""
    import asyncio
    while True:
        await asyncio.sleep(_SYNC_INTERVAL)
        if not _AUTO_SYNC:
            continue
        # BACK OFF when Meta says we're near the rate limit — this prevents #4 / bot flags.
        # >80% usage: skip this tick entirely and let it cool.
        usage = service.app_usage_pct()
        if usage >= 80:
            print(f"[engagement] Meta rate-limit usage {usage:.0f}% — skipping sync tick to cool down", flush=True)
            await asyncio.sleep(_SYNC_INTERVAL * 4)     # extra cooldown
            continue
        try:
            accounts = rags.list_accounts() or []
        except Exception:
            accounts = []
        for a in accounts:
            aid = a.get("id")
            if aid is None:
                continue
            # NO UNWANTED CALLS: auto-poll ONLY accounts running the Business-SK affiliate
            # automation (they have affiliate posts + a Page token, so their calls hit the
            # Page rate-limit bucket, NOT the Application bucket). User-token accounts (the
            # JK real-estate ones) are never auto-polled here — that was the Application-limit
            # drain. They can still be synced on demand via the Sync button.
            try:
                if not store.list_affiliate_posts(int(aid)):
                    continue
                if not store.load_engine_rules(int(aid)):
                    continue
            except Exception:
                continue
            try:
                # light=True → comments only (no insights / no DM-inbox pull) — minimal API calls.
                await asyncio.to_thread(run_account_sync, int(aid), True, True)
            except Exception:
                pass


def start_background_sync() -> None:
    """Launch the poller once, from the app's async startup (lifespan)."""
    global _poller_started
    if _poller_started or not _AUTO_SYNC:
        return
    import asyncio
    try:
        asyncio.get_running_loop().create_task(_auto_sync_loop())
        _poller_started = True
    except RuntimeError:
        pass                                    # no running loop yet; caller retries


def _account_for_ig(ig_id: str) -> Optional[Dict[str, Any]]:
    for a in (rags.list_accounts() or []):
        if str(a.get("ig_business_id")) == ig_id:
            return a
    return None


def _parse_change(change: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a Meta change object to our InboundEvent (comments + messages)."""
    field = change.get("field")
    val = change.get("value") or {}
    if field == "comments":
        cid = val.get("id")
        return {"event_type": "COMMENT_RECEIVED", "external_event_id": f"comment:{cid}",
                "post_id": (val.get("media") or {}).get("id"), "comment_id": cid,
                "event": R.InboundEvent(trigger_type="COMMENT_RECEIVED", text=val.get("text", ""),
                                        post_id=(val.get("media") or {}).get("id"), comment_id=cid,
                                        username=(val.get("from") or {}).get("username"),
                                        user_id=(val.get("from") or {}).get("id"),
                                        external_event_id=f"comment:{cid}")}
    if field in ("messages", "messaging"):
        mid = val.get("message", {}).get("mid") or val.get("id")
        sender = (val.get("sender") or {}).get("id")
        return {"event_type": "DM_RECEIVED", "external_event_id": f"dm:{mid}",
                "conversation_id": sender,
                "event": R.InboundEvent(trigger_type="DM_RECEIVED",
                                        text=val.get("message", {}).get("text", ""),
                                        conversation_id=sender, user_id=sender,
                                        external_event_id=f"dm:{mid}")}
    return None
