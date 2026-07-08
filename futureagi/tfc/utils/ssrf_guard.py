"""SSRF-safe URL fetch primitive.

The IP that gets validated is the exact IP that gets connected to (no
DNS-rebinding TOCTOU gap), and every redirect hop is re-validated.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import certifi
import urllib3

_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT_SECONDS = 5
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024

# 100.64.0.0/10 is RFC 6598 carrier-grade NAT (used by some cloud providers,
# Tailscale). Not covered by ipaddress.is_private/is_reserved.
_EXTRA_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


class SsrfResponse:
    """Small requests.Response-like object returned by safe_fetch.

    `content` is fully buffered in memory (bounded by max_bytes). `iter_content`
    yields chunks of that already-loaded buffer — it does NOT stream; keeping
    the method for drop-in compatibility with callers migrated off requests.
    """

    __slots__ = ("status_code", "headers", "content", "final_url")

    def __init__(self, status_code, headers, content, final_url):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.final_url = final_url

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise urllib3.exceptions.HTTPError(f"HTTP {self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def is_valid_url(url_string: str) -> bool:
    """Cheap syntactic URL check (no network). Does not imply safety."""
    try:
        parsed = urlparse(url_string)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _reject_unsafe_ip(ip_str: str, host: str) -> None:
    ip = ipaddress.ip_address(ip_str)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers 169.254.169.254 metadata endpoint
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in net for net in _EXTRA_BLOCKED_NETWORKS)
    ):
        raise ValueError(
            f"URL host '{host}' resolves to a private/internal address."
        )


def _resolve_pinned_ip(host: str) -> str:
    """Resolve `host` and validate every A/AAAA record. Returns one pinned IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve URL host '{host}': {e}") from None

    resolved_ips = sorted({info[4][0] for info in infos})
    if not resolved_ips:
        raise ValueError(f"Cannot resolve URL host '{host}'.")

    for ip_str in resolved_ips:
        _reject_unsafe_ip(ip_str, host)
    return resolved_ips[0]


def _open_pinned_pool(scheme, pinned_ip, host, port, timeout):
    if scheme == "https":
        return urllib3.HTTPSConnectionPool(
            pinned_ip,
            port=port,
            timeout=timeout,
            retries=False,
            assert_hostname=host,
            server_hostname=host,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
        )
    return urllib3.HTTPConnectionPool(
        pinned_ip, port=port, timeout=timeout, retries=False
    )


def safe_fetch(
    url: str,
    *,
    method: str = "HEAD",
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    headers: dict = None,
) -> SsrfResponse:
    """Fetch `url` with SSRF protection.

    Method: HEAD or GET. For GET, the body is buffered up to `max_bytes`.
    Redirects are followed manually (5 hops max), re-validating each hop.
    Raises ValueError on any SSRF-relevant rejection.
    """
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: '{parsed.scheme}'.")
        host = parsed.hostname
        if not host:
            raise ValueError("Invalid URL: missing host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        pinned_ip = _resolve_pinned_ip(host)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        pool = _open_pinned_pool(parsed.scheme, pinned_ip, host, port, timeout)
        response = None
        try:
            response = pool.request(
                method,
                path,
                headers={"Host": host, **(headers or {})},
                redirect=False,
                preload_content=False,
            )

            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect with no Location header.")
                current_url = urljoin(current_url, location)
                if not is_valid_url(current_url):
                    raise ValueError("Redirect target is not a valid URL.")
                continue

            content = b""
            if method == "GET" and response.status == 200:
                body = bytearray()
                for chunk in response.stream(64 * 1024):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError(
                            f"URL body exceeds {max_bytes} byte limit."
                        )
                content = bytes(body)

            return SsrfResponse(
                status_code=response.status,
                headers=dict(response.headers),
                content=content,
                final_url=current_url,
            )
        except urllib3.exceptions.HTTPError as e:
            raise ValueError(f"Cannot access URL: {e}") from None
        finally:
            if response is not None:
                response.release_conn()
            pool.close()

    raise ValueError("Too many redirects.")
