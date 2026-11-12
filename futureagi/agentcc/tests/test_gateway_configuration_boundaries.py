"""Invalid client configuration must not masquerade as a gateway outage."""

from unittest.mock import patch

import httpx
import pytest
from django.test import RequestFactory

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.views.gateway import AgentccGatewayViewSet


@pytest.mark.parametrize("base_url", ["ftp://127.0.0.1:9", "127.0.0.1:9"])
def test_bad_gateway_scheme_is_not_an_outage(base_url):
    # Exercise HTTPX's actual validation: neither case needs a remote socket.
    with pytest.raises(httpx.UnsupportedProtocol) as caught:
        GatewayClient(base_url).health_check()
    assert str(caught.value) == "Invalid gateway request configuration"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("base_url", ["ftp://127.0.0.1:9", "127.0.0.1:9"])
def test_bad_gateway_scheme_is_not_successful_unreachable_read(base_url):
    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=GatewayClient(base_url)
    ) as getter:
        response = AgentccGatewayViewSet().list(
            RequestFactory().get("/agentcc/gateways/")
        )
    getter.assert_called_once_with()
    assert response.status_code == 400
    assert "Invalid gateway request configuration" in str(response.data)
    assert "unreachable" not in str(response.data)
    assert base_url not in str(response.data)


@pytest.mark.parametrize(
    "error_type",
    [httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.InvalidURL],
)
def test_configuration_refusal_preserves_type_without_values(error_type):
    # Header validation and malformed URLs can include operator values in their
    # original exceptions. The view exposes str(exc), so replace those values.
    original = error_type("synthetic-private-url-and-credential")
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(error_type) as caught:
            GatewayClient("http://127.0.0.1:9", "synthetic-admin").health_check()
    send.assert_called_once()
    assert not isinstance(caught.value, GatewayClientError)
    assert str(caught.value) == "Invalid gateway request configuration"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_http_status_is_preserved(status):
    request = httpx.Request("GET", "http://127.0.0.1:9/healthz")
    response = httpx.Response(status, json={"error": "synthetic"}, request=request)
    with patch.object(
        httpx.HTTPTransport, "handle_request", return_value=response
    ) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert caught.value.status_code == status
    assert str(caught.value) == f"Gateway returned {status}: {response.text}"


@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
def test_timeout_semantics_are_unchanged(error_type):
    original = error_type("synthetic timeout")
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Gateway request timed out: synthetic timeout"
    assert caught.value.status_code is None
    assert caught.value.__cause__ is original
