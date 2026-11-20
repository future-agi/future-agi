"""test-playground reports whether the gateway blocked or let a guardrail hit through."""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

TRIGGERED = {"x-agentcc-guardrail-triggered": "true"}


def _run_playground(auth_client, status_code, headers):
    client = MagicMock()
    client.send_chat_completion.return_value = (status_code, {}, headers)
    with (
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
        patch(
            "agentcc.views.gateway.AgentccGatewayViewSet._ensure_org_config_pushed",
            return_value=True,
        ),
        patch(
            "agentcc.views.gateway.AgentccGatewayViewSet._create_playground_api_key",
            return_value=("playground-key", "sk-test-playground"),
        ),
    ):
        response = auth_client.post(
            "/agentcc/gateways/default/test-playground/",
            {"prompt": "hello", "model": "gpt-4o-mini"},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.json()
    result = response.json()["result"]
    return result["blocked"], result["warned"]


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundOutcome:
    @pytest.mark.parametrize(
        ("status_code", "headers", "expected"),
        [
            # A warn or log hit: the gateway answers 200 and marks it.
            (200, TRIGGERED, (False, True)),
            (200, {"X-Agentcc-Guardrail-Triggered": "true"}, (False, True)),
            # Nothing triggered.
            (200, {}, (False, False)),
            (200, {"x-agentcc-guardrail-triggered": "false"}, (False, False)),
            # A block is a block, whatever the header says.
            (403, TRIGGERED, (True, False)),
            (446, TRIGGERED, (True, False)),
            # Legacy warn status.
            (246, {}, (False, True)),
            # A provider error is not a guardrail warning.
            (502, TRIGGERED, (False, False)),
        ],
    )
    def test_reports_blocked_and_warned(
        self, auth_client, status_code, headers, expected
    ):
        assert _run_playground(auth_client, status_code, headers) == expected
