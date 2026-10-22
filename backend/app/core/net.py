"""The address of the client behind a request.

Browsers reach the API through the frontend server (and usually a reverse
proxy before it), so the TCP peer is a proxy, not the client. The client's
address is taken from X-Forwarded-For — but only when the peer is a proxy
listed in TRUSTED_PROXIES; otherwise anyone could claim any address and dodge
the rate limit or forge the audit trail.

A frontend hosted apart from the API (on Vercel, say) cannot be listed: its
outbound address changes and the platform's own proxies sit in between. It
proves itself with FRONTEND_PROXY_SECRET instead, and reports the client in
X-Evergent-Client-IP, which no proxy on the way adds or rewrites.
"""

from __future__ import annotations

import hmac
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request

from app.core.config import settings

PROXY_SECRET_HEADER = "x-evergent-proxy-secret"
CLIENT_IP_HEADER = "x-evergent-client-ip"


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


def _from_frontend(request: Request) -> bool:
    expected = settings.frontend_proxy_secret
    offered = request.headers.get(PROXY_SECRET_HEADER)
    if not expected or not offered:
        return False
    return hmac.compare_digest(expected.encode(), offered.encode())


def client_ip(request: Request) -> str:
    if _from_frontend(request):
        try:
            return str(ip_address(request.headers.get(CLIENT_IP_HEADER, "").strip()))
        except ValueError:
            pass  # vouched for, but with no usable address: judge it like any request
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
