"""URL fetch regressions with socket fixtures and mocked pool boundaries."""

import asyncio
import datetime
import gzip
import io
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import urllib3
from channels.db import database_sync_to_async
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ee.falcon_ai.consumers import FalconAIConsumer
from ee.falcon_ai.url_fetcher import fetch_url_content
from tfc.utils import ssrf_guard
from tfc.utils.ssrf_guard import safe_fetch


@pytest.fixture(params=["http", "https"])
def slow_url_server(monkeypatch, tmp_path, request):
    paths = []
    disconnected = threading.Event()
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            paths.append(self.path)
            try:
                if self.path.startswith("/redirect/"):
                    stop.wait(0.15)
                    remaining = int(self.path.rsplit("/", 1)[1])
                    self.send_response(302)
                    target = f"/redirect/{remaining - 1}" if remaining else "/fast"
                    self.send_header("Location", target)
                    self.end_headers()
                    return
                if self.path == "/headers":
                    for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if stop.wait(0.04):
                            return
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                body = b"x" * 40
                if self.path == "/chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    body = b"1\r\nx\r\n" * 40 + b"0\r\n\r\n"
                else:
                    if self.path == "/gzip":
                        body = gzip.compress(body)
                        self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.path == "/fast":
                    self.wfile.write(body)
                    return
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if stop.wait(0.04):
                        return
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if request.param == "https":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "public.example")])
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("public.example")]), False
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        monkeypatch.setattr(
            "tfc.utils.ssrf_guard.certifi.where", lambda: str(cert_path)
        )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def connect_to_fixture(connection):
        # Real guard and pool still validate/pin a public IP. Only the socket
        # destination is replaced so these tests never access an external host.
        assert connection.host == "93.184.216.34"
        return real_create_connection(server.server_address, connection.timeout)

    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]
            if host == "public.example"
            else real_getaddrinfo(host, port, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        urllib3.connection.HTTPConnection, "_new_conn", connect_to_fixture
    )
    try:
        yield SimpleNamespace(
            url=f"{request.param}://public.example",
            paths=paths,
            disconnected=disconnected,
        )
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("http://[2606:4700:4700::1111]/page", "[2606:4700:4700::1111]"),
        ("http://[2606:4700:4700::1111]:8080/page", "[2606:4700:4700::1111]:8080"),
        ("https://[2606:4700:4700::1111]:8443/page", "[2606:4700:4700::1111]:8443"),
        ("http://93.184.216.34/page", "93.184.216.34"),
        ("http://93.184.216.34:8080/page", "93.184.216.34:8080"),
        ("https://public.example/page", "public.example"),
        ("https://public.example:8443/page", "public.example:8443"),
    ],
)
def test_request_host_header_matches_url_authority(monkeypatch, url, expected_host):
    captured = []
    response = urllib3.HTTPResponse(
        status=200, headers={}, body=io.BytesIO(), preload_content=False
    )
    pool = Mock()

    def record_request(*args, **kwargs):
        captured.append(kwargs["headers"]["Host"])
        return response

    pool.request.side_effect = record_request
    monkeypatch.setattr(ssrf_guard, "_resolve_pinned_ip", lambda host: host)
    monkeypatch.setattr(ssrf_guard, "_open_pinned_pool", lambda *args: pool)

    assert safe_fetch(url).status_code == 200
    assert captured == [expected_host]


def test_stalled_tcp_connect_respects_total_budget(monkeypatch):
    connect_timeouts = []

    def stalled_connect(connection):
        assert connection.host == "93.184.216.34"
        connect_timeouts.append(connection.timeout)
        # Model a connect that uses its timeout before returning no socket.
        time.sleep(connection.timeout)
        raise TimeoutError("TCP connect timed out")

    monkeypatch.setattr(ssrf_guard, "_resolve_pinned_ip", lambda host: "93.184.216.34")
    monkeypatch.setattr(urllib3.connection.HTTPConnection, "_new_conn", stalled_connect)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        safe_fetch("http://public.example/page", timeout=1.5, total_timeout=0.3)
    elapsed = time.monotonic() - started
    # The connect itself must receive the total budget, not the 1.5s
    # per-operation timeout. Leave room for scheduling around the sleep.
    if connect_timeouts:
        assert 0 < connect_timeouts[0] <= 0.3
    else:
        assert elapsed >= 0.3  # Expired before a connect could start.
    assert elapsed < 1.2, elapsed


