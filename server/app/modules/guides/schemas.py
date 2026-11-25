"""Guide contract: what the model must record, and what the API returns (Rule 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.orchestrator.schemas import ProcessingError, ProcessingWarning


# ---------------------------------------------------------------------------
# 1) Model output (lenient on extra keys, strict on values; long lists trimmed)
# ---------------------------------------------------------------------------
class _LLM(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _trim(limit: int) -> Any:
    def _cut(cls: type, value: object) -> object:
        return value[:limit] if isinstance(value, list) else value

    return classmethod(_cut)


class GuidePick(_LLM):
    option: str = Field(min_length=1, max_length=300)
    why: str | None = Field(default=None, max_length=600)


class GuidePlace(_LLM):
    place: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=400)


class GuideOption(_LLM):
    name: str = Field(min_length=1, max_length=200)
    pros: str | None = Field(default=None, max_length=400)
    cons: str | None = Field(default=None, max_length=400)
    cost: str | None = Field(default=None, max_length=120)


class GuideWho(_LLM):
    name: str = Field(min_length=1, max_length=200)
    why: str | None = Field(default=None, max_length=400)


class GuideDraft(_LLM):
    to: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=2000)


class GuideCommitmentSuggestion(_LLM):
    title: str = Field(min_length=1, max_length=255)
    due_date: datetime | None = None


class GuideSource(_LLM):
    title: str | None = Field(default=None, max_length=300)
    url: str = Field(min_length=8, max_length=2000)


class GuideContent(_LLM):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=600)
    best: GuidePick | None = None
    easiest: GuidePick | None = None
    steps: list[str] = Field(default_factory=list)
    where: list[GuidePlace] = Field(default_factory=list)
    options: list[GuideOption] = Field(default_factory=list)
    who: list[GuideWho] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    check_before: str | None = Field(default=None, max_length=600)
    draft_message: GuideDraft | None = None
    suggested_commitment: GuideCommitmentSuggestion | None = None
    sources: list[GuideSource] = Field(default_factory=list)

    _t_steps = field_validator("steps", mode="before")(_trim(7))
    _t_where = field_validator("where", mode="before")(_trim(4))
    _t_options = field_validator("options", mode="before")(_trim(4))
    _t_who = field_validator("who", mode="before")(_trim(3))
    _t_tips = field_validator("tips", mode="before")(_trim(3))
    _t_sources = field_validator("sources", mode="before")(_trim(8))


# ---------------------------------------------------------------------------
# 2) API
# ---------------------------------------------------------------------------
class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuideCreateRequest(_Out):
    question: str = Field(min_length=1, max_length=1000)
    use_web_search: bool = True


class SuggestedCommitmentOut(_Out):
    commitment_id: uuid.UUID
    approval_action_id: uuid.UUID
    title: str
    due_date: datetime | None
    requires_approval: Literal[True] = True


class GuideSourceOut(_Out):
    title: str | None
    url: str


class GuideOut(_Out):
    title: str
    summary: str
    best: GuidePick | None
    easiest: GuidePick | None
    steps: list[str]
    where: list[GuidePlace]
    options: list[GuideOption]
    who: list[GuideWho]
    tips: list[str]
    check_before: str | None
    draft_message: GuideDraft | None
    suggested_commitment: SuggestedCommitmentOut | None
    sources: list[GuideSourceOut]
    grounded: bool  # True = answer used live web search results
    search_count: int
    generated_at: datetime


class GuideResponse(_Out):
    request_id: uuid.UUID
    status: Literal["success", "error"]
    guide: GuideOut | None = None
    warnings: list[ProcessingWarning] = Field(default_factory=list)
    error: ProcessingError | None = None
    model: str | None = None
    prompt_version: str
