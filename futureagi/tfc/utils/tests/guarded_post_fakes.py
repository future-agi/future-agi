"""Fakes for tests of ``tfc.utils.ssrf_guard.guarded_post`` and its callers.

No network: ``FakeResolver`` replaces the guard's ``socket.getaddrinfo`` and
``FakeTransport`` replaces the urllib3 pool classes the guard connects with,
recording every pool (the pinned address) and every request. ``UnguardedHttp``
records any request that still goes through ``requests`` instead of the guard.
"""

import ipaddress
import socket

import requests
import urllib3

JSON_HEADERS = {"Content-Type": "application/json"}
COMPLETION_BODY = (
    b'{"id": "c1", "choices": [{"message": {"role": "assistant", '
    b'"content": "public model answer"}}]}'
)


class FakeResolver:
    """``socket.getaddrinfo`` over a fixed table; unknown names do not resolve.

    The patch is process-wide (``ssrf_guard.socket`` is the socket module), so
    an address literal, which urllib3 resolves again when it connects to the
    pinned address, goes to the real function and is not counted as a lookup.
    """

    def __init__(self, table=None):
        self.table = {}
        self.lookups = []
        self._real_getaddrinfo = socket.getaddrinfo
        for host, ips in (table or {}).items():
            self.add(host, *ips)

    def add(self, host, *ips):
        self.table[str(host).lower().rstrip(".")] = list(ips)

    def __call__(self, host, port=None, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            pass
        else:
            return self._real_getaddrinfo(host, port, *args, **kwargs)
        self.lookups.append(host)
        ips = self.table.get(str(host).lower().rstrip("."))
        if ips is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in ips:
            if ":" in ip:
                out.append(
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port or 0, 0, 0))
                )
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)))
        return out

    def install(self, monkeypatch):
        monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", self)
        return self


class FakeResponse:
    """The parts of ``urllib3.HTTPResponse`` the guard uses."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = urllib3.HTTPHeaderDict(headers or {})
        self._body = body
        self.released = False

    def stream(self, amt=65536, decode_content=True):
        for start in range(0, len(self._body), amt):
            yield self._body[start : start + amt]

    def release_conn(self):
        self.released = True

    def drain_conn(self):
        pass


class FakeTransport:
    """Stands in for ``urllib3.HTTPConnectionPool``/``HTTPSConnectionPool``."""

    def __init__(self):
        self.pools = []
        self.requests = []
        self.responses = []
        self.error = None

    def respond(self, *responses):
        """Queue responses (or exceptions to raise), one per request."""
        self.responses.extend(responses)
        return self

    def fail_with(self, error):
        self.error = error
        return self

    def _pool_class(self, scheme):
        transport = self

        class _Pool:
            def __init__(self, host, port=None, **kwargs):
                transport.pools.append(
                    {"scheme": scheme, "host": host, "port": port, **kwargs}
                )

            def urlopen(self, method, url, body=None, headers=None, **kwargs):
                transport.requests.append(
                    {
                        "method": method,
                        "url": url,
                        "body": body,
                        "headers": dict(headers or {}),
                        **kwargs,
                    }
                )
                if transport.error is not None:
                    raise transport.error
                if transport.responses:
                    item = transport.responses.pop(0)
                    if isinstance(item, BaseException):
                        raise item
                    return item
                return FakeResponse(200, JSON_HEADERS, COMPLETION_BODY)

            request = urlopen

            def close(self):
                pass

        return _Pool

    def install(self, monkeypatch):
        monkeypatch.setattr(
            "tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool", self._pool_class("http")
        )
        monkeypatch.setattr(
            "tfc.utils.ssrf_guard.urllib3.HTTPSConnectionPool",
            self._pool_class("https"),
        )
        return self

    @property
    def sent_headers(self):
        """The headers of the last request, keyed in lower case."""
        return {k.lower(): v for k, v in self.requests[-1]["headers"].items()}


class UnguardedHttp:
    """Records every request sent through ``requests`` and refuses it."""

    def __init__(self):
        self.calls = []

    def install(self, monkeypatch):
        recorder = self

        def _send(session, prepared, **kwargs):
            recorder.calls.append(prepared.url)
            raise requests.exceptions.ConnectionError(
                "unguarded request refused by test"
            )

        monkeypatch.setattr(requests.sessions.Session, "send", _send)
        return self
