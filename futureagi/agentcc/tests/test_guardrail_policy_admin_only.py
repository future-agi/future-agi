"""Only an org Owner or Admin can change guardrail policies.

A guardrail policy's checks are merged into the org's active config and
pushed to the gateway (``sync_guardrail_policies``). The writes (create,
update, partial update, delete, apply, sync) need an Owner or Admin of the
organization the request acts on; every member keeps read access.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GUARDRAIL_POLICIES = "/agentcc/guardrail-policies/"
COLLECTOR = "https://collector.example.test/in"

LEVEL_ROLES = {
    Level.OWNER: OrganizationRoles.OWNER,
    Level.ADMIN: OrganizationRoles.ADMIN,
    Level.MEMBER: OrganizationRoles.MEMBER,
    Level.VIEWER: OrganizationRoles.MEMBER_VIEW_ONLY,
}


def _user_with_level(organization, level):
    user = User.objects.create_user(
        email=f"t-role-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Role User",
        organization=organization,
        organization_role=LEVEL_ROLES[level],
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=LEVEL_ROLES[level],
        level=level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=Workspace.no_workspace_objects.get(
            organization=organization, is_default=True
        ),
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    return user


@pytest.fixture
def client_with_level(organization, workspace):
    clients = []

    def build(level):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=_user_with_level(organization, level))
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def active_config(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )


@pytest.fixture
def guardrail_policy(organization):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="baseline",
        checks=[{"name": "injection-detector", "action": "block"}],
    )


@pytest.fixture
def gateway():
    """Every push goes to a mock that records what it got."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _snapshot(organization):
    """Everything a refused write would change."""
    return {
        "policies": list(
            AgentccGuardrailPolicy.all_objects.filter(organization=organization)
            .order_by("name")
            .values_list(
                "name",
                "deleted",
                "is_active",
                "scope",
                "mode",
                "checks",
                "applied_keys",
            )
        ),
        "configs": list(
            AgentccOrgConfig.all_objects.filter(organization=organization)
            .order_by("version")
            .values_list("version", "is_active", "deleted", "guardrails")
        ),
    }


# (method, path, body). `{policy}` is filled from the fixture. Bodies are
# valid, so only the role check refuses them.
WRITES = {
    # A create that adds a webhook check.
    "create": (
        "post",
        GUARDRAIL_POLICIES,
        {
            "name": "outbound",
            "checks": [{"name": "outbound-webhook", "config": {"url": COLLECTOR}}],
        },
    ),
    "update": (
        "put",
        f"{GUARDRAIL_POLICIES}{{policy}}/",
        {
            "name": "baseline",
            "checks": [{"name": "outbound-webhook", "config": {"url": COLLECTOR}}],
        },
    ),
    "partial-update": (
        "patch",
        f"{GUARDRAIL_POLICIES}{{policy}}/",
        {"checks": [{"name": "outbound-webhook", "config": {"url": COLLECTOR}}]},
    ),
    "delete": ("delete", f"{GUARDRAIL_POLICIES}{{policy}}/", None),
    "apply": ("post", f"{GUARDRAIL_POLICIES}{{policy}}/apply/", {"key_ids": []}),
    "sync": ("post", f"{GUARDRAIL_POLICIES}sync/", {}),
}


def _send(client, method, path, body, policy):
    path = path.format(policy=policy.pk)
    if body is None:
        return getattr(client, method)(path)
    return getattr(client, method)(path, body, format="json")


@pytest.mark.integration
@pytest.mark.api
class TestMembersCannotChangeGuardrailPolicies:
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    @pytest.mark.parametrize("write", sorted(WRITES))
    def test_member_gets_403_and_nothing_changes(
        self,
        client_with_level,
        organization,
        active_config,
        guardrail_policy,
        gateway,
        level,
        write,
    ):
        client = client_with_level(level)
        before = _snapshot(organization)

        response = _send(client, *WRITES[write], guardrail_policy)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.content
        assert _snapshot(organization) == before
        assert COLLECTOR not in repr(_snapshot(organization))
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestAdminsCanChangeGuardrailPolicies:
    @pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
    def test_admin_gets_200_on_every_guardrail_policy_write(
        self,
        client_with_level,
        organization,
        active_config,
        guardrail_policy,
        gateway,
        level,
    ):
        client = client_with_level(level)
        detail = f"{GUARDRAIL_POLICIES}{guardrail_policy.pk}/"

        responses = {
            "create": client.post(
                GUARDRAIL_POLICIES,
                {"name": "strict", "checks": [{"name": "injection-detector"}]},
                format="json",
            ),
            "update": client.put(
                detail,
                {"name": "baseline", "checks": [{"name": "pii-detector"}]},
                format="json",
            ),
            "partial-update": client.patch(detail, {"mode": "monitor"}, format="json"),
            "apply": client.post(f"{detail}apply/", {"key_ids": []}, format="json"),
            "sync": client.post(f"{GUARDRAIL_POLICIES}sync/", {}, format="json"),
            "delete": client.delete(detail),
        }

        assert {
            name: response.status_code for name, response in responses.items()
        } == dict.fromkeys(responses, status.HTTP_200_OK)
        assert AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="strict"
        ).exists()
        stored = AgentccGuardrailPolicy.all_objects.get(pk=guardrail_policy.pk)
        assert stored.deleted is True
        assert stored.mode == "monitor"
        assert stored.checks == [{"name": "pii-detector"}]
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestMembersKeepReadAccess:
    @pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
    def test_member_lists_and_reads_guardrail_policies(
        self, client_with_level, guardrail_policy, level
    ):
        client = client_with_level(level)

        listed = client.get(GUARDRAIL_POLICIES)
        detail = client.get(f"{GUARDRAIL_POLICIES}{guardrail_policy.pk}/")

        assert listed.status_code == status.HTTP_200_OK, listed.content
        assert detail.status_code == status.HTTP_200_OK, detail.content
        assert detail.json()["result"]["name"] == "baseline"
