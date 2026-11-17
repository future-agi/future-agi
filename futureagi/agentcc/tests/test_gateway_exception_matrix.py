"""Bounded HTTPX 0.28.1 taxonomy: only evidenced outage families may degrade."""

from unittest.mock import patch

import httpx
import pytest

from agentcc.services.gateway_client import GatewayClient, GatewayClientError

# Includes abstract/base classes: an ambiguous transport/protocol failure is not
# evidence of unreachability. The coverage assertion requires review on upgrade.
MATRIX = (
    (httpx.TransportError, "unknown"),
    (httpx.TimeoutException, "timeout"),
    (httpx.ConnectTimeout, "timeout"),
    (httpx.ReadTimeout, "timeout"),
    (httpx.WriteTimeout, "timeout"),
    (httpx.PoolTimeout, "timeout"),
    (httpx.NetworkError, "outage"),
    (httpx.ConnectError, "outage"),
    (httpx.ReadError, "outage"),
    (httpx.WriteError, "outage"),
    (httpx.CloseError, "outage"),
    (httpx.ProtocolError, "unknown"),
    (httpx.LocalProtocolError, "configuration"),
    (httpx.RemoteProtocolError, "outage"),
    (httpx.ProxyError, "proxy"),
    (httpx.UnsupportedProtocol, "configuration"),
)


def test_matrix_covers_pinned_transport_taxonomy():
    assert httpx.__version__ == "0.28.1"
    exposed = {
        cls
        for cls in vars(httpx).values()
        if isinstance(cls, type) and issubclass(cls, httpx.TransportError)
    }
    assert exposed == {cls for cls, _ in MATRIX}


@pytest.mark.parametrize(
    "error_type,category", MATRIX, ids=[c.__name__ for c, _ in MATRIX]
)
def test_transport_classification_matrix(error_type, category):
    original = error_type("synthetic transport detail")
    expected = (
        GatewayClientError
        if category in ("timeout", "outage")
        else httpx.TransportError
        if category == "unknown"
        else error_type
    )
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(expected) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    messages = {
        "timeout": "Gateway request timed out: synthetic transport detail",
        "outage": "Cannot connect to gateway",
        "configuration": "Invalid gateway request configuration",
        "proxy": "Invalid gateway proxy configuration",
        "unknown": "Gateway transport failure",
    }
    assert str(caught.value) == messages[category]
    if category in ("timeout", "outage"):
        assert caught.value.status_code is None
        assert caught.value.__cause__ is original
    else:
        assert not isinstance(caught.value, GatewayClientError)
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError],
)
@pytest.mark.parametrize(
    "message",
    [
        "407 Proxy Authentication Required",
        "Invalid proxy URL",
        "CERTIFICATE_VERIFY_FAILED",
    ],
)
def test_untrusted_error_text_does_not_reclassify_a_transport_reset(
    error_type, message
):
    original = error_type(message)
    original.__cause__ = ConnectionResetError("synthetic reset")
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(GatewayClientError, match="^Cannot connect to gateway$"):
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()


@pytest.mark.parametrize("cause", [None, ConnectionResetError("synthetic reset")])
def test_proxy_error_is_fail_closed_even_without_auth_text(cause):
    original = httpx.ProxyError("synthetic-secret")
    original.__cause__ = cause
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(httpx.ProxyError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Invalid gateway proxy configuration"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_unknown_transport_subclass_fails_closed():
    class FutureTransportError(httpx.TransportError):
        pass

    with patch.object(
        httpx.HTTPTransport,
        "handle_request",
        side_effect=FutureTransportError("synthetic-secret"),
    ) as send:
        with pytest.raises(
            httpx.TransportError, match="^Gateway transport failure$"
        ) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("error_type", [httpx.DecodingError, httpx.TooManyRedirects])
def test_nontransport_request_errors_are_not_outages(error_type):
    original = error_type("synthetic response error")
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(error_type) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert caught.value is original
