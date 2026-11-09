"""T6 urllib3 (GHSA-vxq7-64xx-v4gw): an endless chunk-size line fails closed.

safe_fetch bounds a GET body by counting what response.stream() yields. On a
Transfer-Encoding: chunked response, urllib3 < 2.8.0 reads the chunk-size line
with an unbounded readline(), so a server that never ends that line makes the
client buffer all of it before safe_fetch sees a single byte; 2.8.0 caps the
line and raises. A tenant-supplied eval media URL reaches this path
(agentic_eval/core_evals/fi_evals/function/embedding_evals.py).

The server is one loopback socket in this process. The SSRF guard stays as it
is apart from one admission: ``_reject_unsafe_ip`` (the address allowlist
hook) is wrapped so that host 127.0.0.1 resolving to 127.0.0.1 passes and
every other address goes to the real check. Name resolution, IP pinning,
redirect handling and the body limit all run unchanged, and the last test
shows the same server is refused without the admission. CLOUD_DEPLOYMENT is
cleared so the self-hosted transport rule (http:// allowed) applies.

Run with pytest's --noconftest: no Django setup, database or other service is
needed (settings stay unconfigured).
"""

import socket
import threading
import time
import tracemalloc

import pytest

from tfc.utils import ssrf_guard
from tfc.utils.ssrf_guard import SsrfBlocked, safe_fetch

LOOPBACK = "127.0.0.1"
MAX_BYTES = 256 * 1024
# How much of the chunk-size line the server is willing to send: 32 times the
# body limit, then it closes. A client that buffers the line holds all of it.
LINE_BYTES = 32 * MAX_BYTES
BLOCK = b"Z" * (64 * 1024)  # no CRLF and no hex digit: never a valid size line
TIMEOUT_SECONDS = 5
HEADERS = b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"


class _Server:
    """Answers each connection on 127.0.0.1 with the next scripted reply."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.sent = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind((LOOPBACK, 0))
        self._listener.listen(len(self._replies) or 1)
        self._listener.settimeout(TIMEOUT_SECONDS * 2)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        for reply in self._replies:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(TIMEOUT_SECONDS * 2)
                request = b""
                while b"\r\n\r\n" not in request and len(request) < 65536:
                    data = conn.recv(4096)
                    if not data:
                        break
                    request += data
                self.sent.append(reply(conn))

    def close(self):
        self._listener.close()
        self._thread.join(TIMEOUT_SECONDS * 2)


def _small_chunked_body(conn):
    payload = HEADERS + b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
    conn.sendall(payload)
    return len(payload)


def _endless_size_line(conn):
    sent = 0
    try:
        conn.sendall(HEADERS + b"Transfer-Encoding: chunked\r\n\r\n")
        while sent < LINE_BYTES:
            conn.sendall(BLOCK)
            sent += len(BLOCK)
    except OSError:
        pass  # the client hung up, which is what a bounded client does
    return sent


@pytest.fixture
def admit_test_server(monkeypatch):
    """Admit host 127.0.0.1 at 127.0.0.1 and nothing else."""
    reject_unsafe_ip = ssrf_guard._reject_unsafe_ip

    def admit_only_the_test_server(ip_str, host):
        if ip_str == LOOPBACK and host == LOOPBACK:
            return
        reject_unsafe_ip(ip_str, host)

    monkeypatch.setattr(ssrf_guard, "_reject_unsafe_ip", admit_only_the_test_server)


@pytest.fixture(autouse=True)
def self_hosted(monkeypatch):
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)


def test_endless_chunk_size_line_fails_closed_within_max_bytes(admit_test_server):
    server = _Server([_small_chunked_body, _endless_size_line])
    try:
        url = f"http://{LOOPBACK}:{server.port}/media"
        # A well-formed chunked reply from the same server goes through, so
        # the refusal below comes from the response, not from the guard. It
        # also loads every module the fetch path imports before measuring.
        control = safe_fetch(
            url, method="GET", timeout=TIMEOUT_SECONDS, max_bytes=MAX_BYTES
        )
        assert control.status_code == 200
        assert control.content == b"hello"

        tracemalloc.start()
        started = time.monotonic()
        try:
            with pytest.raises(ValueError) as caught:
                safe_fetch(
                    url, method="GET", timeout=TIMEOUT_SECONDS, max_bytes=MAX_BYTES
                )
            elapsed = time.monotonic() - started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    finally:
        server.close()

    assert peak <= MAX_BYTES, (
        f"safe_fetch held {peak} bytes for a {MAX_BYTES}-byte limit: the "
        f"chunk-size line was buffered (server sent {server.sent[-1]} bytes)"
    )
    assert elapsed < TIMEOUT_SECONDS, f"safe_fetch took {elapsed:.2f}s to refuse"
    error = caught.value
    assert not isinstance(error, SsrfBlocked)
    message = str(error)[:300]
    limit_error = "exceed" in message
    assert limit_error, f"expected the size/limit refusal, got: {message}"


def test_loopback_stays_refused_without_the_test_admission():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((LOOPBACK, 0))
        listener.listen(1)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        with pytest.raises(SsrfBlocked, match="private/internal address"):
            safe_fetch(f"http://{LOOPBACK}:{port}/media", method="GET")
        with pytest.raises(BlockingIOError):
            listener.accept()  # the guard refused before connecting
    finally:
        listener.close()
