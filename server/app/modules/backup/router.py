"""PUT/GET/DELETE /v1/backup — one encrypted blob per account (plus the previous one).

The server only checks that the blob LOOKS like Nasem's encrypted envelope; it never has the key."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.backup.models import UserBackup
from app.shared.api import AppError

router = APIRouter(prefix="/v1/backup", tags=["backup"])

MAX_CHARS = 12_000_000          # ~9 MB of ciphertext: years of heavy use
MIN_INTERVAL = timedelta(seconds=30)


class BackupIn(BaseModel):
    blob: str = Field(min_length=20, max_length=MAX_CHARS)
    device_label: str | None = Field(default=None, max_length=120)


def _b64ok(v: Any, min_len: int = 1) -> bool:
    if not isinstance(v, str) or len(v) < min_len:
        return False
    try:
        base64.b64decode(v, validate=True)
        return True
    except (binascii.Error, ValueError):
        return False


def validate_envelope(blob: str) -> None:
    try:
        env = json.loads(blob)
    except ValueError as e:
        raise AppError(400, "bad_backup", "backup is not an encrypted Nasem envelope") from e
    ok = (isinstance(env, dict) and env.get("v") == 1 and env.get("alg") == "AES-GCM" and env.get("kdf") == "PBKDF2-SHA256"
          and isinstance(env.get("iter"), int) and env["iter"] >= 100_000
          and _b64ok(env.get("salt"), 16) and _b64ok(env.get("iv"), 12) and _b64ok(env.get("ct"), 16))
    if not ok:
        raise AppError(400, "bad_backup", "backup is not an encrypted Nasem envelope")
    # a plaintext backup must never be accepted by mistake
    if "memories" in env or "commitments" in env:
        raise AppError(400, "bad_backup", "plaintext data is not accepted")


def _meta(b: UserBackup | None) -> dict[str, Any]:
    if b is None:
        return {"exists": False}
    return {"exists": True, "size": b.size, "sha256": b.sha256, "device_label": b.device_label,
            "updated_at": b.updated_at.isoformat(), "has_previous": b.prev_blob is not None}


@router.get("/meta")
async def meta(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return _meta(await s.get(UserBackup, user.id))


@router.get("")
async def download(previous: bool = Query(default=False), user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    b = await s.get(UserBackup, user.id)
    if b is None or (previous and b.prev_blob is None):
        raise AppError(404, "no_backup", "no backup stored")
    return {**_meta(b), "blob": b.prev_blob if previous else b.blob, "previous": previous}


@router.put("")
async def upload(body: BackupIn, user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    validate_envelope(body.blob)
    digest = hashlib.sha256(body.blob.encode()).hexdigest()
    now = datetime.now(UTC)
    b = await s.get(UserBackup, user.id)
    if b is not None:
        if b.sha256 == digest:
            return _meta(b)
        if now - b.updated_at < MIN_INTERVAL:
            raise AppError(429, "too_soon", "wait a little before the next backup")
        b.prev_blob, b.prev_at = b.blob, b.updated_at
        b.blob, b.size, b.sha256, b.device_label, b.updated_at = body.blob, len(body.blob), digest, body.device_label, now
    else:
        b = UserBackup(user_id=user.id, blob=body.blob, size=len(body.blob), sha256=digest, device_label=body.device_label, updated_at=now)
        s.add(b)
    await s.commit()
    return _meta(b)


@router.delete("")
async def remove(user: User = Depends(get_current_user), s: AsyncSession = Depends(get_session)) -> dict[str, str]:
    b = await s.get(UserBackup, user.id)
    if b is not None:
        await s.delete(b)
        await s.commit()
    return {"status": "deleted"}
