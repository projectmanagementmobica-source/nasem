"""Schemas for (1) what the LLM must return and (2) what the orchestrator returns to the API."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shared.enums import CommitmentStatus, MemoryCategory

_NON_WORD_RE = re.compile(r"[^\w]+")


def normalize_entity_name(value: str) -> str:
    """'  My   Mother ' -> 'my mother'. Keeps Arabic as is (lower() is a no-op there)."""
    return " ".join(value.split()).lower()


def normalize_attribute_key(value: str) -> str:
    """'Favorite Drink!' -> 'favorite_drink'."""
    return _NON_WORD_RE.sub("_", value.strip().lower()).strip("_")


# ---------------------------------------------------------------------------
# 1) LLM output contract. Lenient on extra keys (ignored), strict on values.
# ---------------------------------------------------------------------------
class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ExtractedMemory(_LLMModel):
    category: MemoryCategory
    entity_name: str = Field(min_length=1, max_length=255)
    attribute_key: str = Field(min_length=1, max_length=255)
    attribute_value: str = Field(min_length=1, max_length=2000)
    confidence_score: float = Field(ge=0.0, le=1.0)

    @field_validator("entity_name")
    @classmethod
    def _normalize_entity(cls, value: str) -> str:
        return normalize_entity_name(value)

    @field_validator("attribute_key")
    @classmethod
    def _normalize_key(cls, value: str) -> str:
        key = normalize_attribute_key(value)
        if not key:
            raise ValueError("attribute_key is empty after normalization")
        return key


class ExtractedCommitment(_LLMModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    due_date: datetime | None = None
    requires_approval: Literal[True] = True

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _force_approval(cls, _value: object) -> Literal[True]:
        # Rule 1: whatever the model says, a commitment is never auto-approved.
        return True


class GuideRequest(_LLMModel):
    question: str = Field(min_length=1, max_length=1000)


class LLMExtraction(_LLMModel):
    """Full contract. Used to build the tool JSON schema sent to the model."""

    extracted_memories: list[ExtractedMemory] = Field(default_factory=list)
    extracted_commitments: list[ExtractedCommitment] = Field(default_factory=list)
    agent_reply: str = Field(min_length=1, max_length=1000)
    guide_request: GuideRequest | None = None


# ---------------------------------------------------------------------------
# 2) API response contract (Rule 4: strict JSON, no unknown keys).
# ---------------------------------------------------------------------------
class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ErrorCode = Literal["invalid_input", "llm_unavailable", "llm_output_invalid", "database_error"]

WarningCode = Literal[
    "memory_item_invalid",
    "commitment_item_invalid",
    "memory_low_confidence",
    "memory_duplicate_in_input",
    "commitment_due_date_in_past",
    "llm_output_repaired",
    "web_search_unavailable",
    "sources_dropped",
    "guide_commitment_invalid",
    "ai_summary_unavailable",
]


class ProcessingWarning(_ResponseModel):
    code: WarningCode
    message: str


class ProcessingError(_ResponseModel):
    code: ErrorCode
    message: str
    retryable: bool


class MemoryResult(_ResponseModel):
    memory_id: uuid.UUID
    action: Literal["created", "confirmed"]
    category: MemoryCategory
    entity_name: str
    attribute_key: str
    attribute_value: str
    confidence_score: float
    last_confirmed_at: datetime


class PendingMemoryUpdate(_ResponseModel):
    """A change to an existing fact. NOT applied: waits for explicit user approval (Rule 1)."""

    action_id: uuid.UUID
    memory_id: uuid.UUID
    category: MemoryCategory
    entity_name: str
    attribute_key: str
    current_value: str
    proposed_value: str
    confidence_score: float
    requires_approval: Literal[True] = True


class CommitmentResult(_ResponseModel):
    commitment_id: uuid.UUID
    approval_action_id: uuid.UUID
    title: str
    description: str | None
    due_date: datetime | None
    status: CommitmentStatus
    requires_approval: Literal[True] = True


class ProcessInputResponse(_ResponseModel):
    request_id: uuid.UUID
    status: Literal["success", "error"]
    agent_reply: str
    requires_user_confirmation: bool
    memories: list[MemoryResult] = Field(default_factory=list)
    pending_memory_updates: list[PendingMemoryUpdate] = Field(default_factory=list)
    commitments: list[CommitmentResult] = Field(default_factory=list)
    warnings: list[ProcessingWarning] = Field(default_factory=list)
    guide_request: GuideRequest | None = None  # client then calls POST /v1/guides
    privacy_hidden_count: int = 0  # personal details masked before the text left the server
    error: ProcessingError | None = None
    model: str | None = None
    prompt_version: str
