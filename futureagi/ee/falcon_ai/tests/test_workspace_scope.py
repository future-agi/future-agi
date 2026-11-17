"""Falcon native tools must distinguish an empty landing from background work."""

from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.tools.context.search import SearchTool
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.consumers import FalconAIConsumer
from model_hub.models.develop_dataset import Dataset
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
    workspace_context,
    workspace_scope_is_empty,
)


@pytest.fixture(params=["absent", "deactivated"])
def scope_state(db, request):
    clear_workspace_context()
    org = Organization.objects.create(name="Native tool scope")
    owner = User.objects.create_user(
        email="native-owner@example.test",
        password="test-password",
        name="Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
        config={},
    )
    member = User.objects.create_user(
        email="native-member@example.test",
        password="test-password",
        name="Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
        config={},
    )
    OrganizationMembership.no_workspace_objects.create(
        user=owner,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    default = Workspace.objects.create(
        name="Default", organization=org, created_by=owner, is_default=True
    )
    private = Workspace.objects.create(
        name="Private", organization=org, created_by=owner
    )
    if request.param == "deactivated":
        WorkspaceMembership.no_workspace_objects.create(
            user=member,
            workspace=private,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=False,
        )
    hidden = Dataset.objects.create(
        name="native-scope-needle-private", workspace=private, organization=org
    )
    visible = Dataset.objects.create(
        name="native-scope-needle-default", workspace=default, organization=org
    )
    yield SimpleNamespace(
        org=org,
        owner=owner,
        member=member,
        membership=membership,
        default=default,
        private=private,
        hidden=hidden,
        visible=visible,
    )
    clear_workspace_context()


def landing(state, user):
    consumer = FalconAIConsumer()
    consumer.user = user
    consumer.organization = state.org
    consumer.organization_id = state.org.pk
    consumer.workspace_id = None
    return FalconAIConsumer.__dict__["_get_workspace"].func(consumer)


def execute(context, tool, params=None, *, use_async=False):
    agent = SimpleNamespace(tool_context=context)
    method = AgentLoop.__dict__["_execute_tool"]
    if use_async:
        return async_to_sync(method)(agent, tool, params or {})
    return method.func(agent, tool, params or {})


def membership_rows(state):
    return list(
        WorkspaceMembership.no_workspace_objects.filter(user=state.member)
        .order_by("pk")
        .values_list("pk", "workspace_id", "is_active")
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_native_search_denies_empty_landing_without_grant(scope_state, use_async):
    state = scope_state
    before = membership_rows(state)
    assert not state.member.can_access_workspace(state.private)
    workspace = landing(state, state.member)
    assert workspace is None
    assert not workspace_scope_is_empty()  # No REST authentication to bind it.

    result = execute(
        ToolContext(state.member, state.org, workspace),
        SearchTool(),
        {"query": "native-scope-needle", "entity_types": ["datasets"]},
        use_async=use_async,
    )

    assert not result.is_error, result
    assert result.data["total"] == 0
    assert result.data["results"] == {}
    assert str(state.hidden.pk) not in str(result.data)
    assert not state.member.can_access_workspace(state.private)
    assert membership_rows(state) == before
    assert Workspace.objects.filter(organization=state.org).count() == 2
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()  # Background semantics are restored.


class ScopeProbe(BaseTool):
    name = "scope_probe"
    description = "Observe native tool scope without external calls"
    category = "context"

    def execute(self, params, context):
        with workspace_context(None, context.organization, context.user):
            return ToolResult(
                content="Scope observed",
                data={
                    "empty": workspace_scope_is_empty(),
                    "workspace": get_current_workspace(),
                    "user": get_current_user(),
                    "organization": get_current_organization(),
                    "objects": Dataset.objects.count(),
                    "unscoped": Dataset.no_workspace_objects.count(),
                },
            )


@pytest.mark.parametrize("ambient_empty", [False, True])
def test_native_scope_survives_nested_tools_and_restores_caller(
    scope_state, ambient_empty
):
    state = scope_state
    workspace = landing(state, state.member)
    assert workspace is None
    ambient_workspace = None if ambient_empty else state.private
    set_workspace_context(
        workspace=ambient_workspace,
        organization=state.org,
        user=state.owner,
        require_workspace=ambient_empty,
    )
    result = execute(ToolContext(state.member, state.org, workspace), ScopeProbe())
    assert not result.is_error, result
    assert result.data == {
        "empty": True,
        "workspace": None,
        "user": state.member,
        "organization": state.org,
        "objects": 0,
        "unscoped": 0,
    }
    assert get_current_workspace() == ambient_workspace
    assert get_current_user() == state.owner
    assert get_current_organization() == state.org
    assert workspace_scope_is_empty() is ambient_empty


@pytest.mark.parametrize("ambient_empty", [False, True])
def test_native_scope_restores_caller_after_exception(scope_state, ambient_empty):
    state = scope_state
    observed = []

    class RaisingTool:
        name = "raising_scope_probe"

        def run(self, params, context):
            observed.append(workspace_scope_is_empty())
            raise RuntimeError("synthetic tool failure")

    set_workspace_context(
        workspace=None,
        organization=state.org,
        user=state.owner,
        require_workspace=ambient_empty,
    )
    result = execute(ToolContext(state.member, state.org, None), RaisingTool())
    assert result.is_error
    assert "synthetic tool failure" in result.content
    assert observed == [True]
    assert get_current_workspace() is None
    assert get_current_user() == state.owner
    assert get_current_organization() == state.org
    assert workspace_scope_is_empty() is ambient_empty


@pytest.mark.parametrize("actor", ["owner", "member"])
def test_native_search_keeps_accessible_landing(scope_state, actor):
    state = scope_state
    user = getattr(state, actor)
    if actor == "member":
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=state.default,
            organization_membership=state.membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
    workspace = landing(state, user)
    assert workspace == state.default
    assert user.can_access_workspace(workspace)
    result = execute(
        ToolContext(user, state.org, workspace),
        SearchTool(),
        {"query": "native-scope-needle", "entity_types": ["datasets"]},
    )
    assert not result.is_error, result
    assert result.data["total"] == 1
    assert result.data["results"]["datasets"] == [
        {"id": str(state.visible.pk), "name": state.visible.name}
    ]
    assert str(state.hidden.pk) not in str(result.data)
    assert not workspace_scope_is_empty()
