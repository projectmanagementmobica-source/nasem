"""Record and read operational counters (never fails the caller)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ops.models import OpsEvent

logger = logging.getLogger(__name__)


async def bump(session: AsyncSession, kind: str, name: str, note: str | None = None, n: int = 1, commit: bool = True) -> None:
    now = datetime.now(UTC)
    try:
        stmt = insert(OpsEvent).values(day=now.date(), kind=kind[:16], name=name[:48], count=n, last_note=(note or None) and note[:240], last_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[OpsEvent.day, OpsEvent.kind, OpsEvent.name],
            set_={"count": OpsEvent.count + n, "last_at": now, **({"last_note": note[:240]} if note else {})},
        )
        await session.execute(stmt)
        if commit:
            await session.commit()
    except Exception as exc:  # the health panel must never break real work
        logger.warning("ops bump failed: %s", exc)
        try:
            await session.rollback()
        except Exception:
            pass


async def health(session: AsyncSession) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    rows = (await session.execute(select(OpsEvent).where(OpsEvent.day >= today - timedelta(days=6)))).scalars().all()
    out: dict[str, Any] = {"today": {}, "week": {}, "notes": [], "last": {}}
    for r in rows:
        key = f"{r.kind}:{r.name}"
        out["week"][key] = out["week"].get(key, 0) + r.count
        if r.day == today:
            out["today"][key] = r.count
        prev = out["last"].get(key)
        if prev is None or r.last_at.isoformat() > prev:
            out["last"][key] = r.last_at.isoformat()
        if r.last_note and r.day == today:
            out["notes"].append({"key": key, "note": r.last_note, "at": r.last_at.isoformat()})
    out["notes"].sort(key=lambda x: x["at"], reverse=True)
    out["notes"] = out["notes"][:12]
    return out


async def purge_ops(session: AsyncSession, keep_days: int = 30) -> None:
    await session.execute(delete(OpsEvent).where(OpsEvent.day < datetime.now(UTC).date() - timedelta(days=keep_days)))
    await session.commit()
