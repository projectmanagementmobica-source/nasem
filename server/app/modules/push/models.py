"""Web Push: device subscriptions + scheduled reminders whose text is end-to-end encrypted by the app."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class PushSubscription(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (Index("uq_push_subscriptions_endpoint", "endpoint", unique=True), Index("ix_push_subscriptions_user_id", "user_id"))


class ScheduledPush(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """The server knows only fire_at. `ciphertext` is AES-GCM sealed on the phone with a key that never leaves it."""

    __tablename__ = "scheduled_pushes"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(80), nullable=False)
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    iv: Mapped[str] = mapped_column(String(40), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_scheduled_pushes_due", "fire_at", postgresql_where=text("sent_at IS NULL")),
        Index("ix_scheduled_pushes_user_id", "user_id"),
    )
