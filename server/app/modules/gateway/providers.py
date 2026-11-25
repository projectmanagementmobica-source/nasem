"""AI providers behind the gateway. Each returns ONE JSON object parsed from the model's answer.

Provider keys live only on the server. The app sends an already-masked prompt; the gateway adds a
second, regex-based masking layer (phones, IDs, amounts, emails) as defense in depth.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
import logging
import re
from anthropic import AsyncAnthropic

from app.core.config import settings
from app.modules.orchestrator.errors import LLMOutputError, LLMUnavailableError
from app.modules.orchestrator.llm_client import parse_json_object

Tier = Literal["main", "quick"]
JSON_ONLY = "\n\nReply with ONLY the JSON object, nothing before or after it."


@dataclass
class ImagePart:
    media_type: str
    data_b64: str


@dataclass
class ProviderCall:
    prompt: str
    tier: Tier = "main"
    images: list[ImagePart] = field(default_factory=list)
    web_search: bool = False
    audio: ImagePart | None = None  # same shape: media type + base64


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def complete_json(self, call: ProviderCall) -> dict[str, Any]: ...


def _http_error(status: int) -> LLMUnavailableError:
    return LLMUnavailableError(f"provider error {status}", retryable=status == 429 or status >= 500)


# ---------------------------------------------------------------- verified web sources
# Links shown to the user come from the search tool's own results, never from the model's text.
def _add_source(out: list[dict[str, Any]], url: Any, title: Any, age: Any = None, cited: bool = False) -> None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return
    for s in out:
        if s["url"] == url:
            s["cited"] = s["cited"] or cited
            return
    out.append({"url": url, "title": str(title or "")[:200], "age": str(age)[:40] if age else None, "cited": cited})


def anthropic_sources(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b.get("type") == "text":
            for c in b.get("citations") or []:
                _add_source(out, c.get("url"), c.get("title"), cited=True)
    for b in blocks:
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if isinstance(r, dict) and r.get("type") == "web_search_result":
                    _add_source(out, r.get("url"), r.get("title"), r.get("page_age"))
    out.sort(key=lambda s: not s["cited"])
    return out[:12]


def gemini_sources(cand: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    gm = cand.get("groundingMetadata") or {}
    out: list[dict[str, Any]] = []
    used = {i for sup in gm.get("groundingSupports") or [] for i in (sup.get("groundingChunkIndices") or [])}
    for i, ch in enumerate(gm.get("groundingChunks") or []):
        web = ch.get("web") or {}
        _add_source(out, web.get("uri"), web.get("title") or web.get("domain"), cited=i in used)
    out.sort(key=lambda s: not s["cited"])
    queries = [str(q)[:200] for q in gm.get("webSearchQueries") or []][:6]
    return out[:12], queries


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client

    def available(self) -> bool:
        key = settings.anthropic_api_key
        return self._client is not None or (key is not None and bool(key.get_secret_value().strip()))

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            assert settings.anthropic_api_key is not None
            self._client = AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                timeout=settings.gateway_timeout_seconds,
                max_retries=1,
            )
        return self._client

    async def complete_json(self, call: ProviderCall) -> dict[str, Any]:
        import anthropic

        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": im.media_type, "data": im.data_b64}}
            for im in call.images
        ]
        content.append({"type": "text", "text": call.prompt + JSON_ONLY})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        tools: list[dict[str, Any]] = (
            [{"type": settings.web_search_tool_type, "name": "web_search", "max_uses": settings.web_search_max_uses}]
            if call.web_search and settings.web_search_enabled
            else []
        )
        model = settings.anthropic_model_quick if call.tier == "quick" else settings.anthropic_model_main
        client = self._get_client()
        seen: list[dict[str, Any]] = []
        for _ in range(4):
            try:
                kwargs: dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": messages}
                if tools:
                    kwargs["tools"] = tools
                resp = await client.messages.create(**kwargs)
            except anthropic.APIConnectionError as exc:
                raise LLMUnavailableError(f"anthropic connection: {exc}", retryable=True) from exc
            except anthropic.APIStatusError as exc:
                raise _http_error(exc.status_code) from exc
            dumped = [b.model_dump(exclude_none=True) for b in resp.content]
            seen += dumped
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": dumped})
                continue
            texts = [b.text for b in resp.content if b.type == "text"]
            out: dict[str, Any] | None = None
            for i in range(len(texts) - 1, -1, -1):
                if "{" in texts[i]:
                    out = parse_json_object("\n".join(texts[i:]))
                    break
            if out is None:
                out = parse_json_object("\n".join(texts))
            if tools:
                out["_sources"] = anthropic_sources(seen)
            return out
        raise LLMOutputError("web search did not finish")


class OpenAIProvider:
    name = "openai"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def available(self) -> bool:
        key = settings.openai_api_key
        return key is not None and bool(key.get_secret_value().strip()) and bool(settings.openai_model_main or settings.openai_model_quick)

    async def complete_json(self, call: ProviderCall) -> dict[str, Any]:
        assert settings.openai_api_key is not None
        content: list[dict[str, Any]] = [{"type": "text", "text": call.prompt + JSON_ONLY}]
        content += [{"type": "image_url", "image_url": {"url": f"data:{im.media_type};base64,{im.data_b64}"}} for im in call.images]
        model = (settings.openai_model_quick if call.tier == "quick" else "") or settings.openai_model_main or settings.openai_model_quick
        http = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        try:
            res = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"},
                json={"model": model, "messages": [{"role": "user", "content": content}], "response_format": {"type": "json_object"}},
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"openai connection: {exc}", retryable=True) from exc
        finally:
            if self._http is None:
                await http.aclose()
        if res.status_code != 200:
            raise _http_error(res.status_code)
        data = res.json()
        return parse_json_object(str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))


class GeminiProvider:
    name = "gemini"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def available(self) -> bool:
        key = settings.gemini_api_key
        return key is not None and bool(key.get_secret_value().strip()) and bool(settings.gemini_model_main or settings.gemini_model_quick)

    async def complete_json(self, call: ProviderCall) -> dict[str, Any]:
        assert settings.gemini_api_key is not None
        parts: list[dict[str, Any]] = [{"text": call.prompt + JSON_ONLY}]
        parts += [{"inline_data": {"mime_type": im.media_type, "data": im.data_b64}} for im in call.images]
        if call.audio is not None:
            parts.append({"inline_data": {"mime_type": call.audio.media_type, "data": call.audio.data_b64}})
        body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
        if call.web_search and settings.web_search_enabled:
            body["tools"] = [{"google_search": {}}]  # grounding and JSON mode can't be combined
        else:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        first = (settings.gemini_model_quick if call.tier == "quick" else "") or settings.gemini_model_main or settings.gemini_model_quick
        http0 = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        try:
            live = await _gemini_live_models(http0, settings.gemini_api_key.get_secret_value())
        finally:
            if self._http is None:
                await http0.aclose()
        models = list(dict.fromkeys([first, settings.gemini_model_main, *settings.gemini_fallback_models, *live]))
        if live:   # skip ids Google no longer serves (e.g. retired model families)
            models = [m for m in models if m in live] or live
        models = [m for m in models if m and m not in _gemini_exhausted_today()]
        if not models:
            raise LLMUnavailableError("all free Gemini models are used up for today", retryable=False)
        http = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        key = settings.gemini_api_key.get_secret_value()

        async def attempt(payload: dict[str, Any]) -> httpx.Response:
            res: httpx.Response | None = None
            overloaded = 0
            for model in models:
                if overloaded >= 2:
                    break                                # Google is overloaded right now: let the next provider answer fast
                if model in _gemini_exhausted_today():
                    continue
                for retry in range(2):           # "high demand" 503s are usually gone a second later
                    try:
                        res = await http.post(
                            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                            headers={"x-goog-api-key": key},
                            json=payload,
                        )
                    except httpx.HTTPError as exc:
                        raise LLMUnavailableError(f"gemini connection: {exc}", retryable=True) from exc
                    if res.status_code in (500, 502, 503, 504) and retry == 0 and settings.gemini_retry_delay > 0:
                        await asyncio.sleep(settings.gemini_retry_delay)
                        continue
                    break
                assert res is not None
                if res.status_code == 429 and "tools" not in payload and re.search(r"limit:\s*0\b", res.text):
                    _mark_gemini_exhausted(model)        # no free quota at all for this model on this key -> skip it today
                    continue
                if res.status_code == 429 and "PerDay" in res.text and "tools" not in payload:
                    _mark_gemini_exhausted(model)        # this model is out for today -> next one
                    continue
                if res.status_code == 404:
                    continue                             # model id not available -> next one
                if res.status_code in (429, 500, 502, 503, 504):
                    log.warning("gemini %s busy (%s): %s", model, res.status_code, res.text[:240].replace("\n", " "))
                    if res.status_code >= 500:
                        overloaded += 1
                    continue                             # overloaded / rate-limited -> next model
                return res
            if res is None:
                raise LLMUnavailableError("all free Gemini models are used up for today", retryable=False)
            return res

        no_search = False
        try:
            res = await attempt(body)
            if res.status_code == 429 and "tools" in body:
                # The free Google Search grounding quota is separate from (and smaller than) plain generation:
                # answer from the model's own knowledge instead of failing, and say so.
                log.warning("gemini web search quota hit, answering without search")
                body = {"contents": body["contents"], "generationConfig": {"responseMimeType": "application/json"}}
                res = await attempt(body)
                no_search = True
        finally:
            if self._http is None:
                await http.aclose()
        if res.status_code != 200:
            raise _http_error(res.status_code)
        data = res.json()
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(str(p.get("text", "")) for p in ((cand.get("content") or {}).get("parts") or []))
        result = parse_json_object(text)
        if "tools" in body:
            result["_sources"], result["_queries"] = gemini_sources(cand)
        elif no_search:
            result["_no_search"] = True
        return result


log = logging.getLogger(__name__)

_LIVE: dict[str, Any] = {"at": 0.0, "models": []}


def _rank_gemini(name: str) -> tuple[int, float, int]:
    import re as _re
    m = _re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    ver = float(m.group(1)) if m else 0.0
    return (1 if "preview" in name or "exp" in name else 0, -ver, 1 if "lite" in name else 0)


async def _gemini_live_models(http: httpx.AsyncClient, key: str) -> list[str]:
    """Text 'flash' models this key can call right now, best first. Cached for 6 hours per instance."""
    import time
    if _LIVE["models"] and time.time() - _LIVE["at"] < 6 * 3600:
        return list(_LIVE["models"])
    try:
        res = await http.get("https://generativelanguage.googleapis.com/v1beta/models",
                             headers={"x-goog-api-key": key}, params={"pageSize": 200})
        if res.status_code != 200:
            return list(_LIVE["models"])
        names = []
        for mdl in res.json().get("models", []):
            name = str(mdl.get("name", "")).removeprefix("models/")
            if "generateContent" not in (mdl.get("supportedGenerationMethods") or []):
                continue
            if not name.startswith("gemini-") or "flash" not in name:
                continue
            if any(x in name for x in ("image", "tts", "audio", "live", "embedding", "thinking", "computer")):
                continue
            names.append(name)
        names.sort(key=_rank_gemini)
        _LIVE["at"], _LIVE["models"] = time.time(), names[:6]
    except (httpx.HTTPError, ValueError):
        pass
    return list(_LIVE["models"])


_EXHAUSTED: dict[str, str] = {}   # model -> UTC day it ran out (quota resets daily)


def _today() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _gemini_exhausted_today() -> set[str]:
    return {m for m, d in _EXHAUSTED.items() if d == _today()}


def _mark_gemini_exhausted(model: str) -> None:
    _EXHAUSTED[model] = _today()


class GroqProvider:
    """Groq free tier (no card). OpenAI-compatible; text only."""

    name = "groq"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def available(self) -> bool:
        k = settings.groq_api_key
        return k is not None and bool(k.get_secret_value().strip())

    async def complete_json(self, call: ProviderCall) -> dict[str, Any]:
        assert settings.groq_api_key is not None
        if call.images or call.audio is not None:
            raise LLMUnavailableError("groq: text only", retryable=False)
        http = self._http or httpx.AsyncClient(timeout=settings.gateway_timeout_seconds)
        last = 0
        try:
            live = await _groq_live_models(http, settings.groq_api_key.get_secret_value())
            wanted = [settings.groq_model_quick if call.tier == "quick" else settings.groq_model_main, settings.groq_model_main, settings.groq_model_quick]
            order = [m for m in dict.fromkeys(wanted + live) if m and (not live or m in live)]
            for model in order[:5]:
                try:
                    res = await http.post("https://api.groq.com/openai/v1/chat/completions",
                                          headers={"authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
                                          json={"model": model, "messages": [{"role": "user", "content": call.prompt + JSON_ONLY}],
                                                "response_format": {"type": "json_object"}, "temperature": 0.4})
                except httpx.HTTPError as exc:
                    raise LLMUnavailableError(f"groq connection: {exc}", retryable=True) from exc
                if res.status_code == 200:
                    data = res.json()
                    out = parse_json_object(str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
                    if call.web_search:
                        out["_no_search"] = True     # Groq answers from the model's knowledge: no verified sources
                    return out
                last = res.status_code
                if res.status_code not in (429, 404):
                    break
        finally:
            if self._http is None:
                await http.aclose()
        raise _http_error(last or 503)


_GROQ_LIVE: dict[str, Any] = {"at": 0.0, "models": []}


async def _groq_live_models(http: httpx.AsyncClient, key: str) -> list[str]:
    """Chat models this Groq key can use now (ids change over time). Cached 6 hours per instance."""
    import time
    if _GROQ_LIVE["models"] and time.time() - _GROQ_LIVE["at"] < 6 * 3600:
        return list(_GROQ_LIVE["models"])
    try:
        res = await http.get("https://api.groq.com/openai/v1/models", headers={"authorization": f"Bearer {key}"})
        if res.status_code == 200:
            ids = [str(m.get("id", "")) for m in res.json().get("data", []) if m.get("active", True)]
            ids = [i for i in ids if i and not re.search(r"whisper|tts|guard|embed|playai|orpheus|distil|prompt", i, re.I)]
            big = lambda i: (0 if re.search(r"gpt-oss-120b|70b|maverick|kimi|qwen3-32b", i) else 1, i)
            _GROQ_LIVE["at"], _GROQ_LIVE["models"] = time.time(), sorted(ids, key=big)[:8]
    except (httpx.HTTPError, ValueError):
        pass
    return list(_GROQ_LIVE["models"])


def decode_image(media_type: str, data_b64: str) -> ImagePart:
    base64.b64decode(data_b64, validate=True)  # reject junk early
    return ImagePart(media_type=media_type, data_b64=data_b64)


def default_providers() -> dict[str, Provider]:
    return {"anthropic": AnthropicProvider(), "openai": OpenAIProvider(), "gemini": GeminiProvider(), "groq": GroqProvider()}


__all__ = ["Provider", "ProviderCall", "ImagePart", "GroqProvider", "decode_image", "default_providers"]
