"""Durable per-policy activation, not a claim about imported history."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.serializers.org_config import AgentccOrgConfigSerializer
from agentcc.services.org_config_writer import write_config_version
from agentcc.tests.test_g8_cap_chokepoints import cap_sections


@pytest.mark.integration
@pytest.mark.django_db
def test_cap_activation_api_survives_limit_changes(organization):
    push = MagicMock(return_value=True)
    first, _ = write_config_version(organization, cap_sections(), push=push)
    cap = first.budgets["agents"]["agent-a"]
    assert cap.get("activated_at"), "enabled cap lacks durable activation"
    changed, _ = write_config_version(
        organization,
        {
            "budgets": {
                "policy_revision": first.budgets["policy_revision"],
                "agents": {"agent-a": {"limit": 0.02}},
            }
        },
        push=push,
    )
    assert changed.budgets["agents"]["agent-a"]["activated_at"] == cap["activated_at"]
    response = AgentccOrgConfigSerializer(changed).data
    policy = response["cap_accounting"]["policies"]["agent:agent-a"]
    assert policy["description"] == f"counts spend admitted since {cap['activated_at']}"
    assert policy["day_boundary"] == "UTC"
    assert policy["includes_byok"] is True


@pytest.mark.integration
@pytest.mark.django_db
def test_cap_activation_cannot_be_spoofed_or_reset(organization):
    push = MagicMock(return_value=True)
    first, _ = write_config_version(organization, cap_sections(), push=push)
    activated = first.budgets["agents"]["agent-a"].get("activated_at")
    assert activated
    cleared, _ = write_config_version(
        organization,
        {
            "budgets": {
                "agents": None,
                "policy_revision": first.budgets["policy_revision"],
            }
        },
        push=push,
    )
    sections = cap_sections()
    sections["budgets"]["policy_revision"] = cleared.budgets["policy_revision"]
    sections["budgets"]["agents"]["agent-a"]["activated_at"] = "2099-01-01T00:00:00Z"
    restored, _ = write_config_version(organization, sections, push=push)
    assert restored.budgets["agents"]["agent-a"]["activated_at"] == activated


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_cap_http_api_discloses_activation(auth_client):
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ):
        created = auth_client.post(
            "/agentcc/org-configs/", cap_sections(), format="json"
        )
    assert created.status_code == 200, created.content
    data = created.json()["result"]
    read = auth_client.get(f"/agentcc/org-configs/{data['id']}/")
    assert read.status_code == 200
    result = read.json()["result"]
    stamp = result["budgets"]["agents"]["agent-a"]["activated_at"]
    assert (
        result["cap_accounting"]["policies"]["agent:agent-a"]["description"]
        == f"counts spend admitted since {stamp}"
    )
