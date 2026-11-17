"""Coverage for the gateway MCP sub-actions and the analytics guardrail endpoints.

The MCP reads and the playground reach the gateway through ``FakeGateway``
(``test_mcp_admin_api``), answered in the shapes the gateway sends.
"""

from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests.test_mcp_admin_api import V1_STATUS, faked_gateway


@pytest.fixture
def gateway_id():
    return "default"


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        yield gateway


def _seed_mcp_config(user, servers=None):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        mcp={"servers": servers or {"server-a": {"url": "http://mcp"}}},
    )


def _seed_members(user):
    """acme-docs, added by URL, is a member of "team" (bare and with an
    account) and the only member of "solo"."""
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=user.organization,
        version=1,
        is_active=True,
        mcp={
            "enabled": True,
            "servers": {
                "wiki": {"url": "http://mcp/wiki"},
                "acme-docs": {"url": "http://mcp/acme-docs"},
            },
            "virtual_servers": {
                "team": {
                    "id": "vs_01JBCDEFGHJKMNPQRSTVWXYZ02",
                    "members": ["wiki", "acme-docs", "acme-docs@work"],
                },
                "solo": {
                    "id": "vs_01JBCDEFGHJKMNPQRSTVWXYZ03",
                    "members": ["acme-docs"],
                },
            },
        },
    )


