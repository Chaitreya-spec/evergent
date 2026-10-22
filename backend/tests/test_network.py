"""Client addresses behind proxies, refusing unsafe production settings, and
reading the database URLs hosting platforms hand out."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url
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


SECRET = "s" * 44


def _via_frontend(secret: str, client: str, peer: str = "198.51.100.20") -> Request:
    """A request as a frontend hosted elsewhere sends it: from an address no
    TRUSTED_PROXIES entry covers, vouched for by the shared secret."""
    headers = [(b"x-evergent-proxy-secret", secret.encode()), (b"x-evergent-client-ip", client.encode())]
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


@pytest.fixture
def proxy_secret(monkeypatch):
    monkeypatch.setattr(settings, "frontend_proxy_secret", SECRET)


class TestFrontendProxySecret:
    def test_the_frontend_reports_the_client(self, proxy_secret) -> None:
        assert net.client_ip(_via_frontend(SECRET, "203.0.113.9")) == "203.0.113.9"

    def test_a_wrong_secret_is_ignored(self, proxy_secret) -> None:
        assert net.client_ip(_via_frontend("x" * 44, "203.0.113.9")) == "198.51.100.20"

    def test_without_a_configured_secret_the_headers_mean_nothing(self, monkeypatch) -> None:
        """Otherwise any client could pick its own address by sending an empty secret."""
        monkeypatch.setattr(settings, "frontend_proxy_secret", None)
        assert net.client_ip(_via_frontend("", "203.0.113.9")) == "198.51.100.20"

    def test_a_vouched_for_request_without_an_address_falls_back_to_the_peer(self, proxy_secret) -> None:
        assert net.client_ip(_via_frontend(SECRET, "not-an-address")) == "198.51.100.20"

    def test_ipv6_clients_are_reported(self, proxy_secret) -> None:
        assert net.client_ip(_via_frontend(SECRET, "2001:db8::7")) == "2001:db8::7"


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

    def test_a_short_proxy_secret_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="FRONTEND_PROXY_SECRET"):
            Settings(
                environment="production",
                secret_key="x" * 48,
                encryption_key="k" * 44,
                frontend_proxy_secret="guessable",
                _env_file=None,
            )

    def test_an_empty_proxy_secret_means_none_is_set(self) -> None:
        configured = Settings(
            environment="production",
            secret_key="x" * 48,
            encryption_key="k" * 44,
            frontend_proxy_secret="",
            _env_file=None,
        )
        assert configured.is_production


class TestDatabaseUrl:
    @pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
    def test_platform_urls_get_the_async_driver(self, scheme: str) -> None:
        """Render, Railway, Heroku and Neon hand out URLs without a driver."""
        configured = Settings(database_url=f"{scheme}u:p@db.internal/evergent", _env_file=None)
        assert configured.database_url == "postgresql+asyncpg://u:p@db.internal/evergent"
        assert configured.sync_database_url == "postgresql://u:p@db.internal/evergent"

    @pytest.mark.parametrize(
        "url", ["postgresql+asyncpg://u:p@localhost/evergent", "sqlite+aiosqlite:///tmp/test.db"]
    )
    def test_urls_naming_a_driver_are_left_alone(self, url: str) -> None:
        assert Settings(database_url=url, _env_file=None).database_url == url

    def test_libpq_tls_options_are_translated_for_each_driver(self) -> None:
        """Neon's string, as copied: asyncpg refuses sslmode= and channel_binding=."""
        configured = Settings(
            database_url="postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb"
            "?sslmode=require&channel_binding=require",
            _env_file=None,
        )
        host = "ep-cool-123.ap-southeast-1.aws.neon.tech"
        assert configured.database_url == f"postgresql+asyncpg://u:p@{host}/neondb?ssl=require"
        assert configured.sync_database_url == f"postgresql://u:p@{host}/neondb?sslmode=require"

    def test_a_neon_pooled_url_connects_directly(self) -> None:
        configured = Settings(
            database_url="postgresql://u:p@ep-cool-123-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
            _env_file=None,
        )
        assert "@ep-cool-123.ap-southeast-1.aws.neon.tech/" in configured.database_url
        assert "@ep-cool-123.ap-southeast-1.aws.neon.tech/" in configured.sync_database_url

    def test_other_hosts_named_pooler_are_left_alone(self) -> None:
        configured = Settings(database_url="postgresql://u:p@db-pooler.example.com/app", _env_file=None)
        assert configured.database_url == "postgresql+asyncpg://u:p@db-pooler.example.com/app"

    @pytest.mark.parametrize(
        "pasted",
        [
            "psql 'postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require'",
            "  postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require\n",
            '"postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"',
            "DATABASE_URL='postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require'",
            'export DATABASE_URL="postgresql://u:p@ep-cool-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"',
        ],
    )
    def test_the_url_is_found_in_what_was_copied(self, pasted: str) -> None:
        """Neon's Connect box offers the string as a psql command and a .env line too."""
        configured = Settings(database_url=pasted, _env_file=None)
        host = "ep-cool-123.ap-southeast-1.aws.neon.tech"
        assert configured.database_url == f"postgresql+asyncpg://u:p@{host}/neondb?ssl=require"

    def test_something_that_is_not_a_url_is_named_clearly(self) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL is not a database URL") as caught:
            Settings(database_url="neondb_owner:hunter2-password", _env_file=None)
        assert "hunter2" not in str(caught.value)

    def test_a_page_address_is_named_clearly(self) -> None:
        """The browser's address bar is an easy thing to copy instead."""
        with pytest.raises(ValidationError, match="starts with https:// but should start with postgresql://"):
            Settings(database_url="https://console.neon.tech/app/projects/cool-123", _env_file=None)

    def test_errors_never_print_the_values_they_were_given(self) -> None:
        """They end up in the host's logs, and some of them are secrets."""
        with pytest.raises(ValidationError) as caught:
            Settings(environment="production", secret_key="short", frontend_proxy_secret="leaky-secret", _env_file=None)
        assert "leaky-secret" not in str(caught.value)

    def test_an_escaped_password_survives_the_rewrite(self) -> None:
        configured = Settings(database_url="postgresql://u:p%40ss%2Fw@h.example/db?sslmode=require", _env_file=None)
        assert configured.database_url == "postgresql+asyncpg://u:p%40ss%2Fw@h.example/db?ssl=require"
        assert make_url(configured.database_url).password == "p@ss/w"
