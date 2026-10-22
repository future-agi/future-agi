"""MCP admin endpoints: admin role, org scope, truthful status and the gateway shapes.

The gateway is faked at its HTTP client (``FakeGateway``), so the real admin
transport, its error mapping and the MCP response adapter all run. A path the
fake has no route for answers 404, as the gateway does when MCP is off and its
``/-/mcp/*`` routes are not registered. Django keeps only the org's own
servers in every answer.
"""

import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import jsonschema
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.services import mcp_admin
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
EXAMPLES_DIR = (
    Path(__file__).resolve().parents[3] / "api_contracts" / "gateway" / "examples"
)

# A /-/mcp/status body listing servers by server_id (server.go).
STATUS_BODY = {
    "enabled": True,
    "sessions": 5,
    "tools": 7,
    "resources": 3,
    "prompts": 2,
    "servers": [
        {"server_id": "alpha", "healthy": True, "tool_count": 3},
        {"server_id": "beta", "healthy": True, "tool_count": 4},
    ],
}

# mcp-admin-status-v1: the org-scoped status shape.
V1_STATUS = {
    "enabled": True,
    "org_enabled": True,
    "servers": [
        {
            "server_id": "alpha",
            "era": "2025-06-18",
            "transport": "http",
            "healthy": True,
            "tool_count": 2,
            "last_error": "",
            "last_refreshed_at": "2026-10-16T10:00:00Z",
        },
        {
            "server_id": "beta",
            "era": "2025-03-26",
            "transport": "http",
            "healthy": False,
            "tool_count": 9,
            "last_error": "connection refused",
            "last_refreshed_at": "2026-10-16T09:58:00Z",
        },
    ],
    "sessions": 4,
    "tools": 11,
    "resources": 6,
    "prompts": 3,
}

# A /-/mcp/tools body: a bare list, attributed by ``server``.
TOOLS_LIST = [
    {"server": "alpha", "original_name": "echo", "name": "alpha_echo"},
    {"server": "beta", "original_name": "secrets", "name": "beta_secrets"},
    {"server": "a_b", "original_name": "x", "name": "a_b_x"},
]

# mcp-admin-tools-v1: the same tools, attributed by ``server_id``.
V1_TOOLS = {
    "tools": [
        {
            "name": t["name"],
            "server_id": t["server"],
            "upstream_name": t["original_name"],
        }
        for t in TOOLS_LIST
    ]
}


class FakeGateway:
    """The gateway admin API, answered from canned routes.

    ``on(method, path, body=..., status=...)`` sets a route and
    ``on(method, path, error=exc)`` makes the call raise ``exc``; an unrouted
    path answers 404. ``calls_to(method, path)`` returns the ``(params, json)``
    of every call made to it.
    """

    def __init__(self):
        self.routes = {}
        self.calls = []

    def on(self, method, path, body=None, status=200, error=None):
        self.routes[(method, path)] = error if error is not None else (status, body)

    def calls_to(self, method, path):
        return [
            (params, body)
            for m, p, params, body in self.calls
            if (m, p) == (method, path)
        ]

    def request(self, method, url, headers=None, params=None, json=None):
        path = urlsplit(url).path
        self.calls.append((method, path, params, json))
        route = self.routes.get((method, path))
        if route is None:
            return httpx.Response(404, text="404 page not found")
        if isinstance(route, Exception):
            raise route
        status, body = route
        return httpx.Response(status, json=body)


@contextmanager
def faked_gateway():
    """Route the gateway admin transport to a ``FakeGateway`` for the block."""
    gateway = FakeGateway()
    client = MagicMock()
    client.__enter__.return_value = gateway
    client.__exit__.return_value = False  # let transport errors propagate
    with patch("agentcc.services.gateway_client.httpx.Client", return_value=client):
        yield gateway


@pytest.fixture
def fake_gateway():
    with faked_gateway() as gateway:
        yield gateway


def seed_mcp(organization, servers):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        mcp={"servers": {sid: {"url": f"https://{sid}.example"} for sid in servers}},
    )


@pytest.fixture
def org_a(user):
    seed_mcp(user.organization, ["alpha", "a"])
    return user.organization


