from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.family.service import FamilyService, InviteIn, JoinIn, RequestIn, Shares, SyncIn

router = APIRouter(prefix="/v1/family", tags=["family"])


class Ok(BaseModel):
    status: str = "ok"


class Code(BaseModel):
    code: str
    expires_in_hours: int = 24


class TakenIn(BaseModel):
    client_id: str
    taken: bool = True


class DecideIn(BaseModel):
    accept: bool


@router.get("")
async def links(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await FamilyService(s).links(user)


@router.post("/invite", response_model=Code)
async def invite(body: InviteIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Code:
    return Code(code=await FamilyService(s).invite(user, body))


@router.post("/join")
async def join(body: JoinIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, str]:
    link = await FamilyService(s).join(user, body)
    return {"link_id": str(link.id), "name": link.member_name}


@router.put("/links/{link_id}/shares", response_model=Ok)
async def shares(link_id: uuid.UUID, body: Shares, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Ok:
    await FamilyService(s).set_shares(user, link_id, body)
    return Ok()


@router.delete("/links/{link_id}", response_model=Ok)
async def revoke(link_id: uuid.UUID, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Ok:
    await FamilyService(s).revoke(user, link_id)
    return Ok()


@router.put("/sync")
async def sync(body: SyncIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, int]:
    return await FamilyService(s).sync(user, body)


@router.post("/taken", response_model=Ok)
async def taken(body: TakenIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Ok:
    await FamilyService(s).taken(user, body.client_id, body.taken)
    return Ok()


@router.post("/emergency")
async def emergency(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, int]:
    return {"notified": await FamilyService(s).emergency(user)}


@router.get("/feed")
async def feed(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await FamilyService(s).feed(user)


@router.post("/requests", response_model=Ok, status_code=201)
async def propose(body: RequestIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Ok:
    await FamilyService(s).propose(user, body)
    return Ok()


@router.get("/requests/incoming")
async def incoming(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await FamilyService(s).incoming(user)


@router.post("/requests/{request_id}/decide", response_model=Ok)
async def decide(request_id: uuid.UUID, body: DecideIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> Ok:
    await FamilyService(s).decide(user, request_id, body.accept)
    return Ok()
