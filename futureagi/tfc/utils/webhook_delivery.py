"""Deliver one webhook to a public, DNS-pinned destination without redirects."""

import json as json_module
from urllib.parse import urlsplit

from tfc.utils.ssrf_guard import SsrfBlocked, _open_pinned_pool, _resolve_pinned_ip


def validate_webhook_url(url: str) -> None:
    """Validate stored destinations; delivery must resolve and pin them again."""
    _destination(url)


def _destination(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
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
        address = _resolve_pinned_ip(parsed.hostname)
    except (ValueError, UnicodeError):
        # Neither credentials in a URL nor DNS diagnostics belong in API errors.
        raise SsrfBlocked(
            "Webhook URL must resolve only to public HTTP(S) destinations."
        ) from None
    return parsed, port, address


def post_webhook(url: str, *, json: dict, headers: dict, timeout: float) -> None:
    """POST once, refusing every redirect so the payload cannot change audience.

    Reuse the same public-address classification, DNS pinning, TLS hostname
    verification and proxy-independent connection pool as safe_fetch. Responses
    are not consumed; their contents are not part of the notification contract.
    """
    parsed, port, address = _destination(url)
    host = parsed.hostname
    host_header = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    if port != default_port:
        host_header = f"{host_header}:{port}"
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    body = json_module.dumps(json, allow_nan=False).encode("utf-8")
    pool = _open_pinned_pool(parsed.scheme, address, host, port, timeout)
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
        if not 200 <= response.status < 300:
            raise ValueError("Webhook destination did not accept the notification.")
    finally:
        if response is not None:
            response.close()
        pool.close()
