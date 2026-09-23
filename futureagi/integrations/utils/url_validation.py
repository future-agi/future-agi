"""
URL validation utility for preventing SSRF attacks.

Validates user-supplied URLs to ensure they:
1. Use only HTTPS (or HTTP for explicitly allowed hosts)
2. Don't resolve to private/internal IP ranges
3. Don't point to cloud metadata endpoints
4. Don't contain path traversal sequences
"""

import ipaddress
import socket
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)

# Cloud metadata endpoints that must always be blocked
_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",                  # AWS/Azure metadata
    "metadata.google.internal",          # GCP metadata
    "metadata.google.com",
    "100.100.100.200",                   # Alibaba Cloud metadata
    "169.254.170.2",                     # AWS ECS task metadata
})

# Hostnames of internal services (Docker compose service names)
_INTERNAL_HOSTS = frozenset({
    "localhost", "db", "redis", "clickhouse", "temporal",
    "pgbouncer", "rabbitmq", "backend", "serving", "agentcc-gateway",
    "code-executor", "peerdb-catalog", "peerdb-minio",
})

# Private IP ranges that should never be targeted by external URLs
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("100.64.0.0/10"),     # shared address space (CGN)
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 private
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def validate_external_url(url: str) -> str | None:
    """Validate a URL for safe external HTTP requests.

    Returns None if the URL is safe, or an error message string if it
    should be rejected.
    """
    if not url or not url.strip():
        return None  # Empty URLs are handled elsewhere

    try:
        parsed = urlparse(url)
    except Exception:
        return "Malformed URL"

    # 1. Scheme validation
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme: {parsed.scheme!r}. Only http/https allowed."

    # 2. Hostname must be present
    hostname = parsed.hostname
    if not hostname:
        return "URL must include a hostname"

    # 3. Block cloud metadata endpoints
    hostname_lower = hostname.lower()
    if hostname_lower in _BLOCKED_HOSTS:
        return f"Access to {hostname!r} is blocked (cloud metadata endpoint)"

    # 4. Block known internal service hostnames
    if hostname_lower in _INTERNAL_HOSTS:
        return f"Access to internal service {hostname!r} is not allowed"

    # 5. Block path traversal in URL path
    if parsed.path and ".." in parsed.path:
        return "URL path contains disallowed traversal sequence '..'"

    # 6. Resolve hostname and check against private IP ranges
    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for _family, _type, _proto, _canonname, sockaddr in resolved_ips:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if any(ip in network for network in _PRIVATE_NETWORKS):
                    logger.warning(
                        "ssrf_blocked_private_ip",
                        url=url,
                        resolved_ip=ip_str,
                    )
                    return (
                        f"URL resolves to private/internal IP ({ip_str}). "
                        "Only publicly routable addresses are allowed."
                    )
            except ValueError:
                continue
    except socket.gaierror:
        # DNS resolution failed — this is fine, the downstream request
        # will also fail with a more descriptive error.
        pass
    except Exception as exc:
        logger.warning("ssrf_url_validation_error", url=url, error=str(exc))
        return f"URL validation failed: {exc}"

    return None  # URL is safe
