from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigCostTrackingDefaults:
    def test_org_config_write_serializer_defaults_cost_tracking_enabled(self):
        serializer = AgentccOrgConfigWriteSerializer(data={})

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["cost_tracking"] == {"enabled": True}

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_config_normalizes_empty_cost_tracking(
        self, mock_get_client, auth_client, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            cost_tracking={},
        )

        mock_client = mock_get_client.return_value
        mock_client.health_check.return_value = {"status": "ok"}

        response = auth_client.get("/agentcc/gateways/default/config/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["cost_tracking"] == {"enabled": True}

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_gateway_reload_backfills_empty_cost_tracking(
        self, _mock_push, auth_client, user
    ):
        config = AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            cost_tracking={},
        )

        response = auth_client.post(
            "/agentcc/gateways/default/reload/", {}, format="json"
        )

        assert response.status_code == 200, response.json()
        config.refresh_from_db()
        assert config.cost_tracking == {"enabled": True}

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_config_without_org_row_defaults_cost_tracking_enabled(
        self, mock_get_client, auth_client
    ):
        mock_client = mock_get_client.return_value
        mock_client.health_check.return_value = {"status": "ok"}

        response = auth_client.get("/agentcc/gateways/default/config/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["cost_tracking"] == {"enabled": True}
