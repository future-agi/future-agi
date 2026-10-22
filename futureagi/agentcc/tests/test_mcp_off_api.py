"""MCP admin reads when the deployment runs with MCP off (``mcp.enabled: false``).

With MCP off the gateway registers no ``/-/mcp/*`` route, so every admin read
answers 404 (``registerMCPRoutes`` returns early). Django tells that apart from
a gateway that is down or broken:

- ``mcp-status`` answers 200 with ``reason: gateway_mcp_disabled`` for every
  org, including one with no MCP servers, and ``gateway_unreachable`` for a
  5xx, a timeout, a transport error or a malformed body;
- ``mcp-tools``, ``mcp-resources`` and ``mcp-prompts`` answer 404 with code
  ``gateway_mcp_disabled``, and 502 with code ``gateway_unreachable`` when the
  gateway fails, never an empty list that reads as "no tools";
- ``test-mcp-tool`` answers 404 with code ``gateway_mcp_disabled``.

The gateway is faked at its HTTP client (``FakeGateway``): an unrouted path
answers 404, as the gateway with MCP off does.
"""

import httpx
import pytest

from agentcc.tests.test_mcp_admin_api import (
    GATEWAY,
    STATUS_BODY,
    TOOLS_LIST,
    faked_gateway,
    seed_mcp,
)

MCP_OFF_MESSAGE = "MCP is turned off for this deployment."

LIST_ROUTES = {
    "mcp-tools": "/-/mcp/tools",
    "mcp-resources": "/-/mcp/resources",
    "mcp-prompts": "/-/mcp/prompts",
}

# Every way the gateway can fail that is not "MCP is off".
GATEWAY_FAILURES = [
    {"status": 500, "body": {"error": "boom"}},
    {"status": 502, "body": {"error": "bad gateway"}},
    {"status": 503, "body": {"error": "down"}},
    {"error": httpx.ReadTimeout("timed out")},
    {"error": httpx.ConnectError("connection refused")},
]
GATEWAY_FAILURE_IDS = ["500", "502", "503", "timeout", "connect"]


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        yield gateway


@pytest.fixture
def org_with_servers(user):
    seed_mcp(user.organization, ["alpha"])
    return user.organization


@pytest.fixture
def org_without_servers(user):
    seed_mcp(user.organization, [])
    return user.organization


def _status(client):
    response = client.get(f"{GATEWAY}/mcp-status/")
    assert response.status_code == 200, response.json()
    return response.json()["result"]


DISABLED = {
    "enabled": False,
    "reason": "gateway_mcp_disabled",
    "sessions": 0,
    "tools": 0,
    "resources": 0,
    "prompts": 0,
    "servers": [],
}


@pytest.mark.integration
@pytest.mark.api
class TestMCPOffStatus:
    def test_org_without_servers_sees_mcp_off(
        self, auth_client, org_without_servers, fake_gateway
    ):
        # No /-/mcp/* route: the gateway runs with MCP off.
        assert _status(auth_client) == DISABLED
        assert fake_gateway.calls_to("GET", "/-/mcp/status") == [
            ({"org_id": str(org_without_servers.id)}, None)
        ]

    def test_org_with_servers_sees_mcp_off(
        self, auth_client, org_with_servers, fake_gateway
    ):
        assert _status(auth_client) == DISABLED

    @pytest.mark.parametrize("route", GATEWAY_FAILURES, ids=GATEWAY_FAILURE_IDS)
    def test_gateway_failure_without_servers_is_unreachable_not_off(
        self, auth_client, org_without_servers, fake_gateway, route
    ):
        fake_gateway.on("GET", "/-/mcp/status", **route)

        result = _status(auth_client)

        assert result["enabled"] is False
        assert result["reason"] == "gateway_unreachable"
        assert result["servers"] == []

    def test_mcp_on_without_servers_lists_no_other_org_server(
        self, auth_client, org_without_servers, fake_gateway
    ):
        # MCP on: Django reports only the org's own servers and counts.
        fake_gateway.on("GET", "/-/mcp/status", body=STATUS_BODY)

        result = _status(auth_client)

        assert result == {
            "enabled": False,
            "sessions": 0,
            "tools": 0,
            "resources": 0,
            "prompts": 0,
            "servers": [],
        }
        # Only the status call: no list is read for an org with no server.
        assert [(m, p) for m, p, _, _ in fake_gateway.calls] == [
            ("GET", "/-/mcp/status")
        ]


@pytest.mark.integration
@pytest.mark.api
class TestMCPOffLists:
    @pytest.mark.parametrize("path", list(LIST_ROUTES))
    def test_list_answers_mcp_disabled_code(
        self, auth_client, org_with_servers, fake_gateway, path
    ):
        response = auth_client.get(f"{GATEWAY}/{path}/")

        assert response.status_code == 404, response.json()
        body = response.json()
        assert body["code"] == "gateway_mcp_disabled"
        assert body["message"] == MCP_OFF_MESSAGE

    @pytest.mark.parametrize("route", GATEWAY_FAILURES, ids=GATEWAY_FAILURE_IDS)
    @pytest.mark.parametrize("path", list(LIST_ROUTES))
    def test_list_gateway_failure_stays_an_error(
        self, auth_client, org_with_servers, fake_gateway, path, route
    ):
        fake_gateway.on("GET", LIST_ROUTES[path], **route)

        response = auth_client.get(f"{GATEWAY}/{path}/")

        assert response.status_code == 502, response.json()
        assert response.json()["code"] == "gateway_unreachable"

    @pytest.mark.parametrize("path", list(LIST_ROUTES))
    def test_list_malformed_body_is_an_error(
        self, auth_client, org_with_servers, fake_gateway, path
    ):
        fake_gateway.on("GET", LIST_ROUTES[path], body={"not": "a list"})

        response = auth_client.get(f"{GATEWAY}/{path}/")

        assert response.status_code == 502, response.json()
        assert response.json()["code"] == "gateway_unreachable"

    @pytest.mark.parametrize("path", list(LIST_ROUTES))
    def test_org_without_servers_lists_nothing_and_calls_nothing(
        self, auth_client, org_without_servers, fake_gateway, path
    ):
        response = auth_client.get(f"{GATEWAY}/{path}/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == []
        assert fake_gateway.calls == []


@pytest.mark.integration
@pytest.mark.api
class TestMCPOffToolTest:
    def test_tool_test_answers_mcp_disabled_code(
        self, auth_client, org_with_servers, fake_gateway
    ):
        response = auth_client.post(
            f"{GATEWAY}/test-mcp-tool/", {"name": "alpha_echo"}, format="json"
        )

        assert response.status_code == 404, response.json()
        assert response.json()["code"] == "gateway_mcp_disabled"
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == []

    def test_mcp_turned_off_between_lookup_and_run(
        self, auth_client, org_with_servers, fake_gateway
    ):
        # The tool list still answers; the run route is gone (404).
        fake_gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)

        response = auth_client.post(
            f"{GATEWAY}/test-mcp-tool/", {"name": "alpha_echo"}, format="json"
        )

        assert response.status_code == 404, response.json()
        assert response.json()["code"] == "gateway_mcp_disabled"

    def test_tool_test_gateway_failure_stays_an_error(
        self, auth_client, org_with_servers, fake_gateway
    ):
        fake_gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)
        fake_gateway.on("POST", "/-/mcp/test", status=500, body={"error": "boom"})

        response = auth_client.post(
            f"{GATEWAY}/test-mcp-tool/", {"name": "alpha_echo"}, format="json"
        )

        assert response.status_code == 400, response.json()
        assert response.json()["code"] != "gateway_mcp_disabled"
