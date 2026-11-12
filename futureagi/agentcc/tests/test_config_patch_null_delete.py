"""Null merge-patch values reach update-config without relaxing validation.

The existing action transports merge patches over POST (not HTTP PATCH).
"""

from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.contracts import GatewayConfigPatchRequestSerializer

SECTIONS = (
    "guardrails", "routing", "cache", "rate_limiting", "budgets",
    "cost_tracking", "ip_acl", "alerting", "privacy", "tool_policy",
    "mcp", "a2a", "audit", "model_database", "model_map",
)
URL = "/agentcc/gateways/default/update-config/"


@pytest.mark.parametrize("section", SECTIONS)
def test_section_children_accept_null_but_whole_sections_do_not(section):
    field = GatewayConfigPatchRequestSerializer().fields[section]
    assert field.child.allow_null is True
    assert field.allow_null is False


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_update_config_removes_only_the_null_model_mapping(
    push, auth_client, organization
):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True,
        model_map={"fast": "gpt-4o-mini", "smart": "gpt-4o"},
    )
    response = auth_client.post(URL, {"model_map": {"smart": None}}, format="json")
    assert response.status_code == 200, response.json()
    active = _active(organization)
    assert active.model_map == {"fast": "gpt-4o-mini"}
    assert active.version == 2
    assert response.json()["result"]["policy_revision"] == 0
    push.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("invalid", [17, False, [], {"target": "gpt-4o"}])
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_non_null_invalid_model_mapping_is_still_rejected(
    push, auth_client, organization, invalid
):
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True,
        model_map={"smart": "gpt-4o"},
    )
    response = auth_client.post(
        URL, {"model_map": {"smart": invalid}}, format="json"
    )
    assert response.status_code == 400, response.json()
    active = _active(organization)
    assert active.pk == original.pk
    assert active.model_map == {"smart": "gpt-4o"}
    push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("invalid", [17, False, [], "not-an-object"])
@patch("agentcc.views.gateway.push_org_config", return_value=True)
def test_model_database_overrides_still_require_an_object(
    push, auth_client, organization, invalid
):
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True,
        model_database={"overrides": {}},
    )
    response = auth_client.post(
        URL, {"model_database": {"overrides": invalid}}, format="json"
    )
    assert response.status_code == 400, response.json()
    assert _active(organization).pk == original.pk
    assert _active(organization).model_database == {"overrides": {}}
    push.assert_not_called()
