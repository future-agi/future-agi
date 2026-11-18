"""SSRF-safe bounded HTTPS range retrieval for completed-recording imports (B07).

Deliberately built on ``http.client`` with an explicit pinned socket: no
``requests``/``urllib.request`` (both honour environment proxies; requests also
reads netrc), no cookies, no Authorization, no redirects, no content decoding.

Every A/AAAA answer must be a globally routable unicast address after
unwrapping IPv4-mapped, 6to4, Teredo and NAT64 forms; one bad answer denies the
host (rebinding). The connection goes to the vetted address with TLS hostname
verification for the original name, and that address stays pinned for every
range of the job attempt. Nothing about the URL, its query or the response is
ever placed in an exception, log line or durable row.
"""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from tracer.services.attachments.contract import AttachmentError

BUFFER_BYTES = 256 * 1024
USER_AGENT = "attachment-importer/1"
MAX_RANGE_BYTES = 8 * 1024 * 1024
_NAT64 = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def unavailable():
    return AttachmentError("source_unavailable", 403)


def address_allowed(value):
    """True only for a public unicast address, including embedded IPv4 forms."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    candidates = [address]
    if address.version == 6:
        if any(address in network for network in _NAT64):
            return False
        for embedded in (address.ipv4_mapped, address.sixtofour):
            if embedded is not None:
                candidates.append(embedded)
        if address.teredo is not None:
            candidates.extend(address.teredo)
    return all(
        item.is_global
        and not item.is_multicast
        and not item.is_reserved
        and not item.is_loopback
        and not item.is_link_local
        and not item.is_private
        and not item.is_unspecified
        for item in candidates
    )


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    target: str = field(repr=False)  # path + query; may hold a signature

    def __repr__(self):
        return "Destination(<redacted>)"


def parse_destination(url, *, approved_hosts):
    """https only, no userinfo/IP literals, host within an approved suffix."""
    try:
        if (
            not isinstance(url, str)
            or len(url) > 4096
            or any(ord(c) < 0x21 or ord(c) == 0x7F for c in url)
        ):
            raise ValueError
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or not host
            or "%" in host
            or parts.fragment
            or (parts.port is not None and parts.port != 443)
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        host.encode("ascii")
        port = parts.port or 443
        if not any(
            host == approved or host.endswith("." + approved)
            for approved in approved_hosts
            if approved
        ):
            raise ValueError
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        return Destination(host=host, port=port, target=target)
    except (ValueError, UnicodeError):
        raise unavailable() from None


def resolve(
    host,
    port,
    *,
    getaddrinfo=socket.getaddrinfo,
    allow=address_allowed,
    timeout_seconds=3,
):
    """Every DNS answer must be public; a stalled resolver never stalls a lease."""
    ready = threading.Event()
    result = []

    def lookup():
        try:
            result.append(
                getaddrinfo(host, port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            )
        except (OSError, UnicodeError):
            result.append(None)
        finally:
            ready.set()

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    if not ready.wait(min(timeout_seconds, 3)) or not result or result[0] is None:
        raise unavailable()
    answers = result[0]
    addresses = []
    for family, _, _, _, sockaddr in answers:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise unavailable()
        addresses.append(sockaddr[0])
    if not addresses or not all(allow(address) for address in addresses):
        raise unavailable()
    return list(dict.fromkeys(addresses))


class _PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, *, address, context, timeout):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._pinned_context = context

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._pinned_context.wrap_socket(
                sock, server_hostname=self.host
            )
            self._transport_socket = self.sock
        except Exception:
            sock.close()
            raise

    def set_tunnel(self, *args, **kwargs):  # pragma: no cover - never proxied
        raise unavailable()


def default_context():
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class TokenBucket:
    def __init__(self, rate, *, clock=time.monotonic, sleep=time.sleep):
        self.rate, self.clock, self.sleep = rate, clock, sleep
        self.allowance, self.last = rate, clock()

    def take(self, amount):
        now = self.clock()
        self.allowance = min(self.rate, self.allowance + (now - self.last) * self.rate)
        self.last = now
        self.allowance -= amount
        if self.allowance < 0:
            self.sleep(-self.allowance / self.rate)


@dataclass
class RangeSource:
    """One pinned remote immutable object read through exact bounded ranges."""

    destination: Destination
    total_bytes: int
    window: int
    timeout_seconds: int
    bytes_per_second: int
    context: ssl.SSLContext | None = None
    getaddrinfo: Callable = socket.getaddrinfo
    allow: Callable = address_allowed
    generation: str | None = None
    generation_kind: str = "http_strong_etag"
    _address: str | None = None
    _bucket: TokenBucket | None = None

    def _connection(self):
        if self.generation is not None:
            validate_revision_query(
                self.destination, self.generation_kind, self.generation
            )
        if self._address is None:
            self._address = resolve(
                self.destination.host,
                self.destination.port,
                getaddrinfo=self.getaddrinfo,
                allow=self.allow,
            )[0]
        return _PinnedConnection(
            self.destination.host,
            self.destination.port,
            address=self._address,
            context=self.context or default_context(),
            timeout=min(self.timeout_seconds, 3),
        )

    def read_range(self, offset, length, sink):
        """Write exactly ``length`` bytes at ``offset`` to ``sink``.

        A response that ignores Range, changes total length or generation,
        is encoded, short or long is rejected without draining it.
        """
        if (
            type(length) is not int
            or type(offset) is not int
            or not 0 < length <= min(self.window, MAX_RANGE_BYTES)
            or offset < 0
            or offset + length > self.total_bytes
        ):
            raise AttachmentError("validation_failed", 422)
        if not valid_generation(self.generation_kind, self.generation):
            raise unavailable()
        if self._bucket is None:
            self._bucket = TokenBucket(self.bytes_per_second)
        deadline = time.monotonic() + self.timeout_seconds
        connection = self._connection()
        end = offset + length - 1
        headers = {
            "Host": self.destination.host
            + ("" if self.destination.port == 443 else f":{self.destination.port}"),
            "Range": f"bytes={offset}-{end}",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        if self.generation_kind == "http_strong_etag":
            headers["If-Match"] = self.generation
        watchdog = _watchdog(connection, min(self.timeout_seconds, 30))
        try:
            connection.request(
                "GET", self.destination.target, headers=headers, encode_chunked=False
            )
            response = connection.getresponse()
            if response.status == 412:
                raise AttachmentError("snapshot_changed", 409)
            if response.status != 206:
                # 200 = Range ignored (whole body), 3xx = redirect: both denied.
                raise unavailable()
            observed = response.getheader(generation_header(self.generation_kind))
            if observed != self.generation:
                raise AttachmentError("snapshot_changed", 409)
            if (
                response.getheader("Content-Range")
                != f"bytes {offset}-{end}/{self.total_bytes}"
                or response.getheader("Content-Length") != str(length)
                or response.getheader("Content-Encoding", "identity") != "identity"
                or response.getheader("Transfer-Encoding") is not None
            ):
                raise AttachmentError("validation_failed", 422)
            received = 0
            while True:
                if time.monotonic() > deadline:
                    raise unavailable()
                # At most ONE byte beyond the declared length is ever read.
                chunk = response.read(min(BUFFER_BYTES, length - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > length:
                    raise AttachmentError("validation_failed", 422)
                self._bucket.take(len(chunk))
                if time.monotonic() > deadline:
                    raise unavailable()
                sink.write(chunk)
            if received != length:
                raise AttachmentError("validation_failed", 422)
            return received
        except AttachmentError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
            raise unavailable() from None
        finally:
            watchdog.cancel()
            connection.close()


def valid_generation(kind, value):
    if not isinstance(value, str) or not value or len(value) > 255:
        return False
    if kind == "http_strong_etag":
        return re.fullmatch(r'"[\x21\x23-\x7e]*"', value) is not None
    if kind == "gcs_generation":
        return value.isascii() and value.isdigit() and int(value) > 0
    if kind == "s3_version":
        return value != "null" and all(0x21 <= ord(c) <= 0x7E for c in value)
    return False


def generation_header(kind):
    try:
        return {
            "http_strong_etag": "ETag",
            "s3_version": "x-amz-version-id",
            "gcs_generation": "x-goog-generation",
        }[kind]
    except KeyError:
        raise unavailable() from None


def inspect_metadata(source):
    """HEAD only, no body, redirects, custom headers or generation adoption.

    The trusted producer may use the returned exact facts once when sealing;
    workers compare them to the previously admitted facts, never update them.
    """
    connection = source._connection()
    watchdog = _watchdog(connection, min(source.timeout_seconds, 30))
    try:
        connection.request(
            "HEAD",
            source.destination.target,
            headers={
                "Host": source.destination.host,
                "Accept-Encoding": "identity",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        length = response.getheader("Content-Length", "")
        revision = response.getheader(generation_header(source.generation_kind))
        mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if (
            response.status != 200
            or not length.isascii()
            or not length.isdigit()
            or not 0 < int(length) <= 1024**3
            or not valid_generation(source.generation_kind, revision)
            or response.getheader("Content-Encoding", "identity") != "identity"
            or response.getheader("Transfer-Encoding") is not None
        ):
            raise unavailable()
        return revision, int(length), mime
    except AttachmentError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
        raise unavailable() from None
    finally:
        watchdog.cancel()
        connection.close()


def validate_revision_query(destination, kind, revision):
    if not valid_generation(kind, revision):
        raise unavailable()
    if kind == "http_strong_etag":
        return
    key = "versionId" if kind == "s3_version" else "generation"
    query = parse_qs(urlsplit(destination.target).query, keep_blank_values=True)
    if query.get(key) != [revision]:
        raise unavailable()


def _watchdog(connection, seconds):
    # A socket inactivity timeout alone permits a peer to drip bytes forever.
    # Shutdown interrupts HTTPResponse.read even if its requested buffer never
    # fills; the main finally still closes the connection on every outcome.
    def stop():
        sock = getattr(connection, "_transport_socket", None) or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    timer = threading.Timer(seconds, stop)
    timer.daemon = True
    timer.start()
    return timer
