"""Wake-up slots already booked with the outside scheduler, so a sync never books the same second twice."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WakeSlot(Base):
    __tablename__ = "wake_slots"

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
