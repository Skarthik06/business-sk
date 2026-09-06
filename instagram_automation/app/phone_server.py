"""Business-SK PHONE server — Option A (smart split), the light always-on half.

This is the 24/7 engagement half that runs on the OPPO F17 Pro (Termux / proot Ubuntu):
    Meta webhook  →  comment/DM event  →  deterministic rules  →  reply + product-card DM
It mounts ONLY the engagement + webhook routers, so it NEVER imports the heavy
content-generation dependencies (torch, transformers, playwright, chromium, rembg,
onnxruntime, docling) that stay on the PC. The application logic is UNCHANGED — this is
purely a slim runtime entrypoint that reuses app/engagement/* (no redesign).

Run:
    uvicorn app.phone_server:app --host 0.0.0.0 --port 8000

Env (see .env.phone.example):
    DATABASE_URL                 local Postgres on the phone (instagram_business)
    ENGAGEMENT_LIVE=1            actually send replies/DMs (0 = dry-run)
    META_WEBHOOK_VERIFY_TOKEN    the token you set in the Meta webhook config
    META_APP_SECRET              Meta app secret (verifies webhook signatures)
    ENGAGEMENT_AUTO_SYNC=0       webhooks are primary; 1 + a large SYNC_INTERVAL adds a
                                 light fallback poll (e.g. 300s) if you want a safety net
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

# Engagement + webhook routers (light imports only: rags, settings, rules, service, store, openai).
from app.engagement.api import router as engagement_router
from app.engagement.api import webhook_router, start_background_sync


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Webhooks (push) are the primary, rate-limit-free path. start_background_sync() is a
    # no-op unless ENGAGEMENT_AUTO_SYNC=1 — set a large ENGAGEMENT_SYNC_INTERVAL (e.g. 300)
    # if you want an occasional fallback poll in addition to webhooks.
    start_background_sync()
    yield


app = FastAPI(title="Business-SK Engagement (phone)", lifespan=lifespan)

# CORS: let the hosted dashboard (GitHub Pages / Cloudflare Pages) call this API from the
# browser. ENGAGEMENT_CORS_ORIGINS is a comma-separated allowlist; default "*" is fine because
# the API is read-mostly and the admin endpoints still require the admin secret.
_origins = [o.strip() for o in os.getenv("ENGAGEMENT_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Auth gate for the engagement/monitoring API -------------------------------
# The engagement endpoints expose leads/conversations/DM text AND have write actions
# (send DM, edit automations). On the public phone URL they MUST require a secret key.
# FAIL-CLOSED: if DASHBOARD_KEY isn't set, every engagement request is rejected. The
# Meta webhook is NOT gated here (it's authenticated by Meta's signature instead), so
# comment->DM keeps working regardless. The dashboard sends the key as X-Dashboard-Key.
_DASH_KEY = os.getenv("DASHBOARD_KEY", "").strip()


def require_key(x_dashboard_key: str = Header(default=""),
                authorization: str = Header(default="")) -> None:
    supplied = (x_dashboard_key or authorization.replace("Bearer ", "")).strip()
    if not _DASH_KEY or supplied != _DASH_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


app.include_router(engagement_router, dependencies=[Depends(require_key)])
app.include_router(webhook_router)  # Meta-signed; not key-gated


@app.get("/healthz")
def healthz():
    """Cheap liveness probe (no DB hit) for uptime checks + the boot script."""
    return {
        "status": "ok",
        "role": "phone-engagement",
        "live": os.getenv("ENGAGEMENT_LIVE", "0") not in ("0", "false", ""),
        "auto_sync": os.getenv("ENGAGEMENT_AUTO_SYNC", "1") not in ("0", "false", ""),
    }


@app.get("/")
def root():
    """Friendly root so a bare visit / uptime ping isn't a 404 (was noisy in the logs)."""
    return {"service": "business-sk-engagement", "ok": True, "health": "/healthz", "docs": "/docs"}


@app.get("/favicon.ico")
def favicon():
    """Browsers auto-request this; answer 204 so it stops logging 404s."""
    return Response(status_code=204)
