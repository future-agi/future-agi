"""The config action says whether it read the gateway's config.

When its GET /-/config read of the gateway fails (or the gateway is
unreachable, or answers something that is not an object), the action still
answers 200 with the org's own config and leaves the gateway's blocks out.
gateway.config_read says which case the console is looking at: "ok" when the
blocks were read, "failed" when they were not, so "enforcement unknown" can
say the settings report could not be read instead of claiming the gateway
does not report it. The value is fixed text, never an error message.
"""

import copy
from unittest.mock import patch

import pytest

from agentcc.serializers.contracts import GatewayConfigResponseSerializer
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.r7_console_a_support import (
    GATEWAY,
    K8_GATEWAY_CONFIG,
    FakeGateway,
)


@pytest.fixture
def gateway():
    fake = FakeGateway(config=copy.deepcopy(K8_GATEWAY_CONFIG))
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


def _read(auth_client):
    response = auth_client.get(f"{GATEWAY}/config/")
    assert response.status_code == 200, response.content
    body = response.json()
    contract = GatewayConfigResponseSerializer(data=body)
    assert contract.is_valid(), contract.errors
    return body["result"]


@pytest.mark.integration
def test_config_read_ok_when_the_gateway_config_was_read(auth_client, gateway):
    gateway.config["org_sections_enforced"] = ["tool_policy"]

    result = _read(auth_client)

    assert result["gateway"] == {"status": "healthy", "config_read": "ok"}
    assert result["org_sections_enforced"] == ["tool_policy"]


@pytest.mark.integration
def test_config_read_failed_when_the_gateway_config_read_fails(
    auth_client, gateway
):
    gateway.config["org_sections_enforced"] = ["tool_policy"]
    gateway.config_error = GatewayClientError("read failed: secret-ish detail")

    result = _read(auth_client)

    assert result["gateway"] == {"status": "healthy", "config_read": "failed"}
    assert "org_sections_enforced" not in result
    assert "secret-ish" not in str(result)


@pytest.mark.integration
@pytest.mark.parametrize("answer", [None, [], "config"])
def test_config_read_failed_when_the_gateway_answers_no_object(
    auth_client, gateway, answer
):
    gateway.config = answer

    result = _read(auth_client)

    assert result["gateway"]["config_read"] == "failed"


@pytest.mark.integration
def test_config_read_failed_when_the_gateway_is_unreachable(auth_client, gateway):
    gateway.health_error = GatewayClientError("connection refused")

    result = _read(auth_client)

    assert result["gateway"] == {"status": "unreachable", "config_read": "failed"}
    assert "org_sections_enforced" not in result
