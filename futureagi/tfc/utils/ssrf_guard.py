"""Shared SSRF-safe URL fetch primitives (TH-5648). Every redirect hop is re-validated and the IP that gets validated is the exact IP that gets connected to (no DNS-rebinding/TOCTOU gap). Lives in tfc.utils (not model_hub.views) so model_hub.models and evaluations can use it without depending on model_hub.views. See PR #962 review."""

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import certifi
import urllib3

_MAX_REDIRECTS = 5
_FETCH_TIMEOUT_SECONDS = 5

# 100.64.0.0/10 (RFC 6598 carrier-grade NAT, used internally by several cloud providers/Tailscale) isn't covered by ipaddress.is_private/is_reserved -- blocked explicitly below.
_EXTRA_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)

# Default cap on bytes _safe_get will buffer; callers can override via `max_bytes`.
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024

_URL_PATTERN = re.compile(
    r"^https?://"  # http:// or https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # or IP
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def is_valid_url(url_string: str) -> bool:
    """Cheap syntactic URL check (no network). Does not imply safety."""
    try:
        return bool(_URL_PATTERN.match(url_string))
    except Exception:
        return False


def _reject_unsafe_ip(ip_str: str, *, file_type: str, host: str) -> None:
    ip = ipaddress.ip_address(ip_str)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers the 169.254.169.254 cloud metadata address
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in net for net in _EXTRA_BLOCKED_NETWORKS)
    ):
        raise ValueError(
            f"{file_type.capitalize()} URL host '{host}' resolves to a "
            f"private/internal address and cannot be used."
        )


def _resolve_pinned_ip(host: str, *, file_type: str) -> str:
    """Resolve `host` to a single IP, validated as public/routable.

    Callers MUST connect to the returned IP directly rather than letting the
    HTTP client re-resolve `host` itself -- that second, independent lookup
    is exactly the gap DNS-rebinding attacks exploit.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(
            f"Cannot resolve {file_type} URL host '{host}': {e}"
        ) from None

    resolved_ips = sorted({info[4][0] for info in infos})
    if not resolved_ips:
        raise ValueError(f"Cannot resolve {file_type} URL host '{host}'.")

    # Validate every A/AAAA record, not just the one we'll use -- a multi-record host could hide a private one.
    for ip_str in resolved_ips:
        _reject_unsafe_ip(ip_str, file_type=file_type, host=host)

    return resolved_ips[0]


def _open_pinned_pool(parsed, pinned_ip, host, port, timeout):
    if parsed.scheme == "https":
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
        pinned_ip,
        port=port,
        timeout=timeout,
        retries=False,
    )


def _safe_head(url: str, *, file_type: str):
    """SSRF-safe HEAD request with manual, re-validated redirect handling.

    Returns (status_code, headers, final_url). `final_url` is the URL after
    following any redirects -- callers must use it (not the original `url`)
    for any further inspection (e.g. extension fallback), since checking the
    pre-redirect URL while acting on the post-redirect response is its own
    bypass.
    """
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported scheme for {file_type} URL: '{parsed.scheme}'."
            )
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid {file_type} URL: missing host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        pinned_ip = _resolve_pinned_ip(host, file_type=file_type)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        pool = None
        try:
            pool = _open_pinned_pool(
                parsed, pinned_ip, host, port, _FETCH_TIMEOUT_SECONDS
            )
            response = pool.request(
                "HEAD",
                path,
                headers={"Host": host},
                redirect=False,
                preload_content=False,
            )
        except urllib3.exceptions.HTTPError as e:
            raise ValueError(f"Cannot access {file_type} URL: {e}") from None
        finally:
            if pool is not None:
                pool.close()

        if response.status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise ValueError(
                    f"{file_type.capitalize()} URL redirected with no Location header."
                )
            current_url = urljoin(current_url, location)
            if not is_valid_url(current_url):
                raise ValueError(
                    f"{file_type.capitalize()} URL redirected to an invalid URL."
                )
            continue

        return response.status, response.headers, current_url

    raise ValueError(f"Too many redirects while validating {file_type} URL.")


def _safe_get(
    url: str,
    *,
    file_type: str,
    max_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    timeout: float = _FETCH_TIMEOUT_SECONDS,
    extra_headers: dict = None,
):
    """SSRF-safe GET with manual, re-validated redirect handling + a body cap.

    Same IP-pinning and per-hop redirect re-validation as `_safe_head`, but
    performs a real GET and returns the (size-capped) response body. Use
    this -- not a raw `requests.get` -- anywhere that needs the actual bytes
    of a user-supplied URL (e.g. downloading an image for eval input
    preprocessing). A HEAD-only pre-flight check does not protect a
    downstream byte-fetch that goes through a different code path; this is
    the primitive that does.

    Returns (status_code, headers, body_bytes, final_url) on success.
    Raises ValueError for any SSRF-relevant rejection (unsafe host, too many
    redirects, oversized body, bad scheme, etc).
    """
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported scheme for {file_type} URL: '{parsed.scheme}'."
            )
        host = parsed.hostname
        if not host:
            raise ValueError(f"Invalid {file_type} URL: missing host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        pinned_ip = _resolve_pinned_ip(host, file_type=file_type)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        pool = None
        response = None
        try:
            pool = _open_pinned_pool(parsed, pinned_ip, host, port, timeout)
            request_headers = {"Host": host, **(extra_headers or {})}
            response = pool.request(
                "GET",
                path,
                headers=request_headers,
                redirect=False,
                preload_content=False,
            )

            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError(
                        f"{file_type.capitalize()} URL redirected with no "
                        f"Location header."
                    )
                current_url = urljoin(current_url, location)
                if not is_valid_url(current_url):
                    raise ValueError(
                        f"{file_type.capitalize()} URL redirected to an "
                        f"invalid URL."
                    )
                continue

            if response.status != 200:
                return response.status, response.headers, b"", current_url

            body = bytearray()
            for chunk in response.stream(64 * 1024):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError(
                        f"{file_type.capitalize()} URL body exceeds "
                        f"{max_bytes} byte limit."
                    )
            return response.status, dict(response.headers), bytes(body), current_url
        except urllib3.exceptions.HTTPError as e:
            raise ValueError(f"Cannot access {file_type} URL: {e}") from None
        finally:
            if response is not None:
                response.release_conn()
            if pool is not None:
                pool.close()

    raise ValueError(f"Too many redirects while fetching {file_type} URL.")
