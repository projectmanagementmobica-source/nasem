from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.gateway.images import ImageProvider, default_image_providers
from app.modules.gateway.providers import Provider, default_providers
from app.modules.gateway.schemas import CompleteRequest, CompleteResponse, ImageRequest, ImageResponse, TtsRequest
from app.modules.gateway.service import GatewayService

router = APIRouter(prefix="/v1/gateway", tags=["gateway"])
_STATUS = {"llm_unavailable": 503, "llm_output_invalid": 502, "invalid_input": 400, "database_error": 500}
_providers: dict[str, Provider] | None = None
_image_providers: dict[str, ImageProvider] | None = None


def get_image_providers() -> dict[str, ImageProvider]:
    global _image_providers
    if _image_providers is None:
        _image_providers = default_image_providers()
    return _image_providers


def get_providers() -> dict[str, Provider]:
    global _providers
    if _providers is None:
        _providers = default_providers()
    return _providers


@router.post("/complete", response_model=CompleteResponse)
async def complete(
    body: CompleteRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    providers: dict[str, Provider] = Depends(get_providers),
) -> CompleteResponse:
    """Masked prompt in, one JSON object out. Nothing is stored except a daily request counter."""
    result = await GatewayService(session, providers).complete(user.id, body, user.daily_limit)
    if result.error is not None:
        response.status_code = 429 if "daily limit" in result.error.message else _STATUS[result.error.code]
    return result


@router.post("/image", response_model=ImageResponse)
async def image(
    body: ImageRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    providers: dict[str, Provider] = Depends(get_providers),
    image_providers: dict[str, ImageProvider] = Depends(get_image_providers),
) -> ImageResponse:
    """A picture WITHOUT text (the app draws Arabic text itself). Separate, lower daily limit."""
    result = await GatewayService(session, providers).image(user.id, body, image_providers)
    if result.error is not None:
        response.status_code = 429 if "daily" in result.error.message else 503
    return result


@router.post("/tts", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def tts(
    body: TtsRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    providers: dict[str, Provider] = Depends(get_providers),
) -> Response:
    """Egyptian (Salma / Shakir) or English neural voice. The Azure key stays on the server."""
    audio = await GatewayService(session, providers).tts(user.id, body)
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
