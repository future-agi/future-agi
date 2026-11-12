"""Console calls explain their availability before contacting the gateway."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.mark.parametrize("action", ["list", "retrieve"])
@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_read_says_whether_console_tests_run(monkeypatch, action, enabled):
    """The console reads this before offering Try it or Guardrail test."""
    from agentcc.serializers.contracts import (
        GatewayDetailResponseSerializer,
        GatewayListResponseSerializer,
    )
    from agentcc.views import gateway

    # A public URL the response contract's URLField accepts.
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(
        gateway, "AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com"
    )
    request = APIRequestFactory().get("/agentcc/gateways/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with (
        override_settings(AGENTCC_DELEGATED_PLAYGROUND_ENABLED=enabled),
        patch.object(gateway, "get_gateway_client") as get_client,
    ):
        # A gateway health field of the same name cannot stand in for it.
        get_client.return_value.health_check.return_value = {
            "status": "ok",
            "delegated_playground_available": not enabled,
        }
        response = gateway.AgentccGatewayViewSet.as_view({"get": action})(request)
    assert response.status_code == 200
    result = response.data["result"]
    data = result[0] if action == "list" else result
    assert data["delegated_playground_available"] is enabled
    serializer = (
        GatewayListResponseSerializer
        if action == "list"
        else GatewayDetailResponseSerializer
    )
    contract = serializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert (
        contract.validated_data["result"][0]
        if action == "list"
        else contract.validated_data["result"]
    )["delegated_playground_available"] is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_read_says_whether_batch_submission_runs(monkeypatch, enabled):
    """The console reads this before offering Submit Batch Job."""
    from agentcc.serializers.contracts import GatewayDetailResponseSerializer
    from agentcc.views import gateway

    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(
        gateway, "AGENTCC_GATEWAY_PUBLIC_URL", "https://gateway.example.com"
    )
    request = APIRequestFactory().get("/agentcc/gateways/default/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with (
        override_settings(
            AGENTCC_DELEGATED_BATCH_ENABLED=enabled,
            AGENTCC_DELEGATED_PLAYGROUND_ENABLED=not enabled,
        ),
        patch.object(gateway, "get_gateway_client") as get_client,
    ):
        # A gateway health field of the same name cannot stand in for it.
        get_client.return_value.health_check.return_value = {
            "status": "ok",
            "delegated_batch_available": not enabled,
        }
        response = gateway.AgentccGatewayViewSet.as_view({"get": "retrieve"})(request)
    assert response.status_code == 200
    data = response.data["result"]
    assert data["delegated_batch_available"] is enabled
    assert data["delegated_playground_available"] is (not enabled)
    contract = GatewayDetailResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors
    assert contract.validated_data["result"]["delegated_batch_available"] is enabled


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "route,payload,issue",
    [
        ("test-playground", {"prompt": "hello", "model": "test/model"}, "KI-733-PG"),
        (
            "test-playground-stream",
            {"prompt": "hello", "model": "test/model"},
            "KI-733-PG",
        ),
        (
            "submit-batch",
            {
                "requests": [
                    {
                        "custom_id": "one",
                        "body": {
                            "model": "test/model",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    }
                ]
            },
            "KI-733-BATCH",
        ),
    ],
)
def test_disabled_console_call_has_no_gateway_work(auth_client, route, payload, issue):
    with patch("agentcc.views.gateway.get_gateway_client") as gateway:
        response = auth_client.post(
            f"/agentcc/gateways/default/{route}/", payload, format="json"
        )
    assert response.status_code == 409
    assert response.json()["code"] == "feature_unavailable_733"
    assert response.json()["known_issue"] == issue
    assert "Unavailable in this release" in response.json()["message"]
    gateway.assert_not_called()
