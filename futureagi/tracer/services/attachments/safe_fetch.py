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
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from tracer.services.attachments.contract import AttachmentError

BUFFER_BYTES = 256 * 1024
USER_AGENT = "futureagi-attachment-importer/1"
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


def resolve(host, port, *, getaddrinfo=socket.getaddrinfo, allow=address_allowed):
    """Return vetted addresses; ANY disallowed answer denies the destination."""
    try:
        answers = getaddrinfo(host, port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise unavailable() from None
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
    _address: str | None = None
    _bucket: TokenBucket | None = None

    def _connection(self):
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
            timeout=min(self.timeout_seconds, 30),
        )

    def read_range(self, offset, length, sink):
        """Write exactly ``length`` bytes at ``offset`` to ``sink``.

        A response that ignores Range, changes total length or generation,
        is encoded, short or long is rejected without draining it.
        """
        if (
            not 0 < length <= self.window
            or offset < 0
            or offset + length > self.total_bytes
        ):
            raise AttachmentError("validation_failed", 422)
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
        if self.generation is not None:
            headers["If-Match"] = self.generation
        try:
            connection.request(
                "GET", self.destination.target, headers=headers, encode_chunked=False
            )
            response = connection.getresponse()
            if response.status == 412:
                raise AttachmentError("conflict", 409)
            if response.status != 206:
                # 200 = Range ignored (whole body), 3xx = redirect: both denied.
                raise unavailable()
            etag = response.getheader("ETag") or ""
            if (
                response.getheader("Content-Range")
                != f"bytes {offset}-{end}/{self.total_bytes}"
                or response.getheader("Content-Length") != str(length)
                or response.getheader("Content-Encoding", "identity") != "identity"
                or response.getheader("Transfer-Encoding") is not None
                or not etag.startswith('"')
                or (self.generation is not None and etag != self.generation)
            ):
                raise AttachmentError("validation_failed", 422)
            self.generation = etag
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
                sink.write(chunk)
            if received != length:
                raise AttachmentError("validation_failed", 422)
            return received
        except AttachmentError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
            raise unavailable() from None
        finally:
            connection.close()
