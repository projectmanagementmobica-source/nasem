"""Approvals module: every non-read action proposed by the agent waits here for the user (Rule 1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, str_enum
from app.shared.enums import ApprovalActionType, ApprovalStatus


class PendingAction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "pending_actions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[ApprovalActionType] = mapped_column(
        str_enum(ApprovalActionType, "action_type"), nullable=False
    )
    # memories.id or commitments.id depending on action_type (polymorphic -> no FK).
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Everything needed to execute the action later, captured at proposal time.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus, "status"),
        nullable=False,
        server_default=ApprovalStatus.PENDING.value,
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="decided_at_matches_status"),
        # Hot path: "what is waiting for user X?"
        Index("ix_pending_actions_user_id_status", "user_id", "status"),
        # At most ONE open action per target (a newer proposal supersedes the old one).
        Index(
            "uq_pending_actions_open_target",
            "action_type",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
