"""Budget deep-link writers retain their acted-on-org server authorization."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agentcc.models.ee_agent_key_policy import AgentccEEWorkspacePolicy
from agentcc.views.ee_agent_key_policy import WorkspaceAgentKeyPolicyView
from agentcc.views.gateway import AgentccGatewayViewSet
from agentcc.views.org_config import AgentccOrgConfigViewSet
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from tfc.constants.levels import Level


@pytest.mark.parametrize(
    "level", [None, Level.VIEWER, Level.MEMBER, Level.ADMIN, Level.OWNER]
)
@pytest.mark.parametrize(
    "view_class, action, method",
    [
        (AgentccOrgConfigViewSet, "create", "post"),
        (AgentccOrgConfigViewSet, "update", "put"),
        (AgentccOrgConfigViewSet, "partial_update", "patch"),
        (AgentccOrgConfigViewSet, "destroy", "delete"),
        (AgentccOrgConfigViewSet, "activate", "post"),
        (AgentccGatewayViewSet, "update_config", "post"),
        (AgentccGatewayViewSet, "set_budget", "post"),
        (AgentccGatewayViewSet, "remove_budget", "post"),
    ],
)
def test_budget_writes_check_the_acted_on_org_before_dispatch(
    monkeypatch, level, view_class, action, method
):
    organization = SimpleNamespace(id="acted-on-org")
    user = Mock(is_authenticated=True, is_active=True)
    user.get_membership_level.return_value = level
    reached = Mock(return_value=Response({"reached": True}))
    monkeypatch.setattr(view_class, action, lambda self, *a, **kw: reached())
    request = getattr(APIRequestFactory(), method)(
        "/budget-test/", {"budgets": {"org_limit": 100}}, format="json"
    )
    request.organization = organization
    force_authenticate(request, user=user)
    response = view_class.as_view({method: action})(request, pk="default")
    allowed = level in (Level.ADMIN, Level.OWNER)
    assert response.status_code == (200 if allowed else 403)
    assert reached.call_count == int(allowed)
    user.get_membership_level.assert_called_once_with(organization)


@pytest.mark.parametrize(
    "level", [None, Level.VIEWER, Level.MEMBER, Level.ADMIN, Level.OWNER]
)
def test_workspace_policy_put_has_the_same_role_boundary(monkeypatch, settings, level):
    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    organization = SimpleNamespace(id="acted-on-org")
    user = Mock(is_authenticated=True, is_active=True)
    user.get_membership_level.return_value = level
    reached = Mock(return_value=Response({"reached": True}))
    monkeypatch.setattr(
        WorkspaceAgentKeyPolicyView, "put", lambda self, *a, **kw: reached()
    )
    request = APIRequestFactory().put(
        "/budget-test/",
        {"budget_usd": "60.00", "budget_period": "monthly"},
        format="json",
    )
    request.organization = organization
    force_authenticate(request, user=user)
    response = WorkspaceAgentKeyPolicyView.as_view()(request, workspace_id="workspace")
    allowed = level in (Level.ADMIN, Level.OWNER)
    assert response.status_code == (200 if allowed else 403)
    assert reached.call_count == int(allowed)
    user.get_membership_level.assert_called_once_with(organization)


@pytest.mark.django_db
def test_workspace_budget_raise_is_persisted_and_cross_org_id_is_not_writable(settings):
    from agentcc.tests.test_mcp_service_provisioning import _org

    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    org, owner, workspace = _org("Budget cap owner")
    _, _, foreign_workspace = _org("Budget cap foreign")
    AgentccEEWorkspacePolicy.objects.create(
        organization=org,
        workspace=workspace,
        agent_key_budget_usd=Decimal("50.00"),
        budget_period="monthly",
        updated_by=owner,
    )

    def put(workspace_id, value):
        request = APIRequestFactory().put(
            "/budget-test/",
            {"budget_usd": value, "budget_period": "monthly"},
            format="json",
        )
        request.organization = org
        force_authenticate(request, user=owner)
        return WorkspaceAgentKeyPolicyView.as_view()(request, workspace_id=workspace_id)

    response = put(workspace.id, "60.25")
    assert response.status_code == 200
    assert response.data["result"]["budget_usd"] == "60.25"
    row = AgentccEEWorkspacePolicy.objects.get(workspace=workspace)
    assert row.agent_key_budget_usd == Decimal("60.25")
    assert row.budget_period == "monthly"
    assert row.updated_by == owner
    assert put(foreign_workspace.id, "999.00").status_code == 404
    assert not AgentccEEWorkspacePolicy.objects.filter(
        workspace=foreign_workspace
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "1000000.01", "1.001"])
def test_workspace_budget_rejects_invalid_money(settings, amount):
    from agentcc.tests.test_mcp_service_provisioning import _org

    settings.EE_SERVICE_PROVISIONING_ENABLED = True
    org, owner, workspace = _org("Budget validation")
    request = APIRequestFactory().put(
        "/budget-test/",
        {"budget_usd": amount, "budget_period": "monthly"},
        format="json",
    )
    request.organization = org
    force_authenticate(request, user=owner)
    response = WorkspaceAgentKeyPolicyView.as_view()(request, workspace_id=workspace.id)
    assert response.status_code == 400
    assert not AgentccEEWorkspacePolicy.objects.filter(workspace=workspace).exists()


def test_unavailable_workspace_policy_never_writes(monkeypatch, settings):
    settings.EE_SERVICE_PROVISIONING_ENABLED = False
    reached = Mock()
    monkeypatch.setattr(WorkspaceAgentKeyPolicyView, "put", reached)
    request = APIRequestFactory().put("/budget-test/", {}, format="json")
    response = WorkspaceAgentKeyPolicyView.as_view()(request, workspace_id="workspace")
    assert response.status_code == 503
    assert response.data["code"] == "feature_unavailable"
    reached.assert_not_called()
