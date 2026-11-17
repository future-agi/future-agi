"""HTTPS for the live smoke: HTTPS only, no redirect, a public address only,
a byte cap, a deadline, and errors that never carry a URL.

A credential can sit in a URL (a query-parameter key or a URL-variable
secret), and errors reach logs, so a failure is reported as a short code
(connect_error, tls_error, timeout, ...) and never as the exception's text,
as the cassette recorder's plainError does (cmd/agentcc-cassette/record/
rpc.go). A redirect is never followed (http.client follows none): it could
carry the credential to another host.

A host is dialled only at a public address. A manifest's host is L0-checked
as a public name, but DNS decides where a credential goes, so an answer in a
private, loopback, link-local (cloud metadata) or reserved range is refused,
as the gateway's egress guard refuses one (internal/mcp/egress).

Each request has one deadline, set before the host is resolved and running
to the last byte read. Each address dialled gets only the time left before
it, and no address is dialled once it has passed, so a host whose addresses
all drop the SYN costs one --timeout, not one per address (the DNS lookup
itself cannot be cut). A per-recv socket timeout alone does not bound a
request either: a server that sends a byte now and then, with no newline,
keeps every recv short while one readline runs on for hours. So a watchdog
shuts the connection down at the deadline, whatever the reader is doing,
and the read reports timeout.

Net carries the two things a test injects: the TLS trust and a route from a
host to a local address. Neither is reachable from the command line.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

USER_AGENT = "futureagi-connector-smoke/1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_BYTES = 16 << 20


class TransportError(Exception):
    """A request that got no usable answer. code is safe to print."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Net:
    """How requests reach the network. The default trusts the system store
    and dials the host's public addresses."""

    ssl_context: ssl.SSLContext | None = None
    route: Callable[[str, int], tuple[str, int]] | None = None  # tests only

    def context(self) -> ssl.SSLContext:
        return self.ssl_context or ssl.create_default_context()


DEFAULT_NET = Net()


class _Watchdog:
    """Shuts a connection down at its deadline. It holds a duplicate of the
    socket's descriptor: a shutdown acts on the connection itself, so it
    wakes a reader blocked on the TLS socket that took over the original."""

    def __init__(self, sock: socket.socket, deadline: float):
        self._dup = sock.dup()
        self._lock = threading.Lock()
        self.fired = False
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            dup, self._dup = self._dup, None
            if dup is None:
                return
            self.fired = True
        try:
            dup.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        dup.close()

    def cancel(self) -> None:
        self._timer.cancel()
        with self._lock:
            dup, self._dup = self._dup, None
        if dup is not None:
            dup.close()


def check_url(url: str) -> None:
    """Refuse a URL a credential must not travel to: anything but HTTPS with a
    host and no user info."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise TransportError("not_https") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise TransportError("not_https")
    if port == 0:
        raise TransportError("not_https")


def public_address(ip: str) -> bool:
    """Whether an address may receive a credential."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _left(deadline: float) -> float:
    """The seconds left before the deadline; none left is a timeout."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TransportError("timeout")
    return left


def _dial_public(host: str, port: int, deadline: float) -> socket.socket:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise TransportError("dns_error") from None
    allowed = [info for info in infos if public_address(info[4][0])]
    if not allowed:
        raise TransportError("address_refused")
    last = "connect_error"
    for family, kind, proto, _, addr in allowed:
        left = _left(deadline)  # no address is dialled after the deadline
        sock = socket.socket(family, kind, proto)
        sock.settimeout(left)  # the time left, not a fresh --timeout
        try:
            sock.connect(addr)
            return sock
        except TimeoutError:
            last = "timeout"
        except OSError:
            last = "connect_error"
        sock.close()
    raise TransportError(last)


def _dial(
    net: Net, host: str, port: int, deadline: float
) -> tuple[ssl.SSLSocket, _Watchdog]:
    if net.route is not None:
        to_host, to_port = net.route(host, port)
        try:
            sock = socket.create_connection((to_host, to_port), timeout=_left(deadline))
        except TimeoutError:
            raise TransportError("timeout") from None
        except OSError:
            raise TransportError("connect_error") from None
    else:
        sock = _dial_public(host, port, deadline)
    try:
        dog = _Watchdog(sock, deadline)
    except OSError:
        sock.close()
        raise TransportError("connect_error") from None
    try:
        return net.context().wrap_socket(sock, server_hostname=host), dog
    except TimeoutError:
        code = "timeout"
    except ssl.SSLError:
        code = "tls_error"
    except OSError:
        code = "connect_error"
    dog.cancel()
    sock.close()
    raise TransportError("timeout" if dog.fired else code)


@dataclass
class Response:
    status: int
    headers: dict  # lower-case name -> value
    _resp: http.client.HTTPResponse
    _conn: http.client.HTTPSConnection
    _deadline: float
    _max_bytes: int
    _dog: _Watchdog | None = None
    _read: int = 0

    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    def _late(self) -> bool:
        return (self._dog is not None and self._dog.fired) or (
            time.monotonic() > self._deadline
        )

    def _check(self, n: int) -> None:
        self._read += n
        if self._read > self._max_bytes:
            raise TransportError("too_large")
        if self._late():
            raise TransportError("timeout")

    def _failed(self) -> TransportError:
        return TransportError("timeout" if self._late() else "read_error")

    def read(self) -> bytes:
        chunks = []
        try:
            while True:
                chunk = self._resp.read(65536)
                if not chunk:
                    if self._late():  # cut at the deadline, not the end
                        raise TransportError("timeout")
                    return b"".join(chunks)
                self._check(len(chunk))
                chunks.append(chunk)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise self._failed() from None

    def readline(self) -> bytes:
        """One line, or b"" at the end of the stream."""
        try:
            line = self._resp.readline(self._max_bytes + 1)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise self._failed() from None
        if not line.endswith(b"\n") and self._late():
            raise TransportError("timeout")  # a partial line cut at the deadline
        self._check(len(line))
        return line

    def close(self) -> None:
        try:
            self._resp.close()
        finally:
            try:
                self._conn.close()
            finally:
                if self._dog is not None:
                    self._dog.cancel()


def request(
    method: str,
    url: str,
    headers=None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    net: Net | None = None,
) -> Response:
    """Send one request. Any HTTP status comes back as a Response; only a
    request with no answer raises TransportError."""
    check_url(url)
    net = net or DEFAULT_NET
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    deadline = time.monotonic() + timeout
    conn = http.client.HTTPSConnection(
        host, port, timeout=timeout, context=net.context()
    )
    conn.sock, dog = _dial(net, host, port, deadline)
    sent = {"User-Agent": USER_AGENT}
    sent.update(headers or {})
    try:
        conn.request(method, target, body=body, headers=sent)
        resp = conn.getresponse()
    except (
        TimeoutError,
        OSError,
        ssl.SSLError,
        http.client.HTTPException,
        ValueError,
    ) as err:
        conn.close()
        dog.cancel()
        late = dog.fired or isinstance(err, TimeoutError)
        raise TransportError("timeout" if late else "connect_error") from None
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    return Response(
        status=resp.status,
        headers=hdrs,
        _resp=resp,
        _conn=conn,
        _deadline=deadline,
        _max_bytes=max_bytes,
        _dog=dog,
    )
