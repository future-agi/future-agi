"""A configured loopback HTTPS proxy's auth rejection is not an offline gateway."""

import socket
import threading
from unittest.mock import patch

from django.test import RequestFactory

from agentcc.services.gateway_client import GatewayClient
from agentcc.views.gateway import AgentccGatewayViewSet


def test_https_proxy_auth_configuration_is_not_successful_unreachable(monkeypatch):
    seen = []
    failures = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        proxy = f"http://127.0.0.1:{listener.getsockname()[1]}"
        # The stand-in never forwards and the requested destination is loopback.
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            monkeypatch.setenv(name, proxy)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(name, "")

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
                    seen.append(head.split(b"\r\n", 1)[0])
                    peer.sendall(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="synthetic"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
                    )
            except Exception as exc:
                failures.append(type(exc).__name__)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            client = GatewayClient("https://127.0.0.1:9", "synthetic-admin")
            with patch(
                "agentcc.views.gateway.get_gateway_client", return_value=client
            ) as getter:
                result = AgentccGatewayViewSet().list(
                    RequestFactory().get("/agentcc/gateways/")
                )
            getter.assert_called_once_with()
        finally:
            worker.join(15)
        assert not worker.is_alive()
        assert failures == []
        assert seen == [b"CONNECT 127.0.0.1:9 HTTP/1.1"], (
            "the real HTTPS proxy handshake must be reached"
        )
    assert result.status_code != 200, (
        "Proxy authentication configuration error became successful unreachable"
    )
    assert "unreachable" not in str(result.data)
    assert proxy not in str(result.data)
    assert "synthetic-admin" not in str(result.data)
