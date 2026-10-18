"""Provider registry.

Adding a provider means writing one class and registering it here. Nothing
else in the system changes — that is the extension point the spec asks for.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NoProviderAvailableError
from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.base import AIProvider
from app.services.providers.google_provider import GoogleProvider
from app.services.providers.openai_compatible import (
    CompatibleGatewayProvider,
    OpenAIProvider,
    OpenRouterProvider,
)

log = get_logger(__name__)

# name -> factory. Instances are cached per process after first use.
PROVIDER_FACTORIES: dict[str, type[AIProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
    "compatible": CompatibleGatewayProvider,
}

_instances: dict[str, AIProvider] = {}
# name -> (api key, base url) stored in the providers table. Each falls back
# to the environment when the row leaves it unset.
_stored: dict[str, tuple[str | None, str | None]] = {}
_disabled: set[str] = set()


def get_provider(name: str) -> AIProvider:
    """Return the provider by name, constructing it on first use."""
    key = name.lower()
    if key not in PROVIDER_FACTORIES:
        raise NoProviderAvailableError(
            f"Unknown provider '{name}'. Known: {', '.join(sorted(PROVIDER_FACTORIES))}"
        )
    if key not in _instances:
        api_key, base_url = _stored.get(key, (None, None))
        _instances[key] = PROVIDER_FACTORIES[key](api_key=api_key, base_url=base_url)
    return _instances[key]


def credential_source(name: str) -> str | None:
    """Where a provider's credential comes from: "database", "environment" or None."""
    if _stored.get(name, (None, None))[0]:
        return "database"
    return "environment" if get_provider(name).is_configured() else None


async def load_credentials(session: AsyncSession) -> None:
    """Read keys stored through the API, so calls actually use them.

    Called at startup, at the start of every run and after any change to a
    provider. A key that cannot be decrypted is skipped, not fatal.
    """
    from app.models.entities import Provider

    stored: dict[str, tuple[str | None, str | None]] = {}
    disabled: set[str] = set()
    for row in await session.scalars(select(Provider)):
        key = None
        if row.encrypted_api_key:
            try:
                key = decrypt_secret(row.encrypted_api_key)
            except ValueError as exc:
                log.error("provider_key_unreadable", provider=row.name, error=str(exc))
        stored[row.name] = (key, row.base_url)
        if not row.is_enabled:
            disabled.add(row.name)
    _stored.clear()
    _stored.update(stored)
    _disabled.clear()
    _disabled.update(disabled)
    _instances.clear()


def configured_providers() -> list[AIProvider]:
    """Providers that actually have credentials, in registration order."""
    found: list[AIProvider] = []
    for name in PROVIDER_FACTORIES:
        if name in _disabled:
            continue
        provider = get_provider(name)
        if provider.is_configured():
            found.append(provider)
    return found


def reset_provider_cache() -> None:
    """Drop cached instances. Used by tests and after credential changes."""
    _instances.clear()
