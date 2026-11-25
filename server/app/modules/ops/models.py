"""Operational counters for the owner's health panel. Metadata only: no prompts, answers or reminder text."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OpsEvent(Base):
    __tablename__ = "ops_events"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)      # ai | push | tick
    name: Mapped[str] = mapped_column(String(48), primary_key=True)      # e.g. "gemini:ok", "sent", "run"
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_note: Mapped[str | None] = mapped_column(String(240), nullable=True)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
