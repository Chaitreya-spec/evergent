"""Seed the provider and model catalog.

Capability scores are hand-seeded priors, deliberately. A benchmark job
rewrites them later and the router improves with no code change — scores live
in data, never in branching logic.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import Model, Provider

log = get_logger(__name__)

# provider -> (display, base_url)
PROVIDERS: dict[str, tuple[str, str | None]] = {
    "anthropic": ("Anthropic", None),
    "openai": ("OpenAI", None),
    "google": ("Google AI", None),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "compatible": ("Compatible Gateway", None),
}

# Prices are USD per token (per-million divided by 1e6).
MODELS: list[dict] = [
    {
        "provider": "anthropic",
        "name": "claude-opus-5",
        "display_name": "Claude Opus 5",
        "remote_id": "claude-opus-5",
        "context_window": 1_000_000,
        "max_output_tokens": 64_000,
        "cost_per_input_token": 5 / 1e6,
        "cost_per_output_token": 25 / 1e6,
        "avg_latency_ms": 9000,
        "capabilities": {
            "reasoning": 9.7, "coding": 9.6, "backend": 9.6, "frontend": 9.2,
            "debugging": 9.7, "mathematics": 9.3, "research": 9.2,
            "writing": 9.3, "long_context": 9.7, "structured_output": 9.5,
            "tool_use": 9.6, "vision": 9.0,
        },
    },
    {
        "provider": "anthropic",
        "name": "claude-sonnet-5",
        "display_name": "Claude Sonnet 5",
        "remote_id": "claude-sonnet-5",
        "context_window": 1_000_000,
        "max_output_tokens": 64_000,
        "cost_per_input_token": 2 / 1e6,
        "cost_per_output_token": 10 / 1e6,
        "avg_latency_ms": 5000,
        "capabilities": {
            "reasoning": 9.1, "coding": 9.2, "backend": 9.2, "frontend": 9.0,
            "debugging": 9.0, "mathematics": 8.9, "research": 8.9,
            "writing": 9.0, "long_context": 9.3, "structured_output": 9.2,
            "tool_use": 9.2, "vision": 8.8,
        },
    },
    {
        "provider": "anthropic",
        "name": "claude-haiku-4-5",
        "display_name": "Claude Haiku 4.5",
        "remote_id": "claude-haiku-4-5",
        "context_window": 200_000,
        "max_output_tokens": 16_000,
        "cost_per_input_token": 1 / 1e6,
        "cost_per_output_token": 5 / 1e6,
        "avg_latency_ms": 2200,
        "capabilities": {
            "reasoning": 8.2, "coding": 8.3, "backend": 8.2, "frontend": 8.2,
            "debugging": 8.1, "mathematics": 8.0, "research": 8.2,
            "writing": 8.6, "long_context": 8.4, "structured_output": 8.6,
            "tool_use": 8.5, "vision": 8.0,
        },
    },
    {
        "provider": "openai",
        "name": "gpt-5.2",
        "display_name": "GPT-5.2",
        "remote_id": "gpt-5.2",
        "context_window": 400_000,
        "max_output_tokens": 64_000,
        "cost_per_input_token": 1.25 / 1e6,
        "cost_per_output_token": 10 / 1e6,
        "avg_latency_ms": 6000,
        "capabilities": {
            "reasoning": 9.4, "coding": 9.3, "backend": 9.3, "frontend": 9.0,
            "debugging": 9.2, "mathematics": 9.4, "research": 9.4,
            "writing": 9.2, "long_context": 9.0, "structured_output": 9.5,
            "tool_use": 9.4, "vision": 9.1,
        },
    },
    # Google's "-latest" aliases, so these rows do not go stale as versions
    # are retired. Checked against a real key on 2026-10-18: both answer;
    # Pro models need a paid quota (free keys get 429), so none is seeded.
    {
        "provider": "google",
        "name": "gemini-flash",
        "display_name": "Gemini Flash",
        "remote_id": "gemini-flash-latest",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "cost_per_input_token": 0.30 / 1e6,
        "cost_per_output_token": 2.50 / 1e6,
        "avg_latency_ms": 2500,
        "capabilities": {
            "reasoning": 8.6, "coding": 8.6, "backend": 8.5, "frontend": 8.9,
            "debugging": 8.3, "mathematics": 8.7, "research": 8.5,
            "writing": 8.6, "long_context": 9.2, "structured_output": 8.8,
            "tool_use": 8.6, "vision": 9.0,
        },
    },
    {
        "provider": "google",
        "name": "gemini-flash-lite",
        "display_name": "Gemini Flash-Lite",
        "remote_id": "gemini-flash-lite-latest",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "cost_per_input_token": 0.10 / 1e6,
        "cost_per_output_token": 0.40 / 1e6,
        "avg_latency_ms": 1500,
        "capabilities": {
            "reasoning": 7.6, "coding": 7.5, "backend": 7.4, "frontend": 7.8,
            "debugging": 7.2, "mathematics": 7.6, "research": 7.6,
            "writing": 8.0, "long_context": 8.8, "structured_output": 8.3,
            "tool_use": 7.8, "vision": 8.4,
        },
    },
    # --- Through the model gateway (COMPATIBLE_BASE_URL) -------------------
    # OmniRoute names models "<provider>/<model>". The "-latest" aliases follow
    # Google's current release, so these rows do not go stale as versions are
    # retired (2.5 is already closed to new users). Scores and prices are
    # priors for whatever each alias points at today.
    {
        "provider": "compatible",
        "name": "gemini-pro-latest",
        "display_name": "Gemini Pro (latest)",
        "remote_id": "gemini/gemini-pro-latest",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "cost_per_input_token": 1.25 / 1e6,
        "cost_per_output_token": 10 / 1e6,
        "avg_latency_ms": 6000,
        "capabilities": {
            "reasoning": 9.1, "coding": 9.0, "backend": 8.9, "frontend": 9.5,
            "debugging": 8.8, "mathematics": 9.2, "research": 9.0,
            "writing": 9.0, "long_context": 9.6, "structured_output": 9.1,
            "tool_use": 9.0, "vision": 9.6,
        },
    },
    {
        "provider": "compatible",
        "name": "gemini-flash-latest",
        "display_name": "Gemini Flash (latest)",
        "remote_id": "gemini/gemini-flash-latest",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "cost_per_input_token": 0.30 / 1e6,
        "cost_per_output_token": 2.50 / 1e6,
        "avg_latency_ms": 2500,
        "capabilities": {
            "reasoning": 8.6, "coding": 8.6, "backend": 8.5, "frontend": 8.9,
            "debugging": 8.3, "mathematics": 8.7, "research": 8.5,
            "writing": 8.6, "long_context": 9.2, "structured_output": 8.8,
            "tool_use": 8.6, "vision": 9.0,
        },
    },
    {
        "provider": "compatible",
        "name": "gemini-flash-lite-latest",
        "display_name": "Gemini Flash-Lite (latest)",
        "remote_id": "gemini/gemini-flash-lite-latest",
        "context_window": 1_048_576,
        "max_output_tokens": 65_536,
        "cost_per_input_token": 0.10 / 1e6,
        "cost_per_output_token": 0.40 / 1e6,
        "avg_latency_ms": 1500,
        "capabilities": {
            "reasoning": 7.6, "coding": 7.5, "backend": 7.4, "frontend": 7.8,
            "debugging": 7.2, "mathematics": 7.6, "research": 7.6,
            "writing": 8.0, "long_context": 8.8, "structured_output": 8.3,
            "tool_use": 7.8, "vision": 8.4,
        },
    },
]


# Models no longer offered. Seeding switches them off where they already
# exist, so the router stops choosing them; their history is kept.
RETIRED_MODELS = {"gemini-3-pro"}


async def seed_catalog(session: AsyncSession) -> tuple[int, int]:
    """Idempotent: safe to run on every boot and in tests."""
    providers_added = models_added = 0

    by_name: dict[str, Provider] = {}
    for name, (display, base_url) in PROVIDERS.items():
        provider = await session.scalar(select(Provider).where(Provider.name == name))
        if provider is None:
            provider = Provider(name=name, display_name=display, base_url=base_url)
            session.add(provider)
            providers_added += 1
        by_name[name] = provider
    await session.flush()

    for spec in MODELS:
        provider = by_name[spec["provider"]]
        existing = await session.scalar(
            select(Model).where(
                Model.provider_id == provider.id, Model.name == spec["name"]
            )
        )
        if existing is not None:
            continue
        session.add(
            Model(
                provider_id=provider.id,
                name=spec["name"],
                display_name=spec["display_name"],
                remote_id=spec["remote_id"],
                context_window=spec["context_window"],
                max_output_tokens=spec["max_output_tokens"],
                cost_per_input_token=spec["cost_per_input_token"],
                cost_per_output_token=spec["cost_per_output_token"],
                avg_latency_ms=spec["avg_latency_ms"],
                capabilities=spec["capabilities"],
            )
        )
        models_added += 1

    for retired in await session.scalars(select(Model).where(Model.name.in_(RETIRED_MODELS))):
        retired.is_enabled = False

    await session.commit()
    log.info("catalog_seeded", providers=providers_added, models=models_added)
    return providers_added, models_added
