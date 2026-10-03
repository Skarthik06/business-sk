"""FastAPI application — the single backend entrypoint.

Run:  uvicorn app.api:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
# Access logs must never contain secrets: older workers put their token in the URL (?token=…).
import logging as _logging  # noqa: E402
import re as _re_redact  # noqa: E402


class _RedactTokens(_logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and "token=" in str(args[2]):
            a = list(args)
            a[2] = _re_redact.sub(r"token=[^&\s]+", "token=REDACTED", str(a[2]))
            record.args = tuple(a)
        return True


_logging.getLogger("uvicorn.access").addFilter(_RedactTokens())

from fastapi.staticfiles import StaticFiles

from app import db, rags, settings
from app.schemas import (
    AccountIn,
    AccountUpdate,
    GenerateRequest,
    PublishRequest,
    SettingsIn,
)
from app.services import generator, news
from app.services.instagram import InstagramError, publish as ig_publish, publish_story, account_info, list_stories
from app.services.llm import LLMError
from pydantic import BaseModel


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    rags.seed_from_env()
    from app.business import store as business_store
    business_store.init_business_db()
    # Start the engagement auto-sync poller (pulls comments/DMs + auto-replies on a timer).
    from app.engagement.api import start_background_sync
    start_background_sync()
    # GPU Watchdog agent: alerts when Colab needs a tap (app/agents/gpu-watchdog.agents.md)
    from app.services import gpu_watchdog
    gpu_watchdog.start()
    yield


app = FastAPI(title="Instagram Automation", version="4.0.0", lifespan=lifespan)

# Real-estate Business platform (upstream intelligence layer) — separate surface.
from app.business.api import router as business_router  # noqa: E402
from app.business.admin_api import router as admin_router  # noqa: E402
from app.business.api_v1 import router as v1_router  # noqa: E402
from app.engagement.api import router as engagement_router, webhook_router, ensure_affiliate_automation  # noqa: E402
from app.business import auth as _auth  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.exception_handlers import http_exception_handler  # noqa: E402
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402
import time as _time, uuid as _uuid  # noqa: E402

app.include_router(admin_router)
app.include_router(v1_router)
app.include_router(business_router)
app.include_router(engagement_router)
app.include_router(webhook_router)


# /api/v1 returns the standard {success,data,error,meta} envelope on errors too.
@app.exception_handler(_StarletteHTTPException)
async def _v1_exc(request, exc):
    if request.url.path.startswith("/api/v1") and not request.url.path.startswith("/api/v1/admin"):
        return JSONResponse(status_code=exc.status_code, content={
            "success": False, "data": None,
            "error": {"code": _V1_CODES.get(exc.status_code, "ERROR"), "message": exc.detail},
            "meta": {"request_id": _uuid.uuid4().hex[:16], "timestamp": int(_time.time())}})
    return await http_exception_handler(request, exc)


_V1_CODES = {400: "BAD_REQUEST", 401: "AUTH_REQUIRED", 404: "NOT_FOUND",
             413: "TOO_LARGE", 500: "INTERNAL_ERROR"}

# ---- Single-admin gate: every /api/* route requires a valid admin token,
# except health + the public login/refresh endpoints. (/cdn images stay open so
# the browser can load rendered slides.)  ADMIN-ONLY architecture.
_OPEN_PATHS = {"/api/health", "/api/v1/admin/login", "/api/v1/admin/google", "/api/v1/admin/refresh",
               # active affiliate tag is semi-public (it appears in every affiliate URL); the
               # affiliate service reads it internally to build links from the stored account.
               "/api/v1/integrations/affiliate/active-tag"}


@app.middleware("http")
async def admin_gate(request, call_next):
    path = request.url.path
    if path.startswith("/api/sk/"):                # which device the Studio runs on → who renders
        from app.services import scene_store as _ss
        _ss.set_render_device(request.headers.get("x-sk-device", ""))
    # Meta webhooks carry no admin auth — they're verified by challenge + signature.
    if (request.method == "OPTIONS" or not path.startswith("/api/")
            or path in _OPEN_PATHS or path.startswith("/api/webhooks/")
            or path.startswith("/api/gpu/worker/") or path.startswith("/api/gpu/device/")
            or path == "/api/gpu/colab/claim"):
        return await call_next(request)
    if not _auth.verify(_auth.token_from_header(request.headers.get("authorization"))):
        return JSONResponse(status_code=401,
                            content={"success": False, "error": {"code": "UNAUTHORIZED",
                                     "message": "Admin authentication required."}})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve locally-rendered previews (before they are pushed to GitHub on publish).
app.mount("/cdn", StaticFiles(directory=str(settings.IMAGES_DIR)), name="cdn")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "openai_key_set": bool(settings.OPENAI_API_KEY),
        "model": settings.OPENAI_MODEL,
        "niches": list(settings.NICHES),
    }


# ===================== ACCOUNTS (rags) =====================

@app.get("/api/accounts")
def list_accounts(niche: str | None = None, active_only: bool = False):
    return {"accounts": rags.list_accounts(niche=niche, active_only=active_only)}


def _page_token(token: str, ig_business_id: str) -> str:
    """Convert a pasted User token into the connected Page token (messaging/publishing need
    the Page token). No-op if already a page token or resolution fails."""
    try:
        from app.services.instagram import resolve_page_token
        return resolve_page_token(token, ig_business_id)
    except Exception:
        return token


@app.post("/api/accounts")
def create_account(body: AccountIn):
    token = _page_token(body.ig_access_token, body.ig_business_id) if body.ig_access_token else body.ig_access_token
    return rags.add_account(
        label=body.label, handle=body.handle, niche=body.niche,
        ig_business_id=body.ig_business_id, ig_access_token=token,
        is_active=body.is_active,
    )


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate):
    fields = body.model_dump(exclude_none=True)
    # If a new token is pasted, convert it to the Page token before storing.
    if fields.get("ig_access_token"):
        ig_id = fields.get("ig_business_id")
        if not ig_id:
            existing = rags.get_account(account_id) or {}
            ig_id = existing.get("ig_business_id")
        fields["ig_access_token"] = _page_token(fields["ig_access_token"], ig_id)
    updated = rags.update_account(account_id, **fields)
    if not updated:
        raise HTTPException(404, "Account not found")
    return updated


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int):
    if not rags.delete_account(account_id):
        raise HTTPException(404, "Account not found")
    return {"deleted": account_id}


# ===================== SETTINGS (rags) =====================

@app.get("/api/settings")
def get_settings():
    return rags.get_public_settings()


@app.put("/api/settings")
def update_settings(body: SettingsIn):
    for key, value in body.model_dump(exclude_none=True).items():
        rags.set_setting(key, str(value))
    return rags.get_public_settings()


# ===================== GENERATION =====================

@app.post("/api/generate")
async def generate(body: GenerateRequest):
    try:
        return await generator.generate(
            niche=body.niche, posts=body.posts, slides=body.slides, topic=body.topic
        )
    except LLMError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        raise HTTPException(500, str(exc))


@app.get("/api/batch/{batch_id}")
def get_batch(batch_id: str):
    batch = generator.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found or expired")
    return batch


@app.post("/api/publish")
def publish(body: PublishRequest):
    try:
        result = generator.publish(
            batch_id=body.batch_id, post_index=body.post_index, account_id=body.account_id
        )
        return {"success": True, **result}
    except InstagramError as exc:
        print(f"[publish] Instagram error: {exc}")  # visible in the server terminal
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        raise HTTPException(500, str(exc))


# ===================== NEWS PREVIEW (optional helper) =====================

@app.get("/api/news")
def preview_news(topic: str | None = None, limit: int = 8):
    return {"items": news.fetch_news(topic=topic, limit=limit)}


# ===================== BUSINESS-SK (affiliate posting) =====================

class SkCarouselReq(BaseModel):
    account_id: int
    image_urls: list[str]
    caption: str = ""
    category: str = ""
    products: list[dict] = []          # [{asin, product_title, price, affiliate_link, image_url}]
    design: bool = True                # render the Still Set designed slides (vs raw product images)
    arc: str = "auto"                  # carousel story arc: auto | ranking
    theme: str = ""                    # optional collection theme line for the cover
    palette: str = "warm"              # slide palette id (see sk_render.palette_options())
    cover_tags: list[str] = []         # selection tags for the cover (audience/style/deals/price/rating)
    templates: list[str] = []          # per-product-slide template overrides ("" = keep the AI pick)
    art: dict | None = None            # AI Art Director plan (/api/sk/art-direct) → AI-scene slides
    post_key: str = ""                 # the Studio's id for THIS post → server-side posted/posting record


class SkPostKeysReq(BaseModel):
    keys: list[str] = []


class SkRenderReq(BaseModel):
    products: list[dict] = []
    category: str = ""
    arc: str = "auto"
    theme: str = ""
    account_id: int | None = None      # resolve the REAL @handle from this account (preview == post)
    handle: str = ""                   # explicit override; blank ⇒ resolved from the account
    templates: list[str] = []          # per-product-slide template overrides ("" = keep the AI pick)
    palette: str = "warm"              # slide palette: warm | sky
    cover_tags: list[str] = []         # selection tags for the cover
    art: dict | None = None            # AI Art Director plan (/api/sk/art-direct) → AI-scene slides


def _hi_res(url: str) -> str:
    """Strip Amazon's size suffix (`._AC_UL320_`, `._SL500_`, …) to get the full-res image.
    Mirrors the frontend `hiRes()` EXACTLY so a re-hosted slide URL can be mapped back onto
    its product for the DM card."""
    if not url:
        return url
    u = str(url).split("?")[0]
    if "._" not in u:
        return u
    base = u.split("._")[0]
    ext = (u.rsplit(".", 1)[-1] or "jpg").lower()
    return f"{base}.{ext if ext in ('jpg', 'jpeg', 'png', 'webp') else 'jpg'}"


def _rehost_for_ig(image_urls: list[str]) -> list[str]:
    """Download product images and re-host them on the public GitHub repo, returning raw
    URLs — 1:1 ALIGNED with the input (a URL that fails to download/host keeps its original
    slot, so callers can safely zip inputs↔outputs). Instagram's fetcher throttles Amazon's
    CDN (Graph error 2207052 'could not be fetched'), so we serve images from
    raw.githubusercontent.com — the same reliable host the real-estate slides use."""
    import os
    import hashlib
    import requests as _rq
    from app.services import hosting
    urls = list(image_urls or [])
    if not urls or not hosting._github_token():
        return urls                                # no token → try the Amazon URLs directly
    media_dir = os.path.join(str(settings.BASE_DIR), "sk_media")
    os.makedirs(media_dir, exist_ok=True)
    picked: list[tuple[int, str]] = []             # (original index, local path) for successful downloads
    for i, url in enumerate(urls):
        try:
            r = _rq.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            name = hashlib.md5(url.encode()).hexdigest() + ".jpg"
            p = os.path.join(media_dir, name)
            with open(p, "wb") as f:
                f.write(r.content)
            picked.append((i, p))
        except Exception:
            continue
    if not picked:
        return urls
    try:
        raw_urls = hosting.publish_images([p for _, p in picked], "Add Business-SK product images")
    except Exception:
        return urls                                # hosting failed → fall back to originals
    out = list(urls)                               # start from originals; overwrite only what we re-hosted
    for (idx, _), raw in zip(picked, raw_urls):
        if raw:
            out[idx] = raw
    # Wait until GitHub's raw CDN actually SERVES each just-pushed image before handing the
    # URLs to Instagram. IG fetches the image at container-creation time, and a freshly-pushed
    # raw.githubusercontent URL can 404 for a few seconds → Graph 2207052 'could not be fetched'.
    import time
    for (idx, _), _raw in zip(picked, raw_urls):
        url = out[idx]
        for _ in range(20):
            try:
                if _rq.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1.5)
    return out


def _recover_recent_media(account: dict, within_seconds: int = 180, tries: int = 4) -> Optional[dict]:
    """Instagram sometimes publishes a post but returns a rate-limit error (eventual
    consistency). VERIFY whether it actually went live by checking the account's NEWEST
    media; if one appeared within `within_seconds`, return it as a successful publish so the
    caller can still record it + set up the automation. Retries a few times because the media
    list can lag a few seconds after publishing. Returns None only if nothing recent is found
    (then the error was real — the post genuinely did not publish)."""
    from datetime import datetime, timezone
    import time as _t
    import requests as _rq
    from app.services.instagram import GRAPH
    ig_id = account.get("ig_business_id"); token = account.get("ig_access_token")
    if not (ig_id and token):
        return None
    for attempt in range(max(1, tries)):
        try:
            r = _rq.get(f"{GRAPH}/{ig_id}/media",
                        params={"fields": "id,permalink,timestamp", "access_token": token, "limit": "1"},
                        timeout=20)
            items = (r.json() or {}).get("data") or []
            if items and items[0].get("timestamp"):
                m = items[0]
                dt = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if 0 <= age <= within_seconds:
                    return {"ig_media_id": m["id"], "permalink": m.get("permalink"), "media_type": "carousel"}
        except Exception:
            pass
        if attempt < tries - 1:
            _t.sleep(4)                    # give IG's media list a moment to show the new post
    return None


@app.post("/api/sk/carousel")
def sk_carousel(body: SkCarouselReq):
    """Publish one Post-to-IG carousel, recorded server-side under body.post_key (see post_ledger):
    the post leaves the Studio queue even if the phone paused/reloaded the page while it ran, and
    the same post can never be published twice."""
    from app.services import post_ledger, post_timing
    over = post_timing.check_cap(body.account_id)           # strictly SK_MAX_POSTS_PER_DAY (2) a day
    if over:
        raise HTTPException(409, over)
    try:
        post_ledger.begin(body.post_key, body.category, body.account_id)
    except post_ledger.AlreadyPosted as e:
        raise HTTPException(409, "This post is already live on Instagram" if e.rec.get("state") == "posted"
                            else "This post is still being published — wait for it to finish")
    try:
        res = _sk_carousel(body)
    except HTTPException as e:
        post_ledger.fail(body.post_key, str(e.detail))
        raise
    except Exception as e:
        post_ledger.fail(body.post_key, f"{type(e).__name__}: {e}")
        raise
    post_ledger.finish(body.post_key, str(res.get("ig_media_id") or ""), str(res.get("permalink") or ""))
    return res


@app.post("/api/sk/post-status")
def sk_post_status(body: SkPostKeysReq):
    """{post_key: {state: posting|posted|failed, media_id, permalink, recorded}} for the Studio queue."""
    from app.services import post_ledger
    return {"success": True, "posts": post_ledger.status(body.keys)}


@app.get("/api/sk/post-plan")
def sk_post_plan(account_id: int | None = None):
    """Today's 2 best posting times (+ the next 2 days), posts done today and the daily limit."""
    from app.services import post_timing
    return post_timing.plan(account_id)