def _active_mcp(user):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=user.organization, is_active=True, deleted=False
    ).mcp


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPStatus:
    def test_mcp_status_returns_disabled_when_no_servers(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            mcp={},
        )

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-status/")
        assert response.status_code == 200
        assert response.json()["result"]["enabled"] is False

    def test_mcp_status_filters_to_configured_servers(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        fake_gateway.on(
            "GET",
            "/-/mcp/status",
            body={
                "enabled": True,
                "org_enabled": True,
                "sessions": 1,
                "tools": 5,
                "resources": 2,
                "prompts": 1,
                "servers": [
                    {"server_id": "server-a", "healthy": True, "tool_count": 2},
                    # Not in the org's config.
                    {"server_id": "server-b", "healthy": True, "tool_count": 3},
                ],
            },
        )

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-status/")
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["enabled"] is True
        assert [s["server_id"] for s in result["servers"]] == ["server-a"]
        assert result["tools"] == 2


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPTools:
    def test_mcp_tools_empty_when_no_configured_servers(
        self, auth_client, gateway_id, user
    ):
        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-tools/")
        assert response.status_code == 200
        assert response.json()["result"] == []

    def test_mcp_tools_filters_to_configured_servers(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        fake_gateway.on(
            "GET",
            "/-/mcp/tools",
            body=[
                {"name": "server-a_tool-1", "server_id": "server-a"},
                {"name": "server-b_tool-2", "server_id": "server-b"},
            ],
        )

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-tools/")
        assert response.status_code == 200
        tools = response.json()["result"]
        assert [(t["name"], t["server_id"]) for t in tools] == [
            ("server-a_tool-1", "server-a")
        ]


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
                "config": {"transport": "http", "url": "http://mcp/server-a"},
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert "server-a" in (active.mcp or {}).get("servers", {})

    @pytest.mark.parametrize(
        "ttl",
        [{"tools_cache_ttl": "10m"}, {"tools_cache_ttl_seconds": 600}],
        ids=["older-duration", "seconds"],
    )
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_mcp_server_keeps_every_key_a_save_sends(
        self, mock_push, auth_client, gateway_id, user, ttl
    ):
        """update-mcp-server stores the entry the console sends (with the
        stored transport and credential kept), so a console save that sends
        back every stored key changes none of them, the tools cache TTL in
        either form included: the gateway reads tools_cache_ttl_seconds, else
        the older tools_cache_ttl duration."""

        def entry():
            return {
                "transport": "http",
                "url": "http://mcp/server-a",
                "era": "auto",
                "timeout_ms": 12000,
                "forward_headers": ["x-tenant"],
                "propagate_depth": True,
                "trusted_annotations": False,
                "enabled": True,
                "labels": ["private_data"],
                "toolkits": ["github"],
                **ttl,
            }

        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            mcp={"servers": {"server-a": entry()}},
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-mcp-server/",
            {"server_id": "server-a", "config": entry()},
            format="json",
        )

        assert response.status_code == 200, response.json()
        stored = _active_mcp(user)["servers"]["server-a"]
        assert {key: stored.get(key) for key in entry()} == entry()

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
    def test_remove_mcp_server_drops_it_from_every_virtual_servers_members(
        self, mock_push, auth_client, gateway_id, user, settings
    ):
        """A server added later under the same id, here by URL, joins no
        virtual server the removed one was a member of."""
        settings.AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED = True
        _seed_members(user)

        removed = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-mcp-server/",
            {"server_id": "acme-docs"},
            format="json",
        )

        assert removed.status_code == 200, removed.json()
        virtual_servers = _active_mcp(user)["virtual_servers"]
        assert virtual_servers["team"]["members"] == ["wiki"]
        # Members only: a virtual server left with none stays.
        assert virtual_servers["solo"]["members"] == []

        readded = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-mcp-server/",
            {
                "server_id": "acme-docs",
                "config": {"transport": "http", "url": "http://mcp/acme-docs"},
            },
            format="json",
        )

        assert readded.status_code == 200, readded.json()
        mcp = _active_mcp(user)
        assert "acme-docs" in mcp["servers"]
        assert mcp["virtual_servers"]["team"]["members"] == ["wiki"]
        assert mcp["virtual_servers"]["solo"]["members"] == []

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_remove_mcp_server_keeps_members_while_virtual_servers_are_off(
        self, mock_push, auth_client, gateway_id, user, settings
    ):
        """No virtual server is changed or served while they are off, so the
        members stay as stored."""
        settings.AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED = False
        _seed_members(user)

        removed = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-mcp-server/",
            {"server_id": "acme-docs"},
            format="json",
        )

        assert removed.status_code == 200, removed.json()
        mcp = _active_mcp(user)
        assert "acme-docs" not in mcp["servers"]
        assert mcp["virtual_servers"]["team"]["members"] == [
            "wiki",
            "acme-docs",
            "acme-docs@work",
        ]

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_mcp_guardrails_writes_config(
        self, mock_push, auth_client, gateway_id, user
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-mcp-guardrails/",
            {"config": {"enabled": True, "blocked_tools": ["shell"]}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert (active.mcp or {}).get("guardrails") == {
            "enabled": True,
            "blocked_tools": ["shell"],
        }


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPTest:
    def test_test_mcp_tool_happy(self, fake_gateway, auth_client, gateway_id, user):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        fake_gateway.on(
            "GET",
            "/-/mcp/tools",
            body=[{"name": "server-a_tool-1", "server_id": "server-a"}],
        )
        result = {
            "content": [{"type": "text", "text": "hi"}],
            "is_error": False,
            "duration_ms": 1.5,
            "guardrail_pre": "skipped",
            "guardrail_post": "skipped",
            "server": "server-a",
        }
        fake_gateway.on("POST", "/-/mcp/test", body=result)

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
            {"name": "server-a_tool-1", "arguments": {"x": 1}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        assert response.json()["result"] == result
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == [
            (
                None,
                {
                    "org_id": str(user.organization.id),
                    "name": "server-a_tool-1",
                    "arguments": {"x": 1},
                },
            )
        ]

    def test_test_mcp_tool_rejects_missing_tool_name(self, auth_client, gateway_id):
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
            {},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestGatewayMCPReadonly:
    def test_mcp_resources_returns_list(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        fake_gateway.on(
            "GET",
            "/-/mcp/resources",
            body=[
                {"uri": "res://a", "name": "a", "server_id": "server-a"},
                {"uri": "res://b", "name": "b", "server_id": "server-b"},
            ],
        )

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-resources/")
        assert response.status_code == 200
        assert [r["uri"] for r in response.json()["result"]] == ["res://a"]

    def test_mcp_prompts_returns_list(
        self, fake_gateway, auth_client, gateway_id, user
    ):
        _seed_mcp_config(user, servers={"server-a": {"url": "http://mcp"}})
        fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        fake_gateway.on(
            "GET",
            "/-/mcp/prompts",
            body=[
                {"name": "server-a_prompt-1", "server_id": "server-a"},
                {"name": "server-b_prompt-2", "server_id": "server-b"},
            ],
        )

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/mcp-prompts/")
        assert response.status_code == 200
        assert [p["name"] for p in response.json()["result"]] == ["server-a_prompt-1"]


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
