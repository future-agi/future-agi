"""Tool revisions and pin approval (M21), the Django side of tool pinning (M20).

The gateway's admin API is faked at its HTTP client (``FakeAdmin`` answers
GET /-/mcp/revisions and GET /-/mcp/tools per org), and config pushes are
recorded from ``get_gateway_client``, so the org config writer, the push
payload builder and the API run as they do in production. Every test user
belongs to an org.
"""

import copy
import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.mcp_tool_revision import AgentccMCPToolRevision
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_revisions as revisions
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

REVISIONS = "/agentcc/mcp/revisions/"
REV1 = "1" * 64
REV2 = "2" * 64
REV3 = "3" * 64
GITHUB_URL = "https://mcp.github.example.com/mcp"
ECHO_V1 = {"description": "Echoes.", "inputSchema": {"type": "object"}, "name": "echo"}
ECHO_V2 = {
    "description": "Echoes, second wording.",
    "inputSchema": {"type": "object"},
    "name": "echo",
}


# --- Fakes and helpers -------------------------------------------------------


@pytest.fixture
def pushes():
    """Every org config the gateway was sent, as (org_id, payload)."""
    client = MagicMock()
    sent = []
    client.set_org_config.side_effect = lambda org_id, payload: sent.append(
        (org_id, copy.deepcopy(payload))
    )
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield sent


class FakeAdmin:
    """The gateway admin API's GET /-/mcp/revisions and /-/mcp/tools, per
    org: ``revisions[org_id]`` and ``tools[org_id]`` are the items it answers
    with, or ``down`` makes it answer 503. Records each (path, org_id)."""

    def __init__(self):
        self.revisions = {}
        self.tools = {}
        self.down = False
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None):
        path = urlsplit(url).path
        org_id = (params or {}).get("org_id")
        self.calls.append((method, path, org_id))
        if self.down:
            return httpx.Response(503, json={"error": {"code": "unavailable"}})
        if (method, path) == ("GET", "/-/mcp/revisions"):
            return httpx.Response(
                200, json={"revisions": self.revisions.get(org_id, [])}
            )
        if (method, path) == ("GET", "/-/mcp/tools"):
            return httpx.Response(200, json={"tools": self.tools.get(org_id, [])})
        raise AssertionError(f"unexpected gateway call {method} {path}")


@pytest.fixture
def gateway():
    admin = FakeAdmin()
    client = MagicMock()
    client.__enter__.return_value = admin
    client.__exit__.return_value = False
    with patch("agentcc.services.gateway_client.httpx.Client", return_value=client):
        yield admin


def _record(
    org,
    *,
    tool="echo",
    revision=REV2,
    previous=REV1,
    definition=None,
    previous_definition=None,
    diff=None,
):
    """One mcp-revision-v1 record of org's github."""
    record = {
        "org_id": str(org.pk),
        "server_id": "github",
        "tool": tool,
        "revision": revision,
        "definition": definition or ECHO_V2,
        "diff": diff if diff is not None else [],
        "first_seen_at": "2026-10-25T10:00:00Z",
        "reason": "changed" if previous else "new",
    }
    if previous:
        record["previous_revision"] = previous
    if previous_definition is not None:
        record["previous_definition"] = previous_definition
    return record


def _admin_tool(tool, revision, *, server="github", quarantined=False):
    return {
        "name": f"{server}__{tool}",
        "server_id": server,
        "upstream_name": tool,
        "revision": revision,
        "quarantined": quarantined,
    }


def _seed(org, pins=None, **pinning):
    mcp = {
        "enabled": True,
        "servers": {"github": {"url": GITHUB_URL, "transport": "http"}},
    }
    if pins is not None or pinning:
        mcp["pinning"] = {**pinning, **({"pins": pins} if pins is not None else {})}
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, mcp=mcp
    )


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _versions(org):
    return AgentccOrgConfig._base_manager.filter(organization=org).count()


def _rows(org, **filters):
    return list(
        AgentccMCPToolRevision.objects.filter(organization=org, **filters).order_by(
            "pk"
        )
    )


