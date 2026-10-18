"""Anthropic provider."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.core.errors import ProviderError
from app.services.providers.base import (
    AIProvider,
    CompletionRequest,
    CompletionResult,
    ToolInvocation,
)

_JSON_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else. "
    "No prose, no markdown fences."
)


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or settings.anthropic_api_key, base_url)
        self._client: AsyncAnthropic | None = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(
                api_key=self.api_key,
                timeout=settings.model_timeout_seconds,
                max_retries=settings.model_max_retries,
            )
        return self._client

    async def verify(self) -> list[str]:
        page = await self.client.models.list()
        return [model.id async for model in page]

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        system = request.system
        # Assistant prefill is rejected on current models, so JSON is requested
        # through the system prompt rather than by seeding an opening brace.
        if request.json_mode:
            system = f"{system}\n\n{_JSON_INSTRUCTION}" if system else _JSON_INSTRUCTION

        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in request.tools
            ]
        return payload

    async def generate(self, request: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        try:
            # Streamed even when the caller wants one result: agent turns can
            # be long, and a non-streaming request risks an HTTP timeout at
            # these max_tokens values.
            async with self.client.messages.stream(**self._payload(request)) as stream:
                if request.on_delta:
                    async for text in stream.text_stream:
                        request.on_delta(text)
                message = await stream.get_final_message()
        except Exception as exc:
            raise ProviderError(f"Anthropic call failed: {exc}") from exc

        text_parts: list[str] = []
        tool_calls: list[ToolInvocation] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolInvocation(
                        name=block.name,
                        arguments=dict(block.input or {}),
                        call_id=block.id,
                    )
                )

        return CompletionResult(
            text="".join(text_parts),
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=message,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        try:
            async with self.client.messages.stream(**self._payload(request)) as s:
                async for text in s.text_stream:
                    yield text
        except Exception as exc:
            raise ProviderError(f"Anthropic stream failed: {exc}") from exc
