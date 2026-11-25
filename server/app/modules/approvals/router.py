from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.approvals.schemas import (
    ApprovalActionOut,
    ApprovalListResponse,
    CommitmentOut,
    DecisionRequest,
    DecisionResponse,
    MemoryOut,
)
from app.modules.approvals.service import ApprovalError, ApprovalService, DecisionOutcome
from app.shared.api import AppError, ErrorResponse
from app.shared.enums import ApprovalStatus

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

_HTTP = {"not_found": 404, "already_decided": 409, "conflict": 409}
_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


def _to_response(outcome: DecisionOutcome) -> DecisionResponse:
    memory = outcome.memory
    commitment = outcome.commitment
    return DecisionResponse(
        action=ApprovalActionOut.model_validate(outcome.action),
        memory=(
            MemoryOut(
                memory_id=memory.id,
                entity_name=memory.entity_name,
                attribute_key=memory.attribute_key,
                attribute_value=memory.attribute_value,
                confidence_score=memory.confidence_score,
                last_confirmed_at=memory.last_confirmed_at,
                revision_number=outcome.revision_number,
            )
            if memory is not None
            else None
        ),
        commitment=(
            CommitmentOut(
                commitment_id=commitment.id,
                title=commitment.title,
                status=commitment.status,
                due_date=commitment.due_date,
                requires_approval=commitment.requires_approval,
                approved_at=commitment.approved_at,
            )
            if commitment is not None
            else None
        ),
    )


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(
    status: ApprovalStatus | None = ApprovalStatus.PENDING,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApprovalListResponse:
    actions = await ApprovalService(session).list_actions(user.id, status, limit)
    return ApprovalListResponse(items=[ApprovalActionOut.model_validate(a) for a in actions])


@router.post("/{action_id}/approve", response_model=DecisionResponse, responses=_ERRORS)
async def approve(
    action_id: uuid.UUID,
    body: DecisionRequest | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DecisionResponse:
    try:
        outcome = await ApprovalService(session).approve(action_id, user.id)
    except ApprovalError as exc:
        raise AppError(_HTTP[exc.code], exc.code, exc.message) from exc
    return _to_response(outcome)


@router.post("/{action_id}/reject", response_model=DecisionResponse, responses=_ERRORS)
async def reject(
    action_id: uuid.UUID,
    body: DecisionRequest | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DecisionResponse:
    try:
        outcome = await ApprovalService(session).reject(
            action_id, user.id, body.reason if body else None
        )
    except ApprovalError as exc:
        raise AppError(_HTTP[exc.code], exc.code, exc.message) from exc
    return _to_response(outcome)
