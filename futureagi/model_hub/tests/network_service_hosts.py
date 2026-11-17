"""Explicit local fixture destinations for backend test network guards."""

import ipaddress
import os
from urllib.parse import urlsplit

LOOPBACK_NAMES = frozenset(
    {"", "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)
SERVICE_HOST_SETTINGS = (
    "PG_HOST",
    "REDIS_URL",
    "REDIS_LOCK_URL",
    "REDIS_CACHE_URL",
    "CH_HOST",
    "CH25_HOST",
    "MINIO_ENDPOINT",
    "MINIO_URL",
)
# Exact compose names only: configuration cannot add arbitrary DNS suffixes.
SERVICE_NAMES = frozenset(
    {
        "postgres",
        "db",
        "test-db",
        "redis",
        "test-redis",
        "clickhouse",
        "clickhouse25",
        "minio",
    }
)
FIXTURE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def host_text(host: object) -> str:
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    text = str(host).strip().lower().rstrip(".")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text


def host_ip(text: str):
    try:
        # Zone identifiers belong only to IPv6. An IPv4-looking name with a
        # percent suffix is still a hostname and must never enter native DNS.
        return ipaddress.ip_address(text.split("%", 1)[0] if ":" in text else text)
    except ValueError:
        return None


def is_loopback_host(host: object) -> bool:
    if host is None:
        return True
    text = host_text(host)
    if text in LOOPBACK_NAMES:
        return True
    address = host_ip(text)
    if address is None:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(
        address.is_loopback
        or address.is_unspecified
        or (mapped is not None and (mapped.is_loopback or mapped.is_unspecified))
    )


def is_fixture_address(host: object) -> bool:
    """Only loopback or RFC1918; no link-local, public, or IPv6 metadata IPs."""
    address = host_ip(host_text(host))
    if address is None:
        return False
    address = getattr(address, "ipv4_mapped", None) or address
    return address.is_loopback or any(
        address in network for network in FIXTURE_NETWORKS
    )


def _setting_host(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return host_text(parsed.hostname) if parsed.hostname else None


def configured_service_hosts(environ=os.environ) -> frozenset[str]:
    """Select fixture names/IPs before any resolution, never arbitrary hosts."""
    hosts = {_setting_host(environ.get(name, "")) for name in SERVICE_HOST_SETTINGS}
    try:
        from django.conf import settings

        if settings.configured:
            for database in settings.DATABASES.values():
                hosts.add(_setting_host(str(database.get("HOST") or "")))
    except Exception:
        pass
    return frozenset(
        host
        for host in hosts
        if host
        and not is_loopback_host(host)
        and (host in SERVICE_NAMES or is_fixture_address(host))
    )
