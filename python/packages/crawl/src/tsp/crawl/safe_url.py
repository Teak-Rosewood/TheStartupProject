"""SSRF guard. Every URL the crawler touches (seed, link, redirect hop) goes through ``safe_url``.

It returns the address to connect to; callers must connect to that pinned IP and never resolve the
name again, which is what stops DNS rebinding.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import UnsafeURL

Resolver = Callable[[str], Awaitable[list[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_PORTS = (80, 443)
_SCHEME_PORT = {"http": 80, "https": 443}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_LEGACY_IPV4 = re.compile(r"[0-9a-fx.]+", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedURL:
    url: str
    scheme: str
    host: str  # as written, lower-case, no trailing dot; use for Host header and TLS SNI
    port: int
    ip: str  # the address to connect to


async def _system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(i[4][0]) for i in infos]


def _literal_ip(host: str) -> IPAddress | None:
    """Parse IP literals, including the legacy decimal, hex, octal and short IPv4 forms."""
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if _LEGACY_IPV4.fullmatch(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _is_public(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_public(ip.ipv4_mapped)
        if ip in _NAT64:
            return _is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.sixtofour is not None:
            return _is_public(ip.sixtofour)
    return ip.is_global and not ip.is_multicast


async def safe_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    allowed_ports: tuple[int, ...] = DEFAULT_PORTS,
) -> ResolvedURL:
    """Validate ``url`` and return the public IP to connect to. Raises ``UnsafeURL``."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as e:
        raise UnsafeURL(f"malformed URL: {e}") from e
    scheme = parts.scheme.lower()
    if scheme not in _SCHEME_PORT:
        raise UnsafeURL(f"scheme not allowed: {scheme or '(none)'}")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("credentials in URL not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("missing host")
    port = port or _SCHEME_PORT[scheme]
    if port not in allowed_ports:
        raise UnsafeURL(f"port not allowed: {port}")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURL("localhost not allowed")

    literal = _literal_ip(host)
    try:
        addresses = (
            [literal]
            if literal
            else [ipaddress.ip_address(a) for a in await (resolver or _system_resolver)(host)]
        )
    except (OSError, ValueError) as e:
        raise UnsafeURL(f"cannot resolve {host}: {e}") from e
    if not addresses:
        raise UnsafeURL(f"no address for {host}")
    # Every answer must be public: a mixed answer is how rebinding tricks get a private address in.
    for ip in addresses:
        if not _is_public(ip):
            raise UnsafeURL(f"{host} resolves to non-public address {ip}")
    return ResolvedURL(url=url.strip(), scheme=scheme, host=host, port=port, ip=str(addresses[0]))
