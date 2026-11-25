from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.access.service import AccessService
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.shared.api import AppError

router = APIRouter(tags=["access"])


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestIn(_M):
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=8, max_length=20)
    device_id: str = Field(min_length=16, max_length=64)
    device_label: str = Field(default="", max_length=120)
    email: str | None = Field(default=None, max_length=160)
    purpose: str | None = Field(default=None, max_length=300)
    accepted_terms: bool = False


class RequestOut(BaseModel):
    request_id: uuid.UUID
    claim_secret: str


class ClaimIn(_M):
    request_id: uuid.UUID
    claim_secret: str = Field(min_length=10, max_length=100)


class ClaimOut(BaseModel):
    status: str
    note: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None


class ApproveIn(_M):
    daily_limit: int | None = Field(default=None, ge=1, le=10000)


class RejectIn(_M):
    note: str | None = Field(default=None, max_length=200)


class UserPatch(_M):
    active: bool | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=10000)
    clear_limit: bool = False


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise AppError(403, "forbidden", "admins only")
    return user


@router.post("/v1/access/request", response_model=RequestOut, status_code=201)
async def request_activation(body: RequestIn, session: AsyncSession = Depends(get_session)) -> RequestOut:
    """Called by the app's «اطلب تفعيل» button. Only the phone that sent it gets the secret to claim it."""
    if not body.accepted_terms:
        raise AppError(400, "terms_required", "the user must accept the terms first")
    rid, secret = await AccessService(session).request(body.name, body.phone, body.device_id, body.device_label, body.email, body.purpose)
    return RequestOut(request_id=rid, claim_secret=secret)


@router.post("/v1/access/claim", response_model=ClaimOut)
async def claim(body: ClaimIn, session: AsyncSession = Depends(get_session)) -> ClaimOut:
    r = await AccessService(session).claim(body.request_id, body.claim_secret)
    if r.tokens is None:
        return ClaimOut(status=r.status, note=r.note)
    return ClaimOut(status="approved", access_token=r.tokens.access_token, refresh_token=r.tokens.refresh_token, expires_in=r.tokens.expires_in)


@router.get("/v1/admin/requests")
async def admin_requests(status: str = "pending", _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    rows = await AccessService(session).list_requests(status)
    return [{"id": str(r.id), "name": r.name, "phone": r.phone, "email": r.email, "purpose": r.purpose, "device": r.device_label, "device_id": r.device_id[-6:],
             "status": r.status, "at": r.created_at.isoformat()} for r in rows]


@router.post("/v1/admin/requests/{request_id}/approve")
async def admin_approve(request_id: uuid.UUID, body: ApproveIn, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    user = await AccessService(session).approve(request_id, body.daily_limit)
    return {"status": "approved", "user_id": str(user.id)}


@router.post("/v1/admin/requests/{request_id}/reject")
async def admin_reject(request_id: uuid.UUID, body: RejectIn, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await AccessService(session).reject(request_id, body.note)
    return {"status": "rejected"}


@router.get("/v1/admin/users")
async def admin_users(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await AccessService(session).users()


@router.patch("/v1/admin/users/{user_id}")
async def admin_patch_user(user_id: uuid.UUID, body: UserPatch, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await AccessService(session).set_user(user_id, active=body.active, daily_limit=body.daily_limit, clear_limit=body.clear_limit)
    return {"status": "ok"}


@router.post("/v1/admin/devices/{device_pk}/revoke")
async def admin_revoke_device(device_pk: uuid.UUID, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await AccessService(session).revoke_device(device_pk)
    return {"status": "revoked"}


@router.get("/v1/admin/stats")
async def admin_stats(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    return await AccessService(session).stats()


@router.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "admin.html", media_type="text/html")
