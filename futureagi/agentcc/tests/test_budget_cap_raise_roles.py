"""Round 2: real org/workspace memberships and real budget write handlers.

The existing server guard is expected to pass before the UI fix. Never weaken
it to manufacture a RED result. Gateway delivery alone is stubbed; permission
checks, validation, version writes and workspace-policy persistence are real.
"""

from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import AgentccOrgConfig
from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
from agentcc.views.ee_agent_key_policy import WorkspaceAgentKeyPolicyView
from agentcc.views.gateway import AgentccGatewayViewSet
from agentcc.views.org_config import AgentccOrgConfigViewSet
from rest_framework.test import APIRequestFactory, force_authenticate
from tfc.constants.levels import Level

# All mutation paths listed by round 1's SURVEY.json, including the immutable
# version aliases. Authorized PUT/PATCH stay 400, not fake successful writes.
WRITES = [
    ("org_create", AgentccOrgConfigViewSet, "create", "post"),
    ("org_update", AgentccOrgConfigViewSet, "update", "put"),
    ("org_partial_update", AgentccOrgConfigViewSet, "partial_update", "patch"),
    ("org_destroy", AgentccOrgConfigViewSet, "destroy", "delete"),
    ("org_activate", AgentccOrgConfigViewSet, "activate", "post"),
    ("gateway_config", AgentccGatewayViewSet, "update_config", "post"),
    ("gateway_set", AgentccGatewayViewSet, "set_budget", "post"),
    ("gateway_remove", AgentccGatewayViewSet, "remove_budget", "post"),
    ("workspace_policy", WorkspaceAgentKeyPolicyView, "put", "put"),
]


def _actor(role):
    org = Organization.objects.create(name="Budget role target")
    home = (
        Organization.objects.create(name="Different home organization")
        if role == "foreign_owner"
        else org
    )
    org_level = {"Owner": Level.OWNER, "Admin": Level.ADMIN}.get(role, Level.MEMBER)
    org_role = Level.to_org_string(org_level)
    user = User.objects.create_user(
        email=f"budget-role-{uuid4().hex}@example.com",
        password=None,
        name="Budget role test",
        organization=home,
        organization_role="Owner" if home != org else org_role,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=org, role=org_role, level=org_level, is_active=True
    )
    if home != org:
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=home,
            role="Owner",
            level=Level.OWNER,
            is_active=True,
        )
    workspace = Workspace.no_workspace_objects.create(
        name="Budget workspace",
        organization=org,
        created_by=user,
        is_default=True,
        is_active=True,
    )
    # In particular, a workspace admin has an org MEMBER membership, not an
    # org Level.ADMIN. Conversely an org Admin needs no workspace-admin grant.
    ws_admin = role in {"workspace_admin", "foreign_owner"}
    ws_level = Level.WORKSPACE_ADMIN if ws_admin else Level.WORKSPACE_MEMBER
    ws_membership = WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        organization_membership=membership,
        role="workspace_admin" if ws_admin else "workspace_member",
        level=ws_level,
        is_active=True,
    )
    assert user.get_membership_level(org) == org_level
    assert ws_membership.level == ws_level
    return org, user, workspace


def _snapshot(org):
    return (
        list(
            AgentccOrgConfig.no_workspace_objects.filter(organization=org)
            .order_by("version")
            .values("id", "budgets", "routing", "is_active", "deleted")
        ),
        list(
            AgentccEEWorkspacePolicy.objects.filter(organization=org)
            .order_by("pk")
            .values(
                "workspace_id", "agent_key_budget_usd", "budget_period", "updated_by_id"
            )
        ),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role", ["workspace_admin", "member", "foreign_owner", "Admin", "Owner"]
)
@pytest.mark.parametrize(
    "name,view_class,action,method", WRITES, ids=[row[0] for row in WRITES]
)
def test_every_budget_write_uses_org_not_workspace_authority(
    monkeypatch, settings, role, name, view_class, action, method
):
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    org, user, workspace = _actor(role)
    seed = {"org_limit": {"limit": 5, "on_exceed": "block"}}
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, budgets=seed
    )
    inactive = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=2,
        is_active=False,
        budgets={"org_limit": {"limit": 10, "on_exceed": "block"}},
    )
    AgentccEEWorkspacePolicy.objects.create(
        organization=org,
        workspace=workspace,
        agent_key_budget_usd=Decimal("50.00"),
        budget_period="monthly",
        updated_by=user,
    )
    pushed = Mock(return_value=True)
    monkeypatch.setattr(
        AgentccGatewayViewSet, "_push_current_config", lambda *args, **kwargs: pushed()
    )
    monkeypatch.setattr(
        AgentccOrgConfigViewSet,
        "_push_config_to_gateway",
        lambda *args, **kwargs: pushed(),
    )
    before = _snapshot(org)
    payload = {"budgets": {"org_limit": {"limit": 25, "on_exceed": "block"}}}
    if name == "gateway_set":
        payload = {"level": "org_limit", "config": {"limit": 25, "on_exceed": "block"}}
    elif name == "gateway_remove":
        payload = {"level": "org_limit"}
    elif name == "workspace_policy":
        payload = {"budget_usd": "60.25", "budget_period": "monthly"}
    elif name in {"org_activate", "org_destroy"}:
        payload = {}

    if name == "workspace_policy":
        url = f"/agentcc/mcp/service/workspaces/{workspace.id}/agent-key-policy/"
        view = view_class.as_view()
        kwargs = {"workspace_id": workspace.id}
    elif name.startswith("gateway_"):
        url = f"/agentcc/gateways/default/{action.replace('_', '-')}/"
        view = view_class.as_view({method: action})
        kwargs = {"pk": "default"}
    else:
        suffix = "activate/" if name == "org_activate" else ""
        url = (
            "/agentcc/org-configs/"
            if name == "org_create"
            else f"/agentcc/org-configs/{inactive.id}/{suffix}"
        )
        view = view_class.as_view({method: action})
        kwargs = {} if name == "org_create" else {"pk": str(inactive.id)}
    request = getattr(APIRequestFactory(), method)(url, payload, format="json")
    request.organization = org
    request.workspace = workspace
    force_authenticate(request, user=user)
    response = view(request, **kwargs)

    if role not in {"Admin", "Owner"}:
        assert response.status_code == 403, (name, role, response.data)
        assert _snapshot(org) == before
        pushed.assert_not_called()
        return

    if name in {"org_update", "org_partial_update"}:
        assert response.status_code == 400
        assert "immutable" in str(response.data).lower()
        assert _snapshot(org) == before
        pushed.assert_not_called()
        return

    assert response.status_code == 200, (name, role, response.data)
    if name == "workspace_policy":
        policy = AgentccEEWorkspacePolicy.objects.get(workspace=workspace)
        assert policy.agent_key_budget_usd == Decimal("60.25")
        assert policy.budget_period == "monthly"
        assert policy.updated_by_id == user.id
        assert response.data["result"]["budget_usd"] == "60.25"
        pushed.assert_not_called()  # Propagation stays at next key sync.
    elif name == "org_destroy":
        inactive.refresh_from_db()
        assert inactive.deleted is True
        pushed.assert_not_called()
    else:
        saved = AgentccOrgConfig.no_workspace_objects.get(
            organization=org, is_active=True, deleted=False
        )
        if name == "org_activate":
            assert saved.pk == inactive.pk
            assert saved.budgets["org_limit"]["limit"] == 10
        elif name == "gateway_remove":
            assert "org_limit" not in saved.budgets
        else:
            assert saved.budgets["org_limit"]["limit"] == 25
            assert saved.created_by_id == user.id
        pushed.assert_called_once()
