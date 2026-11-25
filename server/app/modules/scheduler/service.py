"""Exact-time wake-ups for serverless hosts.

The free database sleeps after 5 idle minutes and has a monthly compute allowance, so nothing polls it.
Instead, for every second at which a reminder is due, we book ONE call to /v1/internal/tick with
Upstash QStash ("Upstash-Not-Before"). An hourly QStash schedule is the safety net: it sends anything
missed, runs family missed-dose checks, books the next 24 hours, and purges old data."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.push.models import ScheduledPush
from app.modules.scheduler.models import WakeSlot

logger = logging.getLogger(__name__)
HORIZON = timedelta(hours=24)            # the hourly safety net books the rest as it comes closer
HOURLY_ID = "nasem-hourly-tick"

Publisher = Callable[[datetime], Awaitable[bool]]


def public_base() -> str:
    """Where QStash must call us. PUBLIC_URL wins; on Vercel the production domain is known."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or ""
    return f"https://{host}" if host else ""


def enabled() -> bool:
    return bool(settings.qstash_token and settings.cron_secret and public_base())


def _headers() -> dict[str, str]:
    assert settings.qstash_token is not None and settings.cron_secret is not None
    return {"Authorization": f"Bearer {settings.qstash_token.get_secret_value()}",
            "Upstash-Forward-Authorization": f"Bearer {settings.cron_secret.get_secret_value()}",
            "Upstash-Retries": "3"}


async def qstash_publish_at(at: datetime) -> bool:
    url = f"{settings.qstash_url.rstrip('/')}/v2/publish/{public_base()}/v1/internal/tick"
    epoch = int(at.timestamp()) + (0 if at.microsecond == 0 else 1)
    headers = {**_headers(), "Upstash-Not-Before": str(epoch), "Upstash-Deduplication-Id": f"nasem-wake-{epoch}", "Upstash-Method": "POST"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, headers=headers)
        if r.status_code >= 300:
            logger.warning("qstash publish failed: %s %s", r.status_code, r.text[:200])
        return r.status_code < 300
    except httpx.HTTPError as e:
        logger.warning("qstash publish error: %s", e)
        return False


async def ensure_hourly_schedule() -> bool:
    """Idempotent: the fixed schedule id makes QStash update instead of adding a second one."""
    if not enabled():
        return False
    url = f"{settings.qstash_url.rstrip('/')}/v2/schedules/{public_base()}/v1/internal/tick"
    headers = {**_headers(), "Upstash-Cron": "0 * * * *", "Upstash-Schedule-Id": HOURLY_ID, "Upstash-Method": "POST"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, headers=headers)
        if r.status_code >= 300:
            logger.warning("qstash schedule failed: %s %s", r.status_code, r.text[:200])
        return r.status_code < 300
    except httpx.HTTPError as e:
        logger.warning("qstash schedule error: %s", e)
        return False


async def book_wakeups(s: AsyncSession, *, publisher: Publisher | None = None, now: datetime | None = None) -> int:
    """Book one wake-up per distinct due second in the next 24 hours that isn't booked yet."""
    if publisher is None:
        if not enabled():
            return 0
        publisher = qstash_publish_at
    now = now or datetime.now(UTC)
    due = (await s.execute(select(ScheduledPush.fire_at).where(
        ScheduledPush.sent_at.is_(None), ScheduledPush.fire_at > now, ScheduledPush.fire_at <= now + HORIZON))).scalars().all()
    seconds = sorted({d.replace(microsecond=0) + (timedelta(seconds=1) if d.microsecond else timedelta(0)) for d in due})
    if not seconds:
        return 0
    have = set((await s.execute(select(WakeSlot.at).where(WakeSlot.at.in_(seconds)))).scalars().all())
    booked = 0
    for at in seconds:
        if at in have:
            continue
        if await publisher(at):
            s.add(WakeSlot(at=at))
            booked += 1
    await s.commit()
    return booked


async def purge_wake_slots(s: AsyncSession, now: datetime | None = None) -> None:
    await s.execute(delete(WakeSlot).where(WakeSlot.at < (now or datetime.now(UTC)) - timedelta(days=2)))
    await s.commit()
