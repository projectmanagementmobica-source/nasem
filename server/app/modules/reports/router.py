"""Summaries (JSON) and print-ready files (PDF / Excel)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.guides.schemas import GuideOut
from app.modules.orchestrator.llm_client import LLMClient
from app.modules.orchestrator.router import _cached_llm_client
from app.modules.orchestrator.schemas import ProcessingWarning
from app.modules.orchestrator.service import _resolve_timezone
from app.modules.reports.data import Period, ReportData, collect_report_data
from app.modules.reports.pdf import build_guide_pdf, build_report_pdf
from app.modules.reports.summary import ReportSummary, generate_summary
from app.modules.reports.xlsx import build_report_xlsx

router = APIRouter(prefix="/v1/reports", tags=["reports"])

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def get_optional_llm() -> LLMClient | None:
    """Files must work without an LLM key; only the AI narrative needs one."""
    try:
        return _cached_llm_client()
    except RuntimeError:
        return None


class SummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Literal["week", "month"]
    start: datetime
    end: datetime
    stats: dict[str, int]
    ai_summary: ReportSummary | None
    warnings: list[ProcessingWarning]


def _file(content: bytes, media_type: str, stem: str, ext: str) -> Response:
    name = f"{stem}-{datetime.now(UTC):%Y-%m-%d}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


async def _data(
    session: AsyncSession, user: User, period: Period, include_sensitive: bool
) -> ReportData:
    tz = _resolve_timezone(user.timezone)
    return await collect_report_data(
        session, user, tz=tz, now=datetime.now(UTC).astimezone(tz), period=period,
        include_sensitive=include_sensitive,
    )


@router.get("/summary", response_model=SummaryResponse)
async def summary(
    period: Period = "week",
    ai: bool = True,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    llm: LLMClient | None = Depends(get_optional_llm),
) -> SummaryResponse:
    data = await _data(session, user, period, False)
    ai_summary = await generate_summary(llm, data) if ai else None
    warnings = (
        [ProcessingWarning(code="ai_summary_unavailable", message="AI narrative could not be generated")]
        if ai and ai_summary is None else []
    )
    return SummaryResponse(period=period, start=data.start, end=data.end, stats=data.stats,
                           ai_summary=ai_summary, warnings=warnings)


@router.get("/report.pdf", response_class=Response, responses={200: {"content": {PDF: {}}}})
async def report_pdf(
    period: Period = "week",
    ai: bool = True,
    include_sensitive: bool = Query(default=False, description="Include health/finance memories"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    llm: LLMClient | None = Depends(get_optional_llm),
) -> Response:
    data = await _data(session, user, period, include_sensitive)
    ai_summary = await generate_summary(llm, data) if ai else None
    return _file(await build_report_pdf(data, ai_summary), PDF, f"nasem-report-{period}", "pdf")


@router.get("/report.xlsx", response_class=Response, responses={200: {"content": {XLSX: {}}}})
async def report_xlsx(
    period: Period = "week",
    include_sensitive: bool = Query(default=False, description="Include health/finance memories"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    data = await _data(session, user, period, include_sensitive)
    return _file(build_report_xlsx(data), XLSX, f"nasem-report-{period}", "xlsx")


@router.post("/guide.pdf", response_class=Response, responses={200: {"content": {PDF: {}}}})
async def guide_pdf(guide: GuideOut, user: User = Depends(get_current_user)) -> Response:
    """Print a guide returned by POST /v1/guides (send its `guide` object back as the body)."""
    return _file(await build_guide_pdf(guide), PDF, "nasem-guide", "pdf")
