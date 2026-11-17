"""TLS negotiation errors are configuration failures, not offline gateways."""

import socket
import ssl
import threading
from unittest.mock import patch

import httpx
import pytest
from django.test import RequestFactory

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.views.gateway import AgentccGatewayViewSet

PRIVATE_DETAIL = "synthetic-private-url-and-credential"
TLS_MESSAGE = "Invalid gateway TLS configuration"


@pytest.fixture
def plaintext_gateway():
    """Prove the HTTPS client reached a real, responsive plaintext listener."""
    seen = []
    failures = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        url = f"https://127.0.0.1:{listener.getsockname()[1]}"

        def serve():
            try:
                peer, _ = listener.accept()
                with peer:
                    peer.settimeout(5)
                    seen.append(peer.recv(65536)[:1] == b"\x16")
                    peer.sendall(
                        b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
            except Exception as error:
                failures.append(type(error).__name__)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield GatewayClient(url, "synthetic-admin")
        finally:
            thread.join(15)
            assert not thread.is_alive()
            assert seen == [True], "TLS ClientHello must reach the loopback listener"
            assert failures == []


def test_https_plaintext_misconfiguration_is_not_an_outage(plaintext_gateway):
    with pytest.raises(ssl.SSLError) as caught:
        plaintext_gateway.health_check()
    assert caught.value.args == (TLS_MESSAGE,)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["list", "retrieve", "config"])
def test_https_plaintext_misconfiguration_is_not_successful_unreachable(
    plaintext_gateway, action
):
    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=plaintext_gateway
    ) as getter:
        response = getattr(AgentccGatewayViewSet(), action)(
            RequestFactory().get("/agentcc/gateways/"),
            **({"pk": "default"} if action != "list" else {}),
        )
    getter.assert_called_once_with()
    assert response.status_code == (404 if action == "retrieve" else 400)
    assert "unreachable" not in str(response.data)
    assert plaintext_gateway.base_url not in str(response.data)
    assert "synthetic-admin" not in str(response.data)
    if action != "retrieve":
        assert TLS_MESSAGE in str(response.data)


@pytest.mark.parametrize("link", ["__cause__", "__context__"])
@pytest.mark.parametrize(
    "transport_type",
    [httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError],
)
def test_wrapped_tls_protocol_error_is_value_free(transport_type, link):
    original = transport_type(PRIVATE_DETAIL)
    middle = OSError(PRIVATE_DETAIL)
    setattr(original, link, middle)
    setattr(middle, link, ssl.SSLError(ssl.SSL_ERROR_SSL, PRIVATE_DETAIL))
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(ssl.SSLError) as caught:
            GatewayClient("http://127.0.0.1:9", "synthetic-admin").health_check()
    send.assert_called_once()
    assert type(caught.value) is ssl.SSLError
    assert caught.value.args == (TLS_MESSAGE,)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "tls_type",
    [ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLSyscallError],
)
@pytest.mark.parametrize(
    "transport_type", [httpx.ConnectError, httpx.ReadError, httpx.WriteError]
)
def test_tls_disconnects_remain_transport_outages(transport_type, tls_type):
    original = transport_type(PRIVATE_DETAIL)
    original.__cause__ = tls_type(PRIVATE_DETAIL)
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Cannot connect to gateway"
    assert caught.value.status_code is None
    assert caught.value.__cause__ is original
