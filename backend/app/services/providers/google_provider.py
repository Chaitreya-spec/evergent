"""Google Gemini provider."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.core.config import settings
from app.core.errors import ProviderError
from app.services.providers.base import (
    AIProvider,
    CompletionRequest,
    CompletionResult,
)


class GoogleProvider(AIProvider):
    name = "google"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key or settings.google_api_key, base_url)
        self._client: genai.Client | None = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                api_key=self.api_key,
                # Milliseconds, unlike the other SDKs.
                http_options=genai_types.HttpOptions(
                    timeout=int(settings.model_timeout_seconds * 1000)
                ),
            )
        return self._client

    async def verify(self) -> list[str]:
        pager = await self.client.aio.models.list()
        return [(model.name or "").removeprefix("models/") async for model in pager]

    def _contents(self, request: CompletionRequest) -> list[dict[str, Any]]:
        # Gemini calls the assistant role "model", and carries the system
        # prompt in config rather than in the message list.
        return [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in request.messages
        ]

    def _config(self, request: CompletionRequest) -> genai_types.GenerateContentConfig:
        return genai_types.GenerateContentConfig(
            max_output_tokens=request.max_tokens,
            temperature=request.temperature,
            system_instruction=request.system,
            response_mime_type="application/json" if request.json_mode else None,
        )

    async def generate(self, request: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        text_parts: list[str] = []
        input_tokens = output_tokens = 0

        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=request.model,
                contents=self._contents(request),
                config=self._config(request),
            )
            async for chunk in stream:
                if chunk.text:
                    text_parts.append(chunk.text)
                    if request.on_delta:
                        request.on_delta(chunk.text)
                # Usage accumulates; the final chunk carries the totals.
                if chunk.usage_metadata:
                    input_tokens = chunk.usage_metadata.prompt_token_count or input_tokens
                    output_tokens = (
                        chunk.usage_metadata.candidates_token_count or output_tokens
                    )
        except Exception as exc:
            raise ProviderError(f"Google call failed: {exc}") from exc

        return CompletionResult(
            text="".join(text_parts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=request.model,
                contents=self._contents(request),
                config=self._config(request),
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise ProviderError(f"Google stream failed: {exc}") from exc