@pytest.fixture
def org_b(db):
    organization = Organization.objects.create(name="Other MCP Org")
    seed_mcp(organization, ["beta", "a_b"])
    return organization


def _client_for_level(organization, workspace, level):
    role = {
        Level.MEMBER: OrganizationRoles.MEMBER,
        Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
    }[level]
    user = User.objects.create_user(
        email=f"mcp-role-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="MCP Role User",
        organization=organization,
        organization_role=role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=organization, role=role, level=level, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


def _status(client):
    response = client.get(f"{GATEWAY}/mcp-status/")
    assert response.status_code == 200, response.json()
    return response.json()["result"]


def _active_mcp(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    ).mcp


MUTATIONS = {
    "update-mcp-server": {
        "server_id": "gamma",
        "config": {"url": "https://gamma.example", "transport": "http"},
    },
    "remove-mcp-server": {"server_id": "alpha"},
    "update-mcp-guardrails": {"config": {"enabled": False}},
    "test-mcp-tool": {"name": "alpha_echo", "arguments": {}},
}


@pytest.mark.integration
@pytest.mark.api
class TestMCPAdminRole:
    @pytest.mark.parametrize("action", sorted(MUTATIONS))
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_member_gets_403_on_mcp_mutations_and_test_tool(
        self, org_a, workspace, fake_gateway, action, level
    ):
        fake_gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)
        fake_gateway.on("POST", "/-/mcp/test", body={"is_error": False})
        client = _client_for_level(org_a, workspace, level)
        before = _active_mcp(org_a)

        with patch("agentcc.views.gateway.push_org_config") as push:
            response = client.post(
                f"{GATEWAY}/{action}/", MUTATIONS[action], format="json"
            )

        assert response.status_code == 403, response.json()
        assert response.json()["message"] == IsOrgGatewayAdmin.message
        assert _active_mcp(org_a) == before
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(organization=org_a).count()
            == 1
        )
        push.assert_not_called()
        assert fake_gateway.calls == []

    def test_member_reads_stay_open(self, org_a, workspace, fake_gateway):
        fake_gateway.on("GET", "/-/mcp/status", body=STATUS_BODY)
        fake_gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)
        # MCP is on, so every /-/mcp/* route answers (a 404 means MCP is off).
        fake_gateway.on("GET", "/-/mcp/resources", body=[])
        fake_gateway.on("GET", "/-/mcp/prompts", body=[])
        client = _client_for_level(org_a, workspace, Level.MEMBER)

        results = {}
        for path in ("mcp-status", "mcp-tools", "mcp-resources", "mcp-prompts"):
            response = client.get(f"{GATEWAY}/{path}/")
            assert response.status_code == 200, path
            results[path] = response.json()["result"]

        assert [s["server_id"] for s in results["mcp-status"]["servers"]] == ["alpha"]
        assert [t["name"] for t in results["mcp-tools"]] == ["alpha_echo"]
        assert results["mcp-resources"] == results["mcp-prompts"] == []


@pytest.mark.integration
@pytest.mark.api
class TestMCPTestToolOrgScope:
    def test_admin_test_tool_rejects_other_org_server_with_same_body_as_unknown(
        self, auth_client, org_a, org_b, fake_gateway
    ):
        fake_gateway.on("GET", "/-/mcp/tools", body=TOOLS_LIST)
        fake_gateway.on("POST", "/-/mcp/test", body={"is_error": False})

        def call(name):
            return auth_client.post(
                f"{GATEWAY}/test-mcp-tool/",
                {"name": name, "arguments": {"q": 1}},
                format="json",
            )

        other_org = call("beta_secrets")
        # "a_b_x" starts with org A's server "a" plus the "_" separator, but
        # the gateway attributes it to org B's server "a_b".
        prefix_collision = call("a_b_x")
        unknown = call("nope_tool")

        for response in (other_org, prefix_collision, unknown):
            assert response.status_code == 404, response.json()
            assert response.json()["code"] == "mcp_tool_not_found"
        assert other_org.json() == unknown.json() == prefix_collision.json()
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == []

        own = call("alpha_echo")

        assert own.status_code == 200, own.json()
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == [
            (
                None,
                {"org_id": str(org_a.id), "name": "alpha_echo", "arguments": {"q": 1}},
            )
        ]
        tool_lookups = fake_gateway.calls_to("GET", "/-/mcp/tools")
        assert tool_lookups and all(
            params == {"org_id": str(org_a.id)} for params, _ in tool_lookups
        )

    def test_test_tool_refused_when_gateway_tool_list_fails(
        self, auth_client, org_a, fake_gateway
    ):
        fake_gateway.on("GET", "/-/mcp/tools", status=503, body={"error": "down"})
        fake_gateway.on("POST", "/-/mcp/test", body={"is_error": False})

        response = auth_client.post(
            f"{GATEWAY}/test-mcp-tool/", {"name": "alpha_echo"}, format="json"
        )

        assert response.status_code == 400, response.json()
        assert fake_gateway.calls_to("POST", "/-/mcp/test") == []


