"""Collect everything a report needs, once, in the user's timezone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401  (registers every mapper)
from app.modules.approvals.models import PendingAction
from app.modules.auth.models import User
from app.modules.commitments.models import Commitment
from app.modules.memory.models import Memory
from app.shared.enums import ApprovalStatus, CommitmentStatus, PrivacyLevel

Period = Literal["week", "month"]
PERIOD_DAYS: dict[Period, int] = {"week": 7, "month": 30}
MAX_ROWS = 500


@dataclass
class ReportData:
    user: User
    tz: tzinfo
    now: datetime
    period: Period
    start: datetime  # look-back window start (completed / new memories)
    end: datetime  # look-ahead window end (upcoming)
    upcoming: list[Commitment] = field(default_factory=list)  # approved, due in window or undated
    overdue: list[Commitment] = field(default_factory=list)
    completed: list[Commitment] = field(default_factory=list)  # completed within look-back
    awaiting: list[PendingAction] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    new_memories: list[Memory] = field(default_factory=list)
    include_sensitive: bool = False

    def local(self, dt: datetime | None) -> datetime | None:
        return dt.astimezone(self.tz) if dt is not None else None

    @property
    def stats(self) -> dict[str, int]:
        return {
            "upcoming": len(self.upcoming),
            "overdue": len(self.overdue),
            "completed": len(self.completed),
            "awaiting_approval": len(self.awaiting),
            "memories_total": len(self.memories),
            "new_memories": len(self.new_memories),
        }


async def collect_report_data(
    session: AsyncSession,
    user: User,
    *,
    tz: tzinfo,
    now: datetime,
    period: Period,
    include_sensitive: bool = False,
) -> ReportData:
    days = PERIOD_DAYS[period]
    data = ReportData(
        user=user, tz=tz, now=now, period=period,
        start=now - timedelta(days=days), end=now + timedelta(days=days),
        include_sensitive=include_sensitive,
    )

    commitments = (
        await session.scalars(
            select(Commitment)
            .where(Commitment.user_id == user.id, Commitment.status.in_([CommitmentStatus.PENDING, CommitmentStatus.COMPLETED]))
            .order_by(Commitment.due_date.asc().nulls_last())
            .limit(MAX_ROWS)
        )
    ).all()
    for c in commitments:
        if c.status == CommitmentStatus.COMPLETED:
            if c.updated_at >= data.start:
                data.completed.append(c)
        elif c.approved_at is not None:
            if c.due_date is not None and c.due_date < now:
                data.overdue.append(c)
            elif c.due_date is None or c.due_date <= data.end:
                data.upcoming.append(c)

    data.awaiting = list(
        (
            await session.scalars(
                select(PendingAction)
                .where(PendingAction.user_id == user.id, PendingAction.status == ApprovalStatus.PENDING)
                .order_by(PendingAction.created_at.desc())
                .limit(MAX_ROWS)
            )
        ).all()
    )

    stmt = select(Memory).where(Memory.user_id == user.id, Memory.is_active.is_(True))
    if not include_sensitive:
        stmt = stmt.where(Memory.privacy_level != PrivacyLevel.SENSITIVE)
    data.memories = list((await session.scalars(stmt.order_by(Memory.category, Memory.entity_name).limit(MAX_ROWS))).all())
    data.new_memories = [m for m in data.memories if m.created_at >= data.start]
    return data