@app.post("/api/sk/post-ack")
def sk_post_ack(body: SkPostKeysReq):
    """The Studio recorded these live posts (dedup memory + storefront) — don't hand them out again."""
    from app.services import post_ledger
    return {"success": True, "acked": sum(1 for k in body.keys[:50] if post_ledger.ack(k))}


def _sk_carousel(body: SkCarouselReq):
    """Publish an affiliate product carousel via a SELECTED rags account. The
    Business-SK affiliate service generates the content; this posts it using the
    account's server-side encrypted token (never exposed to the browser).

    After publishing, it registers the post for engagement and attaches a POST-SPECIFIC
    comment→DM automation grounded on THIS post's products (a comment auto-replies
    publicly + DMs the actual Amazon affiliate links). Post-specific rules suppress the
    real-estate rules, so Business-SK and Business-JK never clash on the same account."""
    account = rags.get_account(body.account_id, with_secret=True)
    if not account:
        raise HTTPException(404, "Account not found")
    # Deals (coupon posts) carry products but NO raw product images — they render their designed
    # slides from the products below. So only reject when there are neither raw images NOR products
    # to render into designed slides.
    if not body.image_urls and not (body.design and body.products):
        raise HTTPException(400, "No images or products to post")
    slides = [_hi_res(u) for u in body.image_urls[:10]]
    images = _rehost_for_ig(slides)                    # serve via GitHub raw (IG can't fetch Amazon reliably)
    # Map each re-hosted slide back onto its product so the comment→DM CARDS use the SAME
    # IG-fetchable GitHub image as the carousel — not the Amazon CDN URL (which IG often
    # can't fetch → "Couldn't load image" on a card). Products whose image wasn't a slide
    # get re-hosted standalone so every card has a working image.
    url_map = {orig: new for orig, new in zip(slides, images) if new and new != orig}
    products = []
    leftover: list[tuple[dict, str]] = []
    for p in (body.products or []):
        q = dict(p)
        q["_art_src"] = (q.get("image_url") or q.get("image") or "").strip()
        hi = _hi_res(q.get("image_url") or q.get("image") or "")
        if hi and hi in url_map:
            q["image_url"] = url_map[hi]
        elif hi:
            leftover.append((q, hi))                   # not among the slides → re-host below
        products.append(q)
    if leftover:
        rehosted = _rehost_for_ig([hi for _, hi in leftover])
        for (q, _), new in zip(leftover, rehosted):
            if new:
                q["image_url"] = new
    # THE STILL SET — render designed slides from the products (product-true images on a
    # branded editorial stage) and post THOSE instead of raw product photos. Falls back to
    # the raw re-hosted images if rendering is unavailable, so a post never fails over design.
    design_meta = None
    if body.design and products:
        try:
            designed = _render_sk_slides(products, category=body.category, arc=body.arc,
                                         theme=body.theme, handle=_at(account.get("handle")),
                                         palette=(getattr(body, "palette", None) or "warm"),
                                         cover_tags=getattr(body, "cover_tags", None) or [],
                                         templates=getattr(body, "templates", None) or [],
                                         art=_ensure_art(getattr(body, "art", None), products, body.category))
            if designed.get("images"):
                images = designed["images"]        # GitHub-raw URLs of the rendered PNGs
                design_meta = {"rendered": True, "count": designed["count"], "plan": designed.get("plan")}
        except Exception as e:                     # never fail a good post over design
            design_meta = {"rendered": False, "error": str(e)}
    try:
        result = ig_publish(account, images, body.caption)
    except InstagramError as e:
        # Instagram QUIRK: under the anti-spam / app rate limit, media_publish can return an
        # error while the carousel STILL goes live (eventual consistency). If so, the post is
        # published but we'd otherwise 400 and skip recording + the comment→DM automation.
        # Recover by checking the account's newest media — if one appeared in the last ~2 min,
        # treat the publish as succeeded so recording + automation still run.
        recovered = _recover_recent_media(account)
        if recovered:
            result = {**recovered, "recovered_after_error": str(e)}
            design_meta = {**(design_meta or {}), "publish_recovered": True, "publish_error": str(e)}
        else:
            raise HTTPException(400, str(e))
    automation = None
    media_id = result.get("ig_media_id")
    if media_id:
        try:
            automation = ensure_affiliate_automation(
                body.account_id, media_id, category=body.category,
                caption=body.caption, permalink=result.get("permalink"),
                products=products)
        except Exception as e:                     # never fail a good post over automation setup
            automation = {"error": str(e)}
    return {"success": True, **result, "automation": automation, "design": design_meta}


