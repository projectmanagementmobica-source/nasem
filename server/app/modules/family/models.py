"""نسيم العيلة: a parent (member) lets relatives (caregivers) see ONLY what the parent chose to share."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class FamilyLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "family_links"

    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    caregiver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    invite_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invite_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'pending'"))  # pending|active|revoked
    member_name: Mapped[str] = mapped_column(String(60), nullable=False)          # how the parent introduces themself
    member_label: Mapped[str | None] = mapped_column(String(40), nullable=True)   # what the caregiver calls them ("ماما")
    caregiver_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    share_meds: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    share_appts: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    share_emergency: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_family_links_member", "member_id"), Index("ix_family_links_caregiver", "caregiver_id"),
                      Index("ix_family_links_invite", "invite_hash"))


class FamilyItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A dose or an appointment the parent's phone published (only kinds some link shares)."""

    __tablename__ = "family_items"

    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)          # dose|appt
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("uq_family_items", "member_id", "kind", "client_id", unique=True), Index("ix_family_items_due", "at"))


class FamilyEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "family_events"

    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)   # dose_taken|dose_missed|emergency|request_accepted|request_rejected
    title: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (Index("ix_family_events_member", "member_id", "created_at"),)


class FamilyRequest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A reminder a caregiver proposes; it reaches the parent's phone and waits for the parent's approval."""

    __tablename__ = "family_requests"

    link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("family_links.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'pending'"))  # pending|accepted|rejected
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_family_requests_link", "link_id", "status"),)
