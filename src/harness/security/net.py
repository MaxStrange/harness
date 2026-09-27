"""Network policy (SEC3, SEC4): web skills never touch private addresses unless allowed by host."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]


class BlockedAddress(Exception):
    """The URL points at something the web skills must not reach."""


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def is_private_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return True  # unparseable is treated as unsafe
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


@dataclass
class NetPolicy:
    """``allowed_hosts`` are exact host names (or IPs) permitted despite being private."""

    allowed_hosts: frozenset[str]
    resolver: Resolver = default_resolver

    @classmethod
    def from_config(
        cls, allowed_private_hosts: list[str], searxng_url: str, resolver: Resolver | None = None
    ) -> NetPolicy:
        hosts = {h.lower() for h in allowed_private_hosts}
        searx_host = urlsplit(searxng_url).hostname
        if searx_host:
            hosts.add(searx_host.lower())  # the search backend is always reachable
        return cls(frozenset(hosts), resolver or default_resolver)

    def check_url(self, url: str) -> str:
        """Return the host name if ``url`` may be fetched; raise :class:`BlockedAddress` otherwise."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise BlockedAddress(f"only http and https URLs are allowed, not {parts.scheme!r}")
        host = parts.hostname
        if not host:
            raise BlockedAddress("URL has no host")
        if parts.username or parts.password:
            raise BlockedAddress("URLs with embedded credentials are not allowed")
        host_l = host.lower()
        if host_l in self.allowed_hosts:
            return host
        if host_l in ("localhost",) or host_l.endswith(
            (".localhost", ".local", ".internal", ".lan", ".home")
        ):
            raise BlockedAddress(f"{host} is a local network name")
        try:
            ipaddress.ip_address(host_l.strip("[]"))
            addresses = [host_l.strip("[]")]
        except ValueError:
            try:
                addresses = self.resolver(host)
            except OSError as exc:
                raise BlockedAddress(f"could not resolve {host}: {exc}") from exc
        if not addresses:
            raise BlockedAddress(f"{host} did not resolve to any address")
        for address in addresses:
            if is_private_ip(address):
                raise BlockedAddress(
                    f"{host} resolves to the private address {address}; web skills do not "
                    "reach the local network (add the host to web.allowed_private_hosts to permit it)"
                )
        return host