def _render_sk_slides(products: list[dict], *, category: str, arc: str, theme: str,
                      handle: str, palette: str = "warm", cover_tags: list[str] | None = None,
                      templates: list[str] | None = None, track_cover: bool = True,
                      art: dict | None = None) -> dict:
    """Render Still Set slides for these products, publish the PNGs to GitHub raw (IG-fetchable),
    and return the raw URLs + plan. Used by /api/sk/carousel (design=True) and the preview."""
    import hashlib
    import time
    from app.services import sk_render, hosting
    slug = hashlib.md5((category + palette + str([p.get("asin") or p.get("product_title") for p in products])).encode()).hexdigest()[:8]
    out_dir = settings.IMAGES_DIR / "sk_slides"
    # ONE render per post: publish the EXACT slides the preview showed (no second render that
    # could come out different). Falls back to rendering when there was no matching preview.
    snap = _preview_snapshot(art, handle, templates, cover_tags, palette)
    if snap:
        res = {"rendered": True, "local": snap["local"], "images": snap["cdn"], "count": len(snap["local"]),
               "plan": snap.get("plan")}
        if track_cover and snap.get("cover_title"):
            sk_render._remember_cover(out_dir, snap["cover_title"])
    else:
        res = sk_render.render_carousel(products, category=category, out_dir=out_dir,
                                        cdn_prefix="/cdn/sk_slides", slug=slug, arc=arc,
                                        theme=theme, handle=handle, palette=palette,
                                        cover_tags=cover_tags or [], templates=templates or [],
                                        track_cover=track_cover, art=art)
    if not res.get("rendered") or not res.get("local"):
        return {"images": [], "count": 0, "plan": res.get("plan"), "error": res.get("error")}
    # push the rendered PNGs to GitHub raw so Instagram can fetch them, then wait for the CDN
    raw_urls: list[str] = []
    if hosting._github_token():
        try:
            raw_urls = hosting.publish_images(res["local"], "Add Business-SK designed slides")
        except Exception:
            raw_urls = []
    if raw_urls:
        import requests as _rq
        for u in raw_urls:
            for _ in range(20):
                try:
                    if _rq.get(u, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(1.5)
        return {"images": raw_urls, "count": len(raw_urls), "plan": res.get("plan"),
                "isolated": res.get("isolated")}
    # no GitHub token → serve locally (fine for preview; IG needs a public URL to post)
    return {"images": res["images"], "count": res["count"], "plan": res.get("plan"),
            "isolated": res.get("isolated"), "local_only": True}


@app.get("/api/sk/render-options")
def sk_render_options():
    """Palettes + per-slide templates for the Content Studio pickers (with human details)."""
    from app.services import sk_render
    return {"success": True,
            "palettes": sk_render.palette_options(),
            "templates": sk_render.template_options()}


def _at(handle: str | None) -> str:
    """Always render the account handle with a leading @ (the CTA slide reads 'Follow @x')."""
    h = (handle or "").strip()
    if not h:
        return "@lostinframes0605.exe"
    return h if h.startswith("@") else "@" + h


def _preview_handle(body) -> str:
    """The @handle the CTA slide should show in a PREVIEW. The preview is advertised as
    "exactly what posts", so it must use the SAME handle the real post uses (the connected
    account) — not a hardcoded default. Order: explicit override → named account → the first
    active account → first account → generic fallback."""
    h = (getattr(body, "handle", "") or "").strip()
    if h:
        return h
    try:
        acct = rags.get_account(body.account_id) if getattr(body, "account_id", None) else None
        if not acct:
            accts = rags.list_accounts(active_only=True) or rags.list_accounts()
            acct = accts[0] if accts else None
        if acct and (acct.get("handle") or "").strip():
            return _at(acct["handle"])
    except Exception:
        pass
    return "@lostinframes0605.exe"


def _render_sig(art: dict | None, handle: str, templates, cover_tags, palette: str) -> str:
    """What makes two renders of the same post identical (AI-scene posts ignore cover chips and
    the palette picker — the Art Director's plan decides those)."""
    import hashlib
    import json
    a = art or {}
    styled = ""
    if a.get("id"):                                # ✨ AI-styled flat-lays change the slides → a new render
        try:
            from app.services import ai_stylist
            import os as _os
            styled = "|".join(sorted(f"{k}={v}@{int(_os.path.getmtime(v))}"      # re-processed image → new render
                                     for k, v in ai_stylist.styled_for(str(a["id"])).items()))
        except Exception:
            styled = ""
    key = [str(a.get("id") or ""), (handle or "").lstrip("@").lower(), [t or "" for t in (templates or [])],
           [] if a else list(cover_tags or []), "" if a.get("palette") else (palette or "")]
    if styled:
        key.append(styled + "#tpl2")             # bump when the styled slide/cover templates change
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:10]


