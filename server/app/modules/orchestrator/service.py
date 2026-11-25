"""AI Orchestrator: user input -> LLM extraction -> validated, approval-safe persistence.

Write policy (Rules 1 & 3):
- New fact                         -> INSERT memory (original_metadata snapshot written once).
- Same fact restated               -> only last_confirmed_at is updated (no value change).
- Existing fact with a new value   -> memory NOT touched. A `pending_actions` row (update_memory)
                                      is created; an older open proposal for the same memory is superseded.
- Commitment                       -> INSERT with status=pending, requires_approval=true, approved_at=NULL
                                      + a `pending_actions` row (approve_commitment). Nothing is executed.
Everything is written in ONE transaction: either all of it is saved, or none of it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401  (registers every mapper; relationships resolve by name)
from app.core.config import settings
from app.modules.approvals.models import PendingAction
from app.modules.commitments.models import Commitment
from app.modules.memory import repository as memory_repo
from app.modules.memory.models import Memory
from app.modules.orchestrator.errors import LLMOutputError, LLMUnavailableError
from app.modules.orchestrator.llm_client import LLMClient
from app.modules.privacy.pseudonymizer import TOKEN_INSTRUCTION, Pseudonymizer
from app.modules.orchestrator.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_tool_schema,
    build_user_message,
)
from app.modules.orchestrator.schemas import (
    CommitmentResult,
    ErrorCode,
    ExtractedCommitment,
    ExtractedMemory,
    GuideRequest,
    MemoryResult,
    PendingMemoryUpdate,
    ProcessInputResponse,
    ProcessingError,
    ProcessingWarning,
)
from app.shared.enums import (
    ApprovalActionType,
    ApprovalStatus,
    CommitmentStatus,
    MemoryCategory,
    PrivacyLevel,
    SourceType,
)

logger = logging.getLogger(__name__)

_SENSITIVE_CATEGORIES = frozenset({MemoryCategory.HEALTH, MemoryCategory.FINANCE})

# (Arabic, English). Used whenever the LLM reply can't be trusted or doesn't exist.
_FALLBACK_REPLIES: dict[ErrorCode, tuple[str, str]] = {
    "invalid_input": (
        "ما وصلنيش كلام واضح، ممكن تعيد تاني؟",
        "I didn't get a clear message. Could you say it again?",
    ),
    "llm_unavailable": (
        "مش قادر أوصل للخدمة دلوقتي، جرّب تاني بعد شوية.",
        "I can't reach the service right now. Please try again shortly.",
    ),
    "llm_output_invalid": (
        "معلش، ما قدرتش أفهم الطلب ده. ممكن تقوله بطريقة تانية؟",
        "Sorry, I couldn't process that. Could you rephrase it?",
    ),
    "database_error": (
        "حصلت مشكلة في الحفظ وما اتحفظش أي حاجة. جرّب تاني.",
        "Something went wrong while saving, so nothing was saved. Please try again.",
    ),
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
@dataclass
class ParsedExtraction:
    agent_reply: str
    memories: list[ExtractedMemory] = field(default_factory=list)
    commitments: list[ExtractedCommitment] = field(default_factory=list)
    warnings: list[ProcessingWarning] = field(default_factory=list)
    guide_request: GuideRequest | None = None
    hidden_count: int = 0


def _summarize(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'item'}: {err['msg']}" for err in exc.errors()[:3]
    )


def parse_extraction(raw: dict[str, Any]) -> ParsedExtraction:
    """Top-level shape must be valid (else LLMOutputError -> re-ask the model).

    Individual invalid items are dropped with a warning instead of failing the whole request.
    """
    reply = raw.get("agent_reply")
    if not isinstance(reply, str) or not reply.strip():
        raise LLMOutputError("'agent_reply' is missing or empty")
    memories_raw = raw.get("extracted_memories", [])
    commitments_raw = raw.get("extracted_commitments", [])
    if not isinstance(memories_raw, list) or not isinstance(commitments_raw, list):
        raise LLMOutputError("'extracted_memories' and 'extracted_commitments' must be arrays")

    parsed = ParsedExtraction(agent_reply=reply.strip()[:1000])
    guide_raw = raw.get("guide_request")
    if guide_raw is not None:
        try:
            parsed.guide_request = GuideRequest.model_validate(guide_raw)
        except ValidationError:
            pass  # optional hint; a bad one is simply ignored
    for i, item in enumerate(memories_raw):
        try:
            parsed.memories.append(ExtractedMemory.model_validate(item))
        except ValidationError as exc:
            parsed.warnings.append(
                ProcessingWarning(
                    code="memory_item_invalid",
                    message=f"extracted_memories[{i}] dropped: {_summarize(exc)}",
                )
            )
    for i, item in enumerate(commitments_raw):
        try:
            parsed.commitments.append(ExtractedCommitment.model_validate(item))
        except ValidationError as exc:
            parsed.warnings.append(
                ProcessingWarning(
                    code="commitment_item_invalid",
                    message=f"extracted_commitments[{i}] dropped: {_summarize(exc)}",
                )
            )
    return parsed


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def _fallback_reply(code: ErrorCode, input_text: str) -> str:
    arabic, english = _FALLBACK_REPLIES[code]
    return arabic if _is_arabic(input_text) or not input_text else english


def _resolve_timezone(name: str | None) -> tzinfo:
    for candidate in (name, settings.default_timezone):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning("unknown timezone %r, falling back", candidate)
    return UTC


def _same_value(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _privacy_for(category: MemoryCategory) -> PrivacyLevel:
    return PrivacyLevel.SENSITIVE if category in _SENSITIVE_CATEGORIES else PrivacyLevel.PRIVATE


@dataclass
class _PersistOutcome:
    memories: list[MemoryResult] = field(default_factory=list)
    pending_updates: list[PendingMemoryUpdate] = field(default_factory=list)
    commitments: list[CommitmentResult] = field(default_factory=list)
    warnings: list[ProcessingWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class AiOrchestratorService:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMClient,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(UTC))

    async def process_user_input(
        self,
        user_id: str,
        input_text: str,
        *,
        source_type: SourceType = SourceType.TEXT,
        user_timezone: str | None = None,
    ) -> ProcessInputResponse:
        """Analyze one user utterance, persist what is safe to persist, and return a JSON payload.

        Never raises for expected failures: they come back as status="error" with an error code.
        """
        request_id = uuid.uuid4()
        text = input_text.strip()

        # 1) Validate input -------------------------------------------------
        try:
            uid = uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            return self._error(request_id, text, "invalid_input", "user_id is not a valid UUID")
        if not text:
            return self._error(request_id, text, "invalid_input", "input_text is empty")
        if len(text) > settings.max_input_chars:
            return self._error(
                request_id,
                text,
                "invalid_input",
                f"input_text exceeds {settings.max_input_chars} characters",
            )

        tz = _resolve_timezone(user_timezone)
        now = self._clock().astimezone(tz)

        # 2) Load context (sensitive memories are never sent to the LLM) -----
        try:
            known = await memory_repo.list_recent_active(
                self._session,
                uid,
                limit=settings.orchestrator_context_limit,
                exclude_privacy=[PrivacyLevel.SENSITIVE],
            )
        except SQLAlchemyError:
            logger.exception("request %s: failed to load memory context", request_id)
            await self._session.rollback()
            return self._error(
                request_id, text, "database_error", "could not load memory context", retryable=True
            )

        # 3) LLM extraction (with re-ask on invalid output) -------------------
        try:
            extraction = await self._extract(text, now, str(tz), known)
        except LLMUnavailableError as exc:
            logger.warning("request %s: LLM unavailable: %s", request_id, exc)
            return self._error(
                request_id, text, "llm_unavailable", str(exc), retryable=exc.retryable
            )
        except LLMOutputError as exc:
            logger.warning("request %s: LLM output invalid: %s", request_id, exc)
            return self._error(request_id, text, "llm_output_invalid", str(exc), retryable=True)

        # 4) Persist in a single transaction -------------------------------
        try:
            outcome = await self._persist(uid, extraction, now, source_type, request_id)
            await self._session.commit()
        except SQLAlchemyError:
            logger.exception("request %s: persistence failed, rolled back", request_id)
            await self._session.rollback()
            return self._error(
                request_id, text, "database_error", "could not save results", retryable=True
            )

        logger.info(
            "request %s: memories=%d pending_updates=%d commitments=%d warnings=%d",
            request_id,
            len(outcome.memories),
            len(outcome.pending_updates),
            len(outcome.commitments),
            len(outcome.warnings),
        )
        return ProcessInputResponse(
            request_id=request_id,
            status="success",
            agent_reply=extraction.agent_reply,
            requires_user_confirmation=bool(outcome.commitments or outcome.pending_updates),
            memories=outcome.memories,
            pending_memory_updates=outcome.pending_updates,
            commitments=outcome.commitments,
            warnings=outcome.warnings,
            guide_request=extraction.guide_request,
            privacy_hidden_count=extraction.hidden_count,
            model=self._llm.model_name,
            prompt_version=PROMPT_VERSION,
        )

    # ------------------------------------------------------------------
    async def _extract(
        self, text: str, now: datetime, timezone_name: str, known: Sequence[Memory]
    ) -> ParsedExtraction:
        schema = build_tool_schema()
        repair_note: str | None = None
        attempts = max(1, settings.llm_max_parse_attempts)
        # Mask names/numbers/amounts; the token map stays in this process for this request only.
        vault = Pseudonymizer(m.entity_name for m in known) if settings.mask_before_llm else None
        system_prompt = f"{SYSTEM_PROMPT}\n\n{TOKEN_INSTRUCTION}" if vault else SYSTEM_PROMPT

        for attempt in range(1, attempts + 1):
            user_content = build_user_message(
                input_text=text,
                now=now,
                timezone_name=timezone_name,
                known_memories=known,
                repair_note=repair_note,
            )
            try:
                raw = await self._llm.extract_structured(
                    system_prompt=system_prompt,
                    user_content=vault.mask(user_content) if vault else user_content,
                    tool_name=TOOL_NAME,
                    tool_description=TOOL_DESCRIPTION,
                    input_schema=schema,
                )
                parsed = parse_extraction(vault.unmask(raw) if vault else raw)
            except LLMOutputError as exc:  # LLMUnavailableError propagates (SDK already retried)
                repair_note = str(exc)
                logger.warning("extraction attempt %d/%d invalid: %s", attempt, attempts, exc)
                continue
            parsed.hidden_count = vault.hidden_count if vault else 0
            if attempt > 1:
                parsed.warnings.append(
                    ProcessingWarning(
                        code="llm_output_repaired",
                        message=f"valid output obtained on attempt {attempt}",
                    )
                )
            return parsed

        raise LLMOutputError(repair_note or "model output invalid")

    # ------------------------------------------------------------------
    async def _persist(
        self,
        user_id: uuid.UUID,
        extraction: ParsedExtraction,
        now: datetime,
        source_type: SourceType,
        request_id: uuid.UUID,
    ) -> _PersistOutcome:
        out = _PersistOutcome(warnings=list(extraction.warnings))

        for mem in self._dedupe(extraction.memories, out.warnings):
            if mem.confidence_score < settings.memory_min_confidence:
                out.warnings.append(
                    ProcessingWarning(
                        code="memory_low_confidence",
                        message=(
                            f"'{mem.entity_name}.{mem.attribute_key}' skipped "
                            f"(confidence {mem.confidence_score:.2f} < {settings.memory_min_confidence})"
                        ),
                    )
                )
                continue

            existing = await memory_repo.get_active_by_attribute(
                self._session, user_id, mem.entity_name, mem.attribute_key
            )

            if existing is None:
                privacy = _privacy_for(mem.category)
                row = Memory(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    category=mem.category,
                    entity_name=mem.entity_name,
                    attribute_key=mem.attribute_key,
                    attribute_value=mem.attribute_value,
                    confidence_score=mem.confidence_score,
                    source_type=source_type,
                    privacy_level=privacy,
                    is_active=True,
                    last_confirmed_at=now,
                    original_metadata={
                        "confidence_score": mem.confidence_score,
                        "source_type": source_type.value,
                        "privacy_level": privacy.value,
                        "category": mem.category.value,
                        "extracted_by": self._llm.model_name,
                        "prompt_version": PROMPT_VERSION,
                        "request_id": str(request_id),
                        "extracted_at": now.isoformat(),
                    },
                )
                self._session.add(row)
                out.memories.append(self._memory_result(row, "created"))

            elif _same_value(existing.attribute_value, mem.attribute_value):
                # Restated fact: confirm only. original_metadata and value are untouched (Rule 3).
                existing.last_confirmed_at = now
                out.memories.append(self._memory_result(existing, "confirmed"))

            else:
                # Changing an existing fact is a non-read op -> needs approval (Rule 1).
                await self._session.execute(
                    update(PendingAction)
                    .where(
                        PendingAction.action_type == ApprovalActionType.UPDATE_MEMORY,
                        PendingAction.target_id == existing.id,
                        PendingAction.status == ApprovalStatus.PENDING,
                    )
                    .values(
                        status=ApprovalStatus.SUPERSEDED,
                        decided_at=now,
                        decision_reason=f"superseded by request {request_id}",
                    )
                )
                action = PendingAction(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    action_type=ApprovalActionType.UPDATE_MEMORY,
                    target_id=existing.id,
                    request_id=request_id,
                    payload={
                        "entity_name": existing.entity_name,
                        "attribute_key": existing.attribute_key,
                        "current_value": existing.attribute_value,
                        "proposed_value": mem.attribute_value,
                        "confidence_score": mem.confidence_score,
                        "source_type": source_type.value,
                    },
                )
                self._session.add(action)
                out.pending_updates.append(
                    PendingMemoryUpdate(
                        action_id=action.id,
                        memory_id=existing.id,
                        category=existing.category,
                        entity_name=existing.entity_name,
                        attribute_key=existing.attribute_key,
                        current_value=existing.attribute_value,
                        proposed_value=mem.attribute_value,
                        confidence_score=mem.confidence_score,
                    )
                )

        for com in extraction.commitments:
            due = com.due_date
            if due is not None and due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            if due is not None and due < now:
                out.warnings.append(
                    ProcessingWarning(
                        code="commitment_due_date_in_past",
                        message=f"'{com.title}' has a due_date in the past ({due.isoformat()})",
                    )
                )
            commitment = Commitment(
                id=uuid.uuid4(),
                user_id=user_id,
                title=com.title,
                description=com.description,
                status=CommitmentStatus.PENDING,
                due_date=due,
                follow_up_at=None,
                is_proactive_generated=False,
                requires_approval=True,  # Rule 1: hard-coded, never taken from the model
            )
            action = PendingAction(
                id=uuid.uuid4(),
                user_id=user_id,
                action_type=ApprovalActionType.APPROVE_COMMITMENT,
                target_id=commitment.id,
                request_id=request_id,
                payload={
                    "title": com.title,
                    "due_date": due.isoformat() if due is not None else None,
                },
            )
            self._session.add_all([commitment, action])
            out.commitments.append(
                CommitmentResult(
                    commitment_id=commitment.id,
                    approval_action_id=action.id,
                    title=commitment.title,
                    description=commitment.description,
                    due_date=commitment.due_date,
                    status=commitment.status,
                )
            )

        await self._session.flush()  # surfaces constraint errors before commit
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe(
        memories: list[ExtractedMemory], warnings: list[ProcessingWarning]
    ) -> list[ExtractedMemory]:
        """Same (entity, attribute) twice in one message -> keep the highest confidence."""
        best: dict[tuple[str, str], ExtractedMemory] = {}
        for mem in memories:
            key = (mem.entity_name, mem.attribute_key)
            current = best.get(key)
            if current is not None:
                warnings.append(
                    ProcessingWarning(
                        code="memory_duplicate_in_input",
                        message=f"duplicate '{key[0]}.{key[1]}' in model output; kept highest confidence",
                    )
                )
                if mem.confidence_score <= current.confidence_score:
                    continue
            best[key] = mem
        return list(best.values())

    @staticmethod
    def _memory_result(row: Memory, action: Literal["created", "confirmed"]) -> MemoryResult:
        return MemoryResult(
            memory_id=row.id,
            action=action,
            category=row.category,
            entity_name=row.entity_name,
            attribute_key=row.attribute_key,
            attribute_value=row.attribute_value,
            confidence_score=row.confidence_score,
            last_confirmed_at=row.last_confirmed_at,
        )

    def _error(
        self,
        request_id: uuid.UUID,
        input_text: str,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> ProcessInputResponse:
        return ProcessInputResponse(
            request_id=request_id,
            status="error",
            agent_reply=_fallback_reply(code, input_text),
            requires_user_confirmation=False,
            error=ProcessingError(code=code, message=message, retryable=retryable),
            model=self._llm.model_name,
            prompt_version=PROMPT_VERSION,
        )
