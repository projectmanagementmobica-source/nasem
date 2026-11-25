from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.orchestrator.llm_client import LLMClient, build_llm_client
from app.modules.orchestrator.schemas import ErrorCode, ProcessInputResponse
from app.modules.orchestrator.service import AiOrchestratorService
from app.shared.api import AppError
from app.shared.enums import SourceType

router = APIRouter(prefix="/v1/orchestrator", tags=["orchestrator"])

_HTTP_STATUS: dict[ErrorCode, int] = {
    "invalid_input": 400,
    "llm_unavailable": 503,
    "llm_output_invalid": 502,
    "database_error": 500,
}


class ProcessInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_text: str = Field(min_length=1, max_length=settings.max_input_chars)
    source_type: SourceType = SourceType.TEXT
    user_timezone: str | None = Field(
        default=None, examples=["Africa/Cairo"], description="Overrides the profile timezone"
    )


@lru_cache(maxsize=1)
def _cached_llm_client() -> LLMClient:
    return build_llm_client()


def get_llm_client() -> LLMClient:
    try:
        return _cached_llm_client()
    except RuntimeError as exc:  # e.g. ANTHROPIC_API_KEY missing
        raise AppError(503, "llm_not_configured", str(exc)) from exc


@router.post("/process", response_model=ProcessInputResponse)
async def process_input(
    body: ProcessInputRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
) -> ProcessInputResponse:
    result = await AiOrchestratorService(session, llm).process_user_input(
        str(user.id),
        body.input_text,
        source_type=body.source_type,
        user_timezone=body.user_timezone or user.timezone,
    )
    if result.error is not None:
        response.status_code = _HTTP_STATUS[result.error.code]
    return result
