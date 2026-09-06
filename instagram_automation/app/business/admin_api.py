"""Single-admin auth + audit endpoints (/api/v1/admin/*).

One administrator. No registration, no roles, no multi-tenancy. login/refresh are
public; everything else is gated by the admin middleware in app/api.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import settings
from app.business import auth, store

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class LoginReq(BaseModel):
    username: str
    password: str


class RefreshReq(BaseModel):
    refresh_token: str


class GoogleReq(BaseModel):
    credential: str


@router.post("/login")
def admin_login(body: LoginReq):
    tokens = auth.login(body.username, body.password)
    if not tokens:
        raise HTTPException(401, "Invalid administrator credentials")
    store.audit("ADMIN_LOGIN", "admin", "admin")
    return {"success": True, **tokens, "admin": {"id": "admin", "name": "Administrator"}}


@router.post("/google")
def admin_login_google(body: GoogleReq):
    """Primary login: verify a Google ID token and admit only allow-listed accounts."""
    tokens = auth.login_google(body.credential)
    if not tokens:
        raise HTTPException(401, "This Google account is not authorized")
    store.audit("ADMIN_LOGIN_GOOGLE", "admin", "admin")
    return {"success": True, **tokens, "admin": {"id": "admin", "name": "Administrator"}}


@router.post("/logout")
def admin_logout(authorization: Optional[str] = Header(None)):
    token = auth.token_from_header(authorization)
    if token:
        auth.revoke(token)
    return {"success": True}


@router.get("/me")
def admin_me(authorization: Optional[str] = Header(None)):
    payload = auth.verify(auth.token_from_header(authorization))
    if not payload:
        raise HTTPException(401, "Not authenticated")
    return {"success": True, "admin": {"id": "admin", "name": "Administrator",
                                       "username": settings.ADMIN_USERNAME}}


@router.post("/refresh")
def admin_refresh(body: RefreshReq):
    result = auth.refresh(body.refresh_token)
    if not result:
        raise HTTPException(401, "Invalid or expired refresh token")
    return {"success": True, **result}


@router.get("/audit")
def admin_audit(limit: int = 100):
    return {"events": store.list_audit(limit)}
