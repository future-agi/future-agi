"""SSRF-safe URL fetch primitive.

The IP that gets validated is the exact IP that gets connected to (no
DNS-rebinding TOCTOU gap), and every redirect hop is re-validated.
"""

import enum
import io
import ipaddress
import math
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import certifi
import urllib3

_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT_SECONDS = 5
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024
_monotonic = time.monotonic


class SsrfBlocked(ValueError):
    """Permanent SSRF rejection — never retriable.

    Subclasses ValueError so existing `except ValueError` sites keep working,
    but retry loops can catch it specifically and fail fast instead of
    burning the exponential-backoff budget on a URL that will never succeed.
    """


class IPClass(enum.Enum):
    """What an address is to a request made on someone else's say-so."""

    PUBLIC = "public"
    # Private, LAN and reserved ranges: refused unless a caller lets the
    # operator opt in (a local model server, say).
    PRIVATE = "private"
    # Loopback, link-local (cloud metadata answers on 169.254.169.254),
    # multicast, broadcast, "this network", and the metadata services outside
    # link-local: never reachable.
    NEVER = "never"


# Never reachable, beyond what ipaddress flags: "this network", broadcast,
# and the metadata services outside link-local (Alibaba's inside CGNAT, AWS
# IMDS over IPv6 inside fc00::/7, Azure's WireServer in public space).
_NEVER_ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "255.255.255.255/32",
        "100.100.100.200/32",
        "fd00:ec2::254/128",
        "168.63.129.16/32",
    )
)
# 100.64.0.0/10 is RFC 6598 carrier-grade NAT (used by some cloud providers,
# Tailscale). Not covered by ipaddress.is_private/is_reserved.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
# Metadata endpoints by name, for callers that refuse them before any lookup.
METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)


class SsrfResponse:
    """Small requests.Response-like object returned by safe_fetch.

    `content` is fully buffered in memory (bounded by max_bytes). `iter_content`
    yields chunks of that already-loaded buffer — it does NOT stream; keeping
    the method for drop-in compatibility with callers migrated off requests.
    """

    __slots__ = ("status_code", "headers", "content", "final_url")

    def __init__(self, status_code, headers, content, final_url):
        self.status_code = status_code
        # HTTPHeaderDict is case-insensitive (matches requests.Response's
        # CaseInsensitiveDict contract): callers can look up 'Content-Type'
        # regardless of the casing the server sent (or a test constructed).
        self.headers = (
            urllib3.HTTPHeaderDict(headers)
            if headers is not None
            else urllib3.HTTPHeaderDict()
        )
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


def classify_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> IPClass:
    """Classify an address. IPv4-mapped IPv6 addresses count as their IPv4
    address."""
    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_link_local  # covers 169.254.169.254 metadata endpoint
        or ip.is_multicast
        or ip.is_unspecified
        or any(ip in net for net in _NEVER_ALLOWED_NETWORKS)
    ):
        return IPClass.NEVER
    if ip.is_private or ip.is_reserved or ip in _CGNAT_NETWORK:
        return IPClass.PRIVATE
    return IPClass.PUBLIC


def _reject_unsafe_ip(ip_str: str, host: str) -> None:
    if classify_ip(ip_str) is not IPClass.PUBLIC:
        raise SsrfBlocked(f"URL host '{host}' resolves to a private/internal address.")


def _resolve_pinned_ip(host: str) -> str:
    """Resolve `host` and validate every A/AAAA record. Returns one pinned IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SsrfBlocked(f"Cannot resolve URL host '{host}': {e}") from None

    resolved_ips = sorted({info[4][0] for info in infos})
    if not resolved_ips:
        raise SsrfBlocked(f"Cannot resolve URL host '{host}'.")

    for ip_str in resolved_ips:
        _reject_unsafe_ip(ip_str, host)
    return resolved_ips[0]


def assert_url_host_public(url: str) -> None:
    """Raise SsrfBlocked unless `url`'s host resolves entirely to public IPs.

    A pre-flight guard for streaming/async callers that do their own fetch and
    only need the reject, not safe_fetch's buffered body. Because the fetch
    re-resolves at connect time this keeps a small DNS-rebinding TOCTOU gap that
    safe_fetch closes — acceptable when the URL is provider-returned over
    authenticated TLS (e.g. a recording URL inside a provider's API response).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlocked(f"Unsupported URL scheme: '{parsed.scheme}'.")
    if not parsed.hostname:
        raise SsrfBlocked("Invalid URL: missing host.")
    _resolve_pinned_ip(parsed.hostname)  # resolves + rejects every A/AAAA record


# getaddrinfo has no timeout API. Bound the caller's wait and the number of
# outstanding resolver threads; an expired lookup can finish but never connect.
_RESOLVERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="url-resolver")
_RESOLVER_SLOTS = threading.BoundedSemaphore(4)


def _remaining(deadline):
    remaining = deadline - _monotonic()
    if remaining <= 0:
        raise TimeoutError("URL fetch deadline exceeded.")
    return remaining


def _resolve_before_deadline(host, deadline):
    slots = _RESOLVER_SLOTS
    if not slots.acquire(timeout=_remaining(deadline)):
        raise TimeoutError("URL fetch deadline exceeded.")
    try:
        _remaining(deadline)
        future = _RESOLVERS.submit(_resolve_pinned_ip, host)
    except BaseException:
        slots.release()
        raise
    # Keep the slot until the lookup really finishes. Cancelling a queued future
    # does not remove its work item from ThreadPoolExecutor's unbounded queue.
    future.add_done_callback(lambda _: slots.release())
    try:
        return future.result(timeout=_remaining(deadline))
    except TimeoutError:
        raise TimeoutError("URL fetch deadline exceeded.") from None


