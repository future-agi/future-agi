"""A local HTTPS server that plays vendor hosts for the runner's tests.

One listening socket on 127.0.0.1 serves every host by its Host header; the
tests route each fixture host there (transport.Net.route) and trust its
self-signed certificate, so the runner's real TLS, hostname check and HTTP
code run unchanged. Every request is recorded (host, method, path, headers,
JSON-RPC method, tool name and arguments), so a test can prove what was and
was not sent. A host the test did not configure answers 404.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEGACY = "2025-11-25"
STATELESS = "2026-07-28"


@dataclass
class Host:
    """How one fake vendor host answers."""

    tools: list = field(default_factory=list)
    # tool name -> MCP result (or a callable of the arguments)
    results: dict = field(default_factory=dict)
    require: dict = field(default_factory=dict)  # header -> exact value; otherwise 401
    era: str = LEGACY  # LEGACY: initialize and a session; STATELESS: server/discover
    sse: bool = False  # answer in a text/event-stream
    page_size: int = 0  # tools/list page size; 0: one page
    delay: float = 0.0  # seconds to wait before answering a POST
    # path (with query) -> (status, headers, body bytes)
    get: dict = field(default_factory=dict)
    # path -> (seconds between bytes, byte count): a slow server that sends
    # one byte at a time with no newline, under its Content-Length
    drip: dict = field(default_factory=dict)


@dataclass
class Seen:
    host: str
    method: str
    path: str
    headers: dict
    rpc: str | None = None
    tool: str | None = None
    arguments: object = None


class Fake:
    def __init__(self, context: ssl.SSLContext):
        self.hosts: dict[str, Host] = {}
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.fake = self
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def record(self, seen: Seen) -> None:
        with self._lock:
            self.seen.append(seen)

    def calls(self, host: str | None = None) -> list:
        """The tools/call names the fake received, in order."""
        with self._lock:
            return [
                s.tool
                for s in self.seen
                if s.rpc == "tools/call" and (host is None or s.host == host)
            ]

    def requests(self, host: str | None = None) -> list:
        with self._lock:
            return [s for s in self.seen if host is None or s.host == host]

    def reset(self) -> None:
        with self._lock:
            self.seen.clear()
        self.hosts.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output clean
        pass

    @property
    def fake(self) -> Fake:
        return self.server.fake

    def _host(self) -> str:
        return (self.headers.get("Host") or "").split(":", 1)[0]

    def _send(self, status: int, body: bytes = b"", headers=None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            if body:
                self.wfile.write(body)
        except OSError:  # the client gave up (a timeout test): nothing to answer
            self.close_connection = True

    def _seen(self, **kw) -> Seen:
        seen = Seen(
            self._host(),
            self.command,
            self.path,
            {k.lower(): v for k, v in self.headers.items()},
            **kw,
        )
        self.fake.record(seen)
        return seen

    def _allowed(self, host: Host) -> bool:
        return all(self.headers.get(k) == v for k, v in host.require.items())

    def do_GET(self):
        self._seen()
        host = self.fake.hosts.get(self._host())
        if host is not None and self.path in host.drip:
            self._drip(*host.drip[self.path])
            return
        if host is None or self.path not in host.get:
            self._send(404)
            return
        if not self._allowed(host):
            self._send(401)
            return
        status, headers, body = host.get[self.path]
        self._send(status, body, headers)

    def _drip(self, interval: float, count: int) -> None:
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(count))
            self.end_headers()
            self.wfile.flush()
            for _ in range(count):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(interval)
        except OSError:  # the client hung up: the point of the test
            pass

    def do_DELETE(self):
        self._seen()
        self._send(200)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            msg = {}
        method = msg.get("method")
        params = msg.get("params") or {}
        self._seen(
            rpc=method,
            tool=params.get("name") if method == "tools/call" else None,
            arguments=params.get("arguments") if method == "tools/call" else None,
        )
        host = self.fake.hosts.get(self._host())
        if host is None:
            self._send(404)
            return
        if not self._allowed(host):
            self._send(401, b"", {"WWW-Authenticate": 'Bearer realm="fake"'})
            return
        if host.delay:
            time.sleep(host.delay)
        if "id" not in msg:  # a notification
            self._send(202)
            return
        self._answer(host, msg["id"], *self._dispatch(host, method, params))

    def _dispatch(self, host: Host, method, params):
        if method == "server/discover":
            if host.era == STATELESS:
                return (
                    {
                        "supportedVersions": [STATELESS],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1"},
                    },
                    None,
                    {},
                )
            return None, {"code": -32601, "message": "method not found"}, {}
        if method == "initialize":
            if host.era == STATELESS:
                return None, {"code": -32601, "message": "method not found"}, {}
            return (
                {
                    "protocolVersion": LEGACY,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1"},
                },
                None,
                {"Mcp-Session-Id": "fake-session-1"},
            )
        if method == "tools/list":
            tools = host.tools
            if host.page_size:
                start = int(params.get("cursor") or 0)
                page = tools[start : start + host.page_size]
                nxt = start + host.page_size
                result = {"tools": page}
                if nxt < len(tools):
                    result["nextCursor"] = str(nxt)
                return result, None, {}
            return {"tools": tools}, None, {}
        if method == "tools/call":
            name = params.get("name")
            if name not in host.results:
                return None, {"code": -32602, "message": "unknown tool"}, {}
            result = host.results[name]
            return (
                (result(params.get("arguments")) if callable(result) else result),
                None,
                {},
            )
        return None, {"code": -32601, "message": "method not found"}, {}

    def _answer(self, host: Host, rid, result, error, headers) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        payload = json.dumps(msg).encode("utf-8")
        if host.sse:
            body = b"event: message\ndata: " + payload + b"\n\n"
            self._send(200, body, {"Content-Type": "text/event-stream", **headers})
        else:
            self._send(200, payload, {"Content-Type": "application/json", **headers})
