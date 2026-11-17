"""A chat's grant lifetime bounds the whole exchange, not each socket read."""

import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from agentcc.services.delegated_calls import Delegation, delegated_response
from agentcc.services.gateway_client import GatewayClient, GatewayClientError


@contextmanager
def gateway(mode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                if mode == "setup":
                    time.sleep(0.8)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for _ in range(40):
                    self.wfile.write(b"data: keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield GatewayClient(
            f"http://127.0.0.1:{server.server_port}", admin_token="test_only"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("mode", ["chunks", "setup"])
def test_whole_stream_has_a_deadline_even_with_frequent_chunks(mode):
    with (
        gateway(mode) as client,
        patch("agentcc.services.delegated_calls._headers", return_value={}),
        patch("agentcc.services.delegated_calls.STREAM_TIMEOUT", 0.2),
    ):
        start = time.monotonic()
        with pytest.raises(GatewayClientError, match="deadline"):
            with delegated_response(
                client, Delegation("test_only", {}), "/chat", {}, streaming=True
            ) as response:
                list(response.iter_lines())
        assert time.monotonic() - start < 0.7


def test_retry_wait_consumes_the_same_deadline():
    import httpx

    async def refused(request):
        return httpx.Response(401, json={"error": "service_envelope_rejected"})

    real_async_client = httpx.AsyncClient
    with (
        patch("agentcc.services.delegated_calls.CHAT_TIMEOUT", 0.2),
        patch("agentcc.services.delegated_calls._headers", return_value={}),
        patch(
            "httpx.AsyncClient",
            side_effect=lambda **kw: real_async_client(
                transport=httpx.MockTransport(refused), **kw
            ),
        ),
    ):
        start = time.monotonic()
        with pytest.raises(GatewayClientError, match="deadline"):
            with delegated_response(
                GatewayClient("http://127.0.0.1:9", admin_token="test_only"),
                Delegation("test_only", {}),
                "/chat",
                {},
            ):
                pytest.fail("a refusal is not a completed response")
        assert time.monotonic() - start < 0.7