class _DeadlineReader(io.RawIOBase):
    """Budget every raw receive, including reads hidden inside HTTP buffering.

    Checking response.stream() chunks is insufficient: one chunk (or one HTTP
    header/chunk-size line) can require arbitrarily many slowly progressing
    socket reads. Keep urllib3's parser/decompression, but bound each raw read.
    """

    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline
        self._raw = sock.makefile("rb", buffering=0)
        self._timeout = sock.gettimeout()

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = _remaining(self._deadline)
        timeout = (
            min(self._timeout, remaining) if self._timeout is not None else remaining
        )
        self._sock.settimeout(timeout)
        return self._raw.readinto(buffer)

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline

    def __getattr__(self, name):
        return getattr(self._sock, name)

    def makefile(self, mode, buffering=None):
        # http.client requests a buffered binary reader for headers and body.
        assert mode == "rb"
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))


class _DeadlineHTTPConnection(urllib3.connection.HTTPConnection):
    def __init__(self, *args, deadline, **kwargs):
        self._deadline = deadline
        super().__init__(*args, **kwargs)

    def connect(self):
        self.timeout = min(self.timeout, _remaining(self._deadline))
        super().connect()

    def _new_conn(self):
        sock = super()._new_conn()
        # TCP establishment and TLS negotiation share the same budget.
        try:
            sock.settimeout(min(self.timeout, _remaining(self._deadline)))
            return sock
        except BaseException:
            sock.close()
            raise

    def send(self, data):
        if self.sock is None:
            self.connect()
        # Connection setup (including TLS) has already spent part of the budget.
        self.sock.settimeout(min(self.timeout, _remaining(self._deadline)))
        return super().send(data)

    def getresponse(self):
        _remaining(self._deadline)
        self.sock = _DeadlineSocket(self.sock, self._deadline)
        return super().getresponse()


class _DeadlineHTTPSConnection(
    _DeadlineHTTPConnection, urllib3.connection.HTTPSConnection
):
    """Keep urllib3's TLS verification/SNI, sharing only the deadline hooks."""


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
    total_timeout: float | None = None,
) -> SsrfResponse:
    """Fetch `url` with SSRF protection.

    Method: HEAD or GET. For GET, the body is buffered up to `max_bytes`.
    Redirects are followed manually (5 hops max), re-validating each hop.
    Raises ValueError when the URL or response is rejected. Optional total_timeout
    bounds DNS waiting, connection setup, headers and body across all hops;
    None preserves the existing per-operation inactivity timeout. A deadline
    raises TimeoutError and closes the active response/pool.
    """
    deadline = None
    if total_timeout is not None:
        if not math.isfinite(total_timeout) or total_timeout <= 0:
            raise ValueError("total_timeout must be finite and positive.")
        deadline = _monotonic() + total_timeout
    current_url = url
    caller_headers = headers or {}
    origin_host = urlparse(url).hostname
    for _ in range(_MAX_REDIRECTS):
        if deadline is not None:
            _remaining(deadline)
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise SsrfBlocked(f"Unsupported URL scheme: '{parsed.scheme}'.")
        host = parsed.hostname
        if not host:
            raise SsrfBlocked("Invalid URL: missing host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        pinned_ip = (
            _resolve_pinned_ip(host)
            if deadline is None
            else _resolve_before_deadline(host, deadline)
        )

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        # Host header keeps IPv6 literals bracketed and non-default ports explicit.
        default_port = 443 if parsed.scheme == "https" else 80
        authority_host = f"[{host}]" if ":" in host else host
        host_header = (
            authority_host if port == default_port else f"{authority_host}:{port}"
        )

        # Strip sensitive headers on cross-origin hops so a redirect to
        # attacker.com does not leak Authorization/Cookie/etc.
        hop_headers = dict(caller_headers)
        if host != origin_host:
            for sensitive in ("Authorization", "Cookie", "Proxy-Authorization"):
                hop_headers.pop(sensitive, None)
                hop_headers.pop(sensitive.lower(), None)

        pool = _open_pinned_pool(parsed.scheme, pinned_ip, host, port, timeout)
        if deadline is not None:
            pool.ConnectionCls = (
                _DeadlineHTTPSConnection
                if parsed.scheme == "https"
                else _DeadlineHTTPConnection
            )
            pool.conn_kw["deadline"] = deadline
        response = None
        try:
            response = pool.request(
                method,
                path,
                headers={"Host": host_header, **hop_headers},
                redirect=False,
                preload_content=False,
            )

            if deadline is not None:
                _remaining(deadline)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect with no Location header.")
                current_url = urljoin(current_url, location)
                if not is_valid_url(current_url):
                    raise SsrfBlocked("Redirect target is not a valid URL.")
                continue

            content = b""
            if method == "GET" and response.status == 200:
                body = bytearray()
                for chunk in response.stream(64 * 1024):
                    if deadline is not None:
                        # Second guard after the raw reader's per-read budget.
                        _remaining(deadline)
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError(f"URL body exceeds {max_bytes} byte limit.")
                content = bytes(body)

            if deadline is not None:
                _remaining(deadline)
            return SsrfResponse(
                status_code=response.status,
                headers=response.headers,
                content=content,
                final_url=current_url,
            )
        except urllib3.exceptions.HTTPError as e:
            if deadline is not None:
                _remaining(deadline)
            raise ValueError(f"Cannot access URL: {e}") from None
        finally:
            if response is not None:
                if deadline is not None:
                    # Incomplete bodies/redirects must close their file/socket,
                    # not just return a still-reading connection to the pool.
                    # Close the response before releasing its connection.
                    response.close()
                response.release_conn()
            pool.close()

    raise ValueError("Too many redirects.")
