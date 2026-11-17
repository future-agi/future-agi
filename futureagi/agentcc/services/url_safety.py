import os
import socket
from urllib.parse import urlparse

import requests as http_requests
from agentcc.services.provider_url_auth import reject_provider_url_userinfo
from django.conf import settings
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

from tfc.ee_loader import is_cloud_env
from tfc.utils.ssrf_guard import METADATA_HOSTNAMES, IPClass, classify_ip

BLOCKED_PORTS = {6379, 5432, 3306, 27017, 9200, 11211, 2379}
WEBHOOK_PRIVATE_URL_ERROR = "Webhook URL cannot point to internal or private addresses"

# Lets provider base URLs point at private and LAN addresses (a local Ollama
# or vLLM, a service on the Docker network) for model discovery and saving
# providers here, and email alerts use an SMTP host there (a relay on the LAN).
# The gateway reads the same variable for the provider requests themselves.
# Off by default: on a shared deployment it would let any org reach internal
# hosts.
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
SMTP_HOST_ERROR = (
    "Invalid SMTP host: loopback, link-local and cloud metadata addresses, and "
    "database ports, are never allowed"
)
SMTP_PRIVATE_HOST_ERROR = (
    "SMTP host points to a private network address, which is refused by "
    "default. A self-hosted deployment can allow a private/LAN SMTP host by "
    f"setting {ALLOW_PRIVATE_PROVIDER_URLS_ENV}=true on the backend."
)


def private_provider_urls_allowed() -> bool:
    # The opt-in is for self-hosted installations only. A hosted operator's
    # environment must not grant untrusted organizations access to its LAN.
    if is_cloud_env(settings.CLOUD_DEPLOYMENT):
        return False
    return os.environ.get(ALLOW_PRIVATE_PROVIDER_URLS_ENV, "").strip().lower() in (
        "1",
        "t",
        "true",
    )


def _checked_addresses(
    hostname: str | None,
    port: int | None,
    message: str,
    exception_cls: type[Exception],
    *,
    allow_private: bool = False,
    private_message: str | None = None,
    allow_unresolvable: bool = False,
) -> list[str]:
    """The addresses ``hostname`` resolves to, in the resolver's order, once
    every one of them may be called; raise otherwise.

    Loopback, link-local, metadata, multicast and unspecified addresses always
    raise ``message``, as do the metadata host names and ``BLOCKED_PORTS``.
    Private/LAN addresses raise ``private_message`` (or ``message``) unless
    ``allow_private``. A host that does not resolve raises ``message``, or
    answers ``[]`` when ``allow_unresolvable``.
    """
    if not hostname:
        raise exception_cls(message)

    if port and port in BLOCKED_PORTS:
        raise exception_cls(message)

    normalized_host = hostname.rstrip(".").lower()
    if normalized_host in METADATA_HOSTNAMES:
        raise exception_cls(message)
    if is_cloud_env(settings.CLOUD_DEPLOYMENT) and (
        normalized_host in {"localhost", "host.docker.internal"}
        or normalized_host.endswith(".localhost")
    ):
        raise exception_cls(message)

    # Literal addresses need no DNS to establish their class. In particular,
    # a nonexistent IPv6 zone must not turn a link-local literal into an
    # "unresolvable hostname" that the save path permits.
    if is_cloud_env(settings.CLOUD_DEPLOYMENT):
        try:
            literal_class = classify_ip(normalized_host.split("%", 1)[0])
        except ValueError:
            literal_class = None
        if literal_class is IPClass.NEVER:
            raise exception_cls(message)
        if literal_class is IPClass.PRIVATE and not allow_private:
            raise exception_cls(private_message or message)

    try:
        resolved = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        resolved = []

    addresses = []
    for _, _, _, _, sockaddr in resolved:
        ip_class = classify_ip(sockaddr[0])
        if ip_class is IPClass.NEVER:
            raise exception_cls(message)
        if ip_class is IPClass.PRIVATE and not allow_private:
            raise exception_cls(private_message or message)
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])

    if not addresses and not allow_unresolvable:
        raise exception_cls(message)
    return addresses