def _org_with_owner(name):
    org = Organization.objects.create(name=name)
    owner = User.objects.create_user(
        email=f"m21-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="M21 Other Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=owner,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    Workspace.objects.create(
        name="Other",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    return org, owner


def _member_client(org, workspace):
    member = User.objects.create_user(
        email=f"m21-member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="M21 Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    return client


def _ok(response, status=200):
    assert response.status_code == status, response.json()
    return response.json()["result"]


def _first_statement(queries):
    for query in queries:
        sql = query["sql"].upper()
        if not sql.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
            return sql
    return ""


# --- Tests -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestToolRevisions:
    def test_pull_upserts_pending_once(self, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [
            _record(org, previous_definition=ECHO_V1),
            _record(
                org,
                tool="wipe",
                revision=REV3,
                previous=None,
                definition={"name": "wipe", "inputSchema": {"type": "object"}},
            ),
        ]
        revisions.pull(org)
        revisions.pull(org)

        pending = _rows(org, status="pending")
        assert [(r.tool, r.revision, r.previous_revision) for r in pending] == [
            ("echo", REV2, REV1),
            ("wipe", REV3, ""),
        ]
        # The accepted definition the gateway sent is kept on its own row,
        # and the diff is computed from it when the gateway sent none.
        accepted = _rows(org, revision=REV1)
        assert len(accepted) == 1 and accepted[0].definition == ECHO_V1
        assert accepted[0].status == "approved"
        assert pending[0].diff == [
            {
                "field": "description",
                "before": ECHO_V1["description"],
                "after": ECHO_V2["description"],
            }
        ]
        # A pull writes no config version: pins change only by a decision.
        assert _versions(org) == 1 and pushes == []

    def test_pull_supersedes_what_the_gateway_no_longer_holds(
        self, user, gateway, pushes
    ):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [_record(org)]
        revisions.pull(org)
        gateway.revisions[str(org.pk)] = []  # the server reverted
        revisions.pull(org)
        assert [r.status for r in _rows(org, tool="echo", revision=REV2)] == [
            "superseded"
        ]

    def test_list_shows_what_changed(self, auth_client, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        diff = [
            {
                "field": "description",
                "before": "Echoes.",
                "after": ECHO_V2["description"],
            }
        ]
        gateway.revisions[str(org.pk)] = [
            _record(org, previous_definition=ECHO_V1, diff=diff)
        ]

        result = _ok(auth_client.get(REVISIONS))
        assert result["gateway_synced"] is True
        [item] = result["revisions"]
        assert item["server_id"] == "github" and item["tool"] == "echo"
        assert (item["reason"], item["status"]) == ("changed", "pending")
        assert (item["revision"], item["previous_revision"]) == (REV2, REV1)
        assert item["definition"] == ECHO_V2
        assert item["previous_definition"] == ECHO_V1
        assert item["diff"] == diff

        # When the gateway cannot answer, the rows last pulled are shown.
        gateway.down = True
        result = _ok(auth_client.get(REVISIONS))
        assert result["gateway_synced"] is False
        assert [i["tool"] for i in result["revisions"]] == ["echo"]

    def test_approve_writes_pin_through_writer_and_pushes(
        self, auth_client, user, gateway, pushes
    ):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [_record(org)]
        [item] = _ok(auth_client.get(REVISIONS))["revisions"]

        result = _ok(
            auth_client.post(f"{REVISIONS}{item['id']}/approve/", {}, format="json")
        )

        assert result["status"] == "approved" and result["gateway_synced"] is True
        assert result["decided_by"] == user.email and result["decided_at"]
        active = _active(org)
        assert active.version == result["version"] == 2
        assert active.mcp["pinning"]["pins"] == {"github/echo": REV2}
        assert active.created_by == user
        # The gateway was sent the version with the new pin.
        assert pushes[-1][1]["mcp"]["pinning"]["pins"] == {"github/echo": REV2}
        # Deciding twice is refused, and writes nothing.
        refused = auth_client.post(
            f"{REVISIONS}{item['id']}/approve/", {}, format="json"
        )
        assert refused.status_code == 409
        assert _versions(org) == 2

    def test_reject_keeps_the_tool_held(self, auth_client, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [_record(org)]
        [item] = _ok(auth_client.get(REVISIONS))["revisions"]

        result = _ok(
            auth_client.post(f"{REVISIONS}{item['id']}/reject/", {}, format="json")
        )

        assert result["status"] == "rejected" and result["decided_by"] == user.email
        # The pin is unchanged, so the gateway keeps holding the changed tool.
        assert _versions(org) == 1 and pushes == []
        assert _active(org).mcp["pinning"]["pins"] == {"github/echo": REV1}
        # A later pull that still sees it keeps the decision.
        revisions.pull(org)
        assert [r.status for r in _rows(org, revision=REV2)] == ["rejected"]
        # An admin can still change their mind and accept it.
        _ok(auth_client.post(f"{REVISIONS}{item['id']}/approve/", {}, format="json"))
        assert _active(org).mcp["pinning"]["pins"] == {"github/echo": REV2}

    def test_approve_baseline_bulk(self, auth_client, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={}, auto_baseline=False)
        gateway.revisions[str(org.pk)] = [
            _record(org, tool="echo", revision=REV1, previous=None, definition=ECHO_V1),
            _record(
                org,
                tool="wipe",
                revision=REV3,
                previous=None,
                definition={"name": "wipe", "inputSchema": {"type": "object"}},
            ),
        ]
        _ok(auth_client.get(REVISIONS))

        result = _ok(
            auth_client.post(
                f"{REVISIONS}approve-baseline/", {"server_id": "github"}, format="json"
            )
        )

        assert sorted(item["tool"] for item in result["approved"]) == ["echo", "wipe"]
        assert _active(org).mcp["pinning"]["pins"] == {
            "github/echo": REV1,
            "github/wipe": REV3,
        }
        assert _versions(org) == 2 and len(pushes) == 1
        # Nothing left pending writes nothing.
        again = _ok(
            auth_client.post(
                f"{REVISIONS}approve-baseline/", {"server_id": "github"}, format="json"
            )
        )
        assert again["approved"] == [] and _versions(org) == 2

    def test_pull_baselines_a_server_with_no_pins(self, user, gateway, pushes):
        org = user.organization
        _seed(org)
        gateway.tools[str(org.pk)] = [
            _admin_tool("echo", REV1),
            _admin_tool("wipe", REV3),
            _admin_tool("search", REV2, server="fagi.web"),  # managed: not the org's
        ]
        result = revisions.pull(org)

        assert result["baselined"] == ["github"]
        assert _active(org).mcp["pinning"]["pins"] == {
            "github/echo": REV1,
            "github/wipe": REV3,
        }
        assert pushes[-1][1]["mcp"]["pinning"]["pins"] == {
            "github/echo": REV1,
            "github/wipe": REV3,
        }
        # A pinned server is never baselined again.
        gateway.tools[str(org.pk)] = [_admin_tool("echo", REV2)]
        assert revisions.pull(org)["baselined"] == []
        assert _versions(org) == 2
        # Without auto_baseline, nothing is pinned for the admin.
        other, _ = _org_with_owner("M21 no baseline")
        _seed(other, auto_baseline=False)
        gateway.tools[str(other.pk)] = [_admin_tool("echo", REV1)]
        assert revisions.pull(other)["baselined"] == []
        assert "pins" not in _active(other).mcp["pinning"]

    def test_member_403(self, auth_client, user, workspace, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [_record(org)]
        [item] = _ok(auth_client.get(REVISIONS))["revisions"]
        member = _member_client(org, workspace)

        for response in (
            member.post(f"{REVISIONS}{item['id']}/approve/", {}, format="json"),
            member.post(f"{REVISIONS}{item['id']}/reject/", {}, format="json"),
            member.post(
                f"{REVISIONS}approve-baseline/", {"server_id": "github"}, format="json"
            ),
        ):
            assert response.status_code == 403, response.json()
        assert _versions(org) == 1 and pushes == []
        assert [r.status for r in _rows(org, revision=REV2)] == ["pending"]
        # Members can see what is held, and their list writes no pins.
        assert [i["tool"] for i in _ok(member.get(REVISIONS))["revisions"]] == ["echo"]

    def test_other_org_revision_404(self, auth_client, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        other, _ = _org_with_owner("M21 other")
        _seed(other, pins={"github/echo": REV1})
        gateway.revisions[str(other.pk)] = [_record(other)]
        revisions.pull(other)
        [foreign] = _rows(other, status="pending")

        for action in ("approve", "reject"):
            response = auth_client.post(
                f"{REVISIONS}{foreign.pk}/{action}/", {}, format="json"
            )
            assert response.status_code == 404, response.json()
        assert _ok(auth_client.get(REVISIONS))["revisions"] == []
        assert _active(other).mcp["pinning"]["pins"] == {"github/echo": REV1}
        assert [r.status for r in _rows(other, revision=REV2)] == ["pending"]
        # The gateway is asked for the request's org only.
        assert {org_id for _, _, org_id in gateway.calls} <= {
            str(org.pk),
            str(other.pk),
        }
        assert all(org_id == str(org.pk) for _, path, org_id in gateway.calls[-1:])

    def test_approve_refused_for_a_removed_server(
        self, auth_client, user, gateway, pushes
    ):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [_record(org)]
        [item] = _ok(auth_client.get(REVISIONS))["revisions"]
        config = _active(org)
        config.mcp = {"enabled": True, "servers": {}}
        config.save(update_fields=["mcp"])

        response = auth_client.post(
            f"{REVISIONS}{item['id']}/approve/", {}, format="json"
        )

        assert response.status_code == 409, response.json()
        assert _versions(org) == 1
        assert [r.status for r in _rows(org, revision=REV2)] == ["pending"]

    def test_writes_take_org_lock_first(self, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        gateway.revisions[str(org.pk)] = [
            _record(org),
            _record(
                org,
                tool="wipe",
                revision=REV3,
                previous=None,
                definition={"name": "wipe", "inputSchema": {"type": "object"}},
            ),
        ]
        revisions.pull(org)
        echo, wipe = _rows(org, status="pending")

        statements = {}
        for name, write in {
            "approve": lambda: revisions.approve(org, echo.pk, user),
            "reject": lambda: revisions.reject(org, wipe.pk, user),
            "approve_baseline": lambda: revisions.approve_baseline(org, "github", user),
        }.items():
            with CaptureQueriesContext(connection) as queries:
                write()
            statements[name] = _first_statement(queries.captured_queries)

        for name, first in statements.items():
            assert "FOR NO KEY UPDATE" in first, (name, first)
            assert '"ACCOUNTS_ORGANIZATION"' in first, (name, first)

    def test_pull_all_pulls_each_org_with_servers(self, user, gateway, pushes):
        org = user.organization
        _seed(org, pins={"github/echo": REV1})
        quiet, _ = _org_with_owner("M21 no servers")
        AgentccOrgConfig.no_workspace_objects.create(
            organization=quiet,
            version=1,
            is_active=True,
            mcp={"enabled": True, "servers": {}},
        )
        gateway.revisions[str(org.pk)] = [_record(org)]

        assert revisions.pull_all() == {"pulled": 1, "failed": 0}
        assert len(_rows(org, status="pending")) == 1
        assert {org_id for _, _, org_id in gateway.calls} == {str(org.pk)}

    def test_routes_reverse_and_are_included_ahead_of_the_router(self):
        assert reverse("agentcc-mcp-revisions") == REVISIONS
        assert reverse("agentcc-mcp-revision-approve", kwargs={"revision_id": 7}) == (
            f"{REVISIONS}7/approve/"
        )
        assert reverse("agentcc-mcp-revision-reject", kwargs={"revision_id": 7}) == (
            f"{REVISIONS}7/reject/"
        )
        assert resolve(f"{REVISIONS}approve-baseline/").url_name == (
            "agentcc-mcp-revisions-approve-baseline"
        )
