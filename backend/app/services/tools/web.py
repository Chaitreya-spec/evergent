"""Web search and page fetching for agents.

Fetching is the dangerous half. A URL chosen by a model — possibly steered by a
web page it just read — must never reach the machine's own network: not
localhost, not the database port, not a cloud metadata endpoint. So every
fetch resolves the host first and refuses anything that is not a public
address, checks again where the connection actually landed (against DNS
rebinding), and follows redirects by hand so each hop is checked too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import settings

USER_AGENT = "Evergent/1.0 (engineering-room agent; read-only fetch)"
MAX_REDIRECTS = 5

TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class BlockedURL(Exception):
    pass


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(slots=True)
class Page:
    url: str
    status: int
    content_type: str
    title: str | None
    text: str
    truncated: bool


# --- search -----------------------------------------------------------------


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


class SearchBackend(ABC):
    name: str

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport, timeout=20, headers={"User-Agent": USER_AGENT}
        )

    @abstractmethod
    async def search(self, query: str, max_results: int) -> list[SearchHit]: ...


class TavilySearch(SearchBackend):
    name = "tavily"

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        async with self._client() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
        response.raise_for_status()
        return [
            SearchHit(r.get("title", ""), r.get("url", ""), r.get("content", ""))
            for r in response.json().get("results", [])
        ]


class BraveSearch(SearchBackend):
    name = "brave"

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        async with self._client() as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                params={"q": query, "count": max_results},
            )
        response.raise_for_status()
        return [
            SearchHit(
                _strip_tags(r.get("title", "")),
                r.get("url", ""),
                _strip_tags(r.get("description", "")),
            )
            for r in (response.json().get("web") or {}).get("results", [])
        ]


def get_search_backend() -> SearchBackend | None:
    if settings.tavily_api_key:
        return TavilySearch(settings.tavily_api_key)
    if settings.brave_search_api_key:
        return BraveSearch(settings.brave_search_api_key)
    return None


# --- fetching -----------------------------------------------------------------


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global excludes loopback, private ranges, link-local (including the
    # 169.254.169.254 metadata service), carrier-grade NAT and reserved space.
    return ip.is_global and not ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return sorted({info[4][0] for info in infos})


async def check_public(host: str, port: int, resolver: Resolver) -> None:
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolver(host, port)
        except OSError as exc:
            raise BlockedURL(f"could not resolve {host}: {exc}") from exc
    if not addresses:
        raise BlockedURL(f"{host} did not resolve")
    # Every address must be public: one private record among public ones is
    # exactly how a rebinding attack is set up.
    private = [a for a in addresses if not _public(a)]
    if private:
        raise BlockedURL(f"{host} resolves to a non-public address ({private[0]})")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOCK = {
        "p", "div", "ul", "ol", "table", "section", "article",
        "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
    }  # fmt: skip
    # Start a single new line rather than a paragraph.
    LINE = {"br", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.LINE:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if self._in_title:
            self.title = (self.title or "") + data.strip()
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str | None, str]:
    parser = _Text()
    parser.feed(html)
    lines = (
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in "".join(parser.parts).split("\n")
    )
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return parser.title, text


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.resolver = resolver
        self.transport = transport
        self.max_bytes = max_bytes or settings.web_fetch_max_bytes
        self.timeout = timeout or settings.web_fetch_timeout_seconds

    async def fetch(self, url: str) -> Page:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1",
            },
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https"):
                    raise BlockedURL("only http and https URLs can be fetched")
                if not parts.hostname:
                    raise BlockedURL("the URL has no host")
                if parts.username or parts.password:
                    raise BlockedURL("URLs with embedded credentials are not fetched")
                port = parts.port or (443 if parts.scheme == "https" else 80)
                await check_public(parts.hostname, port, self.resolver)

                async with client.stream("GET", url) as response:
                    self._check_peer(response)
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue

                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    if content_type and not content_type.startswith(TEXT_TYPES):
                        return Page(url, response.status_code, content_type, None,
                                    f"[{content_type} content is not text and was not read]", False)  # fmt: skip

                    body = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= self.max_bytes:
                            truncated = True
                            break
                    text = bytes(body[: self.max_bytes]).decode(
                        response.encoding or "utf-8", errors="replace"
                    )
                    title = None
                    if "html" in content_type or text.lstrip()[:15].lower().startswith(
                        ("<!doctype", "<html")
                    ):
                        title, text = html_to_text(text)
                    return Page(
                        url, response.status_code, content_type, title, text, truncated
                    )

            raise BlockedURL(f"more than {MAX_REDIRECTS} redirects")

    @staticmethod
    def _check_peer(response: httpx.Response) -> None:
        """Refuse if the connection itself landed on a non-public address.

        The name was checked before connecting, but DNS can answer differently
        the second time. This checks where the socket actually went.
        """
        stream = response.extensions.get("network_stream")
        if stream is None:
            return
        peer = stream.get_extra_info("server_addr")
        if peer and not _public(str(peer[0])):
            raise BlockedURL(f"connection landed on a non-public address ({peer[0]})")
