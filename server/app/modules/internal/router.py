"""/v1/internal/tick — the minute-by-minute background work, called by an outside scheduler
(cron-job.org or Vercel Cron) on hosts without an always-on process."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, Query

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.push.service import run_tick
from app.shared.api import AppError

router = APIRouter(prefix="/v1/internal", tags=["internal"])


def _authorized(authorization: str | None, key: str | None) -> bool:
    if settings.cron_secret is None:
        raise AppError(503, "cron_not_configured", "CRON_SECRET is not set on the server")
    secret = settings.cron_secret.get_secret_value()
    given = (authorization or "").removeprefix("Bearer ").strip() or (key or "")
    return bool(given) and hmac.compare_digest(given.encode(), secret.encode())


@router.api_route("/tick", methods=["GET", "POST"])
async def tick(authorization: str | None = Header(default=None), key: str | None = Query(default=None)) -> dict[str, Any]:
    if not _authorized(authorization, key):
        raise AppError(401, "unauthorized", "bad cron secret")
    now = datetime.now(UTC)
    done = await run_tick(SessionLocal, purge=now.hour == 3 and now.minute < 5)   # the 03:00 UTC hourly call purges
    return {"status": "ok", "at": now.isoformat(), **done}