def test_silent_tls_handshake_respects_total_budget(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    release = threading.Event()
    accepted = threading.Event()
    handshake_timeouts = []
    real_wrap_socket = ssl.SSLContext.wrap_socket

    def capture_handshake_timeout(context, sock, *args, **kwargs):
        # Called after urllib3 constructs the context and loads its CA bundle,
        # immediately before Python starts the TLS handshake.
        handshake_timeouts.append(sock.gettimeout())
        return real_wrap_socket(context, sock, *args, **kwargs)

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", capture_handshake_timeout)

    def hold_connection():
        try:
            connection, _ = listener.accept()
            with connection:
                accepted.set()
                release.wait(3)
        except OSError:
            pass

    serving = threading.Thread(target=hold_connection, daemon=True)
    serving.start()
    real_connect = socket.create_connection

    def connect_to_fixture(connection):
        assert connection.host == "93.184.216.34"
        # Spend part of the budget in TCP before the real TLS handshake stalls.
        time.sleep(0.4)
        return real_connect(listener.getsockname(), connection.timeout)

    monkeypatch.setattr(ssrf_guard, "_resolve_pinned_ip", lambda host: "93.184.216.34")
    monkeypatch.setattr(
        urllib3.connection.HTTPConnection, "_new_conn", connect_to_fixture
    )
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError) as error:
            safe_fetch("https://public.example/page", timeout=1.5, total_timeout=0.6)
        elapsed = time.monotonic() - started
        if handshake_timeouts:
            assert len(handshake_timeouts) == 1
            assert accepted.wait(1)
            assert 0 < handshake_timeouts[0] <= 0.6 - 0.4 + 0.01
        else:
            # A loaded runner can exhaust the budget before TLS starts. It
            # must fail on the deadline rather than silently skip this check.
            assert "deadline" in str(error.value).lower()
            assert elapsed >= 0.6
        # Without the shared deadline, TCP sleep + a 1.5s TLS timeout is 1.9s.
        assert elapsed < 1.4, elapsed
    finally:
        release.set()
        listener.close()
        serving.join(timeout=2)


@pytest.mark.parametrize("path", ["/drip", "/headers", "/chunked", "/gzip"])
def test_progressing_response_stops_at_deadline(monkeypatch, slow_url_server, path):
    read_timeouts = []
    real_readinto = ssrf_guard._DeadlineReader.readinto

    def record_read_timeout(reader, buffer):
        result = real_readinto(reader, buffer)
        # A read can raise before changing the socket timeout when its budget
        # is already gone. Record only the timeouts applied to completed reads.
        read_timeouts.append(reader._sock.gettimeout())
        return result

    monkeypatch.setattr(ssrf_guard._DeadlineReader, "readinto", record_read_timeout)
    started = time.monotonic()
    result = fetch_url_content(f"{slow_url_server.url}{path}", timeout=0.35)
    elapsed = time.monotonic() - started

    assert result.startswith("[Failed to fetch URL:"), result
    assert "deadline" in result.lower(), result
    if read_timeouts:
        # Every actual raw receive gets no more than the original budget;
        # progressing bytes cannot reset the clock to an inactivity timeout.
        assert all(0 < value <= 0.35 for value in read_timeouts)
        if len(read_timeouts) > 1:
            assert read_timeouts[-1] < read_timeouts[0]
    else:
        assert elapsed >= 0.35  # Expired before any raw receive.
    # Without a deadline the progressing response takes at least ~1.5s
    # (and /gzip must finish successfully rather than report a deadline).
    assert elapsed < 1.3, elapsed
    # Timeout must close the actual transport, not just abandon a future while
    # the socket continues occupying a worker in the background.
    if read_timeouts:
        assert slow_url_server.disconnected.wait(0.5)


def test_redirect_chain_shares_one_deadline(slow_url_server):
    started = time.monotonic()
    result = fetch_url_content(f"{slow_url_server.url}/redirect/3", timeout=0.35)
    elapsed = time.monotonic() - started

    assert result.startswith("[Failed to fetch URL:"), result
    assert "deadline" in result.lower(), result
    # Four 0.15s redirects would reach /fast with per-operation timeouts.
    assert elapsed < 1.2, elapsed
    assert "/fast" not in slow_url_server.paths
    assert len(slow_url_server.paths) <= 3


def test_fast_response_completes_within_deadline(slow_url_server):
    url = f"{slow_url_server.url}/fast"
    assert fetch_url_content(url, timeout=1) == f"[URL: {url}]\n" + "x" * 40


