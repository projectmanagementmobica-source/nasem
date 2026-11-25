"""Activation flow: request (from the phone) -> owner approves -> the same phone claims its session."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.config import settings
from app.modules.access.models import ActivationRequest, Device
from app.modules.auth.models import RefreshToken, User
from app.modules.auth.security import hash_password
from app.modules.auth.service import AuthService, IssuedTokens
from app.modules.gateway.models import GatewayUsage
from app.shared.api import AppError

def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0020"):
        d = d[2:]
    if d.startswith("20") and len(d) == 12:
        d = "0" + d[2:]
    if not re.fullmatch(r"0?1[0125]\d{8}|\d{8,15}", d):
        raise AppError(400, "invalid_phone", "phone number looks wrong")
    return d


@dataclass
class ClaimResult:
    status: str
    tokens: IssuedTokens | None = None
    note: str | None = None


class AccessService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        self._s = session
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---------------- phone side ----------------
    async def request(self, name: str, phone: str, device_id: str, device_label: str, email: str | None = None, purpose: str | None = None) -> tuple[uuid.UUID, str]:
        if not settings.activation_open:
            raise AppError(403, "activation_closed", "new activations are closed right now")
        if not re.fullmatch(r"[A-Za-z0-9\-]{16,64}", device_id or ""):
            raise AppError(400, "invalid_device", "bad device id")
        phone_n = normalize_phone(phone)
        existing = (await self._s.execute(select(Device).where(Device.device_id == device_id, Device.status == "active"))).scalar_one_or_none()
        if existing is not None:
            raise AppError(409, "already_active", "this device is already activated")
        pending = await self._s.scalar(select(func.count()).select_from(ActivationRequest).where(ActivationRequest.status == "pending"))
        if (pending or 0) >= 200:
            raise AppError(429, "too_many_requests", "too many pending requests, try later")
        # one live request per device: replace an older pending one
        await self._s.execute(update(ActivationRequest).where(ActivationRequest.device_id == device_id, ActivationRequest.status == "pending")
                              .values(status="rejected", note="replaced by a newer request", decided_at=self._clock()))
        secret = secrets.token_urlsafe(32)
        mail = (email or "").strip().lower()[:160] or None
        if mail and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail):
            raise AppError(400, "invalid_email", "email looks wrong")
        req = ActivationRequest(name=name.strip()[:100], phone=phone_n, device_id=device_id, device_label=(device_label or "جهاز")[:120],
                                email=mail, purpose=((purpose or "").strip()[:300] or None), claim_hash=_sha(secret))
        self._s.add(req)
        await self._s.commit()
        return req.id, secret

    async def claim(self, request_id: uuid.UUID, secret: str) -> ClaimResult:
        req = await self._s.get(ActivationRequest, request_id, with_for_update=True)
        if req is None or not secrets.compare_digest(req.claim_hash, _sha(secret or "")):
            raise AppError(404, "not_found", "request not found")
        if req.status in ("pending", "rejected"):
            return ClaimResult(status=req.status, note=req.note)
        if req.status == "claimed":
            raise AppError(410, "already_claimed", "this activation was already used")
        # approved -> create the device session exactly once
        if req.decided_at and self._clock() - req.decided_at > timedelta(hours=settings.activation_poll_hours):
            raise AppError(410, "expired", "approval expired, send a new request")
        assert req.user_id is not None
        user = await self._s.get(User, req.user_id)
        if user is None or not user.is_active:
            raise AppError(403, "user_blocked", "this account is stopped")
        family = uuid.uuid4()
        old = (await self._s.execute(select(Device).where(Device.device_id == req.device_id))).scalar_one_or_none()
        if old is not None:   # a previously revoked phone being re-approved: reuse its row
            old.user_id, old.label, old.family_id, old.status, old.revoked_at = user.id, req.device_label, family, "active", None
        else:
            self._s.add(Device(user_id=user.id, device_id=req.device_id, label=req.device_label, family_id=family))
        tokens = await AuthService(self._s, clock=self._clock)._issue(user, family_id=family)
        req.status = "claimed"
        await self._s.commit()
        return ClaimResult(status="approved", tokens=tokens)

    # ---------------- owner side ----------------
    async def list_requests(self, status: str = "pending") -> list[ActivationRequest]:
        return list((await self._s.scalars(select(ActivationRequest).where(ActivationRequest.status == status).order_by(ActivationRequest.created_at.desc()).limit(200))).all())

    async def approve(self, request_id: uuid.UUID, daily_limit: int | None = None) -> User:
        req = await self._s.get(ActivationRequest, request_id, with_for_update=True)
        if req is None or req.status != "pending":
            raise AppError(404, "not_found", "no pending request with this id")
        user = (await self._s.execute(select(User).where(User.phone == req.phone))).scalar_one_or_none()
        if user is None:
            user = User(email=req.email if req.email and not (await self._s.execute(select(User).where(User.email == req.email))).scalar_one_or_none() else f"{req.phone}@device.nasem",
                        password_hash=await hash_password(secrets.token_urlsafe(24)),
                        display_name=req.name, phone=req.phone, daily_limit=daily_limit)
            self._s.add(user)
            await self._s.flush()
        else:
            if not user.is_active:
                raise AppError(409, "user_stopped", "this user is stopped; turn them back on first, then approve")
            if daily_limit is not None:
                user.daily_limit = daily_limit
            active = await self._s.scalar(select(func.count()).select_from(Device).where(Device.user_id == user.id, Device.status == "active"))
            if (active or 0) >= settings.max_devices_per_user:
                raise AppError(409, "device_limit", f"this user already has {active} active devices; revoke one first")
        req.status, req.user_id, req.decided_at = "approved", user.id, self._clock()
        await self._s.commit()
        return user

    async def reject(self, request_id: uuid.UUID, note: str | None = None) -> None:
        req = await self._s.get(ActivationRequest, request_id)
        if req is None or req.status != "pending":
            raise AppError(404, "not_found", "no pending request with this id")
        req.status, req.note, req.decided_at = "rejected", (note or None), self._clock()
        await self._s.commit()

    async def users(self) -> list[dict[str, Any]]:
        today = self._clock().date()
        users = (await self._s.scalars(select(User).where(User.is_admin.is_(False)).order_by(User.created_at.desc()).limit(500))).all()
        out = []
        for u in users:
            devices = (await self._s.scalars(select(Device).where(Device.user_id == u.id).order_by(Device.created_at))).all()
            used = await self._s.scalar(select(func.coalesce(func.sum(GatewayUsage.requests), 0)).where(GatewayUsage.user_id == u.id, GatewayUsage.day == today))
            out.append({"id": str(u.id), "name": u.display_name, "phone": u.phone, "active": u.is_active,
                        "daily_limit": u.daily_limit, "used_today": int(used or 0),
                        "devices": [{"id": str(d.id), "label": d.label, "status": d.status, "since": d.created_at.isoformat()} for d in devices]})
        return out

    async def set_user(self, user_id: uuid.UUID, *, active: bool | None = None, daily_limit: int | None = None, clear_limit: bool = False) -> None:
        user = await self._s.get(User, user_id)
        if user is None or user.is_admin:
            raise AppError(404, "not_found", "user not found")
        if active is not None:
            user.is_active = active
            if not active:   # stop every session at once
                await self._s.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=self._clock()))
        if clear_limit:
            user.daily_limit = None
        elif daily_limit is not None:
            user.daily_limit = daily_limit
        await self._s.commit()

    async def revoke_device(self, device_pk: uuid.UUID) -> None:
        d = await self._s.get(Device, device_pk)
        if d is None:
            raise AppError(404, "not_found", "device not found")
        now = self._clock()
        d.status, d.revoked_at = "revoked", now
        await self._s.execute(update(RefreshToken).where(RefreshToken.family_id == d.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        await self._s.commit()

    async def stats(self) -> dict[str, int]:
        today = self._clock().date()
        return {
            "users": int(await self._s.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(False))) or 0),
            "active_devices": int(await self._s.scalar(select(func.count()).select_from(Device).where(Device.status == "active")) or 0),
            "pending": int(await self._s.scalar(select(func.count()).select_from(ActivationRequest).where(ActivationRequest.status == "pending")) or 0),
            "requests_today": int(await self._s.scalar(select(func.coalesce(func.sum(GatewayUsage.requests), 0)).where(GatewayUsage.day == today)) or 0),
        }


async def ensure_admin(session: AsyncSession) -> None:
    """Create or update the owner account from ADMIN_EMAIL / ADMIN_PASSWORD at startup."""
    if not settings.admin_email or settings.admin_password is None:
        return
    email = settings.admin_email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    pw = await hash_password(settings.admin_password.get_secret_value())
    if user is None:
        session.add(User(email=email, password_hash=pw, display_name="Owner", is_admin=True))
    else:
        user.is_admin, user.is_active, user.password_hash = True, True, pw
    await session.commit()
