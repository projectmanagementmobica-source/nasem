"""Events module: life events that drive proactive behaviour (travel, medical, ...)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.shared.enums import EventType

if TYPE_CHECKING:
    from app.modules.commitments.models import Commitment


class EventContext(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "events_context"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[EventType] = mapped_column(str_enum(EventType, "event_type"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Shape: [{"id": "passport", "label": "Check passport", "done": false, "done_at": null}, ...]
    checklist_status: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    commitments: Mapped[list[Commitment]] = relationship(back_populates="related_event")

    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="valid_date_range"),
        # Hot path: "upcoming events for user X" (scheduler + context builder)
        Index("ix_events_context_user_id_start_date", "user_id", "start_date"),
        Index("ix_events_context_user_id_event_type", "user_id", "event_type"),
    )
