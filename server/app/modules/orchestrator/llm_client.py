"""Provider-agnostic LLM interface + Anthropic implementation using a forced tool call.

Forcing `tool_choice` to a single tool makes the model return its answer as a JSON object that already
matches the schema. A text-JSON fallback parser is kept for robustness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
from anthropic import AsyncAnthropic

from app.core.config import settings
from app.modules.orchestrator.errors import LLMOutputError, LLMUnavailableError


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def extract_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the model's JSON object. Raise LLMUnavailableError / LLMOutputError on failure."""
        ...


@dataclass
class GuideLLMResult:
    data: dict[str, Any]
    retrieved: dict[str, str] = field(default_factory=dict)  # url -> title, from real search results
    search_count: int = 0
    web_search_used: bool = False


class GuideLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate_guide(
        self,
        *,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        web_search: bool,
        user_timezone: str | None,
    ) -> GuideLLMResult:
        """Research (optionally with web search) and return the guide JSON + retrieved URLs."""
        ...


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from raw model text (tolerates ```json fences and stray prose)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMOutputError("no JSON object found in model output")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"invalid JSON: {exc.msg} (pos {exc.pos})") from exc
    if not isinstance(data, dict):
        raise LLMOutputError("top-level JSON value is not an object")
    return cast(dict[str, Any], data)


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout_seconds: float,
        max_retries: int,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    async def extract_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            tools=[{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )

        if response.stop_reason == "max_tokens":
            raise LLMOutputError("model output was truncated (max_tokens reached)")

        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                if not isinstance(block.input, dict):
                    raise LLMOutputError("tool input is not a JSON object")
                return cast(dict[str, Any], block.input)

        text = "".join(block.text for block in response.content if block.type == "text")
        if text.strip():
            return parse_json_object(text)
        raise LLMOutputError("model returned neither a tool call nor text")


    async def generate_guide(
        self,
        *,
        system_prompt: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        web_search: bool,
        user_timezone: str | None,
    ) -> GuideLLMResult:
        """Let the model search (server-side web_search tool), then force it to record the guide.

        Loop handles: `pause_turn` (server tool loop paused -> resend to continue) and a model that
        answers in prose instead of calling the tool (-> ask again with tool_choice forced).
        """
        tools: list[dict[str, Any]] = [
            {"name": tool_name, "description": tool_description, "input_schema": input_schema}
        ]
        if web_search:
            search_tool: dict[str, Any] = {
                "type": settings.web_search_tool_type,
                "name": "web_search",
                "max_uses": settings.web_search_max_uses,
            }
            if user_timezone:
                search_tool["user_location"] = {"type": "approximate", "timezone": user_timezone}
            tools.insert(0, search_tool)

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        result = GuideLLMResult(data={}, web_search_used=web_search)
        force = not web_search

        for _ in range(max(1, settings.guide_max_rounds)):
            tool_choice: dict[str, Any] = (
                {"type": "tool", "name": tool_name} if force else {"type": "auto"}
            )
            response = await self._create(
                model=self._model,
                max_tokens=settings.guide_max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                timeout=settings.guide_timeout_seconds,
            )
            for block in response.content:
                if block.type == "server_tool_use":
                    result.search_count += 1
                elif block.type == "web_search_tool_result":
                    if isinstance(block.content, list):
                        for item in block.content:
                            result.retrieved.setdefault(item.url, item.title)
                elif block.type == "tool_use" and block.name == tool_name:
                    if not isinstance(block.input, dict):
                        raise LLMOutputError("guide tool input is not a JSON object")
                    result.data = cast(dict[str, Any], block.input)
                    return result

            if response.stop_reason == "max_tokens":
                raise LLMOutputError("guide output was truncated (max_tokens reached)")
            messages.append(
                {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]}
            )
            if response.stop_reason != "pause_turn":
                messages.append(
                    {
                        "role": "user",
                        "content": f"Now call `{tool_name}` with the final guide, based on what you found.",
                    }
                )
                force = True

        raise LLMOutputError("model did not produce a guide")

    async def _create(self, **kwargs: Any) -> Any:
        """messages.create with provider errors mapped to LLMUnavailableError."""
        try:
            return await self._client.messages.create(**kwargs)
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMUnavailableError(f"LLM connection failed: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise LLMUnavailableError(
                f"LLM API error {exc.status_code}", retryable=retryable
            ) from exc
        except anthropic.AnthropicError as exc:
            raise LLMUnavailableError(f"LLM client error: {exc}", retryable=False) from exc


def build_llm_client() -> AnthropicLLMClient:
    """Factory used by the API layer (FastAPI dependency)."""
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():  # unset OR empty in .env
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return AnthropicLLMClient(
        api_key=key.get_secret_value(),
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
