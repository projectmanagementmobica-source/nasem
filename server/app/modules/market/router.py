"""Market prices relay: official CBE rates (via Frankfurter) and the global gold spot (XAUS).

The app calls these sources directly when the browser allows it; this relay is the fallback.
One upstream call per source per 10 minutes, shared by every user (cheap, and polite to the sources).
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter

from app.shared.api import AppError

router = APIRouter(prefix="/v1/market", tags=["market"])

SOURCES: dict[str, str] = {
    "cbe": "https://api.frankfurter.dev/v2/providers/cbe/rates",
    "gold": "https://xaus.com/api/v1/spot?unit=gram&currency=EGP",
}
TTL = 600.0
_CACHE: dict[str, tuple[float, Any]] = {}


async def fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=15, headers={"user-agent": "NASEM/1.0 (+market relay)"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


@router.get("/raw")
async def raw(src: Literal["cbe", "gold"]) -> dict[str, Any]:
    now = time.time()
    hit = _CACHE.get(src)
    if hit and now - hit[0] < TTL:
        return {"src": src, "fetched_at": hit[0], "cached": True, "data": hit[1]}
    try:
        data = await fetch_json(SOURCES[src])
    except Exception as exc:  # noqa: BLE001
        if hit:   # stale beats nothing; the app shows the time it is from
            return {"src": src, "fetched_at": hit[0], "cached": True, "stale": True, "data": hit[1]}
        raise AppError(503, "market_unavailable", f"{src} source unavailable") from exc
    _CACHE[src] = (now, data)
    return {"src": src, "fetched_at": now, "cached": False, "data": data}
