"""Subscribe, sync the encrypted schedule, and send what is due."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.models  # noqa: F401
from app.core.config import settings
from app.modules.push.models import PushSubscription, ScheduledPush
from app.shared.api import AppError

logger = logging.getLogger(__name__)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubscriptionKeys(_M):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SubscribeRequest(_M):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: SubscriptionKeys


class ScheduleItem(_M):
    client_id: str = Field(min_length=1, max_length=80)
    fire_at: datetime
    ciphertext: str = Field(min_length=8, max_length=3000)  # keeps every push under the 4 KB payload limit
    iv: str = Field(min_length=8, max_length=40)


class ScheduleRequest(_M):
    items: list[ScheduleItem] = Field(default_factory=list)


Sender = Callable[[dict[str, Any], str], None]  # (subscription_info, payload) -> raises on failure


def default_sender(sub: dict[str, Any], payload: str) -> None:
    from pywebpush import webpush

    assert settings.vapid_private_key is not None
    webpush(subscription_info=sub, data=payload, vapid_private_key=settings.vapid_private_key.get_secret_value(),
            vapid_claims={"sub": settings.vapid_subject}, ttl=3600)


class PushService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        self._s = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def subscribe(self, user_id: uuid.UUID, req: SubscribeRequest) -> None:
        existing = (await self._s.execute(select(PushSubscription).where(PushSubscription.endpoint == req.endpoint))).scalar_one_or_none()
        if existing is None:
            self._s.add(PushSubscription(user_id=user_id, endpoint=req.endpoint, p256dh=req.keys.p256dh, auth=req.keys.auth))
        else:  # same browser re-subscribing (or switching account on the same phone)
            existing.user_id, existing.p256dh, existing.auth = user_id, req.keys.p256dh, req.keys.auth
        await self._s.commit()

    async def unsubscribe(self, user_id: uuid.UUID, endpoint: str) -> None:
        await self._s.execute(delete(PushSubscription).where(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint))
        await self._s.commit()

    async def replace_schedule(self, user_id: uuid.UUID, req: ScheduleRequest) -> int:
        """The phone owns the schedule: every sync replaces all future, unsent items."""
        now = self._clock()
        items = [i for i in req.items if i.fire_at > now - timedelta(minutes=1)][: settings.push_max_scheduled_per_user]
        await self._s.execute(delete(ScheduledPush).where(ScheduledPush.user_id == user_id, ScheduledPush.sent_at.is_(None)))
        self._s.add_all([ScheduledPush(user_id=user_id, client_id=i.client_id, fire_at=i.fire_at, ciphertext=i.ciphertext, iv=i.iv) for i in items])
        await self._s.commit()
        if settings.serverless:   # no always-on loop: book exact wake-ups for what's coming
            from app.modules.scheduler.service import book_wakeups
            try:
                await book_wakeups(self._s)
            except Exception:
                logger.exception("booking wake-ups failed (the hourly safety net will retry)")
        return len(items)

    async def send_due(self, sender: Sender | None = None, limit: int = 200) -> int:
        send = sender or default_sender
        now = self._clock()
        due = (await self._s.execute(
            select(ScheduledPush).where(ScheduledPush.sent_at.is_(None), ScheduledPush.fire_at <= now).order_by(ScheduledPush.fire_at).limit(limit).with_for_update(skip_locked=True)
        )).scalars().all()
        sent = failed = orphan = 0
        for item in due:
            subs = (await self._s.execute(select(PushSubscription).where(PushSubscription.user_id == item.user_id))).scalars().all()
            if not subs:
                orphan += 1
            payload = json.dumps({"c": item.ciphertext, "iv": item.iv, "id": item.client_id})
            for sub in subs:
                info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
                try:
                    await asyncio.to_thread(send, info, payload)
                    sent += 1
                except Exception as exc:  # expired subscriptions are removed
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    failed += 1
                    if status in (404, 410):
                        await self._s.delete(sub)
                    else:
                        logger.warning("push failed for user %s: %s", item.user_id, exc)
            item.sent_at = now
        await self._s.commit()
        from app.modules.ops.service import bump
        if sent:
            await bump(self._s, "push", "sent", n=sent)
        if failed:
            await bump(self._s, "push", "failed", n=failed)
        if orphan:
            await bump(self._s, "push", "no_device", "reminder was due but the phone has no notification subscription", n=orphan)
        return sent


async def purge_expired(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Delete what the server no longer needs: delivered reminders and old usage counters."""
    from app.modules.gateway.models import GatewayUsage

    now = now or datetime.now(UTC)
    sent = await session.execute(delete(ScheduledPush).where(
        ScheduledPush.sent_at.is_not(None), ScheduledPush.sent_at < now - timedelta(days=settings.retention_sent_push_days)))
    usage = await session.execute(delete(GatewayUsage).where(GatewayUsage.day < (now - timedelta(days=settings.retention_usage_days)).date()))
    await session.commit()
    return {"sent_pushes": int(getattr(sent, "rowcount", 0) or 0), "usage_rows": int(getattr(usage, "rowcount", 0) or 0)}


async def run_tick(maker: async_sessionmaker[AsyncSession], *, purge: bool) -> dict[str, Any]:
    """One round of background work: due pushes, missed-dose family alerts, and (when asked) retention purge."""
    done: dict[str, Any] = {"push": False, "family": False, "purge": False}
    async with maker() as s:
        from app.modules.ops.service import bump, purge_ops
        await bump(s, "tick", "run")
        if purge:
            await purge_ops(s)
        if settings.vapid_public_key and settings.vapid_private_key:
            await PushService(s).send_due()
            done["push"] = True
        from app.modules.family.service import FamilyService   # local import: family uses push
        await FamilyService(s).check_missed()
        done["family"] = True
        if purge:
            await purge_expired(s)
            from app.modules.family.service import purge_family
            await purge_family(s)
            done["purge"] = True
        if settings.serverless:
            from app.modules.scheduler.service import book_wakeups, purge_wake_slots
            done["booked"] = await book_wakeups(s)
            if purge:
                await purge_wake_slots(s)
    return done


async def push_loop(maker: async_sessionmaker[AsyncSession], stop: asyncio.Event) -> None:
    """Background sender for always-on hosts. One instance is enough; skip_locked keeps several instances safe."""
    last_purge: datetime | None = None
    while not stop.is_set():
        try:
            purge = last_purge is None or datetime.now(UTC) - last_purge > timedelta(hours=6)
            await run_tick(maker, purge=purge)
            if purge:
                last_purge = datetime.now(UTC)
        except Exception:
            logger.exception("push loop iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.push_poll_seconds)
        except asyncio.TimeoutError:
            pass


def require_push_configured() -> None:
    if not settings.vapid_public_key or settings.vapid_private_key is None:
        raise AppError(503, "push_not_configured", "push notifications are not configured on the server")