def _save_preview_snapshot(art: dict | None, handle: str, templates, cover_tags, palette: str, res: dict) -> None:
    """Keep a copy of what the preview showed, so publishing posts exactly those slides."""
    import shutil
    import time
    from app.services import scene_store
    pid = str((art or {}).get("id") or "")
    if not pid or not res.get("rendered") or not res.get("local"):
        return
    sig = _render_sig(art, handle, templates, cover_tags, palette)
    d = settings.IMAGES_DIR / "sk_slides" / f"plan_{pid}_{sig}"
    try:
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        local, cdn = [], []
        for i, src in enumerate(res["local"], 1):
            dst = d / f"slide_{i:02d}.png"
            shutil.copyfile(src, dst)
            local.append(str(dst))
            cdn.append(f"/cdn/sk_slides/{d.name}/{dst.name}")
        cur = (scene_store.plan_get(pid) or {}).get("renders") or {}
        cur[sig] = {"local": local, "cdn": cdn, "plan": res.get("plan"),
                    "cover_title": res.get("cover_title", ""), "t": time.time()}
        scene_store.plan_update(pid, renders=cur)
    except Exception:
        pass


def _art_summary(art: dict | None, snap: dict | None = None) -> dict | None:
    """The '✨ AI direction' panel data for a post that was already rendered."""
    from app.services import scene_store
    pid = str((art or {}).get("id") or "")
    return (scene_store.plan_get(pid) or {}).get("art_summary") if pid else None


