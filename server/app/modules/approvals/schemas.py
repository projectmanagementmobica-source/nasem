from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import ApprovalActionType, ApprovalStatus, CommitmentStatus


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DecisionRequest(_Model):
    reason: str | None = Field(default=None, max_length=500)


class ApprovalActionOut(_Model):
    action_id: uuid.UUID = Field(validation_alias="id")
    action_type: ApprovalActionType
    target_id: uuid.UUID
    status: ApprovalStatus
    payload: dict[str, Any]
    created_at: datetime
    decided_at: datetime | None
    decision_reason: str | None


class ApprovalListResponse(_Model):
    items: list[ApprovalActionOut]


class MemoryOut(_Model):
    memory_id: uuid.UUID
    entity_name: str
    attribute_key: str
    attribute_value: str
    confidence_score: float
    last_confirmed_at: datetime
    revision_number: int | None  # revision created by this decision, if any


class CommitmentOut(_Model):
    commitment_id: uuid.UUID
    title: str
    status: CommitmentStatus
    due_date: datetime | None
    requires_approval: bool
    approved_at: datetime | None


class DecisionResponse(_Model):
    action: ApprovalActionOut
    memory: MemoryOut | None = None
    commitment: CommitmentOut | None = None
