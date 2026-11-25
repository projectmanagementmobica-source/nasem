"""Commitments module: promises, tasks and follow-ups the agent tracks for the user."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.shared.enums import CommitmentStatus

if TYPE_CHECKING:
    from app.modules.events.models import EventContext


class Commitment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commitments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CommitmentStatus] = mapped_column(
        str_enum(CommitmentStatus, "status"),
        nullable=False,
        server_default=CommitmentStatus.PENDING.value,
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Metadata ---
    is_proactive_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    related_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events_context.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Rule 1: safe by default. Both ORM default and DB default are TRUE.
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Set when the user approves (via pending_actions). NULL = not approved yet -> never acted on.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    related_event: Mapped[EventContext | None] = relationship(back_populates="commitments")

    __table_args__ = (
        # Hot path: "pending commitments of user X"
        Index("ix_commitments_user_id_status", "user_id", "status"),
        # Scheduler scan: only APPROVED pending rows that actually need a follow-up
        Index(
            "ix_commitments_follow_up_at_approved_pending",
            "follow_up_at",
            postgresql_where=text(
                "status = 'pending' AND approved_at IS NOT NULL AND follow_up_at IS NOT NULL"
            ),
        ),
        Index("ix_commitments_related_event_id", "related_event_id"),
    )
