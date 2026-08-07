"""CORS origin policy for the API.

Credentialed CORS (``CORS_ALLOW_CREDENTIALS = True``) must never be combined
with a wildcard origin: that lets any website make authenticated cross-origin
requests to the API and read the responses. Only local/test environments use
the wildcard for developer convenience; every other environment falls closed to
an explicit first-party allowlist.
"""

from __future__ import annotations

from collections.abc import Iterable


def _expand_host(host: str) -> list[str]:
    """Turn a host or origin into full CORS origins (scheme included)."""
    host = host.strip().rstrip("/")
    if not host:
        return []
    if "://" in host:
        return [host]
    return [f"https://{host}", f"http://{host}"]


def resolve_cors_origins(
    is_local: bool,
    explicit_origins: Iterable[str],
    frontend_hosts: Iterable[str],
) -> tuple[bool, list[str]]:
    """Resolve ``(CORS_ALLOW_ALL_ORIGINS, CORS_ALLOWED_ORIGINS)``.

    ``explicit_origins`` come from ``CORS_ALLOWED_ORIGINS``. ``frontend_hosts``
    are the first-party hosts that already back CSRF_TRUSTED_ORIGINS and OAuth
    callbacks (APP_URL / FRONTEND_URL); they may be bare hosts or full origins.

    The wildcard is only ever returned for a local/test environment that has no
    explicit allowlist. Otherwise the wildcard is off and the allowlist is the
    explicit origins plus the derived frontend origins, deduplicated.
    """
    explicit = list(explicit_origins)
    if is_local and not explicit:
        return True, []

    allowed = list(explicit)
    for host in frontend_hosts:
        allowed.extend(_expand_host(host))
    return False, list(dict.fromkeys(allowed))
