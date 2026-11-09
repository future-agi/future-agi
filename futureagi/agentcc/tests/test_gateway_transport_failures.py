"""Transport outages must preserve the gateway's unreachable read fallbacks.

The cloud gate runs these real loopback sockets under the control-complete 59l
socket guard. No provider or non-loopback gateway is contacted.
"""

import socket
import struct
import threading
from unittest.mock import Mock, patch

import httpx
import pytest

from agentcc.services.gateway_client import GatewayClient, GatewayClientError


@pytest.fixture
def resetting_gateway():
    """Accept a request, then send TCP RST rather than an HTTP response."""
    accepted = []
    errors = []
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        listener.settimeout(0.1)
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"

        def serve():
            while not stop.is_set():
                try:
                    peer, _ = listener.accept()
                except TimeoutError:
                    continue
                try:
                    with peer:
                        peer.settimeout(2)
                        # Read only enough to know the request arrived. Do not
                        # retain headers, tokens, URLs, or other request values.
                        assert peer.recv(1)
                        accepted.append(True)
                        peer.setsockopt(
                            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                        )
                except Exception as exc:
                    errors.append(type(exc).__name__)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield GatewayClient(address), accepted
        finally:
            stop.set()
            thread.join(5)
            assert not thread.is_alive()
            assert not errors, errors


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        "/agentcc/gateways/",
        "/agentcc/gateways/default/",
        "/agentcc/gateways/default/config/",
    ],
)
def test_reset_degrades_exactly_like_connect_error(
    auth_client, resetting_gateway, path
):
    client, accepted = resetting_gateway
    send = httpx.HTTPTransport.handle_request
    refused = Mock(side_effect=httpx.ConnectError("synthetic unavailable gateway"))

    def connect_error(transport, request):
        # License middleware may also make a loopback request on the first
        # authenticated read. Only replace this gateway's health transport.
        if str(request.url) == client.base_url + "/healthz":
            return refused(request)
        return send(transport, request)

    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=client
    ) as getter:
        with patch.object(httpx.HTTPTransport, "handle_request", connect_error):
            baseline = auth_client.get(path)
        refused.assert_called_once()
        getter.assert_called_once_with()
        assert baseline.status_code == 200
        result = baseline.json()["result"]
        if isinstance(result, list):
            result = result[0]
        assert result.get("gateway", result)["status"] == "unreachable"

        getter.reset_mock()
        reset = auth_client.get(path)
        getter.assert_called_once_with()
    assert len(accepted) == 1, "the real loopback reset path must be reached"
    assert reset.status_code == baseline.status_code
    assert reset.json() == baseline.json()


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError],
)
def test_transport_errors_are_value_free(failure):
    client = GatewayClient("http://127.0.0.1:9/private-gateway")
    error = failure("synthetic private transport detail")
    with patch.object(httpx.HTTPTransport, "handle_request", side_effect=error) as send:
        with pytest.raises(GatewayClientError) as caught:
            client.health_check()
    send.assert_called_once()
    assert str(caught.value) == "Cannot connect to gateway"
    assert caught.value.status_code is None
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
def test_transport_timeout_semantics_unchanged(failure):
    error = failure("synthetic timeout")
    with patch.object(httpx.HTTPTransport, "handle_request", side_effect=error) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Gateway request timed out: synthetic timeout"
    assert caught.value.status_code is None
    assert caught.value.__cause__ is error
