"""Deliver one webhook to a public, DNS-pinned destination without redirects."""

import ipaddress
import json as json_module
import math
from urllib.parse import urlsplit

from tfc.utils import ssrf_guard
from tfc.utils.ssrf_guard import SsrfBlocked, _open_pinned_pool

_DNS_TIMEOUT_SECONDS = 2
_DESTINATION_ERROR = (
    "Webhook URL must use an allowed scheme and a public destination without credentials."
)


def validate_webhook_url(url: str, *, resolve=True, https_only=False) -> None:
    """Validate syntax; optionally check DNS with a bounded wait.

    Task mutations use resolve=False and https_only=True. Keep the bounded DNS
    preflight for existing callers; it never authorizes a later delivery.
    """
    parsed, _ = _destination(url, https_only=https_only)
    if resolve:
        try:
            _resolve_address(
                parsed.hostname, ssrf_guard._monotonic() + _DNS_TIMEOUT_SECONDS
            )
        except TimeoutError:
            raise SsrfBlocked("Webhook destination lookup timed out.") from None


def _destination(url, *, https_only=False):
    """Parse and reject unsafe literal destinations without any network I/O."""
    try:
        parsed = urlsplit(url)
        schemes = ("https",) if https_only else ("http", "https")
        if (
            parsed.scheme not in schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "\\" in url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
        ):
            raise ValueError
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        if not 1 <= port <= 65535:
            raise ValueError
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if (
            address is not None
            and ssrf_guard.classify_ip(address) is not ssrf_guard.IPClass.PUBLIC
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise SsrfBlocked(_DESTINATION_ERROR) from None
    return parsed, port


def _resolve_address(host, deadline):
    try:
        return ssrf_guard._resolve_before_deadline(host, deadline)
    except (ValueError, UnicodeError):
        # Neither credentials in a URL nor DNS diagnostics belong in API errors.
        raise SsrfBlocked(_DESTINATION_ERROR) from None


def post_webhook(url: str, *, json: dict, headers: dict, timeout: float) -> None:
    """POST once under a total deadline, refusing every redirect.

    DNS has a separate short cap within the attempt budget. Reuse safe_fetch's
    bounded resolver, pinned public address, TLS verification and deadline-aware
    connection so slow headers cannot extend the wait via socket activity.
    Responses are not consumed; their contents are not part of the contract.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Webhook timeout must be finite and positive.")
    deadline = ssrf_guard._monotonic() + timeout
    parsed, port = _destination(url)
    host = parsed.hostname
    address = _resolve_address(
        host, min(deadline, ssrf_guard._monotonic() + _DNS_TIMEOUT_SECONDS)
    )
    host_header = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    if port != default_port:
        host_header = f"{host_header}:{port}"
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    body = json_module.dumps(json, allow_nan=False).encode("utf-8")
    ssrf_guard._remaining(deadline)
    pool = _open_pinned_pool(parsed.scheme, address, host, port, timeout)
    pool.ConnectionCls = (
        ssrf_guard._DeadlineHTTPSConnection
        if parsed.scheme == "https"
        else ssrf_guard._DeadlineHTTPConnection
    )
    pool.conn_kw["deadline"] = deadline
    response = None
    try:
        response = pool.request(
            "POST",
            path,
            body=body,
            headers={**headers, "Host": host_header},
            redirect=False,
            retries=False,
            preload_content=False,
        )
        ssrf_guard._remaining(deadline)
        if not 200 <= response.status < 300:
            raise ValueError("Webhook destination did not accept the notification.")
    finally:
        if response is not None:
            response.close()
        pool.close()
