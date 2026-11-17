"""Real loopback proxy refusals differ from direct and proxied socket loss."""

import socket
import struct
import threading
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest
from django.test import RequestFactory

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.views.gateway import AgentccGatewayViewSet

PROXY_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def configure_proxy(monkeypatch, url):
    for name in PROXY_ENV:
        monkeypatch.setenv(name, url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")


@pytest.fixture
def loopback_proxy(monkeypatch):
    @contextmanager
    def run(mode, credentials=False):
        seen, failures = [], []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(5)
            auth = (
                "synthetic-proxy-user:synthetic-proxy-password@" if credentials else ""
            )
            url = f"http://{auth}127.0.0.1:{listener.getsockname()[1]}"
            configure_proxy(monkeypatch, url)

            def serve():
                try:
                    peer, _ = listener.accept()
                    with peer:
                        peer.settimeout(5)
                        head = b""
                        while b"\r\n\r\n" not in head:
                            data = peer.recv(4096)
                            if not data:
                                break
                            head += data
                            assert len(head) <= 32768
                        # Persist booleans only, never proxy credentials/raw headers.
                        seen.append(
                            (
                                head.startswith(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n"),
                                b"proxy-authorization: basic " in head.lower(),
                            )
                        )
                        if mode.startswith("reject-"):
                            code = mode.split("-", 1)[1].encode()
                            peer.sendall(
                                b"HTTP/1.1 " + code + b" synthetic-private-reason\r\n"
                                b'Proxy-Authenticate: Basic realm="synthetic-private-realm"\r\n'
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                            )
                        elif mode == "tunnel-reset":
                            peer.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            assert peer.recv(1), "TLS negotiation must reach the tunnel"
                            peer.setsockopt(
                                socket.SOL_SOCKET,
                                socket.SO_LINGER,
                                struct.pack("ii", 1, 0),
                            )
                        elif mode == "reset":
                            peer.setsockopt(
                                socket.SOL_SOCKET,
                                socket.SO_LINGER,
                                struct.pack("ii", 1, 0),
                            )
                        else:
                            assert mode == "close"
                except Exception as exc:
                    failures.append(type(exc).__name__)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                yield GatewayClient("https://127.0.0.1:9", "synthetic-admin"), url
            finally:
                worker.join(10)
                assert not worker.is_alive()
                assert failures == []
                assert seen == [(True, credentials)], (
                    "the actual CONNECT path must be reached"
                )

    return run


@pytest.mark.parametrize("status", [407, 403, 502])
@pytest.mark.parametrize("credentials", [False, True])
def test_real_proxy_rejection_preserves_sanitized_type(
    loopback_proxy, status, credentials, caplog
):
    with loopback_proxy(f"reject-{status}", credentials) as (client, url):
        with pytest.raises(httpx.ProxyError) as caught:
            client.health_check()
    assert str(caught.value) == "Invalid gateway proxy configuration"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    for value in (
        url,
        "synthetic-admin",
        "synthetic-proxy-user",
        "synthetic-proxy-password",
        "synthetic-private-reason",
        "synthetic-private-realm",
    ):
        assert value not in str(caught.value)
        assert value not in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["list", "retrieve", "config"])
def test_real_proxy_auth_rejection_is_not_successful_unreachable(
    loopback_proxy, action
):
    with loopback_proxy("reject-407", credentials=True) as (client, url):
        with patch(
            "agentcc.views.gateway.get_gateway_client", return_value=client
        ) as getter:
            response = getattr(AgentccGatewayViewSet(), action)(
                RequestFactory().get("/agentcc/gateways/"),
                **({"pk": "default"} if action != "list" else {}),
            )
    getter.assert_called_once_with()
    assert response.status_code == (404 if action == "retrieve" else 400)
    assert "unreachable" not in str(response.data)
    for value in (
        url,
        "synthetic-proxy-user",
        "synthetic-proxy-password",
        "synthetic-admin",
        "synthetic-private-reason",
    ):
        assert value not in str(response.data)
    if action != "retrieve":
        assert "Invalid gateway proxy configuration" in str(response.data)


@pytest.mark.parametrize("mode", ["close", "reset", "tunnel-reset"])
def test_real_proxy_connection_loss_remains_an_outage(loopback_proxy, mode):
    with loopback_proxy(mode) as (client, _):
        with pytest.raises(
            GatewayClientError, match="^Cannot connect to gateway$"
        ) as caught:
            client.health_check()
    assert caught.value.status_code is None
    assert isinstance(
        caught.value.__cause__, (httpx.NetworkError, httpx.RemoteProtocolError)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["list", "retrieve", "config"])
def test_real_proxy_reset_preserves_successful_unreachable(loopback_proxy, action):
    with loopback_proxy("reset") as (client, _):
        with patch(
            "agentcc.views.gateway.get_gateway_client", return_value=client
        ) as getter:
            response = getattr(AgentccGatewayViewSet(), action)(
                RequestFactory().get("/agentcc/gateways/"),
                **({"pk": "default"} if action != "list" else {}),
            )
    getter.assert_called_once_with()
    assert response.status_code == 200
    assert "unreachable" in str(response.data)


@pytest.mark.parametrize(
    "url,error_type,message",
    [
        (
            "ftp://synthetic-proxy-user:synthetic-proxy-password@127.0.0.1:9",
            ValueError,
            "Invalid gateway client configuration",
        ),
        (
            "http://synthetic-proxy-user:synthetic-proxy-password@127.0.0.1:bad-port",
            httpx.InvalidURL,
            "Invalid gateway request configuration",
        ),
    ],
)
def test_real_invalid_proxy_setup_is_value_free(monkeypatch, url, error_type, message):
    configure_proxy(monkeypatch, url)
    with pytest.raises(error_type) as caught:
        GatewayClient("https://127.0.0.1:9").health_check()
    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_request_uses_the_entered_http_client():
    with patch("agentcc.services.gateway_client.httpx.Client") as constructor:
        manager = constructor.return_value
        entered = manager.__enter__.return_value
        entered.request.return_value = httpx.Response(200, json={"status": "ok"})
        assert GatewayClient("http://127.0.0.1:9").health_check() == {"status": "ok"}
    constructor.assert_called_once_with(timeout=10.0)
    manager.__enter__.assert_called_once_with()
    entered.request.assert_called_once()
    manager.request.assert_not_called()
    manager.__exit__.assert_called_once_with(None, None, None)


def test_response_value_error_is_not_mislabeled_as_proxy_setup():
    response = httpx.Response(200, content=b"not-json")
    with patch.object(
        httpx.HTTPTransport, "handle_request", return_value=response
    ) as send:
        with pytest.raises(ValueError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert "Invalid gateway client configuration" not in str(caught.value)
