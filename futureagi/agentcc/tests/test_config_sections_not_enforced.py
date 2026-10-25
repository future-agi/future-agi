"""The gateway's org_sections_not_enforced reaches the console unchanged.

GET /-/config names, with the reason, each org config section the node does
not enforce for a reason the console states: ip_acl, on a gateway that org
configs reach with its own ip_acl switch off and no trusted proxy set, is
"client IP unknown (trusted_proxies not set)". The config action passes that
object through as the gateway reports it, and leaves it out when the gateway
does not report it or reports something that is not a section -> reason
object.
"""

import copy
from unittest.mock import patch

import pytest

from agentcc.tests.console_support import (
    GATEWAY,
    NODE_GATEWAY_CONFIG,
    FakeGateway,
)

REASON = "client IP unknown (trusted_proxies not set)"


@pytest.fixture
def gateway():
    fake = FakeGateway(config=copy.deepcopy(NODE_GATEWAY_CONFIG))
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.integration
def test_config_passes_the_not_enforced_reasons_through(auth_client, gateway):
    gateway.config["org_sections_enforced"] = ["alerting", "budgets", "tool_policy"]
    gateway.config["org_sections_not_enforced"] = {"ip_acl": REASON}

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["org_sections_enforced"] == ["alerting", "budgets", "tool_policy"]
    assert result["org_sections_not_enforced"] == {"ip_acl": REASON}


@pytest.mark.integration
def test_config_leaves_out_reasons_the_gateway_does_not_report(auth_client, gateway):
    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert "org_sections_not_enforced" not in result


@pytest.mark.integration
@pytest.mark.parametrize(
    "reported",
    [["ip_acl"], "ip_acl", {"ip_acl": 1}, {"ip_acl": None}, {3: REASON}],
    ids=["list", "string", "number-reason", "null-reason", "number-key"],
)
def test_config_ignores_reasons_that_are_not_the_contract(
    auth_client, gateway, reported
):
    gateway.config["org_sections_not_enforced"] = reported

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert "org_sections_not_enforced" not in result