def _preview_snapshot(art: dict | None, handle: str, templates, cover_tags, palette: str) -> dict | None:
    import os
    from app.services import scene_store
    pid = str((art or {}).get("id") or "")
    if not pid:
        return None
    snap = ((scene_store.plan_get(pid) or {}).get("renders") or {}).get(
        _render_sig(art, handle, templates, cover_tags, palette))
    if snap and snap.get("local") and all(os.path.exists(p) for p in snap["local"]):
        return snap
    return None


# ---- AI Art Director + laptop GPU worker (agent: post-art-director) ----------------------
def _ensure_art(art: dict | None, products: list[dict], category: str) -> dict | None:
    """AI-scene design is THE format for product posts: when the client sent no plan, the server
    runs the Art Director itself. Coupon/deal posts (no product photos) keep their deal cards."""
    if art or not products or all(p.get("deal") for p in products):
        return art
    try:
        from app.services import art_director
        return art_director.direct(products, category)
    except Exception:
        return None

class ArtDirectReq(BaseModel):
    products: list[dict] = []
    category: str = ""
    look: str = ""                     # ai (default) | premium | a palette | <scene key>
    styles: str = ""                   # slash-command presets, e.g. "/premium /cinematic"
    fresh: bool = False                # explicit re-roll → a NEW plan (+ one new paint) for this post


@app.post("/api/sk/art-direct")
def sk_art_direct(body: ArtDirectReq):
    """The Art Director looks at the products (photos + details) and plans the post's scene +
    per-slide layouts. Queues the GPU work (cut-outs, any new scene) on the laptop worker.
    Pass the returned plan as `art` to /api/sk/render-preview and /api/sk/carousel."""
    from app.services import art_director
    if not body.products:
        raise HTTPException(400, "No products")
    return {"success": True, "art": art_director.direct(body.products, body.category, body.look, body.styles,
                                                        fresh=body.fresh)}


@app.get("/api/sk/scenes")
def sk_scenes():
    """Backdrop library (with small thumbnails) + GPU worker/queue status for the Studio panel."""
    from app.services import scene_store
    from app.services import art_director as _ad
    from app.services import gpu_watchdog
    return {"success": True, "status": scene_store.status(), "alerts": gpu_watchdog.alerts(),
            "presets": [{"id": k, **v} for k, v in _ad.presets().items()],
            "library": [dict(s, thumb=scene_store.thumb(s["key"])) for s in scene_store.library()]}


def _scraper_call(method: str, path: str, timeout: int = 10) -> dict:
    """Call the internal Scraper API with its key (admin-gated here). Never leaks the key or URL."""
    import json as _json
    import os as _os
    import urllib.error as _ue
    import urllib.request as _ur
    base = (_os.getenv("SCRAPER_API_URL") or "").strip().rstrip("/")
    key = (_os.getenv("SCRAPER_API_KEY") or "").strip()
    if not base or not key:
        return {"success": False, "error": "not configured"}
    try:
        req = _ur.Request(base + path, method=method, headers={"X-API-Key": key})
        with _ur.urlopen(req, timeout=timeout) as r:
            return {"success": True, **_json.loads(r.read().decode("utf-8") or "{}")}
    except _ue.HTTPError as e:
        try:
            detail = _json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:
            detail = None
        return {"success": False, "error": detail or f"HTTP {e.code}"}
    except Exception as e:
        return {"success": False, "error": f"unreachable ({type(e).__name__})"}


