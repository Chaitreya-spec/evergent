"""A provider that stops answering fails the call instead of hanging the run.

Found against a real gateway: OmniRoute held some requests open indefinitely,
and with the SDKs' defaults (ten minutes per attempt, three attempts) a build
sat in "planning" for half an hour before anything was reported.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.config import settings
from app.core.errors import ProviderError
from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.base import ChatMessage, CompletionRequest
from app.services.providers.google_provider import GoogleProvider
from app.services.providers.openai_compatible import CompatibleGatewayProvider


@pytest.fixture
def short_limits(monkeypatch):
    monkeypatch.setattr(settings, "model_timeout_seconds", 0.5)
    monkeypatch.setattr(settings, "model_max_retries", 0)


async def test_a_gateway_that_never_answers_fails_the_call(short_limits) -> None:
    release = asyncio.Event()

    async def accept_and_say_nothing(reader, writer) -> None:
        await reader.read(65536)
        await release.wait()
        writer.close()

    server = await asyncio.start_server(accept_and_say_nothing, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        provider = CompatibleGatewayProvider(
            api_key="k", base_url=f"http://127.0.0.1:{port}/v1"
        )
        started = time.monotonic()
        with pytest.raises(ProviderError):
            await provider.generate(
                CompletionRequest(
                    model="gemini/gemini-pro-latest", messages=[ChatMessage("user", "hi")]
                )
            )
        assert time.monotonic() - started < 5
    finally:
        release.set()
        server.close()
        await server.wait_closed()


def test_every_provider_client_carries_the_limits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "model_timeout_seconds", 42.0)
    monkeypatch.setattr(settings, "model_max_retries", 0)

    gateway = CompatibleGatewayProvider(
        api_key="k", base_url="http://127.0.0.1:9/v1"
    ).client
    anthropic = AnthropicProvider(api_key="k").client
    google = GoogleProvider(api_key="k").client

    assert (gateway.timeout, gateway.max_retries) == (42.0, 0)
    assert (anthropic.timeout, anthropic.max_retries) == (42.0, 0)
    assert google._api_client._http_options.timeout == 42_000
