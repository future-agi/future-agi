"""Config history is the organization's, whatever workspace reads it.

An earlier release saved each config version with the workspace it was saved
from (the post-save workspace assignment). The config writer now saves every
new version with no workspace. The history list, a version's detail, the diff
and the rollback must still see all of the organization's versions from any
of its workspaces, as the active read does, and never another organization's.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import User
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

GATEWAY = "/agentcc/gateways/default"
ORG_CONFIGS = "/agentcc/org-configs/"
WHERE = ["default", "other"]
_CLIENTS = []


@pytest.fixture(autouse=True)
def _stop_clients():
    yield
    while _CLIENTS:
        _CLIENTS.pop().stop_workspace_injection()


@pytest.fixture
def gateway():
    """Every push and readiness probe goes to a mock; nothing is sent."""
    client = MagicMock()
    client.base_url = "http://gateway.test"
    client.health_check.return_value = {"status": "ok"}
    client.set_org_config.return_value = {"status": "ok"}
    client.admission_cap_ledger_status.return_value = "unknown"
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
        patch(
            "agentcc.services.cap_ledger_status.get_gateway_client",
            return_value=client,
        ),
    ):
        yield client


def _client(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    _CLIENTS.append(client)
    return client


def _join(user, workspace, level):
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=workspace.organization
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=(
            OrganizationRoles.WORKSPACE_ADMIN
            if level >= Level.WORKSPACE_ADMIN
            else OrganizationRoles.WORKSPACE_MEMBER
        ),
        level=level,
        organization_membership=membership,
        is_active=True,
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _result(response):
    assert response.status_code == 200, response.content[:800]
    return response.json()["result"]


@pytest.fixture
def history(user, workspace, gateway):
    """v1 as an earlier release stored it (saved from workspace W, active),
    then v2 saved through update-config from W, which the writer now stores
    with no workspace."""
    organization = workspace.organization
    other = Workspace.objects.create(
        name="Config history W",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    _join(user, other, Level.WORKSPACE_ADMIN)
    v1 = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, workspace=other, version=1, is_active=True
    )
    clients = {"default": _client(user, workspace), "other": _client(user, other)}
    _result(
        clients["other"].post(
            f"{GATEWAY}/update-config/",
            {"routing": {"strategy": "round_robin"}},
            format="json",
        )
    )
    v2 = _active(organization)
    v1.refresh_from_db()
    # The shape this pair is about: the earlier version carries W, the new
    # one carries no workspace.
    assert (v1.version, v1.workspace_id, v1.is_active) == (1, other.id, False)
    assert (v2.version, v2.workspace_id) == (2, None)
    return SimpleNamespace(
        organization=organization,
        default=workspace,
        other=other,
        v1=v1,
        v2=v2,
        clients=clients,
    )


@pytest.mark.parametrize("where", WHERE)
def test_list_shows_every_version_of_the_organization(history, where):
    rows = _result(history.clients[where].get(ORG_CONFIGS))
    assert [(row["id"], row["version"]) for row in rows] == [
        (str(history.v2.id), 2),
        (str(history.v1.id), 1),
    ]
    assert [row["is_active"] for row in rows] == [True, False]


@pytest.mark.parametrize("where", WHERE)
def test_every_version_opens(history, where):
    for version in (history.v1, history.v2):
        row = _result(history.clients[where].get(f"{ORG_CONFIGS}{version.id}/"))
        assert (row["id"], row["version"]) == (str(version.id), version.version)


@pytest.mark.parametrize("where", WHERE)
def test_activating_the_active_version_answers_200(history, where):
    _result(history.clients[where].post(f"{ORG_CONFIGS}{history.v2.id}/activate/"))
    assert _active(history.organization).pk == history.v2.pk


@pytest.mark.parametrize("where", WHERE)
def test_restoring_the_earlier_version(history, where, gateway):
    _result(history.clients[where].post(f"{ORG_CONFIGS}{history.v1.id}/activate/"))
    assert _active(history.organization).pk == history.v1.pk
    assert gateway.set_org_config.call_count >= 2


@pytest.mark.parametrize("where", WHERE)
def test_diff_both_ways(history, where):
    client = history.clients[where]
    older = _result(
        client.get(f"{ORG_CONFIGS}{history.v1.id}/diff/?compare_to={history.v2.id}")
    )
    assert (older["from_version"], older["to_version"]) == (2, 1)
    newer = _result(
        client.get(f"{ORG_CONFIGS}{history.v2.id}/diff/?compare_to={history.v1.id}")
    )
    assert (newer["from_version"], newer["to_version"]) == (1, 2)


@pytest.mark.parametrize("where", WHERE)
def test_another_organizations_versions_never_appear(history, user, where):
    foreign = Organization.objects.create(name="Config history foreign")
    foreign_default = Workspace.objects.create(
        name="Foreign default",
        organization=foreign,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    theirs = [
        AgentccOrgConfig.no_workspace_objects.create(
            organization=foreign, version=1, is_active=False
        ),
        AgentccOrgConfig.no_workspace_objects.create(
            organization=foreign, workspace=foreign_default, version=2, is_active=True
        ),
    ]
    client = history.clients[where]
    listed = {row["id"] for row in _result(client.get(ORG_CONFIGS))}
    assert listed == {str(history.v1.id), str(history.v2.id)}
    for version in theirs:
        assert client.get(f"{ORG_CONFIGS}{version.id}/").status_code == 404
        assert client.post(f"{ORG_CONFIGS}{version.id}/activate/").status_code == 404
        # The diff action answers a version it cannot see with its generic
        # 400, as before; what matters is that nothing of it is returned.
        refused = client.get(
            f"{ORG_CONFIGS}{version.id}/diff/?compare_to={history.v1.id}"
        )
        assert refused.status_code in (400, 404)
        assert "from_version" not in refused.content.decode()
        assert (
            client.get(
                f"{ORG_CONFIGS}{history.v1.id}/diff/?compare_to={version.id}"
            ).status_code
            == 404
        )
    assert _active(foreign).pk == theirs[1].pk
    assert _active(history.organization).pk == history.v2.pk


def test_member_reads_the_history_but_cannot_restore(history):
    member = User.objects.create_user(
        email=f"history-member-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="History member",
        organization=history.organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=history.organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    _join(member, history.other, Level.WORKSPACE_MEMBER)
    client = _client(member, history.other)
    rows = _result(client.get(ORG_CONFIGS))
    assert [row["version"] for row in rows] == [2, 1]
    response = client.post(f"{ORG_CONFIGS}{history.v1.id}/activate/")
    assert response.status_code == 403
    assert _active(history.organization).pk == history.v2.pk
