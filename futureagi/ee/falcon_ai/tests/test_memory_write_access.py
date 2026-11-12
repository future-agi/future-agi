"""Shared memories require current writer authority, not just socket admission."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests import workspace_write_support as write_support
from ai_tools.base import ToolResult
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.models import Conversation, FalconMemory
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from ee.falcon_ai.tests.test_native_tool_access_gate import interactive_context
from ee.falcon_ai.tools.context.delete_memory import DeleteMemoryTool
from ee.falcon_ai.tools.context.list_memories import ListMemoriesTool
from ee.falcon_ai.tools.context.save_memory import SaveMemoryTool
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

scope_state = native_scope.scope_state
scope_no_egress = write_support.scope_no_egress
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.parametrize("scope_state", ["absent"], indirect=True),
]
DENIED = ToolResult.permission_denied(
    "Workspace write access is required to change shared memories."
)
OPERATIONS = ["save-new", "overwrite", "delete"]
DISPATCHES = ["direct", "agent-sync", "agent-async"]


@pytest.fixture
def memory_state(scope_no_egress, scope_state):
    state = scope_state
    state.grant = WorkspaceMembership.no_workspace_objects.create(
        user=state.member,
        workspace=state.private,
        organization_membership=state.membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    state.conversation = Conversation.objects.create(
        user=state.member, organization=state.org, workspace=state.private
    )
    state.memory = FalconMemory.objects.create(
        workspace=state.private,
        organization=state.org,
        key="shared-setup",
        value="other writer's original value",
        source="user",
        created_by=state.owner,
    )
    state.context = interactive_context(state.member, state.org, state.private)
    assert state.member.can_write_to_workspace(state.private)
    return state


def snapshot():
    # Include every field, soft-deleted rows and all workspaces, so a scoped
    # empty query cannot conceal an insertion, overwrite or attribution change.
    return {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (
            FalconMemory,
            Conversation,
            OrganizationMembership,
            WorkspaceMembership,
        )
    }


def mutation(operation):
    if operation == "delete":
        return DeleteMemoryTool(), {"key": "shared-setup"}
    key = "new-memory" if operation == "save-new" else "shared-setup"
    return SaveMemoryTool(), {"key": key, "value": "replacement value"}


def dispatch(state, context, tool, params, route):
    if route == "direct":
        return tool.run(params, context)
    # Real native dispatch, with the existing actor-owned conversation. No
    # model, billing, Redis or consumer stream needs to execute for this sink.
    agent = SimpleNamespace(tool_context=context, conversation=state.conversation)
    method = AgentLoop.__dict__["_execute_tool"]
    if route == "agent-async":
        return async_to_sync(method)(agent, tool, params)
    return method.func(agent, tool, params)


def downgrade(state):
    WorkspaceMembership._base_manager.filter(pk=state.grant.pk).update(
        role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER
    )
    grant = WorkspaceMembership._base_manager.get(pk=state.grant.pk)
    membership = OrganizationMembership._base_manager.get(pk=state.membership.pk)
    current_user = User.objects.get(pk=state.member.pk)
    assert current_user.is_active and grant.is_active and membership.is_active
    assert grant.role == OrganizationRoles.WORKSPACE_VIEWER and grant.level == 1
    assert current_user.can_access_workspace(state.private)
    assert not current_user.can_write_to_workspace(state.private)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("route", DISPATCHES)
@pytest.mark.parametrize("fresh_context", [False, True], ids=["cached", "fresh"])
def test_viewer_cannot_mutate_shared_memory(
    memory_state, operation, route, fresh_context
):
    state = memory_state
    downgrade(state)
    context = state.context
    if fresh_context:
        context = interactive_context(
            User.objects.get(pk=state.member.pk),
            Organization.objects.get(pk=state.org.pk),
            Workspace.objects.get(pk=state.private.pk),
        )
    # A fresh turn can retain this owned conversation after the role downgrade.
    assert Conversation.objects.filter(
        pk=state.conversation.pk,
        user=context.user,
        workspace=context.workspace,
        organization=context.organization,
    ).exists()
    assert state.memory.created_by_id != context.user.pk
    before = snapshot()
    tool, params = mutation(operation)
    result = dispatch(state, context, tool, params, route)
    assert snapshot() == before
    assert result == DENIED


@pytest.mark.parametrize("route", DISPATCHES)
def test_viewer_can_still_list_shared_memories(memory_state, route):
    state = memory_state
    downgrade(state)
    before = snapshot()
    result = dispatch(state, state.context, ListMemoriesTool(), {}, route)
    assert not result.is_error, result
    assert result.data == {"count": 1}
    assert state.memory.key in result.content
    assert state.memory.value in result.content
    assert snapshot() == before


@pytest.mark.parametrize("actor", ["member", "workspace-admin", "org-admin"])
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("route", DISPATCHES)
def test_current_writer_can_mutate_shared_memory(memory_state, actor, operation, route):
    state = memory_state
    if actor == "workspace-admin":
        WorkspaceMembership._base_manager.filter(pk=state.grant.pk).update(
            role=OrganizationRoles.WORKSPACE_ADMIN, level=Level.WORKSPACE_ADMIN
        )
    elif actor == "org-admin":
        OrganizationMembership._base_manager.filter(pk=state.membership.pk).update(
            role=OrganizationRoles.ADMIN, level=Level.ADMIN
        )
        WorkspaceMembership._base_manager.filter(pk=state.grant.pk).delete()
    assert User.objects.get(pk=state.member.pk).can_write_to_workspace(state.private)
    tool, params = mutation(operation)
    result = dispatch(state, state.context, tool, params, route)
    assert not result.is_error, result
    rows = FalconMemory._base_manager.filter(
        workspace=state.private, organization=state.org
    )
    if operation == "delete":
        assert not rows.exists()
    else:
        memory = rows.get(key=params["key"])
        assert memory.value == params["value"]
        assert memory.created_by_id == state.member.pk
        assert memory.source == "agent"
        assert rows.count() == (2 if operation == "save-new" else 1)
        if operation == "save-new":
            state.memory.refresh_from_db()
            assert state.memory.value == "other writer's original value"
            assert state.memory.created_by_id == state.owner.pk


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "revocation",
    [
        "inactive-user",
        "inactive-workspace",
        "deleted-workspace",
        "moved-workspace",
        "inactive-org-membership",
        "inactive-workspace-membership",
        "missing-user",
        "missing-workspace",
        "missing-organization",
        "writer-check-error",
    ],
)
def test_direct_memory_sink_fails_closed(
    memory_state, monkeypatch, operation, revocation
):
    state = memory_state
    context = state.context
    if revocation == "inactive-user":
        User.objects.filter(pk=state.member.pk).update(is_active=False)
        assert context.user.is_active  # Cached principal is deliberately stale.
    elif revocation == "inactive-workspace":
        Workspace._base_manager.filter(pk=state.private.pk).update(is_active=False)
        assert context.workspace.is_active
    elif revocation == "deleted-workspace":
        Workspace._base_manager.filter(pk=state.private.pk).update(deleted=True)
    elif revocation == "moved-workspace":
        other_org = Organization.objects.create(name="Moved workspace")
        Workspace._base_manager.filter(pk=state.private.pk).update(
            organization=other_org
        )
    elif revocation == "inactive-org-membership":
        OrganizationMembership._base_manager.filter(pk=state.membership.pk).update(
            is_active=False
        )
    elif revocation == "inactive-workspace-membership":
        WorkspaceMembership._base_manager.filter(pk=state.grant.pk).update(
            is_active=False
        )
    elif revocation == "writer-check-error":
        monkeypatch.setattr(
            User,
            "can_write_to_workspace",
            Mock(side_effect=RuntimeError("test failure")),
        )
    else:
        setattr(context, revocation.removeprefix("missing-"), None)
    before = snapshot()
    tool, params = mutation(operation)
    result = tool.run(params, context)
    assert snapshot() == before
    assert result == DENIED
