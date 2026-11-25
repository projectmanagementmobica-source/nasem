"""Image generation behind the gateway: Google first, OpenAI as fallback (both already our processors).

Pictures are generated WITHOUT text. The app draws Arabic text on top itself, so Arabic is always correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings
from app.modules.orchestrator.errors import LLMUnavailableError

NO_TEXT = " No text, letters, words, numbers, logos or watermarks anywhere in the image."
OPENAI_SIZES = {"1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024"}


@dataclass
class GeneratedImage:
    data_b64: str
    media_type: str


class ImageProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def generate(self, prompt: str, aspect: str) -> GeneratedImage: ...


def _status_error(status: int) -> LLMUnavailableError:
    return LLMUnavailableError(f"image provider error {status}", retryable=status == 429 or status >= 500)


class GoogleImageProvider:
    name = "google"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def available(self) -> bool:
        k = settings.gemini_api_key
        return k is not None and bool(k.get_secret_value().strip()) and bool(settings.gemini_image_model)

    async def generate(self, prompt: str, aspect: str) -> GeneratedImage:
        assert settings.gemini_api_key is not None
        body = {
            "contents": [{"role": "user", "parts": [{"text": f"{prompt}. Aspect ratio {aspect}.{NO_TEXT}"}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        http = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        try:
            res = await http.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_image_model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key.get_secret_value()}, json=body,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"google image connection: {exc}", retryable=True) from exc
        finally:
            if self._http is None:
                await http.aclose()
        if res.status_code != 200:
            raise _status_error(res.status_code)
        for cand in res.json().get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data"):
                    return GeneratedImage(inline["data"], inline.get("mime_type") or inline.get("mimeType") or "image/png")
        raise LLMUnavailableError("google returned no image", retryable=False)


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def available(self) -> bool:
        k = settings.openai_api_key
        return k is not None and bool(k.get_secret_value().strip()) and bool(settings.openai_image_model)

    async def generate(self, prompt: str, aspect: str) -> GeneratedImage:
        assert settings.openai_api_key is not None
        http = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        try:
            res = await http.post(
                "https://api.openai.com/v1/images/generations",
                headers={"authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"},
                json={"model": settings.openai_image_model, "prompt": prompt + NO_TEXT, "size": OPENAI_SIZES[aspect], "n": 1},
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"openai image connection: {exc}", retryable=True) from exc
        finally:
            if self._http is None:
                await http.aclose()
        if res.status_code != 200:
            raise _status_error(res.status_code)
        data = (res.json().get("data") or [{}])[0]
        if not data.get("b64_json"):
            raise LLMUnavailableError("openai returned no image", retryable=False)
        return GeneratedImage(data["b64_json"], "image/png")


def default_image_providers() -> dict[str, ImageProvider]:
    return {"google": GoogleImageProvider(), "openai": OpenAIImageProvider()}
