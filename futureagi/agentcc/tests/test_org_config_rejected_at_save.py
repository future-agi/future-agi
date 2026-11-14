"""A config version the gateway contract rejects must not become active."""

import json
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected, validate_org_config

# A key the dashboard used to write. The per-org gateway contract has no such field.
REJECTED_ROUTING = {"default_strategy": "round-robin"}


@pytest.fixture
def gateway_id():
    return "default"


def _make_config(organization, version=1, is_active=True, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=version, is_active=is_active, **fields
    )


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


class TestValidateOrgConfig:
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_names_every_rejected_field(self, _providers):
        config = AgentccOrgConfig(
            organization_id=uuid.uuid4(),
            routing=REJECTED_ROUTING,
            mcp={"guardrails": {"custom_patterns": []}},
        )

        with pytest.raises(OrgConfigRejected) as rejected:
            validate_org_config(config)

        assert "routing.default_strategy" in str(rejected.value)
        assert "mcp.guardrails.custom_patterns" in str(rejected.value)

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_accepts_a_config_the_contract_accepts(self, _providers):
        config = AgentccOrgConfig(
            organization_id=uuid.uuid4(), routing={"strategy": "round_robin"}
        )

        validate_org_config(config)


@pytest.mark.integration
@pytest.mark.api
class TestRejectedConfigDoesNotBecomeActive:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_keeps_the_active_version(
        self, mock_push, auth_client, gateway_id, organization
    ):
        _make_config(organization, routing={"strategy": "round_robin"})

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-config/",
            {"routing": REJECTED_ROUTING},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "routing.default_strategy" in json.dumps(response.json())
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_create_keeps_the_active_version(
        self, mock_push, auth_client, organization
    ):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/org-configs/", {"routing": REJECTED_ROUTING}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "routing.default_strategy" in json.dumps(response.json())
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_activate_refuses_an_older_rejected_version(
        self, mock_push, auth_client, organization
    ):
        stale = _make_config(
            organization, version=1, is_active=False, routing=REJECTED_ROUTING
        )
        _make_config(organization, version=2, is_active=True)

        response = auth_client.post(f"/agentcc/org-configs/{stale.id}/activate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "routing.default_strategy" in json.dumps(response.json())
        assert _versions(organization) == [(1, False), (2, True)]
        mock_push.assert_not_called()

    @patch("agentcc.services.config_push.push_org_config", return_value=True)
    def test_routing_policy_sync_keeps_the_active_version(
        self, mock_push, auth_client, organization
    ):
        # The sync writes routing as {"policies": ...}, which the contract has
        # no field for, so it must not replace the active version.
        _make_config(organization, routing={"strategy": "round_robin"})

        response = auth_client.post(
            "/agentcc/routing-policies/",
            {"name": "fastest", "config": {"strategy": "fastest"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is False
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()
