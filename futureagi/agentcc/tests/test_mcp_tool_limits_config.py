"""Non-dollar MCP limits use the existing admin-only, org-scoped config API.
No second config store or activation endpoint: absent fields remain off.
"""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from agentcc.models.org_config import AgentccOrgConfig

URL = "/agentcc/gateways/default/update-config/"


def test_tool_budget_refusal_uses_payment_contract():
    path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts/gateway/examples/error-402-v1.schema.json"
    )
    validator = Draft202012Validator(json.loads(path.read_text()))
    error = {
        "error": {
            "type": "rate_limit_error",
            "code": "tool_budget_exhausted",
            "message": "Call budget exhausted",
            "param": None,
            "metadata": {
                "limit_source": "agentcc_tool_budget",
                "remedy_hint": "Ask an administrator to increase the limit.",
                "scope": "run:synthetic",
                "resets_at": None,
                "kind": "calls",
                "remaining": 0,
            },
        }
    }
    validator.validate(error)
    for field in ("kind", "remaining", "remedy_hint"):
        invalid = copy.deepcopy(error)
        del invalid["error"]["metadata"][field]
        assert list(validator.iter_errors(invalid)), field


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_tool_limits_save_and_clear_through_config_api(push, auth_client, organization):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        mcp={"enabled": False, "virtual_servers": {"review": {"members": ["mock"]}}},
    )
    budgets = {
        "rpm_per_key": 7,
        "max_tool_calls_per_run": 3,
        "per_tool": {"mock/*": {"rpm": 2, "max_calls_per_run": 0}},
    }
    response = auth_client.post(
        URL,
        {"mcp": {"virtual_servers": {"review": {"budgets": budgets}}}},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )
    assert active.mcp["virtual_servers"]["review"]["budgets"] == budgets
    assert active.mcp["enabled"] is False
    push.assert_called_once()
    response = auth_client.post(
        URL,
        {
            "mcp": {
                "virtual_servers": {
                    "review": {
                        "budgets": {"rpm_per_key": None, "per_tool": {"mock/*": None}}
                    }
                }
            }
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )
    remaining = active.mcp["virtual_servers"]["review"]["budgets"]
    assert "rpm_per_key" not in remaining
    assert "mock/*" not in remaining.get("per_tool", {})
    assert remaining["max_tool_calls_per_run"] == 3


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "budgets",
    [
        {"rpm_per_key": -1},
        {"max_tool_calls_per_run": -1},
        {"per_tool": {"mock/*": {"rpm": -1}}},
        {"per_tool": {"mock/*": {"max_calls_per_run": -1}}},
        {"per_tool": {"mock/*": {"unknown": 1}}},
    ],
)
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_invalid_tool_limits_do_not_push(push, auth_client, budgets):
    response = auth_client.post(
        URL,
        {
            "mcp": {
                "virtual_servers": {"review": {"members": ["mock"], "budgets": budgets}}
            }
        },
        format="json",
    )
    assert response.status_code == 400, response.json()
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
def test_anonymous_cannot_configure_tool_limits(api_client):
    response = api_client.post(
        URL,
        {"mcp": {"virtual_servers": {"review": {"budgets": {"rpm_per_key": 1}}}}},
        format="json",
    )
    assert response.status_code in (401, 403)