def test_unspecified_deadline_preserves_inactivity_timeout(slow_url_server):
    started = time.monotonic()
    response = safe_fetch(f"{slow_url_server.url}/drip", method="GET", timeout=0.35)
    assert response.content == b"x" * 40
    assert time.monotonic() - started > 0.8


def test_dns_wait_stops_at_deadline(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    connections = []

    def resolve(host):
        try:
            assert release.wait(2)
            return "93.184.216.34"
        finally:
            finished.set()

    monkeypatch.setattr("tfc.utils.ssrf_guard._resolve_pinned_ip", resolve)
    monkeypatch.setattr(
        urllib3.connection.HTTPConnection, "connect", connections.append
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(ssrf_guard, "_RESOLVERS", executor)
        started = time.monotonic()
        try:
            result = fetch_url_content("http://public.example/fast", timeout=0.1)
            assert "deadline" in result.lower(), result
            assert time.monotonic() - started < 0.5
        finally:
            release.set()
            assert finished.wait(1)
    assert connections == []


def test_expired_dns_waits_do_not_accumulate_queued_lookups(monkeypatch):
    release = threading.Event()

    def resolve(host):
        assert release.wait(3)
        return "93.184.216.34"

    with ThreadPoolExecutor(max_workers=4) as executor:
        submit = Mock(wraps=executor.submit)
        monkeypatch.setattr(executor, "submit", submit)
        monkeypatch.setattr(ssrf_guard, "_RESOLVERS", executor)
        monkeypatch.setattr(
            ssrf_guard, "_RESOLVER_SLOTS", threading.BoundedSemaphore(4)
        )
        monkeypatch.setattr(ssrf_guard, "_resolve_pinned_ip", resolve)
        try:
            for _ in range(12):
                with pytest.raises(TimeoutError, match="deadline"):
                    ssrf_guard._resolve_before_deadline(
                        "public.example", time.monotonic() + 0.03
                    )
            assert submit.call_count == 4
        finally:
            release.set()


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_request_write_uses_budget_remaining_after_connect(monkeypatch, scheme):
    clock = [10.0]
    monkeypatch.setattr(ssrf_guard, "_monotonic", lambda: clock[0])
    connection_class = (
        ssrf_guard._DeadlineHTTPSConnection
        if scheme == "https"
        else ssrf_guard._DeadlineHTTPConnection
    )
    connection = connection_class("93.184.216.34", timeout=1, deadline=11)
    sock = Mock()

    def connect():
        clock[0] = 10.8
        connection.sock = sock

    monkeypatch.setattr(connection, "connect", connect)
    connection.send(b"GET / HTTP/1.1\r\nHost: public.example\r\n\r\n")
    sock.settimeout.assert_called_once_with(pytest.approx(0.2))
    sock.sendall.assert_called_once()


@pytest.mark.parametrize("message", ["hello", "http://public.example/fast"])
def test_chat_url_dispatch_does_not_wait_for_model_stream_executor(
    monkeypatch, message
):
    release = threading.Event()
    started = threading.Event()

    def model_stream():
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(
        "ee.falcon_ai.url_fetcher.fetch_urls_from_message",
        lambda message: "Fetched context",
    )
    consumer = FalconAIConsumer()
    consumer._save_user_message = AsyncMock(return_value=None)
    consumer.send_json = AsyncMock()

    async def chat():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        stream = loop.run_in_executor(None, model_stream)
        try:
            assert started.wait(1)
            await asyncio.wait_for(
                consumer._handle_chat(
                    {"conversation_id": "test-conversation", "message": message}
                ),
                timeout=0.3,
            )
            assert not stream.done()
        finally:
            release.set()
            await stream

    asyncio.run(chat())


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_slow_fetch_does_not_occupy_database_executor(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fetch(message):
        started.set()
        assert release.wait(3), "test must release the fetch worker"
        return "Fetched context"

    monkeypatch.setattr("ee.falcon_ai.url_fetcher.fetch_urls_from_message", fetch)
    consumer = FalconAIConsumer()
    consumer._save_user_message = AsyncMock(return_value=None)
    consumer.send_json = AsyncMock()
    chat = asyncio.create_task(
        consumer._handle_chat(
            {
                "conversation_id": "test-conversation",
                "message": "http://public.example/drip",
            }
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # No private ThreadSensitiveContext: exercise the same process-wide
        # database executor used by unrelated WebSocket consumers.
        assert (
            await asyncio.wait_for(database_sync_to_async(lambda: "ready")(), 0.3)
            == "ready"
        )
        assert not chat.done()
    finally:
        release.set()
        await asyncio.wait_for(chat, 3)