@app.get("/api/sk/scraper-dashboard")
def sk_scraper_dashboard():
    """Scraper API ops view for the Studio (admin-gated here; the Scraper API itself is internal)."""
    return _scraper_call("GET", "/v1/dashboard")


@app.post("/api/sk/scraper/pairing")
def sk_scraper_pairing():
    """One-time code + QR to pair a phone running the Business-SK Helper app (valid 10 min)."""
    return _scraper_call("POST", "/v1/admin/pairing")


@app.get("/api/sk/scraper/devices")
def sk_scraper_devices():
    return _scraper_call("GET", "/v1/admin/devices")


@app.delete("/api/sk/scraper/devices/{device_id}")
def sk_scraper_revoke_device(device_id: int):
    return _scraper_call("DELETE", f"/v1/admin/devices/{int(device_id)}")


@app.delete("/api/sk/scraper/workers/{worker_id}")
def sk_scraper_remove_worker(worker_id: str):
    """Remove a worker (e.g. an old phone after re-pairing) — a paired phone is also revoked."""
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", worker_id or ""):
        return {"success": False, "error": "bad worker id"}
    return _scraper_call("DELETE", f"/v1/admin/workers/{worker_id}")


class GpuResultReq(BaseModel):
    model_config = {"extra": "forbid"}
    token: str
    job_id: str
    b64: str
    meta: dict | None = None


@app.get("/api/gpu/worker/jobs")
def gpu_worker_jobs(token: str = "", n: int = 4, gpu: str = "", ver: str = "", hb: int = 0, kind: str = "",
                    x_worker_token: str = Header("", alias="X-Worker-Token")):
    """GPU worker poll — the laptop, or a Google Colab session (token-authenticated; bypasses the
    admin gate). hb=1 = heartbeat only."""
    from app.services import scene_store
    if not scene_store.worker_token_ok(x_worker_token or token):     # header (new) or ?token= (old)
        raise HTTPException(401, "bad worker token")
    info = {"gpu": gpu, "ver": ver, "kind": "colab" if kind == "colab" else "laptop"}
    return {"jobs": scene_store.take_jobs(n, info, heartbeat=bool(hb))}


COLAB_NOTEBOOK = "https://colab.research.google.com/github/Skarthik06/business-sk/blob/main/colab/sk_gpu_worker.ipynb"


@app.post("/api/sk/gpu/colab")
def sk_gpu_colab(force: bool = False):
    """"Render with Colab": if the laptop GPU is online nothing is needed (unless force=1, e.g. the
    Overview's "Get Colab code"); otherwise a one-time session code for the Colab notebook (you open
    it and press Run — Colab can't be started for you)."""
    from app.services import scene_store
    # on the laptop with its GPU on → nothing to do; on the phone (or laptop GPU off) → a Colab code
    if not force and scene_store.render_target() != "colab" and scene_store.laptop_online():
        return {"success": True, "laptop_online": True}
    return {"success": True, "laptop_online": scene_store.laptop_online(), "colab_online": scene_store.colab_online(),
            "notebook": COLAB_NOTEBOOK, **scene_store.new_colab_code()}


@app.post("/api/sk/gpu/colab-key")
def sk_gpu_colab_key(days: int = 90):
    """A long-lived Colab key, shown ONCE: save it in Colab → Secrets as SK_COLAB_KEY and the notebook
    connects with no code (Open → Run all). Replaces any previous key."""
    from app.services import scene_store
    return {"success": True, **scene_store.create_colab_key(days), "secret_name": "SK_COLAB_KEY"}


@app.delete("/api/sk/gpu/colab-key")
def sk_gpu_colab_key_revoke():
    from app.services import scene_store
    return {"success": True, "revoked": scene_store.revoke_colab_keys()}


class ColabClaimReq(BaseModel):
    code: str


@app.post("/api/gpu/colab/claim")
def gpu_colab_claim(body: ColabClaimReq):
    """PUBLIC: the Colab notebook swaps its one-time code for a temporary GPU worker token."""
    from app.services import scene_store
    tok = scene_store.claim_colab_code(body.code)
    if not tok:
        raise HTTPException(400, "code is wrong or expired — press Render with Colab again in the Studio")
    return {"ok": True, "token": tok, "hours": 12}


@app.post("/api/gpu/worker/result")
def gpu_worker_result(body: GpuResultReq):
    from app.services import scene_store
    if not scene_store.worker_token_ok(body.token):
        raise HTTPException(401, "bad worker token")
    res = scene_store.submit(body.job_id, body.b64, body.meta)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "rejected"))
    return res


# ── phones as cut-out workers (SK Helper + ML Kit), authenticated with the phone's OWN token ──
_DEVICE_TOKENS: dict = {}                      # sha256(token) → (worker_id | None, expires)


