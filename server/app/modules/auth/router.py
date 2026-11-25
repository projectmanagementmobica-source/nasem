from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    LogoutResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.modules.auth.service import AuthService, IssuedTokens
from app.shared.api import ErrorResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])
_ERR: dict[int | str, dict[str, object]] = {401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}


def _tokens(t: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=t.access_token,
        expires_in=t.expires_in,
        refresh_token=t.refresh_token,
        refresh_expires_in=t.refresh_expires_in,
        user=UserOut.model_validate(t.user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, responses=_ERR)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    return _tokens(
        await AuthService(session).register(body.email, body.password, body.display_name, body.timezone)
    )


@router.post("/login", response_model=TokenResponse, responses=_ERR)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    return _tokens(await AuthService(session).login(body.email, body.password))


@router.post("/refresh", response_model=TokenResponse, responses=_ERR)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    return _tokens(await AuthService(session).refresh(body.refresh_token))


@router.post("/logout", response_model=LogoutResponse)
async def logout(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> LogoutResponse:
    await AuthService(session).logout(body.refresh_token)
    return LogoutResponse()


@router.get("/me", response_model=UserOut, responses=_ERR)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
