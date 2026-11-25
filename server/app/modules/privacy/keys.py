"""Per-user data keys: create on first use, load, and shred."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import CryptoError, KeyProvider, UserCipher, new_data_key
from app.db.base import Base


class UserKey(Base):
    __tablename__ = "user_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # NULL = shredded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    shredded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KeyService:
    def __init__(self, session: AsyncSession, provider: KeyProvider) -> None:
        self._session = session
        self._provider = provider

    async def cipher_for(self, user_id: uuid.UUID, *, create: bool = True) -> UserCipher:
        row = await self._session.get(UserKey, user_id, with_for_update=create)
        if row is None:
            if not create:
                raise CryptoError("no data key for user")
            dek = new_data_key()
            self._session.add(UserKey(user_id=user_id, wrapped_key=self._provider.wrap(dek, user_id=user_id)))
            await self._session.flush()
            return UserCipher(user_id, dek)
        if row.wrapped_key is None:
            raise CryptoError("user data key was shredded")
        return UserCipher(user_id, self._provider.unwrap(row.wrapped_key, user_id=user_id))

    async def shred(self, user_id: uuid.UUID) -> None:
        """Irreversible: every value sealed for this user becomes unreadable, backups included."""
        row = await self._session.get(UserKey, user_id, with_for_update=True)
        if row is not None and row.wrapped_key is not None:
            row.wrapped_key = None
            row.shredded_at = datetime.now(UTC)
            await self._session.flush()

    async def is_shredded(self, user_id: uuid.UUID) -> bool:
        wrapped = await self._session.scalar(select(UserKey.wrapped_key).where(UserKey.user_id == user_id))
        return wrapped is None and await self._session.get(UserKey, user_id) is not None
