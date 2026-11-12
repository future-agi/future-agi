"""OAuth tool grants are an upper bound on mutable connection policy.

Only synthetic tools and a mocked executor are used; no API handler is called.
"""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from mcp_server import generated_registry, mcp_app, oauth_utils, usage_helpers
from mcp_server.generated_registry import GeneratedToolRegistry


@pytest.fixture
def oauth_identity(monkeypatch, db):
    # Composed OAuth admission consults the real durable revocation authority.
    organization_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()
    organization = SimpleNamespace(pk=organization_id, id=organization_id)
    workspace = SimpleNamespace(
        pk=workspace_id, id=workspace_id, organization_id=organization_id
    )
    user = SimpleNamespace(
        pk=user_id,
        id=user_id,
        is_authenticated=True,
        is_active=True,
        can_access_workspace=Mock(return_value=True),
    )
    users = Mock()
    users.select_related.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr(User.objects, "all", Mock(return_value=users))
    monkeypatch.setattr(
        Organization.objects, "filter", Mock(return_value=Mock(first=lambda: organization))
    )
    monkeypatch.setattr(
        OrganizationMembership.no_workspace_objects,
        "filter",
        Mock(return_value=Mock(exists=lambda: True)),
    )
    monkeypatch.setattr(
        Workspace.no_workspace_objects,
        "filter",
        Mock(return_value=Mock(first=lambda: workspace)),
    )
    payload = {
        "type": "mcp_oauth",
        "user_id": str(user_id),
        "org_id": str(organization_id),
        "workspace_id": str(workspace_id),
        "scope": ["context"],
    }
    monkeypatch.setattr(oauth_utils, "decrypt_oauth_token", lambda unused: payload)
    mcp_app._clear_context()
    yield SimpleNamespace(
        payload=payload,
        user=user,
        organization=organization,
        workspace=workspace,
        authenticate=lambda: mcp_app._authenticate_via_oauth("mocked-token"),
    )
    mcp_app._clear_context()


@pytest.fixture
def tool_policy(monkeypatch):
    from mcp_server import rate_limiter
    from tfc.utils import tenant_scope

    tools = [
        SimpleNamespace(
            name=name,
            group=group,
            is_available=lambda: True,
            to_mcp_tool=lambda name=name: SimpleNamespace(name=name),
        )
        for name, group in [("context_probe", "context"), ("dataset_probe", "datasets")]
    ]
    registry = GeneratedToolRegistry(tools)
    monkeypatch.setattr(generated_registry, "registry", registry)
    monkeypatch.setattr(mcp_app, "registry", registry)
    config = SimpleNamespace(enabled_groups=["context", "datasets"], disabled_tools=[])
    connection = SimpleNamespace(tool_config=config)
    lookup = Mock(return_value=connection)
    create = Mock(return_value=connection)
    monkeypatch.setattr(usage_helpers, "get_connection", lookup)
    monkeypatch.setattr(usage_helpers, "get_or_create_connection", create)
    monkeypatch.setattr(tenant_scope, "acts_in_organization", lambda *args: True)
    session = Mock(return_value=SimpleNamespace())
    rate_check = Mock()
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(usage_helpers, "get_or_create_session", session)
    monkeypatch.setattr(usage_helpers, "record_usage", Mock())
    monkeypatch.setattr(usage_helpers, "update_session_counters", Mock())
    monkeypatch.setattr(rate_limiter, "get_rate_limit_tier", lambda unused: "test")
    monkeypatch.setattr(rate_limiter, "check_rate_limit", rate_check)
    monkeypatch.setattr(mcp_app.executor, "execute", execute)
    return SimpleNamespace(
        registry=registry,
        config=config,
        lookup=lookup,
        create=create,
        session=session,
        rate_check=rate_check,
        execute=execute,
    )


def _listed_names():
    return {tool.name for tool in async_to_sync(mcp_app.list_generated_tools)()}


def _assert_dataset_denied(policy):
    result = async_to_sync(mcp_app.call_generated_tool)("dataset_probe", {})
    assert result.isError
    assert result.structuredContent["error"]["code"] == "FORBIDDEN"
    policy.execute.assert_not_awaited()
    policy.session.assert_not_called()
    policy.rate_check.assert_not_called()


