"""Stateless AI gateway: auth'd user -> daily quota -> extra masking -> provider (with fallback) -> unmask.

Nothing about the conversation is stored: only a per-day request counter for quota and cost.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from xml.sax.saxutils import escape

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.config import settings
from app.modules.gateway.models import GatewayUsage
from app.modules.gateway.providers import Provider, ProviderCall, decode_image
from app.modules.gateway.images import ImageProvider
from app.modules.gateway.schemas import CompleteRequest, CompleteResponse, ImageRequest, ImageResponse, TtsRequest
from app.modules.orchestrator.errors import LLMOutputError, LLMUnavailableError
from app.modules.orchestrator.schemas import ErrorCode, ProcessingError
from app.modules.privacy.pseudonymizer import Pseudonymizer
from app.shared.api import AppError

from app.modules.ops.service import bump

logger = logging.getLogger(__name__)

DEFAULT_VOICE = {"ar": "ar-EG-SalmaNeural", "en": "en-US-JennyNeural"}


class GatewayService:
    def __init__(
        self,
        session: AsyncSession,
        providers: dict[str, Provider],
        *,
        clock: Callable[[], datetime] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._session = session
        self._providers = providers
        self._clock = clock or (lambda: datetime.now(UTC))
        self._http = http

    async def _count(self, user_id: uuid.UUID, kind: str, chars: int) -> int:
        """Atomically add one request; return today's total (after this one)."""
        stmt = (
            insert(GatewayUsage)
            .values(user_id=user_id, day=self._clock().date(), kind=kind, requests=1, input_chars=chars)
            .on_conflict_do_update(
                index_elements=["user_id", "day", "kind"],
                set_={"requests": GatewayUsage.requests + 1, "input_chars": GatewayUsage.input_chars + chars},
            )
            .returning(GatewayUsage.requests)
        )
        total = (await self._session.execute(stmt)).scalar_one()
        await self._session.commit()
        return int(total)

    async def complete(self, user_id: uuid.UUID, req: CompleteRequest, daily_limit: int | None = None) -> CompleteResponse:
        request_id = uuid.uuid4()
        used = await self._count(user_id, "complete", len(req.prompt))
        if used > (daily_limit or settings.gateway_daily_requests_per_user):
            return self._error(request_id, "llm_unavailable", "daily limit reached, try again tomorrow", False)

        vault = Pseudonymizer()  # regex layer: phones, IDs, emails, amounts (the app already masked names)
        call = ProviderCall(
            prompt=vault.mask(req.prompt),
            tier=req.tier,
            images=[decode_image(im.media_type, im.data_b64) for im in req.images],
            web_search=req.web_search,
            audio=decode_image(req.audio.media_type, req.audio.data_b64) if req.audio else None,
        )
        order = [p for p in settings.gateway_provider_order if p in self._providers and self._providers[p].available()]
        if call.tier == "best" and "anthropic" in order:   # the hardest jobs go to Claude first when it is connected
            order = ["anthropic"] + [p for p in order if p != "anthropic"]
        if call.audio is not None:  # of the three, only Gemini accepts audio directly
            order = [p for p in order if p == "gemini"]
            if not order:
                return self._error(request_id, "llm_unavailable", "voice notes need Gemini configured on the server", False)
        if not order:
            return self._error(request_id, "llm_unavailable", "no AI provider configured on the server", False)

        last: LLMUnavailableError | None = None
        for name in order:
            try:
                data = await self._providers[name].complete_json(call)
            except LLMOutputError as exc:
                # The model answered but not in JSON: the app re-asks with a repair note.
                logger.info("gateway %s: %s returned invalid JSON", request_id, name)
                await bump(self._session, "ai", f"{name}:invalid")
                return self._error(request_id, "llm_output_invalid", str(exc), True)
            except LLMUnavailableError as exc:
                logger.warning("gateway %s: %s unavailable (%s), trying next", request_id, name, exc)
                await bump(self._session, "ai", f"{name}:fail", str(exc))
                last = exc
                continue
            except Exception as exc:   # a provider bug must never take the whole answer down: log it and use the next one
                logger.exception("gateway %s: %s crashed, trying next", request_id, name)
                await bump(self._session, "ai", f"{name}:fail", f"crash: {type(exc).__name__}: {exc}"[:200])
                last = LLMUnavailableError(f"{name} crashed: {type(exc).__name__}", retryable=True)
                continue
            await bump(self._session, "ai", f"{name}:ok" + (":nosearch" if isinstance(data, dict) and data.get("_no_search") else ""))
            return CompleteResponse(
                request_id=request_id, status="success", data=vault.unmask(data),
                provider=name,  # type: ignore[arg-type]
                hidden_count=vault.hidden_count,
            )
        assert last is not None
        return self._error(request_id, "llm_unavailable", str(last), last.retryable)

    async def image(self, user_id: uuid.UUID, req: ImageRequest, providers: dict[str, ImageProvider]) -> ImageResponse:
        request_id = uuid.uuid4()
        used = await self._count(user_id, "image", len(req.prompt))
        if used > settings.gateway_daily_images_per_user:
            return ImageResponse(request_id=request_id, status="error",
                                 error=ProcessingError(code="llm_unavailable", message="daily image limit reached", retryable=False))
        order = [p for p in settings.image_provider_order if p in providers and providers[p].available()]
        if not order:
            return ImageResponse(request_id=request_id, status="error",
                                 error=ProcessingError(code="llm_unavailable", message="no image provider configured on the server", retryable=False))
        last: LLMUnavailableError | None = None
        for name in order:
            try:
                img = await providers[name].generate(req.prompt, req.aspect)
            except LLMUnavailableError as exc:
                last = exc
                continue
            return ImageResponse(request_id=request_id, status="success", image_b64=img.data_b64,
                                 media_type=img.media_type, provider=name)  # type: ignore[arg-type]
        assert last is not None
        return ImageResponse(request_id=request_id, status="error",
                             error=ProcessingError(code="llm_unavailable", message=str(last), retryable=last.retryable))

    async def tts(self, user_id: uuid.UUID, req: TtsRequest) -> bytes:
        key = settings.azure_speech_key
        if key is None or not key.get_secret_value().strip() or not settings.azure_speech_region:
            raise AppError(503, "tts_not_configured", "Egyptian voice is not configured on the server")
        used = await self._count(user_id, "tts", len(req.text))
        if used > settings.gateway_daily_requests_per_user:
            raise AppError(429, "daily_limit", "daily voice limit reached")
        voice = req.voice or DEFAULT_VOICE[req.lang]
        pct = round((req.rate - 1) * 100)
        ssml = (
            f'<speak version="1.0" xml:lang="{"en-US" if req.lang == "en" else "ar-EG"}">'
            f'<voice name="{voice}"><prosody rate="{pct:+d}%">{escape(req.text)}</prosody></voice></speak>'
        )
        http = self._http or httpx.AsyncClient(timeout=30)
        try:
            res = await http.post(
                f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": key.get_secret_value(),
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                },
                content=ssml.encode(),
            )
        except httpx.HTTPError as exc:
            raise AppError(503, "tts_unavailable", "voice service unreachable") from exc
        finally:
            if self._http is None:
                await http.aclose()
        if res.status_code != 200:
            raise AppError(502, "tts_failed", f"voice service error {res.status_code}")
        return res.content

    @staticmethod
    def _error(request_id: uuid.UUID, code: ErrorCode, message: str, retryable: bool) -> CompleteResponse:
        return CompleteResponse(
            request_id=request_id, status="error",
            error=ProcessingError(code=code, message=message, retryable=retryable),
        )
