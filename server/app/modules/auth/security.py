"""Password hashing (argon2id) and token primitives. No DB access here."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings
from app.shared.api import AppError

_ALGORITHM = "HS256"
_hasher = PasswordHasher()  # argon2id with library defaults
# Verified against when the email doesn't exist -> same timing, no user enumeration.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


async def hash_password(password: str) -> str:
    # argon2 is CPU-heavy by design -> keep it off the event loop.
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    def _verify() -> bool:
        try:
            _hasher.verify(password_hash or _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return False
        return password_hash is not None

    return await asyncio.to_thread(_verify)


class TokenError(Exception):
    def __init__(self, code: Literal["token_expired", "invalid_token"]) -> None:
        super().__init__(code)
        self.code: Literal["token_expired", "invalid_token"] = code


def _secret() -> str:
    key = settings.jwt_secret_key
    if key is None or len(key.get_secret_value()) < 32:
        raise AppError(503, "auth_not_configured", "JWT_SECRET_KEY must be set (>= 32 characters)")
    return key.get_secret_value()


def create_access_token(user_id: uuid.UUID, now: datetime) -> tuple[str, int]:
    """Return (token, ttl_seconds)."""
    ttl = settings.access_token_ttl_minutes * 60
    issued_at = int(now.timestamp())
    claims = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + ttl,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "type": "access",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, _secret(), algorithm=_ALGORITHM), ttl


def decode_access_token(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],  # pinned: blocks "alg": "none" / algorithm confusion
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=10,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid_token") from exc
    if claims.get("type") != "access":
        raise TokenError("invalid_token")
    try:
        return uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise TokenError("invalid_token") from exc


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Return (raw token for the client, sha256 hash for the DB)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)
