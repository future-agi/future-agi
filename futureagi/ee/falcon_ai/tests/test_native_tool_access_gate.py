"""Cached Falcon contexts must pass live authorization before native dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests import workspace_write_support as write_support
from ai_tools.base import ToolContext
from ai_tools.tools.context.search import SearchTool
from ee.falcon_ai import consumers
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from ee.falcon_ai.tests.test_workspace_scope import execute
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    get_current_workspace,
    workspace_scope_is_empty,
)

scope_state = native_scope.scope_state
scope_no_egress = write_support.scope_no_egress
pytestmark = pytest.mark.django_db(transaction=True)
PARAMS = {"query": "native-scope-needle", "entity_types": ["datasets"]}


def interactive_context(user, organization, workspace):
    context = ToolContext(user, organization, workspace)
    # Assign after construction so the test-only overlay also runs on the
    # pre-flag product and fails behavior assertions, not constructor errors.
    context.interactive_falcon_socket = True
    return context


def granted_context(state):
    grant, _ = WorkspaceMembership.no_workspace_objects.update_or_create(
        user=state.member,
        workspace=state.private,
        defaults={
            "organization_membership": state.membership,
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
        },
    )
    context = interactive_context(state.member, state.org, state.private)
    result = execute(context, SearchTool(), PARAMS)
    assert not result.is_error, result
    assert str(state.hidden.pk) in str(result.data)
    return context, grant


@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "revocation",
    [
        "workspace-inactive-grant",
        "workspace-deleted-grant",
        "org-inactive-grant",
        "org-deleted-grant",
        "workspace-inactive",
        "workspace-deleted",
        "workspace-moved",
    ],
)
def test_cached_native_context_refuses_revocation(
    scope_no_egress, scope_state, monkeypatch, revocation, use_async
):
    state = scope_state
    context, grant = granted_context(state)
    if revocation.startswith("org-"):
        other_org = Organization.objects.create(name="Still active elsewhere")
        OrganizationMembership.no_workspace_objects.create(
            user=state.member,
            organization=other_org,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
        rows = OrganizationMembership._base_manager.filter(pk=state.membership.pk)
        if revocation == "org-inactive-grant":
            rows.update(is_active=False)
        else:
            rows.delete()
        # The cached object still points at the former organization; another
        # active org keeps this a live account, not a logged-out-user test.
        assert state.member.is_active
        assert state.member.can_access_organization(other_org)
    elif revocation == "workspace-inactive-grant":
        WorkspaceMembership._base_manager.filter(pk=grant.pk).update(is_active=False)
    elif revocation == "workspace-deleted-grant":
        WorkspaceMembership._base_manager.filter(pk=grant.pk).delete()
    elif revocation == "workspace-inactive":
        Workspace._base_manager.filter(pk=state.private.pk).update(is_active=False)
    elif revocation == "workspace-deleted":
        Workspace._base_manager.filter(pk=state.private.pk).update(deleted=True)
    else:
        other_org = Organization.objects.create(name="Moved workspace org")
        Workspace._base_manager.filter(pk=state.private.pk).update(
            organization=other_org
        )

    tool = SearchTool()
    body = Mock(wraps=tool.execute)
    monkeypatch.setattr(tool, "execute", body)
    result = execute(context, tool, PARAMS, use_async=use_async)
    assert result.is_error
    assert result.error_code == "workspace_access_revoked"
    assert str(state.hidden.pk) not in str(result.data)
    body.assert_not_called()
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()


@pytest.mark.parametrize("actor", ["member", "owner", "admin"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_authorized_native_context_executes(
    scope_no_egress, scope_state, actor, use_async
):
    state = scope_state
    if actor == "member":
        context, _ = granted_context(state)
    else:
        user = state.owner
        if actor == "admin":
            OrganizationMembership._base_manager.filter(user=user).update(
                role=OrganizationRoles.ADMIN, level=Level.ADMIN
            )
        assert not WorkspaceMembership._base_manager.filter(user=user).exists()
        context = interactive_context(user, state.org, state.private)
    result = execute(context, SearchTool(), PARAMS, use_async=use_async)
    assert not result.is_error, result
    assert result.data["total"] == 1
    assert str(state.hidden.pk) in str(result.data)


@pytest.mark.parametrize("revoked", [False, True])
def test_empty_native_context_checks_org_without_changing_binding(
    scope_no_egress, scope_state, monkeypatch, revoked
):
    state = scope_state
    context = interactive_context(state.member, state.org, None)
    if revoked:
        OrganizationMembership._base_manager.filter(pk=state.membership.pk).update(
            is_active=False
        )
    tool = SearchTool()
    body = Mock(wraps=tool.execute)
    monkeypatch.setattr(tool, "execute", body)
    result = execute(context, tool, PARAMS)
    if revoked:
        assert result.error_code == "workspace_access_revoked"
        body.assert_not_called()
    else:
        assert not result.is_error, result
        assert result.data["total"] == 0
        body.assert_called_once()
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()


@pytest.mark.parametrize("empty", [False, True], ids=["workspace", "empty"])
@pytest.mark.parametrize("deactivated", [False, True], ids=["active", "deactivated"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_native_dispatch_checks_live_user_status(
    scope_no_egress, scope_state, monkeypatch, empty, deactivated, use_async
):
    state = scope_state
    context, grant = granted_context(state)
    if empty:
        context = interactive_context(state.member, state.org, None)
    if deactivated:
        User._base_manager.filter(pk=state.member.pk).update(is_active=False)
    assert context.user.is_active is True  # Deliberately stale cached principal.
    assert OrganizationMembership._base_manager.get(pk=state.membership.pk).is_active
    assert WorkspaceMembership._base_manager.get(pk=grant.pk).is_active
    tool = SearchTool()
    body = Mock(wraps=tool.execute)
    monkeypatch.setattr(tool, "execute", body)
    result = execute(context, tool, PARAMS, use_async=use_async)
    if deactivated:
        assert result.is_error
        assert result.error_code == "workspace_access_revoked"
        body.assert_not_called()
    else:
        assert not result.is_error, result
        assert result.data["total"] == (0 if empty else 1)
        body.assert_called_once()
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()


@pytest.mark.parametrize("empty", [False, True])
def test_no_user_background_keeps_previous_behavior(
    scope_no_egress, scope_state, empty
):
    state = scope_state
    context = ToolContext(None, state.org, None if empty else state.private)
    result = execute(context, SearchTool(), PARAMS)
    assert not result.is_error, result
    assert result.data["total"] == (0 if empty else 1)


@pytest.mark.parametrize("revocation", ["no-workspace-grant", "removed-org"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
def test_background_user_without_access_keeps_previous_behavior(
    scope_no_egress, scope_state, monkeypatch, revocation, use_async
):
    state = scope_state
    if revocation == "removed-org":
        OrganizationMembership._base_manager.filter(pk=state.membership.pk).delete()
    assert not state.member.can_access_workspace(state.private)
    # Background jobs retain a user for Conversation ownership; neither the
    # presence of that user nor a headless option identifies a socket context.
    context = ToolContext(state.member, state.org, state.private)
    assert not getattr(context, "interactive_falcon_socket", False)
    tool = SearchTool()
    body = Mock(wraps=tool.execute)
    monkeypatch.setattr(tool, "execute", body)
    result = execute(context, tool, PARAMS, use_async=use_async)
    assert not result.is_error, result
    assert str(state.hidden.pk) in str(result.data)
    body.assert_called_once()
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()


def test_consumer_marks_interactive_context(scope_no_egress, scope_state, monkeypatch):
    state = scope_state
    consumer = consumers.FalconAIConsumer()
    consumer.user = state.member
    consumer.organization = state.org
    consumer.organization_id = state.org.pk
    consumer.workspace = state.private
    conversation = object()
    consumer._get_conversation_and_history = AsyncMock(return_value=(conversation, []))
    consumer.send_json = AsyncMock()
    stream = Mock()
    monkeypatch.setattr(consumers, "StreamBuffer", Mock(return_value=stream))
    factory = Mock()
    monkeypatch.setattr(consumers, "AgentLoop", factory)
    # Stop before LLM execution, but after the real consumer constructs and
    # passes its ToolContext to AgentLoop. No Redis/provider/billing calls.
    monkeypatch.setattr(
        consumers,
        "check_usage",
        Mock(return_value=SimpleNamespace(allowed=False, reason="test stop")),
    )
    async_to_sync(consumer._run_agent)("test-conversation", "hello", "develop")
    factory.assert_called_once()
    context = factory.call_args.kwargs["tool_context"]
    assert context.user is state.member
    assert context.organization is state.org
    assert context.workspace is state.private
    assert getattr(context, "interactive_falcon_socket", False) is True
    assert factory.call_args.kwargs["conversation"] is conversation
