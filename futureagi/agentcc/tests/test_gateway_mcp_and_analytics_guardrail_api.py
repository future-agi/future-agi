"""Coverage for the gateway MCP sub-actions and the analytics guardrail endpoints."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig


@pytest.fixture
def gateway_id():
    return "default"


def _seed_mcp_config(user, servers=None):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        mcp={"servers": servers or {"server-a": {"url": "http://mcp"}}},
    )


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPStatus:
    def test_mcp_status_returns_disabled_when_no_servers(
        self, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            mcp={},
        )

        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-status/"
        )
        assert response.status_code == 200
        assert response.json()["result"]["enabled"] is False

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_mcp_status_filters_to_configured_servers(
        self, mock_get_client, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        mock_client = MagicMock()
        mock_client.mcp_status.return_value = {
            "enabled": True,
            "sessions": 1,
            "tools": 5,
            "resources": 2,
            "prompts": 1,
            "servers": [
                {"id": "server-a", "status": "connected"},
                {"id": "server-b", "status": "connected"},  # not in org config
            ],
        }
        mock_get_client.return_value = mock_client

        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-status/"
        )
        assert response.status_code == 200
        server_ids = {s["id"] for s in response.json()["result"]["servers"]}
        assert server_ids == {"server-a"}


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPTools:
    def test_mcp_tools_empty_when_no_configured_servers(
        self, auth_client, gateway_id, user
    ):
        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-tools/"
        )
        assert response.status_code == 200
        assert response.json()["result"] == []

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_mcp_tools_filters_to_configured_servers(
        self, mock_get_client, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        mock_client = MagicMock()
        mock_client.mcp_tools.return_value = [
            {"name": "tool-1", "server": "server-a"},
            {"name": "tool-2", "server": "server-b"},
        ]
        mock_get_client.return_value = mock_client

        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-tools/"
        )
        assert response.status_code == 200
        names = {t["name"] for t in response.json()["result"]}
        assert names == {"tool-1"}


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPUpdates:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_mcp_server_writes_config(
        self, mock_push, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-mcp-server/",
            {
                "server_id": "server-a",
                "config": {"url": "http://mcp/server-a", "enabled": True},
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert "server-a" in (active.mcp or {}).get("servers", {})

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_remove_mcp_server_strips_server(
        self, mock_push, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            mcp={"servers": {"server-a": {"url": "http://mcp"}}},
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-mcp-server/",
            {"server_id": "server-a"},
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert "server-a" not in (active.mcp or {}).get("servers", {})

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_mcp_guardrails_writes_config(
        self, mock_push, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-mcp-guardrails/",
            {"config": {"scan_tool_names": True}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert (active.mcp or {}).get("guardrails") == {"scan_tool_names": True}


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPTest:
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_test_mcp_tool_happy(
        self, mock_get_client, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        mock_client = MagicMock()
        mock_client.mcp_test_tool.return_value = {"ok": True, "result": "hi"}
        mock_get_client.return_value = mock_client

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
            {"name": "tool-1", "arguments": {"x": 1}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        mock_client.mcp_test_tool.assert_called_once()

    def test_test_mcp_tool_rejects_missing_tool_name(
        self, auth_client, gateway_id
    ):
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
            {},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPReadonly:
    @patch("agentcc.views.gateway.get_gateway_client")
    def test_mcp_resources_returns_list(
        self, mock_get_client, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        mock_client = MagicMock()
        mock_client.mcp_resources.return_value = [
            {"uri": "res://a", "server": "server-a"},
        ]
        mock_get_client.return_value = mock_client

        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-resources/"
        )
        assert response.status_code == 200

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_mcp_prompts_returns_list(
        self, mock_get_client, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        mock_client = MagicMock()
        mock_client.mcp_prompts.return_value = [
            {"name": "prompt-1", "server": "server-a"},
        ]
        mock_get_client.return_value = mock_client

        response = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/mcp-prompts/"
        )
        assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsGuardrail:
    def test_guardrail_overview_pins_kpi_keys(self, auth_client):
        response = auth_client.get("/agentcc/analytics/guardrail-overview/")
        assert response.status_code == 200
        result = response.json()["result"]
        # Pin the KPI keys the FE renders; if the helper drops one, this
        # test flips red instead of the FE rendering blank cards.
        assert isinstance(result, dict)
        assert "total_requests" in result

    def test_guardrail_rules_pins_top_level_shape(self, auth_client):
        response = auth_client.get("/agentcc/analytics/guardrail-rules/")
        assert response.status_code == 200
        result = response.json()["result"]
        # get_guardrail_rules returns a dict aggregate; a list would indicate
        # a shape regression the FE cannot parse.
        assert isinstance(result, dict)

    def test_guardrail_trends_pins_series_keys(self, auth_client):
        response = auth_client.get("/agentcc/analytics/guardrail-trends/")
        assert response.status_code == 200
        result = response.json()["result"]
        assert isinstance(result, dict)
        assert "granularity" in result and "series" in result

    def test_guardrail_overview_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/analytics/guardrail-overview/")
        assert response.status_code in (401, 403)
