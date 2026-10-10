"""Live gateway probes report their completion time without persisted state."""

import datetime
import json
from functools import partial
from unittest.mock import MagicMock

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.services.gateway_client import GatewayClient
from agentcc.views import gateway


@pytest.mark.unit
@pytest.mark.parametrize("action", ["list", "retrieve", "health_check"])
@pytest.mark.parametrize("outcome", ["healthy", "connection_error", "timeout"])
def test_gateway_reports_probe_completion_time(monkeypatch, action, outcome):
    started = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    completed = started + datetime.timedelta(seconds=10)
    clock = {"now": started}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    probes = []

    def transport(request):
        assert request.method == "GET"
        assert request.url == "http://gateway.test/healthz"
        probes.append(request)
        clock["now"] = completed
        if outcome == "connection_error":
            raise httpx.ConnectError("Gateway offline", request=request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("Gateway timed out", request=request)
        return httpx.Response(200, json={"status": "ok"})

    # Keep the real GatewayClient and its HTTP error handling; replace transport only.
    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(transport))
    )
    monkeypatch.setattr(
        gateway, "get_gateway_client", lambda: GatewayClient("http://gateway.test")
    )

    def provider_rows(**kwargs):
        # Provider enrichment and config sync must not change the probe's timestamp.
        clock["now"] = completed + datetime.timedelta(seconds=20)
        providers = MagicMock()
        providers.count.return_value = 0
        providers.__iter__.return_value = iter([])
        return providers

    monkeypatch.setattr(
        gateway.AgentccProviderCredential.no_workspace_objects, "filter", provider_rows
    )
    monkeypatch.setattr(gateway.cache, "get", lambda key: True)

    factory = APIRequestFactory()
    method = "post" if action == "health_check" else "get"
    request = getattr(factory, method)("/agentcc/gateways/default/", {}, format="json")
    request.organization = Organization(name="Probe timestamp test")
    force_authenticate(request, user=User(email="health-check@example.test"))
    view = gateway.AgentccGatewayViewSet.as_view({method: action})
    response = view(request, **({"pk": "default"} if action != "list" else {}))
    response.render()
    payload = json.loads(response.content)

    assert len(probes) == 1
    unreachable = outcome != "healthy"
    assert response.status_code == (
        400 if unreachable and action == "health_check" else 200
    )
    result = payload["result"][0] if action == "list" else payload["result"]
    assert result["status"] == ("unreachable" if unreachable else "healthy")
    assert datetime.datetime.fromisoformat(result["last_health_check"]) == completed

    schema = getattr(gateway.AgentccGatewayViewSet, action)._swagger_auto_schema
    schema = schema.get(method, schema)
    serializer = schema["responses"][response.status_code](data=payload)
    assert serializer.is_valid(), serializer.errors
    validated_result = serializer.validated_data["result"]
    if action == "list":
        validated_result = validated_result[0]
    assert "last_health_check" in validated_result


@pytest.mark.unit
@pytest.mark.parametrize("authenticated", [True, False])
def test_rejected_health_check_does_not_claim_a_completed_probe(
    monkeypatch, authenticated
):
    client = MagicMock()
    monkeypatch.setattr(gateway, "get_gateway_client", client)
    request = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {"unexpected": True}, format="json"
    )
    if authenticated:
        force_authenticate(request, user=User(email="health-check@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        request, pk="default"
    )
    response.render()

    assert response.status_code == (400 if authenticated else 401)
    assert "last_health_check" not in response.content.decode()
    client.assert_not_called()
    if authenticated:
        schema = getattr(
            gateway.AgentccGatewayViewSet.health_check, "_swagger_auto_schema"
        )["post"]
        serializer = schema["responses"][400](data=json.loads(response.content))
        assert serializer.is_valid(), serializer.errors