def _device_worker(token: str) -> str | None:
    """The paired phone this token belongs to (asked from the Scraper API, cached 60 s)."""
    import hashlib as _hl
    import json as _json
    import os as _os
    import time as _time
    import urllib.request as _ur
    token = (token or "").strip()
    if not token.startswith("skw_") or len(token) > 200:
        return None
    k = _hl.sha256(token.encode()).hexdigest()
    hit = _DEVICE_TOKENS.get(k)
    if hit and hit[1] > _time.time():
        return hit[0]
    wid = None
    base = (_os.getenv("SCRAPER_API_URL") or "").strip().rstrip("/")
    if base:
        try:
            req = _ur.Request(base + "/v1/workers/whoami", data=b"{}", method="POST",
                              headers={"X-Worker-Token": token, "Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=5) as r:
                d = _json.loads(r.read().decode("utf-8") or "{}")
            wid = d.get("worker_id") if d.get("kind") == "device" else None
        except Exception:
            wid = None
    _DEVICE_TOKENS[k] = (wid, _time.time() + (60 if wid else 10))
    return wid


class DeviceResultReq(BaseModel):
    job_id: str
    b64: str = ""
    meta: dict | None = None
    error: str | None = None                   # the phone couldn't do it → released for another GPU


@app.get("/api/gpu/device/jobs")
def gpu_device_jobs(n: int = 2, ver: str = "", x_worker_token: str = Header("", alias="X-Worker-Token")):
    """A paired phone asks for cut-out jobs (given only while the laptop GPU is offline)."""
    from app.services import scene_store
    wid = _device_worker(x_worker_token)
    if not wid:
        raise HTTPException(401, "bad device token")
    from app.services import gpu_watchdog
    jobs = scene_store.take_cutout_jobs(n, {"worker": wid, "ver": ver}) if n > 0 else []
    # the laptop does laptop-started work → the phone polls less; phone-started work → poll fast
    return {"jobs": jobs, "laptop_gpu": scene_store.laptop_online() and scene_store.render_target() != "colab",
            "alerts": gpu_watchdog.alerts(actionable_only=True),          # SK Helper turns these into notifications
            "post_plan": _post_plan_safe()}                                # → the phone schedules the post reminders


def _post_plan_safe() -> dict | None:
    try:
        from app.services import post_timing
        return post_timing.plan()
    except Exception as e:                                                 # noqa: BLE001 — never break the poll
        print(f"[post-timing] plan failed: {e}", flush=True)
        return None


@app.get("/api/gpu/device/post-plan")
def gpu_device_post_plan(x_worker_token: str = Header("", alias="X-Worker-Token")):
    """The phone re-checks right before a reminder (did you already post?) — no jobs, no side effects."""
    if not _device_worker(x_worker_token):
        raise HTTPException(401, "bad device token")
    return _post_plan_safe() or {"success": False}


@app.post("/api/gpu/device/result")
def gpu_device_result(body: DeviceResultReq, x_worker_token: str = Header("", alias="X-Worker-Token")):
    from app.services import scene_store
    wid = _device_worker(x_worker_token)
    if not wid:
        raise HTTPException(401, "bad device token")
    if not str(body.job_id).startswith("cut_"):
        raise HTTPException(400, "phones only do cut-outs")
    if body.error:
        print(f"[phone cut-out] {wid} could not do {body.job_id}: {body.error[:200]}", flush=True)
        return {"ok": scene_store.release_job(body.job_id, f"{wid}: {body.error}")}
    res = scene_store.submit(body.job_id, body.b64, {**(body.meta or {}), "by": wid})
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "rejected"))
    return res


# ── AI Stylist (app/agents/ai-stylist.agents.md): styled flat-lays, only when YOU press the button ──
class StylistReq(BaseModel):
    products: list
    art_id: str = ""
    dark: bool = True


@app.get("/api/sk/stylist/budget")
def sk_stylist_budget():
    from app.services import ai_stylist
    return {"success": True, **ai_stylist.budget()}


@app.post("/api/sk/stylist/estimate")
def sk_stylist_estimate(body: StylistReq):
    """What pressing "Style with AI" would cost for this post (nothing is spent)."""
    from app.services import ai_stylist
    surface = ai_stylist.pick_surface(body.art_id, body.dark)
    return {"success": True, "surface": surface, **ai_stylist.estimate(body.products[:10], surface)}


@app.post("/api/sk/stylist/style")
def sk_stylist_style(body: StylistReq):
    """Style every product of a post (money guards checked before each paid image)."""
    from app.services import ai_stylist
    if not body.art_id:
        raise HTTPException(400, "Preview the post first (the styled slides belong to its design plan)")
    return {"success": True, **ai_stylist.style_post(body.products[:10], body.art_id, body.dark)}


@app.post("/api/sk/render-preview")
def sk_render_preview(body: SkRenderReq):
    """Render the Still Set slides for a set of products and return preview URLs WITHOUT
    posting. Serves the PNGs from the local /cdn mount so you can see the design first."""
    if not body.products:
        raise HTTPException(400, "No products to render")
    import hashlib
    from app.services import sk_render
    slug = hashlib.md5(str([p.get("asin") or p.get("product_title") for p in body.products]).encode()).hexdigest()[:8]
    out_dir = settings.IMAGES_DIR / "sk_slides"
    art = _ensure_art(getattr(body, "art", None), body.products, body.category)
    handle = _preview_handle(body)
    palette = getattr(body, "palette", None) or "warm"
    cover_tags = getattr(body, "cover_tags", None) or []
    templates = getattr(body, "templates", None) or []
    snap = _preview_snapshot(art, handle, templates, cover_tags, palette)
    if snap:                                 # this post was already rendered → show THAT render
        return {"success": True, "images": snap["cdn"], "count": len(snap["cdn"]), "plan": snap.get("plan"),
                "art": _art_summary(art, snap), "cost": _post_cost(body.products, art)}
    res = sk_render.render_carousel(body.products, category=body.category, out_dir=out_dir,
                                    cdn_prefix="/cdn/sk_slides", slug=slug, arc=body.arc,
                                    theme=body.theme, handle=handle, palette=palette,
                                    cover_tags=cover_tags, templates=templates,
                                    track_cover=False,   # preview: don't consume the cover-uniqueness history
                                    art=art)
    if not res.get("rendered"):
        raise HTTPException(500, f"Render failed: {res.get('error')}")
    _save_preview_snapshot(art, handle, templates, cover_tags, palette, res)
    if res.get("art"):
        from app.services import scene_store as _ss
        if art and art.get("id"):
            _ss.plan_update(art["id"], art_summary=res["art"])
    snap = _preview_snapshot(art, handle, templates, cover_tags, palette)
    if snap:                                 # every view of this post points at the SAME files
        res["images"] = snap["cdn"]
    return {"success": True, "images": res["images"], "count": res["count"],
            "plan": res.get("plan"), "isolated": res.get("isolated"), "art": res.get("art"),
            "cost": _post_cost(body.products, res.get("art"))}


