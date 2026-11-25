"""Activation requests and approved devices. The owner decides who uses Nasem and on how many devices."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ActivationRequest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "activation_requests"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)       # random id made by the app, kept on the phone
    device_label: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(300), nullable=True)   # job / why they want Nasem
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)      # SHA-256 of the secret only that phone holds
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))  # pending|approved|rejected|claimed
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (Index("ix_activation_requests_status", "status"), Index("ix_activation_requests_device", "device_id"))


class Device(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)   # its refresh-token family
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))  # active|revoked
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("uq_devices_device_id", "device_id", unique=True), Index("ix_devices_user_id", "user_id"))
