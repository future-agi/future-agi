"""MCP admin reads against a gateway whose MCP answer is not org-scoped.

A gateway without per-org MCP answers ``/-/mcp/status`` with no
``org_enabled`` field: it keeps one registry for every org, built from its own
config file, never from an org's servers. Django then attributes nothing in
it to the org:

- ``mcp-status`` reads as disabled with ``reason`` ``gateway_not_org_scoped``
  and lists no server, with or without org servers;
- ``mcp-tools``, ``mcp-resources`` and ``mcp-prompts`` are empty, and no list
  is read;
- ``test-mcp-tool`` answers 400 as for an org with no MCP server, and runs
  nothing.

An org-scoped status (with ``org_enabled``) attributes the org's own servers.
The gateway is faked at its HTTP client (``FakeGateway``).
"""

import pytest

from agentcc.services import mcp_admin
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.test_mcp_admin_api import (
    GATEWAY,
    STATUS_BODY,
    TOOLS_LIST,
    V1_STATUS,
    faked_gateway,
    seed_mcp,
)

NOT_ORG_SCOPED = {
    "enabled": False,
    "reason": "gateway_not_org_scoped",
    "sessions": 0,
    "tools": 0,
    "resources": 0,
    "prompts": 0,
    "servers": [],
}

LIST_PATHS = ["mcp-tools", "mcp-resources", "mcp-prompts"]


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        yield gateway


@pytest.fixture
def org_alpha(user):
    # The shared registry (STATUS_BODY) also lists a server "alpha".
    seed_mcp(user.organization, ["alpha"])
    return user.organization


def shared_registry(gateway, status=STATUS_BODY):
    """Every /-/mcp/* route answers, from one registry that names "alpha"."""
    gateway.on("GET", "/-/mcp/status", body=status)
    gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)
    gateway.on(
        "GET", "/-/mcp/resources", body=[{"uri": "res://alpha/1", "server": "alpha"}]
    )
    gateway.on("GET", "/-/mcp/prompts", body=[{"name": "alpha_p", "server": "alpha"}])
    gateway.on("POST", "/-/mcp/test", body={"is_error": False})


def gateway_paths(gateway):
    return [(method, path) for method, path, _, _ in gateway.calls]


def run_tool(client, name="alpha_echo"):
    return client.post(
        f"{GATEWAY}/test-mcp-tool/", {"name": name, "arguments": {}}, format="json"
    )


@pytest.mark.integration
@pytest.mark.api
class TestMCPNotOrgScopedAPI:
    def test_status_attributes_no_server_to_the_org(
        self, auth_client, org_alpha, fake_gateway
    ):
        shared_registry(fake_gateway)

        response = auth_client.get(f"{GATEWAY}/mcp-status/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == NOT_ORG_SCOPED
        # Only the status call: no list is read for the counts.
        assert gateway_paths(fake_gateway) == [("GET", "/-/mcp/status")]

    def test_status_without_org_servers_gives_the_same_reason(
        self, auth_client, user, fake_gateway
    ):
        seed_mcp(user.organization, [])
        shared_registry(fake_gateway)

        response = auth_client.get(f"{GATEWAY}/mcp-status/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == NOT_ORG_SCOPED

    @pytest.mark.parametrize("path", LIST_PATHS)
    def test_lists_are_empty_without_a_list_read(
        self, auth_client, org_alpha, fake_gateway, path
    ):
        shared_registry(fake_gateway)

        response = auth_client.get(f"{GATEWAY}/{path}/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == []
        assert gateway_paths(fake_gateway) == [("GET", "/-/mcp/status")]

    def test_tool_test_runs_nothing(self, auth_client, org_alpha, fake_gateway):
        shared_registry(fake_gateway)

        response = run_tool(auth_client)

        assert response.status_code == 400, response.json()
        assert response.json()["message"] == mcp_admin.NO_MCP_SERVERS_MESSAGE
        assert gateway_paths(fake_gateway) == [("GET", "/-/mcp/status")]

    def test_org_scoped_status_still_attributes_the_org_servers(
        self, auth_client, org_alpha, fake_gateway
    ):
        shared_registry(fake_gateway, status=V1_STATUS)

        status = auth_client.get(f"{GATEWAY}/mcp-status/").json()["result"]
        tools = auth_client.get(f"{GATEWAY}/mcp-tools/").json()["result"]
        run = run_tool(auth_client)

        assert status["enabled"] is True
        assert "reason" not in status
        assert [s["server_id"] for s in status["servers"]] == ["alpha"]
        assert [t["name"] for t in tools] == ["alpha_echo"]
        assert run.status_code == 200, run.json()
        assert len(fake_gateway.calls_to("POST", "/-/mcp/test")) == 1


@pytest.mark.integration
class TestMCPNotOrgScopedService:
    def test_service_attributes_nothing(self, org_alpha, fake_gateway):
        shared_registry(fake_gateway)

        assert mcp_admin.org_status(org_alpha) == NOT_ORG_SCOPED
        assert mcp_admin.org_tools(org_alpha) == []
        assert mcp_admin.org_resources(org_alpha) == []
        assert mcp_admin.org_prompts(org_alpha) == []
        with pytest.raises(mcp_admin.NoMCPServers):
            mcp_admin.run_tool_test(org_alpha, "alpha_echo", {})
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == []
        for path in ("/-/mcp/tools", "/-/mcp/resources", "/-/mcp/prompts"):
            assert fake_gateway.calls_to("GET", path) == []

    def test_status_read_failures_keep_their_answers(self, org_alpha, fake_gateway):
        # MCP off (no route, 404) and an outage keep their own answers.
        with pytest.raises(mcp_admin.MCPDisabled):
            mcp_admin.org_tools(org_alpha)
        with pytest.raises(mcp_admin.MCPDisabled):
            mcp_admin.run_tool_test(org_alpha, "alpha_echo", {})
        fake_gateway.on("GET", "/-/mcp/status", status=503, body={"error": "down"})
        with pytest.raises(GatewayClientError):
            mcp_admin.org_prompts(org_alpha)
