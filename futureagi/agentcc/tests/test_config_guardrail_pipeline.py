"""The gateway's guardrail_pipeline reaches the console unchanged.

GET /-/config reports how the node runs an organization's guardrail checks:
{"enabled", "fail_open", "mode"}. The console's Guardrails tab shows them
read-only in place of an org's own Fail Open and Mode, which the gateway
stores and does not read. The config action passes the block through as
the gateway reports it, and leaves it out when the gateway does not report
it or reports something else.
"""

import copy
from unittest.mock import patch

import pytest

from agentcc.tests.console_support import (
    GATEWAY,
    NODE_GATEWAY_CONFIG,
    FakeGateway,
)

PIPELINE = {"enabled": True, "fail_open": False, "mode": "sequential"}


@pytest.fixture
def gateway():
    fake = FakeGateway(config=copy.deepcopy(NODE_GATEWAY_CONFIG))
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.integration
def test_config_passes_the_guardrail_pipeline_through(auth_client, gateway):
    gateway.config["guardrail_pipeline"] = dict(PIPELINE)

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["guardrail_pipeline"] == PIPELINE


@pytest.mark.integration
def test_config_leaves_out_a_pipeline_the_gateway_does_not_report(auth_client, gateway):
    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert "guardrail_pipeline" not in result


@pytest.mark.integration
@pytest.mark.parametrize(
    "reported",
    [
        "sequential",
        ["sequential"],
        {"enabled": True, "fail_open": "yes", "mode": "sequential"},
        {"enabled": True, "fail_open": True},
        {"enabled": True, "fail_open": True, "mode": 1},
    ],
    ids=["string", "list", "fail-open-string", "no-mode", "number-mode"],
)
def test_config_ignores_a_pipeline_that_is_not_the_contract(
    auth_client, gateway, reported
):
    gateway.config["guardrail_pipeline"] = reported

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert "guardrail_pipeline" not in result