@pytest.mark.integration
@pytest.mark.api
class TestMCPStatusTruth:
    @pytest.mark.parametrize(
        "body", [STATUS_BODY, V1_STATUS], ids=["server-list", "v1"]
    )
    def test_status_uses_server_id_and_golden_shape(
        self, auth_client, org_a, fake_gateway, body
    ):
        fake_gateway.on("GET", "/-/mcp/status", body=body)

        result = _status(auth_client)

        assert result["enabled"] is True
        assert "reason" not in result
        assert [s["server_id"] for s in result["servers"]] == ["alpha"]
        assert result["servers"][0] == body["servers"][0]
        assert result["tools"] == body["servers"][0]["tool_count"]
        assert fake_gateway.calls_to("GET", "/-/mcp/status") == [
            ({"org_id": str(org_a.id)}, None)
        ]

    def test_status_disabled_when_gateway_mcp_off(
        self, auth_client, org_a, fake_gateway
    ):
        # No /-/mcp/* route: the gateway runs with MCP off and answers 404.
        result = _status(auth_client)

        assert result == {
            "enabled": False,
            "reason": "gateway_mcp_disabled",
            "sessions": 0,
            "tools": 0,
            "resources": 0,
            "prompts": 0,
            "servers": [],
        }

    @pytest.mark.parametrize(
        "route",
        [
            {"error": httpx.ConnectError("connection refused")},
            {"error": httpx.ReadTimeout("timed out")},
            {"error": httpx.RemoteProtocolError("connection reset")},
            {"error": httpx.DecodingError("bad content-encoding")},
            {"error": httpx.TooManyRedirects("redirect loop")},
            {"status": 500, "body": {"error": "boom"}},
            {"status": 200, "body": ["not", "a", "status"]},
        ],
        ids=[
            "connect",
            "timeout",
            "reset",
            "decoding",
            "redirects",
            "5xx",
            "malformed",
        ],
    )
    def test_status_unreachable_never_marks_servers_configured(
        self, auth_client, org_a, fake_gateway, route
    ):
        fake_gateway.on("GET", "/-/mcp/status", **route)

        result = _status(auth_client)

        assert result["enabled"] is False
        assert result["reason"] == "gateway_unreachable"
        assert result["servers"] == []
        assert "configured" not in json.dumps(result)

    def test_status_without_org_servers_only_asks_whether_mcp_is_on(
        self, auth_client, user, fake_gateway
    ):
        seed_mcp(user.organization, [])
        fake_gateway.on("GET", "/-/mcp/status", body=STATUS_BODY)

        result = _status(auth_client)

        assert result["enabled"] is False
        assert result["servers"] == []
        # One status call, so a deployment with MCP off says so to every org
        # (test_mcp_off_api); no list is read for an org with no server.
        assert [(m, p) for m, p, _, _ in fake_gateway.calls] == [
            ("GET", "/-/mcp/status")
        ]

    def test_counts_are_org_scoped(self, auth_client, org_a, fake_gateway):
        fake_gateway.on("GET", "/-/mcp/status", body=STATUS_BODY)
        fake_gateway.on(
            "GET",
            "/-/mcp/resources",
            body=[
                {"uri": "res://alpha/1", "server_id": "alpha"},
                {"uri": "res://beta/1", "server_id": "beta"},
                {"uri": "res://beta/2", "server_id": "beta"},
            ],
        )
        fake_gateway.on(
            "GET",
            "/-/mcp/prompts",
            body=[
                {"name": "alpha_p", "server_id": "alpha"},
                {"name": "beta_p", "server_id": "beta"},
            ],
        )

        result = _status(auth_client)

        assert result["tools"] == 3  # alpha's tool_count, not the gateway's 7
        assert result["resources"] == 1
        assert result["prompts"] == 1
        # A status without org_enabled has no per-org session count: 0.
        assert result["sessions"] == 0

    def test_org_scoped_v1_status_reports_its_sessions(
        self, auth_client, org_a, fake_gateway
    ):
        fake_gateway.on("GET", "/-/mcp/status", body=V1_STATUS)

        result = _status(auth_client)

        assert result["sessions"] == V1_STATUS["sessions"]
        assert result["tools"] == 2
        assert result["resources"] == 0 and result["prompts"] == 0

    def test_v1_status_with_org_mcp_off_is_disabled(
        self, auth_client, org_a, fake_gateway
    ):
        fake_gateway.on(
            "GET", "/-/mcp/status", body={**V1_STATUS, "org_enabled": False}
        )

        assert _status(auth_client)["enabled"] is False


