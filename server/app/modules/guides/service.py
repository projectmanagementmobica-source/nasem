"""Guide generation: research (web search) -> validated guide -> sources checked -> next step proposed.

- Sources: a URL is kept only if it was actually returned by the web search in this request.
- Web search not available (e.g. disabled for the org) -> one automatic retry without it, grounded=false.
- suggested_commitment -> saved as a PENDING commitment + approval action (Rule 1), same flow as /process.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401  (registers every mapper)
from app.core.config import settings
from app.modules.approvals.models import PendingAction
from app.modules.auth.models import User
from app.modules.commitments.models import Commitment
from app.modules.guides.prompts import (
    GUIDE_PROMPT_VERSION,
    GUIDE_TOOL_DESCRIPTION,
    GUIDE_TOOL_NAME,
    build_guide_system_prompt,
    build_guide_tool_schema,
    build_guide_user_message,
)
from app.modules.guides.schemas import (
    GuideContent,
    GuideOut,
    GuideResponse,
    GuideSourceOut,
    SuggestedCommitmentOut,
)
from app.modules.memory import repository as memory_repo
from app.modules.orchestrator.errors import LLMOutputError, LLMUnavailableError
from app.modules.orchestrator.llm_client import GuideLLM, GuideLLMResult
from app.modules.orchestrator.schemas import ErrorCode, ProcessingError, ProcessingWarning
from app.modules.orchestrator.service import _resolve_timezone
from app.modules.privacy.pseudonymizer import TOKEN_INSTRUCTION, Pseudonymizer
from app.shared.enums import ApprovalActionType, CommitmentStatus, PrivacyLevel

logger = logging.getLogger(__name__)


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


class GuideService:
    def __init__(
        self, session: AsyncSession, llm: GuideLLM, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._session = session
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_guide(
        self, user: User, question: str, *, use_web_search: bool = True
    ) -> GuideResponse:
        request_id = uuid.uuid4()
        warnings: list[ProcessingWarning] = []
        tz = _resolve_timezone(user.timezone)
        now = self._clock().astimezone(tz)

        try:
            known = await memory_repo.list_recent_active(
                self._session,
                user.id,
                limit=settings.orchestrator_context_limit,
                exclude_privacy=[PrivacyLevel.SENSITIVE],
            )
        except SQLAlchemyError:
            logger.exception("guide %s: failed to load context", request_id)
            await self._session.rollback()
            return self._error(request_id, "database_error", "could not load memory context", True)

        user_content = build_guide_user_message(
            question=question.strip(), now=now, timezone_name=str(tz), known_memories=known
        )
        # Masked text also keeps names/numbers out of the web-search queries the model writes.
        vault = Pseudonymizer(m.entity_name for m in known) if settings.mask_before_llm else None
        if vault is not None:
            user_content = vault.mask(user_content)
        grounded = use_web_search and settings.web_search_enabled

        try:
            try:
                result = await self._generate(user_content, str(tz), web_search=grounded, masked=vault is not None)
            except LLMUnavailableError as exc:
                if not (grounded and not exc.retryable):
                    raise
                # Most likely web search isn't enabled for this API key/org: answer without it.
                logger.warning("guide %s: web search failed (%s), retrying without it", request_id, exc)
                warnings.append(
                    ProcessingWarning(
                        code="web_search_unavailable",
                        message="web search unavailable; answer is based on model knowledge only",
                    )
                )
                grounded = False
                result = await self._generate(user_content, str(tz), web_search=False, masked=vault is not None)
            content = GuideContent.model_validate(vault.unmask(result.data) if vault else result.data)
        except LLMUnavailableError as exc:
            return self._error(request_id, "llm_unavailable", str(exc), exc.retryable)
        except (LLMOutputError, ValidationError) as exc:
            logger.warning("guide %s: invalid guide output: %s", request_id, exc)
            return self._error(request_id, "llm_output_invalid", "the guide could not be generated", True)

        # ---- sources: keep only URLs the search really returned ----
        retrieved = {_norm_url(u): (u, t) for u, t in result.retrieved.items()}
        sources: list[GuideSourceOut] = []
        dropped = 0
        for src in content.sources:
            hit = retrieved.get(_norm_url(src.url))
            if hit is None:
                dropped += 1
                continue
            if all(s.url != hit[0] for s in sources):
                sources.append(GuideSourceOut(title=src.title or hit[1], url=hit[0]))
        if dropped:
            warnings.append(
                ProcessingWarning(
                    code="sources_dropped",
                    message=f"{dropped} cited source(s) removed: not found in this request's search results",
                )
            )
        grounded = grounded and result.search_count > 0

        # ---- suggested next step -> pending commitment (Rule 1) ----
        suggested: SuggestedCommitmentOut | None = None
        sc = content.suggested_commitment
        try:
            if sc is not None:
                due = sc.due_date
                if due is not None and due.tzinfo is None:
                    due = due.replace(tzinfo=now.tzinfo)
                commitment = Commitment(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    title=sc.title,
                    description=f"من دليل: {content.title}",
                    status=CommitmentStatus.PENDING,
                    due_date=due,
                    is_proactive_generated=True,
                    requires_approval=True,
                )
                action = PendingAction(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    action_type=ApprovalActionType.APPROVE_COMMITMENT,
                    target_id=commitment.id,
                    request_id=request_id,
                    payload={
                        "title": sc.title,
                        "due_date": due.isoformat() if due else None,
                        "source": "guide",
                    },
                )
                self._session.add_all([commitment, action])
                await self._session.commit()
                suggested = SuggestedCommitmentOut(
                    commitment_id=commitment.id,
                    approval_action_id=action.id,
                    title=sc.title,
                    due_date=due,
                )
        except SQLAlchemyError:
            logger.exception("guide %s: could not save suggested commitment", request_id)
            await self._session.rollback()
            warnings.append(
                ProcessingWarning(
                    code="guide_commitment_invalid", message="suggested next step could not be saved"
                )
            )

        guide = GuideOut(
            title=content.title,
            summary=content.summary,
            best=content.best,
            easiest=content.easiest,
            steps=content.steps,
            where=content.where,
            options=content.options,
            who=content.who,
            tips=content.tips,
            check_before=content.check_before,
            draft_message=content.draft_message,
            suggested_commitment=suggested,
            sources=sources,
            grounded=grounded,
            search_count=result.search_count,
            generated_at=now,
        )
        logger.info(
            "guide %s: grounded=%s searches=%d sources=%d dropped=%d",
            request_id, grounded, result.search_count, len(sources), dropped,
        )
        return GuideResponse(
            request_id=request_id,
            status="success",
            guide=guide,
            warnings=warnings,
            model=self._llm.model_name,
            prompt_version=GUIDE_PROMPT_VERSION,
        )

    async def _generate(
        self, user_content: str, tz: str, *, web_search: bool, masked: bool
    ) -> GuideLLMResult:
        system = build_guide_system_prompt(web_search=web_search)
        return await self._llm.generate_guide(
            system_prompt=f"{system}\n\n{TOKEN_INSTRUCTION}" if masked else system,
            user_content=user_content,
            tool_name=GUIDE_TOOL_NAME,
            tool_description=GUIDE_TOOL_DESCRIPTION,
            input_schema=build_guide_tool_schema(),
            web_search=web_search,
            user_timezone=tz,
        )

    def _error(
        self, request_id: uuid.UUID, code: ErrorCode, message: str, retryable: bool
    ) -> GuideResponse:
        return GuideResponse(
            request_id=request_id,
            status="error",
            error=ProcessingError(code=code, message=message, retryable=retryable),
            model=self._llm.model_name,
            prompt_version=GUIDE_PROMPT_VERSION,
        )
