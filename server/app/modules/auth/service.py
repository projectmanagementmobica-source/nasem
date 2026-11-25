"""Register / login / refresh (with rotation + reuse detection) / logout."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401  (registers every mapper)
from app.core.config import settings
from app.modules.auth.models import RefreshToken, User
from app.modules.auth.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.shared.api import AppError

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


@dataclass
class IssuedTokens:
    user: User
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    refresh_token_id: uuid.UUID


class AuthService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def register(
        self, email: str, password: str, display_name: str | None, timezone: str | None
    ) -> IssuedTokens:
        if await self._user_by_email(email) is not None:
            raise AppError(409, "email_taken", "an account with this email already exists")
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=await hash_password(password),
            display_name=display_name,
            timezone=timezone,
            is_active=True,
        )
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # concurrent registration with the same email
            await self._session.rollback()
            raise AppError(409, "email_taken", "an account with this email already exists") from exc
        tokens = await self._issue(user, family_id=uuid.uuid4())
        await self._session.commit()
        await self._session.refresh(user)
        return tokens

    async def login(self, email: str, password: str) -> IssuedTokens:
        user = await self._user_by_email(email)
        ok = await verify_password(password, user.password_hash if user else None)
        if user is None or not ok or not user.is_active:
            raise AppError(401, "invalid_credentials", "email or password is incorrect")
        user.last_login_at = self._clock()
        tokens = await self._issue(user, family_id=uuid.uuid4())
        await self._session.commit()
        return tokens

    async def refresh(self, raw_token: str) -> IssuedTokens:
        now = self._clock()
        row = await self._token_for_update(raw_token)
        if row is None:
            raise AppError(401, "invalid_token", "refresh token is invalid", headers=_UNAUTHORIZED)
        if row.revoked_at is not None:
            # A rotated token came back -> likely stolen. Kill every session in this family.
            await self._revoke_family(row.family_id, now)
            await self._session.commit()
            raise AppError(
                401, "token_reused", "refresh token reuse detected; please log in again",
                headers=_UNAUTHORIZED,
            )
        if row.expires_at <= now:
            raise AppError(401, "token_expired", "refresh token expired", headers=_UNAUTHORIZED)
        user = await self._session.get(User, row.user_id)
        if user is None or not user.is_active:
            raise AppError(401, "invalid_token", "user not found or inactive", headers=_UNAUTHORIZED)

        tokens = await self._issue(user, family_id=row.family_id)
        row.revoked_at = now
        row.replaced_by_id = tokens.refresh_token_id
        await self._session.commit()
        return tokens

    async def logout(self, raw_token: str) -> None:
        """Idempotent: unknown tokens are ignored (no oracle for attackers)."""
        row = await self._token_for_update(raw_token)
        if row is not None:
            await self._revoke_family(row.family_id, self._clock())
            await self._session.commit()

    # ------------------------------------------------------------------
    async def _user_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _token_for_update(self, raw_token: str) -> RefreshToken | None:
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def _issue(self, user: User, *, family_id: uuid.UUID) -> IssuedTokens:
        now = self._clock()
        access, access_ttl = create_access_token(user.id, now)
        raw, token_hash = new_refresh_token()
        refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)
        row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=now + refresh_ttl,
        )
        self._session.add(row)
        await self._session.flush()
        return IssuedTokens(
            user=user,
            access_token=access,
            expires_in=access_ttl,
            refresh_token=raw,
            refresh_expires_in=int(refresh_ttl.total_seconds()),
            refresh_token_id=row.id,
        )
