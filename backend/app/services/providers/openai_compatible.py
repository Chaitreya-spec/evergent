"""OpenAI and every OpenAI-compatible gateway.

OpenAI itself, OpenRouter, OmniRoute, vLLM and friends speak the same wire
format, so one implementation serves them all; only the base URL, credential
and token parameter differ.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.errors import ProviderError
from app.services.providers.base import (
    AIProvider,
    CompletionRequest,
    CompletionResult,
    ToolInvocation,
)


class OpenAICompatibleProvider(AIProvider):
    """Base for any endpoint speaking the OpenAI chat-completions API."""

    name = "openai_compatible"
    # OpenAI's own API wants max_completion_tokens; gateways still take
    # max_tokens. Subclasses set whichever their endpoint accepts.
    token_param = "max_tokens"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key, base_url)
        self._client: AsyncOpenAI | None = None
        self._headers = default_headers

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=self._headers,
                timeout=settings.model_timeout_seconds,
                max_retries=settings.model_max_retries,
            )
        return self._client

    async def verify(self) -> list[str]:
        page = await self.client.models.list()
        return [model.id async for model in page]

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(
            {"role": m.role, "content": m.content} for m in request.messages
        )

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            self.token_param: request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        return payload

    async def generate(self, request: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        payload = self._payload(request)
        # Usage only arrives in the final chunk of a stream, and only if asked.
        payload |= {"stream": True, "stream_options": {"include_usage": True}}

        text_parts: list[str] = []
        tool_fragments: dict[int, dict[str, Any]] = {}
        input_tokens = output_tokens = 0

        try:
            stream = await self.client.chat.completions.create(**payload)
            async for chunk in stream:
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens or 0
                    output_tokens = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    if request.on_delta:
                        request.on_delta(delta.content)
                # Tool arguments stream in fragments keyed by index.
                for call in delta.tool_calls or []:
                    frag = tool_fragments.setdefault(
                        call.index, {"name": "", "arguments": "", "id": None}
                    )
                    if call.id:
                        frag["id"] = call.id
                    if call.function and call.function.name:
                        frag["name"] = call.function.name
                    if call.function and call.function.arguments:
                        frag["arguments"] += call.function.arguments
        except Exception as exc:
            raise ProviderError(f"{self.name} call failed: {exc}") from exc

        tool_calls: list[ToolInvocation] = []
        for frag in tool_fragments.values():
            try:
                args = json.loads(frag["arguments"] or "{}")
            except json.JSONDecodeError:
                # A truncated tool call is reported as empty rather than
                # crashing the task; the caller validates before executing.
                args = {}
            tool_calls.append(
                ToolInvocation(name=frag["name"], arguments=args, call_id=frag["id"])
            )

        return CompletionResult(
            text="".join(text_parts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        payload = self._payload(request) | {"stream": True}
        try:
            stream = await self.client.chat.completions.create(**payload)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            raise ProviderError(f"{self.name} stream failed: {exc}") from exc


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    token_param = "max_completion_tokens"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or settings.openai_api_key, base_url)


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            api_key or settings.openrouter_api_key,
            base_url or "https://openrouter.ai/api/v1",
            default_headers={"X-Title": settings.app_name},
        )


class CompatibleGatewayProvider(OpenAICompatibleProvider):
    """Any self-hosted OpenAI-compatible gateway (OmniRoute, vLLM, ...)."""

    name = "compatible"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            api_key or settings.compatible_api_key,
            base_url or settings.compatible_base_url,
        )

    def is_configured(self) -> bool:
        # A base URL is what makes this provider meaningful; some local
        # gateways need no credential at all.
        return bool(self.base_url)