@pytest.mark.integration
@pytest.mark.api
class TestMCPListsOrgScope:
    def test_resources_prompts_filtered_by_server_id(
        self, auth_client, org_a, fake_gateway
    ):
        fake_gateway.on(
            "GET",
            "/-/mcp/resources",
            body=[
                {"uri": "res://alpha/doc", "name": "doc", "server_id": "alpha"},
                {"uri": "res://beta/doc", "name": "doc", "server_id": "beta"},
                # A resource with no server_id is not provably the org's: left out.
                {"uri": "res://unattributed", "name": "loose"},
            ],
        )
        fake_gateway.on(
            "GET",
            "/-/mcp/prompts",
            body={
                "prompts": [
                    {"name": "alpha_greet", "server_id": "alpha"},
                    {"name": "beta_greet", "server_id": "beta"},
                    {"name": "a_b_greet"},
                ]
            },
        )

        resources = auth_client.get(f"{GATEWAY}/mcp-resources/").json()["result"]
        prompts = auth_client.get(f"{GATEWAY}/mcp-prompts/").json()["result"]

        assert [r["uri"] for r in resources] == ["res://alpha/doc"]
        assert [p["name"] for p in prompts] == ["alpha_greet"]
        for path in ("/-/mcp/resources", "/-/mcp/prompts"):
            assert fake_gateway.calls_to("GET", path) == [
                ({"org_id": str(org_a.id)}, None)
            ]

    @pytest.mark.parametrize(
        "body",
        [TOOLS_LIST, V1_TOOLS],
        ids=["server-list", "v1"],
    )
    def test_tools_filtered_by_server_id(self, auth_client, org_a, fake_gateway, body):
        fake_gateway.on("GET", "/-/mcp/tools", body=body)

        tools = auth_client.get(f"{GATEWAY}/mcp-tools/").json()["result"]

        assert [(t["name"], t["server_id"]) for t in tools] == [("alpha_echo", "alpha")]
        assert fake_gateway.calls_to("GET", "/-/mcp/tools") == [
            ({"org_id": str(org_a.id)}, None)
        ]

    @pytest.mark.parametrize("path", ["mcp-tools", "mcp-resources", "mcp-prompts"])
    @pytest.mark.parametrize(
        "error",
        [httpx.ConnectError("refused"), httpx.DecodingError("bad content-encoding")],
        ids=["connect", "decoding"],
    )
    def test_lists_error_when_gateway_unreachable(
        self, auth_client, org_a, fake_gateway, path, error
    ):
        for route in ("/-/mcp/tools", "/-/mcp/resources", "/-/mcp/prompts"):
            fake_gateway.on("GET", route, error=error)

        response = auth_client.get(f"{GATEWAY}/{path}/")

        # An outage is an error, not an empty list that reads as "no tools".
        assert response.status_code == 502, response.json()
        assert response.json()["code"] == "gateway_unreachable"