@pytest.mark.parametrize("scope", [["context"], "context"])
def test_oauth_scope_is_preserved_as_an_immutable_grant(oauth_identity, scope):
    oauth_identity.payload["scope"] = scope
    context = oauth_identity.authenticate()
    assert context is not None
    assert context.granted_tool_groups == frozenset({"context"})
    assert isinstance(context.granted_tool_groups, frozenset)
    if isinstance(scope, list):
        scope.append("datasets")
        assert context.granted_tool_groups == frozenset({"context"})
    with pytest.raises(FrozenInstanceError):
        context.granted_tool_groups = frozenset({"datasets"})


@pytest.mark.parametrize("existing_connection", [False, True])
def test_context_grant_limits_discovery_and_calls(
    oauth_identity, tool_policy, existing_connection
):
    if not existing_connection:
        tool_policy.lookup.return_value = None
    context = oauth_identity.authenticate()
    assert context is not None
    assert _listed_names() == {"context_probe"}
    tool_policy.create.assert_not_called()
    _assert_dataset_denied(tool_policy)

    # The allowed group still executes, through the mocked executor only.
    result = async_to_sync(mcp_app.call_generated_tool)("context_probe", {})
    assert not result.isError
    tool_policy.execute.assert_awaited_once_with(
        tool_policy.registry.get("context_probe"), {}, context
    )


@pytest.mark.parametrize("scope", [["context", "datasets"], "context datasets"])
def test_granted_dataset_group_can_execute(oauth_identity, tool_policy, scope):
    oauth_identity.payload["scope"] = scope
    context = oauth_identity.authenticate()
    assert context is not None
    assert _listed_names() == {"context_probe", "dataset_probe"}
    result = async_to_sync(mcp_app.call_generated_tool)("dataset_probe", {})
    assert not result.isError
    tool_policy.execute.assert_awaited_once_with(
        tool_policy.registry.get("dataset_probe"), {}, context
    )


def test_connection_edits_cannot_expand_a_bound_grant(oauth_identity, tool_policy):
    tool_policy.config.enabled_groups = ["context"]
    assert oauth_identity.authenticate() is not None
    assert _listed_names() == {"context_probe"}
    tool_policy.config.enabled_groups.append("datasets")
    assert _listed_names() == {"context_probe"}
    _assert_dataset_denied(tool_policy)


@pytest.mark.parametrize("restriction", ["group", "tool", "unavailable"])
def test_connection_restrictions_still_apply(oauth_identity, tool_policy, restriction):
    oauth_identity.payload["scope"] = ["context", "datasets"]
    if restriction == "group":
        tool_policy.config.enabled_groups = ["context"]
    elif restriction == "tool":
        tool_policy.config.disabled_tools = ["dataset_probe"]
    else:
        tool_policy.registry.get("dataset_probe").is_available = lambda: False
    assert oauth_identity.authenticate() is not None
    assert _listed_names() == {"context_probe"}
    _assert_dataset_denied(tool_policy)


@pytest.mark.parametrize("scope", [[], "", ["unknown-group"], "contextual"])
def test_empty_or_unknown_grants_allow_no_tools(oauth_identity, tool_policy, scope):
    oauth_identity.payload["scope"] = scope
    assert oauth_identity.authenticate() is not None
    assert _listed_names() == set()
    _assert_dataset_denied(tool_policy)


@pytest.mark.parametrize("scope", [None, False, 7, {"context": True}, ["context", None]])
def test_malformed_scope_is_not_authenticated(oauth_identity, scope):
    oauth_identity.payload["scope"] = scope
    assert oauth_identity.authenticate() is None


def test_missing_scope_is_not_authenticated(oauth_identity):
    del oauth_identity.payload["scope"]
    assert oauth_identity.authenticate() is None


def test_api_key_auth_keeps_connection_policy(oauth_identity, tool_policy, monkeypatch):
    from accounts import api_key_policy

    key = SimpleNamespace(
        user=oauth_identity.user,
        organization=oauth_identity.organization,
        workspace=oauth_identity.workspace,
    )
    monkeypatch.setattr(api_key_policy, "authenticate_api_key", lambda *args: key)
    context = mcp_app._authenticate_and_set_context("mocked-key", "mocked-secret")
    assert context is not None
    assert context.api_key is key
    assert _listed_names() == {"context_probe", "dataset_probe"}
    result = async_to_sync(mcp_app.call_generated_tool)("dataset_probe", {})
    assert not result.isError
    tool_policy.execute.assert_awaited_once_with(
        tool_policy.registry.get("dataset_probe"), {}, context
    )
