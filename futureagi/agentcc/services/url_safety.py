import os
import socket
from urllib.parse import urlparse

import requests as http_requests

from tfc.utils.ssrf_guard import METADATA_HOSTNAMES, IPClass, classify_ip

BLOCKED_PORTS = {6379, 5432, 3306, 27017, 9200, 11211, 2379}
WEBHOOK_PRIVATE_URL_ERROR = "Webhook URL cannot point to internal or private addresses"

# Lets provider base URLs point at private and LAN addresses (a local Ollama
# or vLLM, a service on the Docker network) for model discovery and saving
# providers here. The gateway reads the same variable for the requests
# themselves. Off by default: on a shared deployment it would let any org reach
# internal hosts.
ALLOW_PRIVATE_PROVIDER_URLS_ENV = "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS"

PROVIDER_URL_ERROR = (
    "Invalid base URL: it must be an http(s) URL whose host resolves, and "
    "loopback, link-local and cloud metadata addresses are never allowed"
)
# Saving accepts a host that does not resolve here, so this one does not ask
# for that.
PROVIDER_SAVE_URL_ERROR = (
    "Invalid base URL: it must be an http(s) URL, and loopback, link-local and "
    "cloud metadata addresses are never allowed"
)
PROVIDER_PRIVATE_URL_ERROR = (
    "Base URL points to a private network address, which is refused by "
    "default. A self-hosted deployment can allow private/LAN provider URLs by "
    f"setting {ALLOW_PRIVATE_PROVIDER_URLS_ENV}=true on the backend and the "
    "gateway."
)


def private_provider_urls_allowed() -> bool:
    return os.environ.get(ALLOW_PRIVATE_PROVIDER_URLS_ENV, "").strip().lower() in (
        "1",
        "t",
        "true",
    )


def _raise_if_unsafe_url(
    url: str,
    message: str,
    exception_cls: type[Exception],
    *,
    allow_private: bool = False,
    private_message: str | None = None,
    allow_unresolvable: bool = False,
) -> None:
    """Raise unless every address ``url``'s host resolves to may be called.

    Loopback, link-local, metadata, multicast and unspecified addresses always
    raise ``message``. Private/LAN addresses raise ``private_message`` (or
    ``message``) unless ``allow_private``. A host that does not resolve raises
    ``message`` unless ``allow_unresolvable``.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    port = parsed.port
    scheme = parsed.scheme.lower()

    if not hostname or scheme not in ("http", "https"):
        raise exception_cls(message)

    if port and port in BLOCKED_PORTS:
        raise exception_cls(message)

    if hostname.rstrip(".").lower() in METADATA_HOSTNAMES:
        raise exception_cls(message)

    try:
        resolved = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        if allow_unresolvable:
            return
        raise exception_cls(message) from None

    for _, _, _, _, sockaddr in resolved:
        ip_class = classify_ip(sockaddr[0])
        if ip_class is IPClass.NEVER:
            raise exception_cls(message)
        if ip_class is IPClass.PRIVATE and not allow_private:
            raise exception_cls(private_message or message)


def ensure_public_http_url(
    url: str,
    error_message: str,
    *,
    allow_private: bool = False,
    private_message: str | None = None,
) -> None:
    _raise_if_unsafe_url(
        url,
        error_message,
        ValueError,
        allow_private=allow_private,
        private_message=private_message,
    )


def ensure_provider_base_url_allowed(
    base_url: str, *, saved_base_url: str | None = None
) -> None:
    """Raise ``ValueError`` if a provider may not be saved with ``base_url``.

    Checked on model discovery's terms, which are close to the gateway's (not
    identical: this also refuses some ports and reserved ranges the gateway
    lets through), so a provider whose every request would be refused is not
    saved. Empty means the provider's default endpoint. A host that does not
    resolve from here passes: the gateway checks it again on every request, and
    saving should not depend on this container's DNS. An unchanged saved base
    URL passes too, so rows saved earlier (or while the opt-in was on) can
    still be edited.
    """
    if not base_url:
        return
    if not isinstance(base_url, str):
        raise ValueError(PROVIDER_SAVE_URL_ERROR)
    if saved_base_url and base_url.rstrip("/") == saved_base_url.rstrip("/"):
        return
    _raise_if_unsafe_url(
        base_url,
        PROVIDER_SAVE_URL_ERROR,
        ValueError,
        allow_private=private_provider_urls_allowed(),
        private_message=PROVIDER_PRIVATE_URL_ERROR,
        allow_unresolvable=True,
    )


def build_ssrf_safe_session(
    connect_error_message: str, *, allow_private: bool = False
) -> http_requests.Session:
    class _SSRFSafeAdapter(http_requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            _raise_if_unsafe_url(
                request.url,
                connect_error_message,
                ConnectionError,
                allow_private=allow_private,
            )
            return super().send(request, **kwargs)

    session = http_requests.Session()
    adapter = _SSRFSafeAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
