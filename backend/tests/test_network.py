"""Client addresses behind proxies, and refusing unsafe production settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core import net
from app.core.config import Settings, settings


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


@pytest.fixture
def trust_private(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", ["127.0.0.1/32", "10.0.0.0/8"])
    net._trusted.cache_clear()
    yield
    net._trusted.cache_clear()


class TestClientIp:
    def test_a_trusted_proxy_passes_the_client_through(self, trust_private) -> None:
        assert net.client_ip(_request("10.0.0.5", "203.0.113.9")) == "203.0.113.9"

    def test_hops_through_several_proxies(self, trust_private) -> None:
        assert net.client_ip(_request("127.0.0.1", "198.51.100.1, 203.0.113.9, 10.0.0.7")) == "203.0.113.9"

    def test_an_untrusted_peer_cannot_claim_an_address(self, trust_private) -> None:
        assert net.client_ip(_request("198.51.100.20", "1.2.3.4")) == "198.51.100.20"

    def test_without_the_header_it_is_the_peer(self, trust_private) -> None:
        assert net.client_ip(_request("10.0.0.5")) == "10.0.0.5"


class TestProductionSettings:
    def test_development_defaults_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY") as caught:
            Settings(environment="production", secret_key="dev-only-insecure-change-me", _env_file=None)
        assert "ENCRYPTION_KEY" in str(caught.value)

    def test_a_proper_configuration_starts(self) -> None:
        configured = Settings(
            environment="production",
            secret_key="x" * 48,
            encryption_key="k" * 44,
            debug=False,
            _env_file=None,
        )
        assert configured.is_production

    def test_lists_accept_commas_from_the_environment(self, monkeypatch) -> None:
        """Regression: a comma-separated CORS_ORIGINS, as documented, failed to parse."""
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8, 172.16.0.0/12")
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")
        configured = Settings(_env_file=None)
        assert configured.trusted_proxies == ["10.0.0.0/8", "172.16.0.0/12"]
        assert configured.cors_origins == ["https://a.example", "https://b.example"]