@pytest.mark.integration
class TestMCPAdminService:
    def test_no_organization_has_no_servers_and_calls_nothing(self, db, fake_gateway):
        assert mcp_admin.org_server_ids(None) == frozenset()
        assert mcp_admin.org_status(None)["enabled"] is False
        assert mcp_admin.org_tools(None) == []
        with pytest.raises(mcp_admin.NoMCPServers):
            mcp_admin.run_tool_test(None, "alpha_echo", {})
        assert fake_gateway.calls == []

    @pytest.mark.parametrize(
        "stored", [["alpha"], "alpha", {"servers": ["alpha"]}], ids=repr
    )
    def test_mcp_config_of_the_wrong_shape_has_no_servers(
        self, auth_client, user, fake_gateway, stored
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization, version=1, is_active=True, mcp=stored
        )

        assert mcp_admin.org_server_ids(user.organization) == frozenset()
        status = auth_client.get(f"{GATEWAY}/mcp-status/")
        tools = auth_client.get(f"{GATEWAY}/mcp-tools/")

        assert status.status_code == 200
        assert status.json()["result"]["enabled"] is False
        assert tools.status_code == 200
        assert tools.json()["result"] == []
        # Only the status call that asks whether MCP is on.
        assert [(m, p) for m, p, _, _ in fake_gateway.calls] == [
            ("GET", "/-/mcp/status")
        ]


def _golden(name):
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{path.name} is not in this tree")
    golden = json.loads(path.read_text())
    schema_path = EXAMPLES_DIR / f"{name}.schema.json"
    if schema_path.exists():
        _golden_validator(json.loads(schema_path.read_text())).validate(golden)
    return golden


def _golden_validator(schema):
    """A validator that resolves a golden schema's admin-contract refs locally.

    A golden schema names its shape by ``$ref`` into the admin contract; without
    a registry holding that contract, jsonschema would fetch it from the
    schema's ``$id`` host (as ``test_contract_v2._schema_validator`` avoids too).
    """
    admin = json.loads((EXAMPLES_DIR.parent / "agentcc-admin.schema.json").read_text())
    registry = Registry().with_resource(admin["$id"], Resource.from_contents(admin))
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.integration
@pytest.mark.api
class TestMCPAdminGoldens:
    def test_parser_accepts_mcp_admin_status_v1_golden(
        self, auth_client, user, fake_gateway
    ):
        golden = _golden("mcp-admin-status-v1")
        servers = [s["server_id"] for s in golden["servers"]]
        seed_mcp(user.organization, servers)
        fake_gateway.on("GET", "/-/mcp/status", body=golden)

        result = _status(auth_client)

        assert result["servers"] == golden["servers"]
        assert result["tools"] == sum(s["tool_count"] for s in golden["servers"])
        assert result["sessions"] == golden["sessions"]
        assert result["enabled"] is bool(golden["enabled"] and golden["org_enabled"])

    def test_parser_accepts_mcp_admin_tools_v1_golden(
        self, auth_client, user, fake_gateway
    ):
        golden = _golden("mcp-admin-tools-v1")
        seed_mcp(user.organization, sorted({t["server_id"] for t in golden["tools"]}))
        fake_gateway.on("GET", "/-/mcp/tools", body=golden)

        tools = auth_client.get(f"{GATEWAY}/mcp-tools/").json()["result"]

        assert tools == golden["tools"]


@pytest.mark.unit
class TestGoldenCheck:
    def test_golden_schema_refs_resolve_without_network(self):
        # org-config-v2 is in the tree and, like the MCP admin goldens, names
        # its shape by "$ref": "../agentcc-admin.schema.json#/$defs/...".
        with patch(
            "urllib.request.urlopen", side_effect=AssertionError("outward fetch")
        ) as urlopen:
            golden = _golden("org-config-v2")
            schema = json.loads(
                (EXAMPLES_DIR / "org-config-v2.schema.json").read_text()
            )
            with pytest.raises(jsonschema.ValidationError):
                _golden_validator(schema).validate({**golden, "providers": 5})

        assert "providers" in golden
        urlopen.assert_not_called()
