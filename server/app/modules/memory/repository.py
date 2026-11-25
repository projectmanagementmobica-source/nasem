"""Read queries for memories used by other modules."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.memory.models import Memory
from app.shared.enums import PrivacyLevel


async def get_active_by_attribute(
    session: AsyncSession, user_id: uuid.UUID, entity_name: str, attribute_key: str
) -> Memory | None:
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.entity_name == entity_name,
        Memory.attribute_key == attribute_key,
        Memory.is_active.is_(True),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_recent_active(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    exclude_privacy: Sequence[PrivacyLevel] = (),
) -> list[Memory]:
    stmt = select(Memory).where(Memory.user_id == user_id, Memory.is_active.is_(True))
    if exclude_privacy:
        stmt = stmt.where(Memory.privacy_level.not_in(list(exclude_privacy)))
    stmt = stmt.order_by(Memory.last_confirmed_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())