def _checked_url_addresses(
    url: str,
    message: str,
    exception_cls: type[Exception],
    *,
    allow_private: bool = False,
    private_message: str | None = None,
    allow_unresolvable: bool = False,
) -> list[str]:
    """``_checked_addresses`` for an http(s) URL's host and port. A URL that
    does not parse (whitespace or a control character in it, a bad port) or
    whose scheme is not http(s) raises ``message``."""
    try:
        if any(ch.isspace() or not ch.isprintable() for ch in url):
            raise ValueError("invalid URL character")
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme.lower()
    except (ValueError, TypeError):
        raise exception_cls(message) from None
    if scheme not in ("http", "https"):
        raise exception_cls(message)
    return _checked_addresses(
        hostname,
        port,
        message,
        exception_cls,
        allow_private=allow_private,
        private_message=private_message,
        allow_unresolvable=allow_unresolvable,
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
    """Raise unless every address ``url``'s host resolves to may be called
    (``_checked_addresses``)."""
    _checked_url_addresses(
        url,
        message,
        exception_cls,
        allow_private=allow_private,
        private_message=private_message,
        allow_unresolvable=allow_unresolvable,
    )


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
    URL passes too on self-hosted installs, so rows saved earlier (or while
    the opt-in was on) can still be edited. Hosted saves always recheck it.
    """
    if not base_url:
        return
    if not isinstance(base_url, str):
        raise ValueError(PROVIDER_SAVE_URL_ERROR)
    reject_provider_url_userinfo(base_url)
    if (
        not is_cloud_env(settings.CLOUD_DEPLOYMENT)
        and saved_base_url
        and base_url.rstrip("/") == saved_base_url.rstrip("/")
    ):
        return
    _raise_if_unsafe_url(
        base_url,
        PROVIDER_SAVE_URL_ERROR,
        ValueError,
        allow_private=private_provider_urls_allowed(),
        private_message=PROVIDER_PRIVATE_URL_ERROR,
        allow_unresolvable=True,
    )


def _port_number(port) -> int | None:
    """``port`` as an int when it is one (or all digits), else None."""
    if isinstance(port, bool):
        return None
    if isinstance(port, int):
        return port
    if isinstance(port, str) and port.strip().isdigit():
        return int(port.strip())
    return None


def ensure_smtp_host_allowed(host: str, port=None) -> None:
    """Raise ``ValueError`` if an email alert may not be saved with this SMTP
    host and port.

    The provider base URL's terms (``ensure_provider_base_url_allowed``):
    loopback, link-local, metadata and database ports never; private/LAN
    addresses only with the self-hosted opt-in. A host that does not resolve
    from here passes: the send checks it again (``smtp_connect_addresses``).
    """
    if not isinstance(host, str) or not host.strip():
        raise ValueError(SMTP_HOST_ERROR)
    _checked_addresses(
        host.strip(),
        _port_number(port),
        SMTP_HOST_ERROR,
        ValueError,
        allow_private=private_provider_urls_allowed(),
        private_message=SMTP_PRIVATE_HOST_ERROR,
        allow_unresolvable=True,
    )


def smtp_connect_addresses(host: str, port=None) -> list[str]:
    """The addresses an SMTP connection to ``host`` may open, in the
    resolver's order: ``host`` is resolved once and every address checked on
    ``ensure_smtp_host_allowed``'s terms (one that does not resolve is refused
    here). The caller tries them in this order and never resolves the name
    again (email_sender._PinnedSMTP), so every address connected to is an
    address checked. Raises ``ValueError``."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError(SMTP_HOST_ERROR)
    return _checked_addresses(
        host.strip(),
        _port_number(port),
        SMTP_HOST_ERROR,
        ValueError,
        allow_private=private_provider_urls_allowed(),
        private_message=SMTP_PRIVATE_HOST_ERROR,
    )


def _host_header(parsed) -> str:
    """The Host header for a parsed URL: the name, with the port when it is
    not the scheme's default."""
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    if parsed.port in (None, default_port):
        return host
    return f"{host}:{parsed.port}"


def _never_connected(exc: http_requests.exceptions.ConnectionError) -> bool:
    """True when requests raised ``exc`` because the connection was never
    opened (refused, unreachable, connect timeout): nothing of the request
    went out, so the next checked address may be tried. Not after the
    connection opened (TLS, a dropped or slow answer): the server may have the
    request, and it is never sent twice."""
    cause = exc.args[0] if exc.args else None
    return isinstance(cause, MaxRetryError) and isinstance(
        cause.reason, ConnectTimeoutError
    )


class _PinnedAddressAdapter(http_requests.adapters.HTTPAdapter):
    """Sends each request to the addresses it checked.

    ``send`` resolves the URL's host once, refuses it unless every address may
    be called, and gives the addresses to the connection pool one at a time,
    in the resolver's order, so urllib3 connects to a checked address and
    never resolves the name again: an answer that changes after the check (DNS
    rebinding) is never dialed. An address whose connection cannot be opened
    falls through to the next, as urllib3 does for a name (a dual-stack host
    without an IPv6 route); the last one's error is raised. TLS still sends
    the name as SNI and verifies the certificate for it, and the Host header
    carries the name. Each redirect hop comes back through ``send`` and is
    checked and pinned again. Environment proxies are not used, as in the
    gateway's transports: a proxy would resolve the name itself.
    """

    _PINNED = "_agentcc_pinned_address"

    def __init__(self, connect_error_message: str, allow_private: bool):
        self._connect_error_message = connect_error_message
        self._allow_private = allow_private
        super().__init__()

    def send(self, request, **kwargs):
        addresses = _checked_url_addresses(
            request.url,
            self._connect_error_message,
            ConnectionError,
            allow_private=self._allow_private,
        )
        # A copy: Session.resolve_redirects builds the next hop from the
        # request it was given, which must not carry this hop's Host.
        pinned = request.copy()
        pinned.headers["Host"] = _host_header(urlparse(request.url))
        kwargs["proxies"] = None
        for address in addresses[:-1]:
            setattr(pinned, self._PINNED, address)
            try:
                return super().send(pinned, **kwargs)
            except http_requests.exceptions.ConnectionError as exc:
                if not _never_connected(exc):
                    raise
        setattr(pinned, self._PINNED, addresses[-1])
        return super().send(pinned, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        address = getattr(request, self._PINNED, None)
        if address is None:  # only send() hands requests to the pool
            raise ConnectionError(self._connect_error_message)
        hostname = host_params["host"]
        host_params["host"] = address
        if host_params["scheme"] == "https":
            pool_kwargs["server_hostname"] = hostname
            pool_kwargs["assert_hostname"] = hostname
        return host_params, pool_kwargs


def build_ssrf_safe_session(
    connect_error_message: str, *, allow_private: bool = False
) -> http_requests.Session:
    """A requests session whose every request (and redirect hop) is checked on
    ``ensure_public_http_url``'s terms and connected to an address checked
    (``_PinnedAddressAdapter``). A refused address raises ``ConnectionError``
    with ``connect_error_message``."""
    session = http_requests.Session()
    adapter = _PinnedAddressAdapter(connect_error_message, allow_private)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
