from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.orchestrator.schemas import ProcessingError

ALLOWED_IMAGE_TYPES = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageIn(_Strict):
    media_type: ALLOWED_IMAGE_TYPES
    data_b64: str = Field(min_length=4, max_length=7_000_000)  # ~5 MB image


class AudioIn(_Strict):
    media_type: Literal["audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/aac", "audio/webm"]
    data_b64: str = Field(min_length=4, max_length=21_000_000)  # ~15 MB voice note


class CompleteRequest(_Strict):
    prompt: str = Field(min_length=1, max_length=200_000)
    audio: AudioIn | None = None
    tier: Literal["main", "quick", "best"] = "main"   # best: design, feasibility — Claude first when connected
    web_search: bool = False
    images: list[ImageIn] = Field(default_factory=list, max_length=4)


class CompleteResponse(_Strict):
    request_id: uuid.UUID
    status: Literal["success", "error"]
    data: dict[str, Any] | None = None
    provider: Literal["anthropic", "openai", "gemini", "groq"] | None = None
    hidden_count: int = 0  # extra details masked by the gateway itself
    error: ProcessingError | None = None


class ImageRequest(_Strict):
    prompt: str = Field(min_length=3, max_length=2000)
    aspect: Literal["1:1", "9:16", "16:9"] = "1:1"


class ImageResponse(_Strict):
    request_id: uuid.UUID
    status: Literal["success", "error"]
    image_b64: str | None = None
    media_type: str | None = None
    provider: Literal["google", "openai"] | None = None
    error: ProcessingError | None = None


class TtsRequest(_Strict):
    text: str = Field(min_length=1, max_length=2000)
    lang: Literal["ar", "en"] = "ar"
    voice: Literal[
        "ar-EG-SalmaNeural", "ar-EG-ShakirNeural",
        "en-US-JennyNeural", "en-US-GuyNeural", "en-GB-SoniaNeural", "en-GB-RyanNeural",
    ] | None = None
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
