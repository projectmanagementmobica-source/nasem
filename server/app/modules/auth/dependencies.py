from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.models import User
from app.modules.auth.security import TokenError, decode_access_token
from app.shared.api import AppError

_bearer = HTTPBearer(auto_error=False)  # adds the "Authorize" button in /docs
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "not_authenticated", "missing bearer token", headers=_CHALLENGE)
    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise AppError(401, exc.code, "access token is invalid or expired", headers=_CHALLENGE) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(401, "invalid_token", "user not found or inactive", headers=_CHALLENGE)
    return user
