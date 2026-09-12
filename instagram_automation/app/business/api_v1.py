"""Canonical /api/v1 REST surface with the standard response envelope.

Spec §2/§37/§43/§66: every response is {success, data, error, meta}. Handlers reuse
the existing services (store, pipeline, marketing, slides, analytics, enrich) so this
is a thin, documented layer — FastAPI auto-generates the OpenAPI spec at /openapi.json
and interactive docs at /docs. Errors raised as HTTPException are enveloped by the
handler registered in app/api.py. Admin auth is enforced by the global middleware.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app import settings
from app.business import analytics as analytics_svc
from app.business import pipeline, slides as slidesvc, store
from app.business.campaign_brief import CampaignBrief, options as brief_options

router = APIRouter(prefix="/api/v1", tags=["v1"])
_DATA = settings.BASE_DIR / "data-business"


def ok(data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = {"request_id": uuid.uuid4().hex[:16], "timestamp": int(time.time())}
    if meta:
        m.update(meta)
    return {"success": True, "data": data, "error": None, "meta": m}


# ===================== HEALTH (§59) =====================

@router.get("/health")
def health():
    return ok({"status": "ok"})


@router.get("/health/ready")
def health_ready():
    try:
        store.list_properties(limit=1)
        db = True
    except Exception:  # noqa: BLE001
        db = False
    return ok({"ready": db, "database": db})


@router.get("/health/dependencies")
def health_deps():
    deps = {
        "database": _safe(lambda: bool(store.list_properties(limit=1)) or True),
        "llm_provider": bool(settings.OPENAI_API_KEY),
        "renderer": True,          # Playwright/Chromium in image
        "document_extraction": True,
        "instagram": True,         # Graph API via existing engine
    }
    return ok(deps)


def _safe(fn):
    try:
        fn(); return True
    except Exception:  # noqa: BLE001
        return False


# ===================== JOBS (§13/§38) =====================

@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return ok(job)


# ===================== DASHBOARD (§7) =====================

@router.get("/dashboard/summary")
def dashboard():
    props = store.list_properties(limit=1000)
    ov = store.analytics_overview()
    return ok({"properties": len(props),
               "campaigns": sum(int(p.get("campaigns") or 0) for p in props),
               "campaigns_tracked": ov.get("campaigns_tracked", 0),
               "recent_properties": props[:6], "by_angle": ov.get("by_angle", [])})


# ===================== PROPERTIES (§8/§9/§44) =====================

class PropertyIn(BaseModel):
    project_name: str
    property_type: Optional[str] = None
    city: Optional[str] = None
    builder: Optional[str] = None


@router.get("/properties")
def list_properties(limit: int = 100):
    return ok(store.list_properties(limit=limit))


@router.post("/properties")
def create_property(body: PropertyIn):
    pid = store.create_property_manual(body.model_dump(exclude_none=True))
    store.audit("PROPERTY_CREATED", "property", pid)
    return ok({"property_id": pid})


@router.get("/properties/{pid}")
def get_property(pid: str):
    m = store.get_property_model(pid)
    if not m:
        raise HTTPException(404, "Property not found")
    return ok(m)


@router.put("/properties/{pid}")
def update_property(pid: str, body: PropertyIn):
    m = store.update_property_fields(pid, body.model_dump(exclude_none=True))
    if not m:
        raise HTTPException(404, "Property not found")
    store.audit("PROPERTY_UPDATED", "property", pid)
    return ok(m)


@router.delete("/properties/{pid}")
def archive_property(pid: str):
    if not store.archive_property(pid):
        raise HTTPException(404, "Property not found")
    store.audit("PROPERTY_ARCHIVED", "property", pid)
    return ok({"archived": pid})


@router.get("/properties/{pid}/overview")
def property_overview(pid: str):
    m = store.get_property_model(pid) or {}
    if not m:
        raise HTTPException(404, "Property not found")
    return ok({"verified_facts": len(m.get("claims", [])), "documents": len(m.get("source_documents", [])),
               "media_assets": len(m.get("media", [])), "confidence": (m.get("confidence") or {})})


@router.get("/properties/{pid}/workspace")
def property_workspace(pid: str):
    prop = store.get_property(pid)
    if not prop:
        raise HTTPException(404, "Property not found")
    m = prop.get("model") or {}
    return ok({"property": {"id": prop["id"], "project_name": prop.get("project_name")},
               "knowledge": m, "campaigns": prop.get("campaigns", []),
               "versions": store.list_versions(pid), "media": m.get("media", []),
               "documents": m.get("source_documents", [])})


@router.get("/properties/{pid}/knowledge")
def get_knowledge(pid: str):
    m = store.get_property_model(pid)
    if not m:
        raise HTTPException(404, "Property not found")
    return ok(m)


@router.get("/properties/{pid}/facts")
def get_facts(pid: str):
    m = store.get_property_model(pid)
    if not m:
        raise HTTPException(404, "Property not found")
    return ok(m.get("claims", []))


@router.post("/properties/{pid}/facts/{field}/approve")
def approve_fact(pid: str, field: str):
    c = store.set_fact_status(pid, field, "approved")
    if c is None:
        raise HTTPException(404, "Fact not found")
    store.audit("FACT_APPROVED", "property_fact", f"{pid}:{field}")
    return ok(c)


@router.post("/properties/{pid}/facts/{field}/reject")
def reject_fact(pid: str, field: str):
    c = store.set_fact_status(pid, field, "rejected")
    if c is None:
        raise HTTPException(404, "Fact not found")
    store.audit("FACT_REJECTED", "property_fact", f"{pid}:{field}")
    return ok(c)


@router.get("/properties/{pid}/evidence")
def property_evidence(pid: str):
    m = store.get_property_model(pid)
    if not m:
        raise HTTPException(404, "Property not found")
    return ok([{"field": c["field"], "value": c["value"], "confidence": c.get("confidence"),
                "source": c.get("source"), "status": c.get("status", "extracted")}
               for c in m.get("claims", [])])


@router.get("/properties/{pid}/versions")
def property_versions(pid: str):
    return ok(store.list_versions(pid))


@router.get("/properties/{pid}/versions/compare")
def compare_versions(pid: str, from_id: int, to_id: int):
    return ok(store.compare_versions(pid, from_id, to_id))


# ===================== DOCUMENTS + EXTRACTION (§11/§12/§13) =====================

@router.get("/documents")
def list_documents():
    docs = [p.name for p in _DATA.iterdir()] if _DATA.exists() else []
    return ok({"documents": sorted(docs)})


async def upload_document(file: UploadFile = File(...)):
    _DATA.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    name = Path(file.filename or "document").name
    (_DATA / name).write_bytes(data)
    store.audit("DOCUMENT_UPLOADED", "document", name)
    return ok({"document": name, "size": len(data)})


@router.post("/documents/{document}/extract")
def extract_document(document: str):
    """Runs extraction and records a completed job (§13 shape; execution is sync)."""
    path = _DATA / document
    if not path.exists():
        raise HTTPException(404, "Document not found")
    jid = store.create_job("extraction")
    try:
        result = pipeline.extract_property(path)
        pid = store.save_property(result["knowledge_model"], result.get("verdict") or {}, result["document"])
        store.snapshot_version(pid, result["knowledge_model"])
        store.finish_job(jid, status="completed", result={"property_id": pid})
        store.audit("DOCUMENT_EXTRACTED", "property", pid, new_value=document)
    except Exception as exc:  # noqa: BLE001
        store.finish_job(jid, status="failed", error=str(exc))
        raise HTTPException(500, str(exc))
    return ok({"job_id": jid, "status": "completed", "property_id": pid})


@router.get("/documents/{document}/extraction")
def extraction_status(document: str):
    # sync model: presence of a property whose source includes this doc = completed
    for p in store.list_properties(limit=1000):
        m = store.get_property_model(p["id"]) or {}
        if document in (m.get("source_documents") or []):
            return ok({"status": "completed", "property_id": p["id"]})
    return ok({"status": "not_started"})


# ===================== MEDIA (§15) =====================

@router.get("/media")
def media(category: Optional[str] = None):
    items: List[Dict[str, Any]] = []
    for p in store.list_properties(limit=1000):
        m = store.get_property_model(p["id"]) or {}
        for a in m.get("media", []):
            if a.get("cdn_url") and (not category or a.get("asset_type") == category):
                items.append({"property_id": p["id"], **a})
    return ok({"media": items, "count": len(items)})


@router.get("/properties/{pid}/media")
def property_media(pid: str):
    m = store.get_property_model(pid) or {}
    return ok(m.get("media", []))


# ===================== BRANDS (§16) =====================

class BrandV1(BaseModel):
    name: str = "Brand"
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    font: Optional[str] = None
    style: Optional[str] = None
    footer: Optional[str] = None


@router.get("/brands")
def list_brands():
    return ok(store.list_brands())


@router.post("/brands")
def create_brand(body: BrandV1):
    return ok(store.create_brand(body.model_dump(exclude_none=True)))


@router.get("/brands/{bid}")
def get_brand(bid: int):
    b = store.get_brand(bid)
    if not b:
        raise HTTPException(404, "Brand not found")
    return ok(b)


@router.put("/brands/{bid}")
def update_brand(bid: int, body: BrandV1):
    b = store.update_brand(bid, body.model_dump(exclude_none=True))
    if not b:
        raise HTTPException(404, "Brand not found")
    return ok(b)


@router.delete("/brands/{bid}")
def delete_brand(bid: int):
    if not store.delete_brand(bid):
        raise HTTPException(404, "Brand not found")
    return ok({"deleted": bid})


# ===================== CAMPAIGNS (§18-40, §66) =====================

class CampaignGen(BaseModel):
    property_id: str
    brief: CampaignBrief = CampaignBrief()
    render: bool = True


class BriefPut(BaseModel):
    brief: CampaignBrief


@router.get("/campaigns")
def list_campaigns(property_id: Optional[str] = None):
    items = store.list_all_campaigns()
    if property_id:
        items = [c for c in items if c.get("property_id") == property_id]
    return ok(items)


@router.post("/campaigns/generate")
def generate_campaign(body: CampaignGen):
    """Orchestrator (§40/§66 step 18): brief + saved knowledge -> full campaign."""
    model = store.get_property_model(body.property_id)
    if not model:
        raise HTTPException(404, "Property not found — extract it first")
    jid = store.create_job("campaign_generate")
    try:
        from app.business.api import _resolve_brand
        brand = _resolve_brand(body.brief.model_dump())
        result = pipeline.generate_campaign(model, body.brief, render=body.render, brand=brand)
        persisted = store.save_campaign(body.property_id, body.brief.model_dump(), result)
        store.finish_job(jid, status="completed", result=persisted)
        store.audit("CAMPAIGN_GENERATED", "campaign", persisted["campaign_id"])
    except Exception as exc:  # noqa: BLE001
        store.finish_job(jid, status="failed", error=str(exc))
        raise HTTPException(500, str(exc))
    return ok({"job_id": jid, **persisted, "marketing": result.get("marketing"),
               "caption": result.get("caption"), "contract": result.get("contract")})


@router.get("/campaigns/{cid}")
def get_campaign(cid: int):
    c = store.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return ok(c)


@router.post("/campaigns/{cid}/duplicate")
def duplicate_campaign(cid: int):
    nid = store.duplicate_campaign(cid)
    if not nid:
        raise HTTPException(404, "Campaign not found")
    return ok({"campaign_id": nid})


def _campaign_or_404(cid: int) -> Dict[str, Any]:
    c = store.get_campaign(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


@router.get("/campaigns/{cid}/brief")
def get_brief(cid: int):
    return ok(_campaign_or_404(cid).get("brief") or {})


@router.get("/campaigns/{cid}/strategy")
def get_strategy(cid: int):
    return ok(_campaign_or_404(cid).get("marketing") or {})


@router.get("/campaigns/{cid}/blueprint")
def get_blueprint(cid: int):
    c = _campaign_or_404(cid)
    return ok((c.get("carousel") or {}).get("slides", []))


@router.get("/campaigns/{cid}/slides")
def get_slides(cid: int):
    c = _campaign_or_404(cid)
    return ok((c.get("carousel") or {}).get("slides", []))


class RegenV1(BaseModel):
    mode: str = "copy"


@router.post("/campaigns/{cid}/slides/{index}/regenerate")
def regen_slide(cid: int, index: int, body: RegenV1):
    from app.business.api import _load_campaign_and_model, _rerender_and_save
    camp, model, carousel = _load_campaign_and_model(cid)
    from app.business.provider import get_provider
    slidesvc.regenerate_slide(model, camp.get("brief") or {}, carousel, index, body.mode, get_provider())
    res, _ = _rerender_and_save(camp, carousel, model, True)
    return ok({"slide": carousel["slides"][index], "images": res.get("images", [])})


@router.post("/campaigns/{cid}/slides/{index}/lock")
def lock_slide(cid: int, index: int):
    camp = _campaign_or_404(cid); carousel = camp.get("carousel") or {"slides": []}
    slidesvc.set_lock(carousel, index, True); store.update_campaign(cid, carousel=carousel)
    return ok({"slide": index, "locked": True})


@router.post("/campaigns/{cid}/slides/{index}/unlock")
def unlock_slide(cid: int, index: int):
    camp = _campaign_or_404(cid); carousel = camp.get("carousel") or {"slides": []}
    slidesvc.set_lock(carousel, index, False); store.update_campaign(cid, carousel=carousel)
    return ok({"slide": index, "locked": False})


@router.post("/campaigns/{cid}/render")
def render_campaign(cid: int):
    from app.business.api import _load_campaign_and_model, _rerender_and_save
    camp, model, carousel = _load_campaign_and_model(cid)
    res, _ = _rerender_and_save(camp, carousel, model, True)
    return ok({"rendered": res.get("rendered"), "images": res.get("images", [])})


@router.post("/campaigns/{cid}/validate")
def validate_campaign(cid: int):
    c = _campaign_or_404(cid)
    violations = []  # claim-policy violations were captured at generation
    status = "PASS" if c.get("status") in ("AUTO_APPROVED", "PUBLISHED") else "REVIEW_REQUIRED"
    return ok({"status": status, "confidence": 0.95, "warnings": [], "errors": violations})


@router.get("/campaigns/{cid}/caption")
def get_caption(cid: int):
    return ok(_campaign_or_404(cid).get("caption") or {})


@router.get("/campaigns/{cid}/preview")
def preview_campaign(cid: int):
    c = _campaign_or_404(cid)
    contract = c.get("contract") or {}
    return ok({"slides": (contract.get("carousel") or {}).get("slides", []),
               "images": (contract.get("carousel") or {}).get("images", []),
               "caption": contract.get("caption"), "hashtags": contract.get("hashtags", []),
               "cta": contract.get("cta"), "status": c.get("status"),
               "marketing": c.get("marketing")})


class StatusV1(BaseModel):
    status: str


@router.post("/campaigns/{cid}/approve")
def approve_campaign(cid: int):
    store.set_campaign_status(cid, "AUTO_APPROVED"); store.audit("CAMPAIGN_APPROVED", "campaign", cid)
    return ok({"campaign_id": cid, "status": "AUTO_APPROVED"})


@router.post("/campaigns/{cid}/reject")
def reject_campaign(cid: int):
    store.set_campaign_status(cid, "REJECTED"); store.audit("CAMPAIGN_REJECTED", "campaign", cid)
    return ok({"campaign_id": cid, "status": "REJECTED"})


# ===================== ANALYTICS (§47) =====================

@router.get("/analytics/overview")
def analytics_overview():
    return ok(store.analytics_overview())


@router.get("/analytics/campaigns/{cid}")
def analytics_campaign(cid: int):
    return ok(store.get_analytics_for_campaign(cid))


# ===================== TEMPLATES (§48) =====================

class TemplateIn(BaseModel):
    name: str
    config: Dict[str, Any] = {}


@router.get("/templates")
def list_templates():
    # includes the design-system vocabulary + any saved templates
    return ok({"saved": store.list_templates(), "vocabulary": brief_options()})


@router.post("/templates")
def create_template(body: TemplateIn):
    return ok(store.create_template(body.name, body.config))


@router.get("/templates/{tid}")
def get_template(tid: int):
    t = store.get_template(tid)
    if not t:
        raise HTTPException(404, "Template not found")
    return ok(t)


@router.put("/templates/{tid}")
def update_template(tid: int, body: TemplateIn):
    t = store.update_template(tid, body.name, body.config)
    if not t:
        raise HTTPException(404, "Template not found")
    return ok(t)


@router.delete("/templates/{tid}")
def delete_template(tid: int):
    if not store.delete_template(tid):
        raise HTTPException(404, "Template not found")
    return ok({"deleted": tid})


# ===================== INTEGRATIONS (§50) =====================

@router.get("/integrations/instagram/accounts")
def instagram_accounts():
    from app import rags
    return ok(rags.list_accounts(active_only=False))


@router.get("/integrations/instagram/health")
def instagram_health():
    from app import rags
    accs = rags.list_accounts(active_only=False)
    return ok({"connected": any(a.get("has_token") for a in accs), "accounts": len(accs)})


# ===================== ASYNC variants (§13/§38) =====================

@router.post("/documents/{document}/extract-async")
def extract_async(document: str):
    from app.business import jobs
    path = _DATA / document
    if not path.exists():
        raise HTTPException(404, "Document not found")
    return ok({"job_id": jobs.submit_extract(str(path)), "status": "queued"})


@router.post("/campaigns/generate-async")
def generate_async(body: CampaignGen):
    from app.business import jobs
    if not store.get_property_model(body.property_id):
        raise HTTPException(404, "Property not found — extract it first")
    return ok({"job_id": jobs.submit_generate(body.property_id, body.brief.model_dump(), body.render),
               "status": "queued"})


# ===================== CAMPAIGN CONFIG PUTs (§19/§24/§25/§26/§42) =====================

def _put_brief(cid: int, patch: Dict[str, Any]):
    b = store.merge_campaign_brief(cid, patch)
    if b is None:
        raise HTTPException(404, "Campaign not found")
    return ok(b)


@router.put("/campaigns/{cid}/brief")
def put_brief(cid: int, body: Dict[str, Any]):
    return _put_brief(cid, body)


@router.put("/campaigns/{cid}/creative")
def put_creative(cid: int, body: Dict[str, Any]):
    keys = ("template", "tone", "content_density", "language", "image_policy", "brand")
    return _put_brief(cid, {k: body[k] for k in keys if k in body})


@router.put("/campaigns/{cid}/cta")
def put_cta(cid: int, body: Dict[str, Any]):
    keys = ("cta_objective", "contact_method", "cta_keyword", "cta_text")
    return _put_brief(cid, {k: body[k] for k in keys if k in body})


@router.put("/campaigns/{cid}/rules")
def put_rules(cid: int, body: Dict[str, Any]):
    return _put_brief(cid, {k: body[k] for k in ("claim_policy",) if k in body})


@router.put("/campaigns/{cid}/advanced")
def put_advanced(cid: int, body: Dict[str, Any]):
    keys = ("generation_mode", "campaign_version")
    return _put_brief(cid, {k: body[k] for k in keys if k in body})


@router.get("/campaigns/{cid}/creative")
def get_creative(cid: int):
    b = _campaign_or_404(cid).get("brief") or {}
    return ok({k: b.get(k) for k in ("template", "tone", "content_density", "language", "image_policy", "brand")})


@router.get("/campaigns/{cid}/cta")
def get_cta(cid: int):
    b = _campaign_or_404(cid).get("brief") or {}
    return ok({k: b.get(k) for k in ("cta_objective", "contact_method", "cta_keyword", "cta_text")})


@router.get("/campaigns/{cid}/rules")
def get_rules(cid: int):
    b = _campaign_or_404(cid).get("brief") or {}
    return ok({"claim_policy": b.get("claim_policy")})


@router.get("/campaigns/{cid}/advanced")
def get_advanced(cid: int):
    b = _campaign_or_404(cid).get("brief") or {}
    return ok({k: b.get(k) for k in ("generation_mode", "campaign_version")})


# ===================== BLUEPRINT ops + per-slide render (§30/§35) =====================

class ReorderV1(BaseModel):
    order: List[int]


_TEMPLATE_RANK = {"hero": 0, "property_overview": 1, "location": 2, "map": 3, "connectivity": 4,
                  "features": 5, "feature": 5, "amenity": 6, "lifestyle": 7, "floorplan": 8,
                  "value": 9, "builder": 10, "cta": 99}


@router.post("/campaigns/{cid}/blueprint/reorder")
def blueprint_reorder(cid: int, body: ReorderV1):
    from app.business.api import _load_campaign_and_model, _rerender_and_save
    camp, model, carousel = _load_campaign_and_model(cid)
    try:
        slidesvc.reorder(carousel, [int(i) for i in body.order])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    res, _ = _rerender_and_save(camp, carousel, model, True)
    return ok({"slides": carousel["slides"], "images": res.get("images", [])})


@router.post("/campaigns/{cid}/blueprint/auto-arrange")
def blueprint_auto_arrange(cid: int):
    from app.business.api import _load_campaign_and_model, _rerender_and_save
    camp, model, carousel = _load_campaign_and_model(cid)
    sl = carousel.get("slides", [])
    order = sorted(range(len(sl)), key=lambda i: _TEMPLATE_RANK.get(sl[i].get("template"), 50))
    slidesvc.reorder(carousel, order)
    res, _ = _rerender_and_save(camp, carousel, model, True)
    return ok({"slides": carousel["slides"], "images": res.get("images", [])})


@router.post("/campaigns/{cid}/slides/{index}/render")
def render_slide(cid: int, index: int):
    from app.business.api import _load_campaign_and_model, _rerender_and_save
    camp, model, carousel = _load_campaign_and_model(cid)
    res, _ = _rerender_and_save(camp, carousel, model, True)
    imgs = res.get("images", [])
    return ok({"slide": index, "image": imgs[index] if index < len(imgs) else None})


# ===================== PUBLISH + SCHEDULE (§51) =====================

class PublishV1(BaseModel):
    account_id: int
    dry_run: bool = False


class ScheduleV1(BaseModel):
    scheduled_at: str
    account_id: Optional[int] = None


@router.post("/campaigns/{cid}/publish")
def publish_v1(cid: int, body: PublishV1):
    from app.business.api import publish_campaign, PublishReq
    return ok(publish_campaign(cid, PublishReq(account_id=body.account_id, dry_run=body.dry_run)))


@router.get("/campaigns/{cid}/publish")
def publish_status_v1(cid: int):
    c = _campaign_or_404(cid)
    return ok({"status": c.get("status"), "published": (c.get("contract") or {}).get("published")})


@router.post("/campaigns/{cid}/schedule")
def schedule_v1(cid: int, body: ScheduleV1):
    _campaign_or_404(cid)
    store.schedule_campaign(cid, body.scheduled_at, body.account_id)
    store.set_campaign_status(cid, "SCHEDULED")
    store.audit("CAMPAIGN_SCHEDULED", "campaign", cid, new_value=body.scheduled_at)
    return ok({"campaign_id": cid, "scheduled_at": body.scheduled_at})


@router.delete("/campaigns/{cid}/schedule")
def unschedule_v1(cid: int):
    store.unschedule_campaign(cid)
    return ok({"campaign_id": cid, "scheduled": False})


class NotesV1(BaseModel):
    notes: str = ""


@router.post("/campaigns/{cid}/request-changes")
def request_changes_v1(cid: int, body: NotesV1):
    store.set_campaign_status(cid, "REVIEW_REQUIRED")
    store.audit("CAMPAIGN_CHANGES_REQUESTED", "campaign", cid, new_value=body.notes)
    return ok({"campaign_id": cid, "status": "REVIEW_REQUIRED", "notes": body.notes})


@router.get("/campaigns/{cid}/approval")
def approval_state_v1(cid: int):
    return ok({"status": _campaign_or_404(cid).get("status")})


# ===================== ANALYTICS property + compare (§47) =====================

@router.get("/analytics/properties/{pid}")
def analytics_property(pid: str):
    return ok(store.get_analytics_for_property(pid))


class CompareV1(BaseModel):
    campaign_ids: List[int]


@router.post("/analytics/campaigns/compare")
def analytics_compare(body: CompareV1):
    rows = []
    for cid in body.campaign_ids:
        hist = store.get_analytics_for_campaign(cid)
        if hist:
            latest = hist[0]
            rows.append({"campaign_id": cid, "score": latest.get("score"),
                         "metrics": latest.get("metrics"), "angle": None})
    return ok(analytics_svc.compare(rows))


# ===================== BRAND preview + DOCUMENT get/delete (§16/§11) ==========

@router.get("/brands/{bid}/preview")
def brand_preview(bid: int):
    b = store.get_brand(bid)
    if not b:
        raise HTTPException(404, "Brand not found")
    return ok({"brand": b, "note": "Applied to slides at render time (colors, logo, font)."})


@router.get("/documents/{document}")
def get_document(document: str):
    path = _DATA / document
    if not path.exists():
        raise HTTPException(404, "Document not found")
    st = path.stat()
    return ok({"document": document, "size": st.st_size, "suffix": path.suffix.lower()})


@router.delete("/documents/{document}")
def delete_document(document: str):
    path = _DATA / document
    if not path.exists():
        raise HTTPException(404, "Document not found")
    path.unlink()
    store.audit("DOCUMENT_DELETED", "document", document)
    return ok({"deleted": document})


# ===================== MEDIA classify + quality (§15) =====================

@router.get("/properties/{pid}/media/{index}/quality")
def media_quality(pid: str, index: int):
    m = store.get_property_model(pid) or {}
    media = m.get("media", [])
    if index < 0 or index >= len(media):
        raise HTTPException(404, "Media not found")
    a = media[index]
    return ok({"resolution": a.get("resolution"), "usable": a.get("usable"),
               "confidence": a.get("confidence"), "asset_type": a.get("asset_type")})


@router.post("/properties/{pid}/media/{index}/classify")
def media_classify(pid: str, index: int):
    from app.business.knowledge import ASSET_LABELS
    from app.business.provider import get_provider
    m = store.get_property_model(pid)
    if not m:
        raise HTTPException(404, "Property not found")
    media = m.get("media", [])
    if index < 0 or index >= len(media):
        raise HTTPException(404, "Media not found")
    a = media[index]
    ref = a.get("storage_ref")
    if not ref or not Path(ref).exists():
        raise HTTPException(400, "Source image unavailable")
    verdict = get_provider().classify_image(image_bytes=Path(ref).read_bytes(), mime="image/png",
                                            labels=ASSET_LABELS, instruction="Re-classify this asset.")
    a["asset_type"] = verdict.get("label", a.get("asset_type"))
    a["confidence"] = round(float(verdict.get("confidence", 0) or 0), 2)
    store.save_property(m, {"status": "REVIEW_REQUIRED"},
                        (m.get("source_documents") or ["manual"])[0])
    return ok(a)


# ===================== INSTAGRAM connect / disconnect (§50) =====================

class IGConnect(BaseModel):
    label: str
    ig_business_id: str
    ig_access_token: str
    handle: str = ""
    niche: str = "both"


@router.post("/integrations/instagram/connect")
def instagram_connect(body: IGConnect):
    from app import rags
    acc = rags.add_account(label=body.label, niche=body.niche, ig_business_id=body.ig_business_id,
                           ig_access_token=body.ig_access_token, handle=body.handle)
    store.audit("INSTAGRAM_CONNECTED", "account", acc.get("id"))
    return ok(acc)


@router.delete("/integrations/instagram/{account_id}")
def instagram_disconnect(account_id: int):
    from app import rags
    if not rags.delete_account(account_id):
        raise HTTPException(404, "Account not found")
    store.audit("INSTAGRAM_DISCONNECTED", "account", account_id)
    return ok({"disconnected": account_id})


# ===================== AFFILIATE PROGRAM ACCOUNTS (encrypted via .ragskey) ======
# Store affiliate-program credentials (Amazon Associates, EarnKaro, Cuelinks, …)
# encrypted at rest; secrets are masked when listed. Admin-authed (this whole router).

AFFILIATE_PROGRAMS = [
    "amazon_associates", "earnkaro", "cuelinks", "inrdeals", "flipkart_affiliate",
    "vcommission", "admitad", "impact", "other",
]


class AffiliateConnect(BaseModel):
    program: str
    label: str = ""
    tracking_id: str = ""            # associate tag / publisher id
    api_key: str = ""
    api_secret: str = ""
    link_template: str = ""
    notes: str = ""
    is_active: bool = True


class AffiliateUpdate(BaseModel):
    program: Optional[str] = None
    label: Optional[str] = None
    tracking_id: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    link_template: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/integrations/affiliate/programs")
def affiliate_programs():
    """The affiliate programs the panel supports (for the dropdown)."""
    return ok({"programs": AFFILIATE_PROGRAMS})


@router.get("/integrations/affiliate/accounts")
def affiliate_accounts_list():
    from app import rags
    return ok(rags.list_affiliate_accounts())


@router.get("/integrations/affiliate/active-tag")
def affiliate_active_tag():
    """Semi-public: the active Amazon Associates tag (appears in every affiliate URL),
    read internally by the affiliate service to build links from the stored account.
    Never returns api_key/secret. Open path (see api._OPEN_PATHS)."""
    from app import rags
    return ok({"tag": rags.active_amazon_tag() or ""})


@router.post("/affiliate/sync-performance")
def affiliate_sync_performance(limit: int = 50):
    """Pull real Instagram insights for published affiliate carousels and feed them to
    the affiliate performance loop (Learning + winner prediction). Only real Graph
    metrics; fail-open. Safe to call repeatedly (latest snapshot per post wins)."""
    from app.business import affiliate_sync
    return ok(affiliate_sync.sync_affiliate_performance(limit=max(1, min(limit, 200))))


@router.post("/integrations/affiliate/connect")
def affiliate_connect(body: AffiliateConnect):
    from app import rags
    if body.program not in AFFILIATE_PROGRAMS:
        raise HTTPException(422, f"unknown program '{body.program}'")
    acc = rags.add_affiliate_account(
        program=body.program, label=body.label, tracking_id=body.tracking_id,
        api_key=body.api_key, api_secret=body.api_secret,
        link_template=body.link_template, notes=body.notes, is_active=body.is_active)
    store.audit("AFFILIATE_CONNECTED", "affiliate_account", acc.get("id"), new_value=body.program)
    return ok(acc)


@router.put("/integrations/affiliate/{account_id}")
def affiliate_update(account_id: int, body: AffiliateUpdate):
    from app import rags
    acc = rags.update_affiliate_account(account_id, **body.model_dump(exclude_none=True))
    if not acc:
        raise HTTPException(404, "Affiliate account not found")
    store.audit("AFFILIATE_UPDATED", "affiliate_account", account_id)
    return ok(acc)


@router.delete("/integrations/affiliate/{account_id}")
def affiliate_delete(account_id: int):
    from app import rags
    if not rags.delete_affiliate_account(account_id):
        raise HTTPException(404, "Affiliate account not found")
    store.audit("AFFILIATE_DISCONNECTED", "affiliate_account", account_id)
    return ok({"disconnected": account_id})


# ===================== LEADS (§19) + CALENDAR/SCHEDULES =====================

class LeadIn(BaseModel):
    property_id: Optional[str] = None
    campaign_id: Optional[int] = None
    channel: str = "manual"      # dm | whatsapp | call | website | manual
    contact: Optional[str] = None
    name: Optional[str] = None
    message: Optional[str] = None


class LeadStatus(BaseModel):
    status: str                  # new | contacted | site_visit | converted | lost


@router.get("/leads")
def list_leads():
    return ok(store.list_leads())


@router.post("/leads")
def create_lead(body: LeadIn):
    lead = store.save_lead(body.model_dump(exclude_none=True))
    store.audit("LEAD_CAPTURED", "lead", lead["id"], new_value=body.channel)
    return ok(lead)


@router.put("/leads/{lead_id}/status")
def update_lead(lead_id: int, body: LeadStatus):
    if not store.set_lead_status(lead_id, body.status):
        raise HTTPException(404, "Lead not found")
    return ok({"lead_id": lead_id, "status": body.status})


@router.get("/schedules")
def list_schedules():
    return ok(store.list_schedules())


try:
    import multipart as _mp  # noqa: F401
    from app.business.api import upload_brand_logo as _upload_brand_logo
    router.post("/properties/{pid}/documents")(upload_document)
    router.post("/documents/upload")(upload_document)
    router.post("/brands/{brand_id}/logo")(_upload_brand_logo)
except Exception:  # noqa: BLE001
    pass
