from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.guides.schemas import GuideCreateRequest, GuideResponse
from app.modules.guides.service import GuideService
from app.modules.orchestrator.llm_client import GuideLLM, build_llm_client
from app.modules.orchestrator.schemas import ErrorCode
from app.shared.api import AppError

router = APIRouter(prefix="/v1/guides", tags=["guides"])

_HTTP_STATUS: dict[ErrorCode, int] = {
    "invalid_input": 400,
    "llm_unavailable": 503,
    "llm_output_invalid": 502,
    "database_error": 500,
}


@lru_cache(maxsize=1)
def _cached_guide_llm() -> GuideLLM:
    return build_llm_client()


def get_guide_llm() -> GuideLLM:
    try:
        return _cached_guide_llm()
    except RuntimeError as exc:
        raise AppError(503, "llm_not_configured", str(exc)) from exc


@router.post("", response_model=GuideResponse)
async def create_guide(
    body: GuideCreateRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    llm: GuideLLM = Depends(get_guide_llm),
) -> GuideResponse:
    """Research a need and return a decision-ready guide. Can take 30-90 s with web search."""
    result = await GuideService(session, llm).create_guide(
        user, body.question, use_web_search=body.use_web_search
    )
    if result.error is not None:
        response.status_code = _HTTP_STATUS[result.error.code]
    return result
