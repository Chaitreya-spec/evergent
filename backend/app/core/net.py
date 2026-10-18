"""The address of the client behind a request.

Browsers reach the API through the frontend server (and usually a reverse
proxy before it), so the TCP peer is a proxy, not the client. The client's
address is taken from X-Forwarded-For — but only when the peer is a proxy
listed in TRUSTED_PROXIES; otherwise anyone could claim any address and dodge
the rate limit or forge the audit trail.
"""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request

from app.core.config import settings


@lru_cache(maxsize=1)
def _trusted() -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(entry, strict=False) for entry in settings.trusted_proxies)


def _is_trusted(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _trusted())


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    if not _is_trusted(peer):
        return peer or "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    # Walk back from the nearest hop; the first address no trusted proxy
    # vouches for is the client.
    for hop in reversed(hops):
        if not _is_trusted(hop):
            return hop
    return hops[0] if hops else (peer or "unknown")
