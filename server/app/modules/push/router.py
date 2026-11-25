from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.push.service import PushService, ScheduleRequest, SubscribeRequest, require_push_configured

router = APIRouter(prefix="/v1/push", tags=["push"])


class PublicKey(BaseModel):
    public_key: str


class Count(BaseModel):
    scheduled: int


class Ok(BaseModel):
    status: str = "ok"


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid-public-key", response_model=PublicKey)
async def vapid_public_key() -> PublicKey:
    require_push_configured()
    return PublicKey(public_key=settings.vapid_public_key)


@router.post("/subscribe", response_model=Ok)
async def subscribe(body: SubscribeRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Ok:
    require_push_configured()
    await PushService(session).subscribe(user.id, body)
    return Ok()


@router.post("/unsubscribe", response_model=Ok)
async def unsubscribe(body: UnsubscribeRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Ok:
    await PushService(session).unsubscribe(user.id, body.endpoint)
    return Ok()


@router.put("/schedule", response_model=Count)
async def schedule(body: ScheduleRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Count:
    """Encrypted reminders from the phone. The server can't read them: it only knows when to deliver."""
    return Count(scheduled=await PushService(session).replace_schedule(user.id, body))
