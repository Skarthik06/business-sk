"""FastAPI application — the single backend entrypoint.

Run:  uvicorn app.api:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
_OPEN_PATHS = {"/api/health", "/api/v1/admin/login", "/api/v1/admin/google", "/api/v1/admin/refresh"}


@app.middleware("http")
async def admin_gate(request, call_next):
    path = request.url.path
    # Meta webhooks carry no admin auth — they're verified by challenge + signature.
    if (request.method == "OPTIONS" or not path.startswith("/api/")
            or path in _OPEN_PATHS or path.startswith("/api/webhooks/")):
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


class SkRenderReq(BaseModel):
    products: list[dict] = []
    category: str = ""
    arc: str = "auto"
    theme: str = ""
    handle: str = "@business.sk"


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


@app.post("/api/sk/carousel")
def sk_carousel(body: SkCarouselReq):
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
    if not body.image_urls:
        raise HTTPException(400, "No images to post")
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
                                         theme=body.theme, handle=(account.get("handle") or "@business.sk"))
            if designed.get("images"):
                images = designed["images"]        # GitHub-raw URLs of the rendered PNGs
                design_meta = {"rendered": True, "count": designed["count"], "plan": designed.get("plan")}
        except Exception as e:                     # never fail a good post over design
            design_meta = {"rendered": False, "error": str(e)}
    try:
        result = ig_publish(account, images, body.caption)
    except InstagramError as e:
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
                      handle: str) -> dict:
    """Render Still Set slides for these products, publish the PNGs to GitHub raw (IG-fetchable),
    and return the raw URLs + plan. Used by /api/sk/carousel (design=True) and the preview."""
    import hashlib
    import time
    from app.services import sk_render, hosting
    slug = hashlib.md5((category + str([p.get("asin") or p.get("product_title") for p in products])).encode()).hexdigest()[:8]
    out_dir = settings.IMAGES_DIR / "sk_slides"
    res = sk_render.render_carousel(products, category=category, out_dir=out_dir,
                                    cdn_prefix="/cdn/sk_slides", slug=slug, arc=arc,
                                    theme=theme, handle=handle)
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
    res = sk_render.render_carousel(body.products, category=body.category, out_dir=out_dir,
                                    cdn_prefix="/cdn/sk_slides", slug=slug, arc=body.arc,
                                    theme=body.theme, handle=body.handle)
    if not res.get("rendered"):
        raise HTTPException(500, f"Render failed: {res.get('error')}")
    return {"success": True, "images": res["images"], "count": res["count"],
            "plan": res.get("plan"), "isolated": res.get("isolated")}


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
    """The GitHub Pages URL the storefront is served at (owner is lowercased by Pages)."""
    try:
        from app.services import hosting
        user, repo, _ = hosting._git_cfg()
        if not user or not repo:
            return None
        return f"https://{user.lower()}.github.io/{repo}/storefront/"
    except Exception:
        return None


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