def _post_cost(products: list[dict], art: dict | None) -> dict:
    """Every LLM token this post cost, priced: the caption writer (the product generator's ONE
    compose call, shared by all products) + the Art Director. USD + INR (knobs in art_director)."""
    from app.services import art_director as _ad
    lines = []
    ct = next((p.get("content_tokens") for p in (products or []) if isinstance(p.get("content_tokens"), dict)), None)
    if ct and (ct.get("input") or ct.get("output")):
        usd = _ad.price_usd(int(ct.get("input") or 0), 0, int(ct.get("output") or 0))
        lines.append({"step": "Caption writer", "input": int(ct.get("input") or 0), "cached": 0,
                      "output": int(ct.get("output") or 0), "reasoning": None, "usd": round(usd, 6)})
    u = (art or {}).get("usage") or {}
    if u.get("total"):
        lines.append({"step": "Art Director", "input": u.get("input"), "cached": u.get("cached"),
                      "output": u.get("output"), "reasoning": u.get("reasoning"), "usd": u.get("usd")})
    import os as _o
    rate = float(_o.getenv("USD_INR", "88"))
    total = round(sum(float(x["usd"] or 0) for x in lines), 6)
    for x in lines:
        x["inr"] = round(float(x["usd"] or 0) * rate, 4)
    return {"model": (u.get("model") or ""), "lines": lines,
            "tokens": sum(int(x["input"] or 0) + int(x["output"] or 0) for x in lines),
            "usd": total, "inr": round(total * rate, 4)}


class SkStoryReq(BaseModel):
    account_id: int
    media_url: str
    is_video: bool = False


@app.post("/api/sk/story")
def sk_story(body: SkStoryReq):
    """Publish a single-image (or video) STORY via a selected rags account. Instagram-catalog
    music cannot be attached through the API — only audio baked into an uploaded video."""
    account = rags.get_account(body.account_id, with_secret=True)
    if not account:
        raise HTTPException(404, "Account not found")
    if not body.media_url:
        raise HTTPException(400, "No media URL")
    media = body.media_url if body.is_video else (_rehost_for_ig([body.media_url]) or [body.media_url])[0]
    try:
        return {"success": True, **publish_story(account, media, body.is_video)}
    except InstagramError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sk/account")
def sk_account(account_id: int):
    """Right-side account panel data: profile metrics (followers/following/posts, avatar, bio)
    + active Stories. Highlights are intentionally absent — the Graph API has no Highlights
    edge, so we never fabricate them."""
    account = rags.get_account(account_id, with_secret=True)
    if not account:
        raise HTTPException(404, "Account not found")
    info = account_info(account)
    stories = list_stories(account)
    return {
        "account_id": account_id,
        "label": account.get("label"),
        "handle": account.get("handle"),
        "info": info,                              # {} if the token can't read profile fields
        "stories": stories,                        # active stories (last 24h)
        "story_count": len(stories),
        "highlights_supported": False,             # honest: no API for Highlights
    }


# ---- public Storefront hosting (GitHub Pages) -----------------------------
import os as _os

SK_API_TARGET = _os.getenv("SK_API_TARGET", "http://affiliate_backend:8100")


def _storefront_public_url() -> str | None:
    """The public URL the storefront lives at — the Vercel-hosted store (trusted vercel.app domain,
    edge-cached, always live). Override with the STOREFRONT_URL env var if the domain changes."""
    import os
    return (os.getenv("STOREFRONT_URL", "https://lostinframes-sk-store.vercel.app").strip() or None)


@app.get("/api/sk/storefront/url")
def sk_storefront_url():
    """Where the public storefront lives (without republishing) + whether the repo is reachable."""
    from app.services import hosting
    return {"url": _storefront_public_url(), "repo": hosting.check_repo_access()}


@app.post("/api/sk/storefront/publish")
def sk_storefront_publish():
    """Render the live product hub and publish it to GitHub Pages as a single public page —
    the 'all my products in one Amazon-tagged link' bio surface. Auto-updates every time
    it's called (e.g. after a posting run). Reuses the same public repo + token as the
    real-estate image hosting; only this one file is staged, never secrets."""
    import requests
    from app.services import hosting
    try:
        r = requests.get(f"{SK_API_TARGET}/hub", timeout=30)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"Could not render storefront: {e}")
    if not hosting._github_token():
        raise HTTPException(400, "No GitHub token configured — set it in the Settings panel to publish.")
    path = _os.path.join(str(settings.BASE_DIR), "storefront", "index.html")
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(r.text)
    try:
        hosting._upload_via_api([path], "Update Business-SK storefront")
    except Exception as e:
        raise HTTPException(502, f"Publish to GitHub failed: {e}")
    return {"success": True, "url": _storefront_public_url(),
            "note": "GitHub Pages may take ~1 minute to reflect the newest version."}


# ===================== HISTORY / STATS =====================

@app.get("/api/posts")
def posts(limit: int = 50, niche: str | None = None):
    return {"posts": db.get_published_posts(limit=limit, niche=niche)}


@app.get("/api/stats")
def stats():
    all_posts = db.get_published_posts(limit=1000)
    by_niche = {"quotes": 0, "news": 0}
    for p in all_posts:
        if p["niche"] in by_niche:
            by_niche[p["niche"]] += 1
    return {
        "total_posts": len(all_posts),
        "by_niche": by_niche,
        "accounts": len(rags.list_accounts()),
        "recent": all_posts[:6],
    }


if __name__ == "__main__":
    import uvicorn

    # Bind to localhost only — the API is for this machine, not the network.
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=True)
