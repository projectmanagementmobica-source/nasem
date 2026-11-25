"""Async engine + session factory + FastAPI dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  (registers every mapper before first use)
from app.core.config import settings

# Neon's "-pooler" host is PgBouncer in transaction mode: asyncpg's prepared-statement cache must be off there.
_connect_args = {"statement_cache_size": 0} if "-pooler." in settings.database_url else {}
engine = create_async_engine(settings.database_url, echo=settings.db_echo, pool_pre_ping=True, connect_args=_connect_args)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
