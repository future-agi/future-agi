"""A real HTTP receiver on 127.0.0.1 for the WH-1b operations tests, and a dispatcher that sends to it through the
real pinned transport (``tfc.utils.ssrf_post.safe_post`` behind ``PinnedTransport``).

The receiver records every request (arrival time on a monotonic clock, path, headers, body) and a gauge of
concurrent requests, and answers by path: ``/ok`` 200, ``/fail`` 503, ``/sleep/<ms>`` waits then 200, ``/hang``
never answers in time, ``/fail-for/<ms>`` answers 503 for that long after the receiver started and then 200,
``/script/<name>`` follows a scripted status list, ``/r429/<n>/<s>`` answers the first <n> requests 429 with
``Retry-After: <s>``. It verifies nothing itself; tests verify
the signatures with ``webhooks.core.signing.verify``.

The address rules never admit loopback (DESIGN.md section 6.2), so the dispatcher resolves the endpoint's public test
name to a public address (the core vets that answer as it would any other) and :class:`LoopbackTransport` connects
that one vetted request to the receiver's port on 127.0.0.1. Everything else is the production send path: the URL
rules, signing at send, the one wall-clock deadline and the watchdog, the 4 KB read and the settle.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.db import close_old_connections

from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher
from webhooks.core.ipclass import Profile
from webhooks.core.ports import PinnedPost
from webhooks.core.tests.fakes import FakeResolver
from webhooks.store import OrmStore
from webhooks.transport import PinnedTransport

PUBLIC = "93.184.215.14"


class Clock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic(self) -> float:
        return time.monotonic()


class Server(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with a real server's listen backlog. socketserver's default of 5 overflows when a
    dispatcher opens a burst of connections at once: the kernel drops the SYNs past it and the client retries after
    1 s, so a test would measure the receiver's backlog, not the dispatcher (WT-30's profile: 38 % of the worker's
    thread samples were in ``create_connection``)."""

    request_queue_size = 1024
    daemon_threads = True
    block_on_close = False


class Receiver:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.active = 0
        self.max_active = 0
        self.max_active_by_path: dict[str, int] = {}
        self._active_by_path: dict[str, int] = {}
        self.scripts: dict[str, list[int]] = {}
        self.t0 = time.monotonic()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                arrived = time.monotonic()
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                key = self.path.split("?")[0]
                with outer.lock:
                    outer.active += 1
                    outer.max_active = max(outer.max_active, outer.active)
                    a = outer._active_by_path.get(key, 0) + 1
                    outer._active_by_path[key] = a
                    outer.max_active_by_path[key] = max(
                        outer.max_active_by_path.get(key, 0), a
                    )
                    outer.requests.append(
                        {
                            "at": arrived,
                            "path": key,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body,
                        }
                    )
                try:
                    self._answer(key)
                finally:
                    with outer.lock:
                        outer.active -= 1
                        outer._active_by_path[key] -= 1

            def _answer(self, key: str) -> None:
                parts = key.strip("/").split("/")
                status = 200
                if parts[0] == "fail":
                    status = 503
                elif parts[0] == "sleep":
                    time.sleep(int(parts[1]) / 1000)
                elif parts[0] == "hang":
                    time.sleep(40)
                elif parts[0] == "fail-for":
                    # 503 for the first <ms> after the receiver started, then 200 (an outage that ends).
                    if (time.monotonic() - outer.t0) * 1000 < int(parts[1]):
                        status = 503
                elif parts[0] == "script":
                    with outer.lock:
                        seq = outer.scripts.get(parts[1]) or [200]
                        status = seq.pop(0) if len(seq) > 1 else seq[0]
                retry_after = None
                if parts[0] == "r429":
                    # The first <n> requests get 429 with Retry-After: <s>, then 200.
                    with outer.lock:
                        outer.r429_seen = getattr(outer, "r429_seen", 0) + 1
                        if outer.r429_seen <= int(parts[1]):
                            status, retry_after = 429, parts[2]
                payload = b'{"ok":true}'
                try:
                    self.send_response(status)
                    if retry_after is not None:
                        self.send_header("Retry-After", retry_after)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str, host: str = "hooks.acme.ai") -> str:
        return f"http://{host}:{self.port}{path}"

    def arrivals(self, path: str) -> list[float]:
        with self.lock:
            return [r["at"] for r in self.requests if r["path"] == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class LoopbackTransport(PinnedTransport):
    """The production transport, sent to the receiver: the core vetted ``PUBLIC``; the socket goes to 127.0.0.1."""

    def __init__(self, port: int) -> None:
        super().__init__()
        self.port = port

    def post(self, request: PinnedPost):
        return super().post(replace(request, addresses=("127.0.0.1",), port=self.port))


def psl(host: str) -> str | None:
    return "ai" if host.endswith(".ai") else None


def real_dispatcher(
    receiver: Receiver, *, scale: float = 1.0, slots: dict | None = None
) -> Dispatcher:
    """One dispatcher loop ("replica") with its own store and its own database connections, sending over the real
    transport to ``receiver``. HTTP and the receiver's port are admitted as an operator would on a self-hosted install
    (``FI_WEBHOOKS_ALLOW_HTTP``); the address the core vets is public."""
    clock = Clock()
    profile = Profile(mode="self_hosted", allow_http=True)
    sender = Sender(
        profile, psl, FakeResolver([PUBLIC]), LoopbackTransport(receiver.port), clock
    )
    return Dispatcher(
        OrmStore(scale=scale),
        sender,
        clock,
        scale=scale,
        slots=slots,
        after_send=close_old_connections,
    )


def run_loops(dispatchers, seconds: float) -> None:
    """Run each dispatcher's real loop in its own thread for ``seconds``, then drain them."""
    stop = threading.Event()

    def loop(d):
        try:
            d.run(stop, run_for_s=seconds)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=loop, args=(d,), daemon=True) for d in dispatchers
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(seconds + 60)
    stop.set()
