"""L1's Django half: persist and push cache settings while the node is off.

The node report is org_sections_enforced, not the org's saved cache.enabled.
Only gateway transport is mocked: update-config, the version writer, database
readback, contract normalization and config_push are real. Actual node restart,
cache hits and TTL expiry belong to the Go lifecycle test, not this mock.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved_ttl", [4, "4s"], ids=["seconds", "editor-duration"])
def test_cache_settings_store_and_push_while_node_cache_is_off(
    auth_client, organization, saved_ttl
):
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        cache={"enabled": False, "default_ttl": 60},
        routing={"strategy": "round_robin"},
    )
    client = MagicMock(spec=["health_check", "get_config", "set_org_config"])
    node_sections = ["alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"]
    client.get_config.return_value = {"org_sections_enforced": node_sections}

    with (
        patch.object(gateway_views, "get_gateway_client", return_value=client),
        patch.object(config_push, "get_gateway_client", return_value=client),
    ):
        # Establish the actual config-read path's explicit node-off precondition
        # before saving, rather than treating an org opt-out as a node flag.
        before = auth_client.get(f"{GATEWAY}/config/")
        assert before.status_code == status.HTTP_200_OK, before.json()
        assert before.json()["result"]["org_sections_enforced"] == node_sections
        assert "cache" not in node_sections
        client.get_config.assert_called_once_with()
        client.set_org_config.assert_not_called()

        response = auth_client.post(
            f"{GATEWAY}/update-config/",
            {"cache": {"enabled": True, "default_ttl": saved_ttl}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["version"] == 2
        assert result["gateway_synced"] is True
        assert result["warnings"] == []

        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert active.pk != original.pk
        assert active.version == 2
        assert active.cache == {"enabled": True, "default_ttl": saved_ttl}
        assert active.routing == original.routing
        original.refresh_from_db()
        assert original.is_active is False
        assert original.cache == {"enabled": False, "default_ttl": 60}

        client.set_org_config.assert_called_once()
        org_id, pushed = client.set_org_config.call_args.args
        assert org_id == str(organization.id)
        assert pushed["cache"] == {
            "enabled": True,
            "default_ttl": 4,
            "max_entries": 10000,
        }
        assert pushed["routing"] == original.routing
        assert pushed["meta"]["version"] == active.version
        assert pushed["meta"]["config_id"] == str(active.id)

        # The same read now reports org opt-in=true AND node cache still off.
        # Saving must not relabel the node, and a subsequent read must retain
        # the saved TTL (normalized seconds even for the editor's "4s").
        after = auth_client.get(f"{GATEWAY}/config/")
        assert after.status_code == status.HTTP_200_OK, after.json()
        current = after.json()["result"]
        assert current["org_sections_enforced"] == node_sections
        assert current["cache"]["enabled"] is True
        assert current["cache"]["default_ttl"] == 4
        assert client.get_config.call_count == 2
        client.set_org_config.assert_called_once()
