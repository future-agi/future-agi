"""The client address Omega's credential limits group attempts by (SB W2-KEYS, READINESS L03).

A per-key limit alone lets anyone who knows a key's public id lock its owner out: their wrong
secrets spend the budget the owner's right one needs. So each key attempt is also counted per
source, and the per-source limit is what a single flooding client meets (omega_session_authentication).

The source is the first address, reading ``X-Forwarded-For`` from the right, that was not written by
a proxy this deployment trusts. ``OMEGA_SESSION_TRUSTED_PROXIES`` lists those proxies as addresses or
CIDR ranges, comma-separated (the Omega gateway, the ingress). Empty, the default and the only safe
value when nothing is known about the ingress: only ``REMOTE_ADDR`` counts, and a client-written
``X-Forwarded-For`` is never believed. A hop that is not an address ends the walk at the proxy that
wrote it (stricter, never looser).

IPv4 addresses are grouped one by one, IPv6 by /64: a single host is usually given a whole /64, so
counting each IPv6 address apart would make the limit free to step around.

The Omega Hub has its own helper (``omega_hub/client_ip.py``, a proxy depth). This one names
proxies instead of a depth because the exchange is reached by two paths of different depth: directly
through the ingress, and through the gateway, which sends the client it saw as ``X-Forwarded-For``.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
from functools import lru_cache

from django.conf import settings

TRUSTED_PROXIES_ENV = "OMEGA_SESSION_TRUSTED_PROXIES"


def _address(value):
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and "]" in value:  # "[2001:db8::1]:443"
        value = value[1 : value.index("]")]
    elif value.count(":") == 1:  # "203.0.113.7:51234"
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _networks(raw: str):
    networks = []
    for part in raw.split(","):
        if part.strip():
            # A malformed entry stops the process at the first request rather than trusting less
            # or more than the operator wrote.
            networks.append(ipaddress.ip_network(part.strip(), strict=False))
    return tuple(networks)


def trusted_proxies():
    return _networks(os.environ.get(TRUSTED_PROXIES_ENV, ""))


def _trusted(address, networks) -> bool:
    return any(address.version == n.version and address in n for n in networks)


def client_address(request):
    """The client's address (an ``ipaddress`` object), or None when ``REMOTE_ADDR`` is unusable."""
    current = _address(request.META.get("REMOTE_ADDR"))
    networks = trusted_proxies()
    if current is None or not networks:
        return current
    hops = [
        hop
        for hop in (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")
        if hop.strip()
    ]
    while hops and _trusted(current, networks):
        hop = _address(hops.pop())
        if hop is None:
            break
        current = hop
    return current


def client_source(request) -> str:
    """A short keyed digest of the client's address group; the address itself is never stored."""
    address = client_address(request)
    mapped = getattr(
        address, "ipv4_mapped", None
    )  # "::ffff:203.0.113.7" is that IPv4 client
    if address is None:
        group = "unknown"
    elif mapped is not None:
        group = str(mapped)
    elif address.version == 6:
        group = str(ipaddress.ip_network(f"{address}/64", strict=False))
    else:
        group = str(address)
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, group.encode("ascii"), hashlib.sha256).hexdigest()[:24]
