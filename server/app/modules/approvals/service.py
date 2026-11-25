"""Approve / reject pending actions. The ONLY place where agent-proposed writes are executed.

- Row locks (SELECT ... FOR UPDATE) prevent double execution on concurrent clicks.
- Memory updates are optimistic: if the memory changed since the proposal, the action is
  superseded instead of overwriting newer data.
- Rule 3: original_metadata is never touched; the old value + metadata go to memory_revisions,
  and last_confirmed_at is updated.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, NoReturn

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401  (registers every mapper)
from app.modules.approvals.models import PendingAction
from app.modules.commitments.models import Commitment
from app.modules.memory.models import Memory, MemoryRevision
from app.shared.enums import ApprovalActionType, ApprovalStatus, CommitmentStatus, RevisionActor

ApprovalErrorCode = Literal["not_found", "already_decided", "conflict"]


class ApprovalError(Exception):
    def __init__(self, code: ApprovalErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ApprovalErrorCode = code
        self.message = message


@dataclass
class DecisionOutcome:
    action: PendingAction
    memory: Memory | None = None
    revision_number: int | None = None
    commitment: Commitment | None = None


class ApprovalService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def list_actions(
        self, user_id: uuid.UUID, status: ApprovalStatus | None, limit: int
    ) -> list[PendingAction]:
        stmt = select(PendingAction).where(PendingAction.user_id == user_id)
        if status is not None:
            stmt = stmt.where(PendingAction.status == status)
        stmt = stmt.order_by(PendingAction.created_at.desc()).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    # ------------------------------------------------------------------
    async def approve(self, action_id: uuid.UUID, user_id: uuid.UUID) -> DecisionOutcome:
        action = await self._lock_open_action(action_id, user_id)
        now = self._clock()
        if action.action_type == ApprovalActionType.UPDATE_MEMORY:
            outcome = await self._apply_memory_update(action, now)
        else:
            outcome = await self._approve_commitment(action, now)
        self._close(action, ApprovalStatus.APPROVED, now, None)
        await self._session.commit()
        return outcome

    async def reject(
        self, action_id: uuid.UUID, user_id: uuid.UUID, reason: str | None
    ) -> DecisionOutcome:
        action = await self._lock_open_action(action_id, user_id)
        now = self._clock()
        outcome = DecisionOutcome(action=action)
        if action.action_type == ApprovalActionType.APPROVE_COMMITMENT:
            commitment = await self._session.get(Commitment, action.target_id, with_for_update=True)
            if commitment is not None and commitment.status == CommitmentStatus.PENDING:
                commitment.status = CommitmentStatus.CANCELLED
            outcome.commitment = commitment
        # update_memory rejected -> memory is simply left as is.
        self._close(action, ApprovalStatus.REJECTED, now, reason)
        await self._session.commit()
        return outcome

    # ------------------------------------------------------------------
    async def _lock_open_action(self, action_id: uuid.UUID, user_id: uuid.UUID) -> PendingAction:
        stmt = select(PendingAction).where(PendingAction.id == action_id).with_for_update()
        action = (await self._session.execute(stmt)).scalar_one_or_none()
        if action is None or action.user_id != user_id:  # never reveal other users' actions
            raise ApprovalError("not_found", "approval action not found")
        if action.status != ApprovalStatus.PENDING:
            raise ApprovalError("already_decided", f"action is already {action.status.value}")
        return action

    async def _supersede_and_fail(
        self, action: PendingAction, now: datetime, reason: str
    ) -> NoReturn:
        self._close(action, ApprovalStatus.SUPERSEDED, now, reason)
        await self._session.commit()
        raise ApprovalError("conflict", reason)

    async def _apply_memory_update(self, action: PendingAction, now: datetime) -> DecisionOutcome:
        memory = await self._session.get(Memory, action.target_id, with_for_update=True)
        if memory is None or not memory.is_active:
            await self._supersede_and_fail(action, now, "memory no longer exists or is inactive")
        if memory.attribute_value != action.payload.get("current_value"):
            await self._supersede_and_fail(action, now, "memory changed since this proposal")

        last = await self._session.scalar(
            select(func.coalesce(func.max(MemoryRevision.revision_number), 0)).where(
                MemoryRevision.memory_id == memory.id
            )
        )
        revision_number = int(last or 0) + 1
        self._session.add(
            MemoryRevision(
                memory_id=memory.id,
                revision_number=revision_number,
                previous_value=memory.attribute_value,
                new_value=str(action.payload["proposed_value"]),
                previous_metadata={
                    "confidence_score": memory.confidence_score,
                    "source_type": memory.source_type.value,
                    "privacy_level": memory.privacy_level.value,
                    "is_active": memory.is_active,
                    "last_confirmed_at": memory.last_confirmed_at.isoformat(),
                },
                changed_by=RevisionActor.USER,
                change_reason=f"approved pending action {action.id}",
            )
        )
        memory.attribute_value = str(action.payload["proposed_value"])
        memory.confidence_score = float(action.payload.get("confidence_score", memory.confidence_score))
        memory.last_confirmed_at = now
        # memory.original_metadata intentionally untouched (Rule 3)
        await self._session.flush()
        return DecisionOutcome(action=action, memory=memory, revision_number=revision_number)

    async def _approve_commitment(self, action: PendingAction, now: datetime) -> DecisionOutcome:
        commitment = await self._session.get(Commitment, action.target_id, with_for_update=True)
        if commitment is None or commitment.status != CommitmentStatus.PENDING:
            await self._supersede_and_fail(action, now, "commitment no longer pending")
        commitment.approved_at = now
        await self._session.flush()
        return DecisionOutcome(action=action, commitment=commitment)

    @staticmethod
    def _close(
        action: PendingAction, status: ApprovalStatus, now: datetime, reason: str | None
    ) -> None:
        action.status = status
        action.decided_at = now
        action.decision_reason = reason
