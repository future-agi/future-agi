"""A failed health probe answers with the typed result its contract declares.

The health check answers a failed live probe with 400 and a typed result: the
status (unreachable), the error, and the time the probe completed. Every other
refusal carries a string result. The declared 400 response must describe that
result, so the published schema and the generated client can type it.
"""

import datetime
import json
import socket
from functools import partial

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.serializers.contracts import GatewayHealthErrorResponseSerializer
from agentcc.services.gateway_client import GatewayClient
from agentcc.tests import test_gateway_health_error_boundaries as tls_boundaries
from agentcc.views import gateway

# Register the real loopback TLS gateway fixture under its own name.
untrusted_tls_gateway = tls_boundaries.untrusted_tls_gateway


def _declared(status_code):
    schema = gateway.AgentccGatewayViewSet.health_check._swagger_auto_schema
    schema = schema.get("post", schema)
    return schema["responses"][status_code]


@pytest.mark.unit
def test_the_health_check_declares_its_typed_probe_error():
    assert _declared(400) is GatewayHealthErrorResponseSerializer


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["connection_error", "timeout"])
def test_a_failed_probe_reports_its_completion_time_in_the_declared_shape(
    monkeypatch, failure
):
    started = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    completed = started + datetime.timedelta(seconds=10)
    clock = {"now": started}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])

    def transport(request):
        clock["now"] = completed
        if failure == "connection_error":
            raise httpx.ConnectError("Gateway offline", request=request)
        raise httpx.ReadTimeout("Gateway timed out", request=request)

    # The real client and its error handling; only the transport is replaced.
    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(transport))
    )
    monkeypatch.setattr(
        gateway, "get_gateway_client", lambda: GatewayClient("http://gateway.test")
    )

    request = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {}, format="json"
    )
    request.organization = Organization(name="Probe error contract test")
    force_authenticate(request, user=User(email="health-probe@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        request, pk="default"
    )
    response.render()
    payload = json.loads(response.content)

    assert response.status_code == 400
    result = payload["result"]
    assert result["status"] == "unreachable"
    assert result["error"]
    assert datetime.datetime.fromisoformat(result["last_health_check"]) == completed
    serializer = _declared(400)(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert set(serializer.validated_data["result"]) == {
        "status",
        "error",
        "last_health_check",
    }


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["connection_error", "timeout", "gateway_503"])
def test_a_failed_probe_answers_with_value_free_error_text(monkeypatch, failure):
    # The gateway's answer, or the transport's error, may carry the gateway's
    # URL, its body, its headers and a key. None of it reaches the 400: the
    # error is a fixed sentence for the cause.
    secret = "sk-agentcc-synthetic-health-probe-0000000000000000"

    def transport(request):
        if failure == "connection_error":
            raise httpx.ConnectError(
                f"cannot reach {request.url} with {secret}", request=request
            )
        if failure == "timeout":
            raise httpx.ReadTimeout(
                f"timed out reading {request.url} with {secret}", request=request
            )
        return httpx.Response(
            503,
            json={"error": {"message": f"down: {secret}", "code": "overloaded"}},
            headers={"X-Upstream-Detail": secret},
            request=request,
        )

    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(transport))
    )
    monkeypatch.setattr(
        gateway,
        "get_gateway_client",
        lambda: GatewayClient("http://gateway.test", "synthetic-admin-token"),
    )

    request = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {}, format="json"
    )
    request.organization = Organization(name="Probe error text test")
    force_authenticate(request, user=User(email="health-probe@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        request, pk="default"
    )
    response.render()
    payload = json.loads(response.content)

    assert response.status_code == 400
    assert payload["result"]["error"] == {
        "connection_error": gateway.HEALTH_CHECK_UNREACHABLE,
        "timeout": gateway.HEALTH_CHECK_TIMED_OUT,
        "gateway_503": "The gateway returned HTTP 503.",
    }[failure]
    body = response.content.decode()
    for value in (
        "gateway.test",
        "/healthz",
        secret,
        "synthetic-admin-token",
        "X-Upstream-Detail",
        "overloaded",
    ):
        assert value not in body, value


def _closed_local_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.unit
@pytest.mark.parametrize("cause", ["name_lookup", "connection_refused", "certificate"])
def test_a_failed_check_logs_the_cause_class_and_never_the_raw_error(
    request, monkeypatch, cause
):
    # The log says which kind of failure it was. The raw error text can carry
    # the gateway's address, credentials in it, or an upstream body, so it is
    # never logged. Each case goes through the real client and a real socket:
    # a name that cannot resolve, a closed local port, and a local TLS
    # gateway whose certificate is not trusted.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    if cause == "name_lookup":
        client = GatewayClient("http://gateway-health-check.invalid")
    elif cause == "connection_refused":
        client = GatewayClient(f"http://127.0.0.1:{_closed_local_port()}")
    else:
        client = request.getfixturevalue("untrusted_tls_gateway")
    monkeypatch.setattr(gateway, "get_gateway_client", lambda: client)
    logged = []
    monkeypatch.setattr(
        gateway.logger, "warning", lambda event, **kw: logged.append((event, kw))
    )

    check = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {}, format="json"
    )
    check.organization = Organization(name="Probe cause log test")
    force_authenticate(check, user=User(email="health-probe@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        check, pk="default"
    )
    response.render()

    assert response.status_code == 400
    event = (
        "health_check_error"
        if cause == "certificate"
        else "gateway_health_check_failed"
    )
    fields = dict(logged)[event]
    assert fields["cause"] == cause
    body = response.content.decode()
    for raw in (
        "gateway-health-check.invalid",
        "127.0.0.1",
        "Errno",
        "certificate verify failed",
        "TLSV1",
        "_ssl.c",
    ):
        assert raw not in repr(fields), raw
        assert raw not in body, raw
