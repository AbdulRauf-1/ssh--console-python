"""Outbound abuse guard. A tool that will dial any address on request is a scanning
and SSRF vector waiting to be misused. This refuses the addresses that are never a
legitimate SSH target and are the classic abuse destinations: link-local — which
includes the cloud metadata endpoint 169.254.169.254 — and the unspecified /
multicast ranges.

It deliberately does NOT block private ranges (10/8, 192.168/16, 172.16/12):
managing servers on a private network is exactly what SSH Console is for, so
blocking them would break the common self-hosted use. The guard is narrow on
purpose.

Go pinned the check to the dialer's Control hook (the actual resolved socket
address). AsyncSSH resolves internally, so here we resolve up front, refuse if ANY
resolved address is blocked, and then connect to a vetted address — which closes
the same DNS-rebinding gap a naive resolve-then-dial check would leave open.
"""

from __future__ import annotations

import ipaddress
import socket


class BlockedTarget(Exception):
    """Raised when a dial is refused because the target is a link-local / metadata
    (or otherwise non-routable) address."""


def is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """Whether an address must never be dialled. Pure, so it is tested directly."""
    return bool(
        ip.is_link_local          # 169.254.0.0/16 and fe80::/10 (includes cloud metadata)
        or ip.is_multicast
        or ip.is_unspecified      # 0.0.0.0 / ::
    )


def resolve_and_vet(host: str, port: int) -> str:
    """Resolve host:port, refuse if any resolved address is blocked, and return a
    single vetted IP to connect to (closing the rebinding gap). Raises BlockedTarget
    on a blocked address; lets socket.gaierror surface for a genuine DNS failure."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos:
        raise socket.gaierror(f"could not resolve {host}")
    chosen: str | None = None
    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        if is_blocked_ip(ip):
            raise BlockedTarget(
                f"refusing to connect to {ip}: link-local and metadata addresses "
                f"are not valid SSH targets"
            )
        if chosen is None:
            chosen = ip_str
    assert chosen is not None
    return chosen
