"""N06-N08: live OAuth selectors and authenticated-empty MCP binding."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import save_config
from mcp_server import mcp_app
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.utils.workspace_scope import explicit_workspace_filter
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    workspace_context,
    workspace_scope_is_empty,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    mcp_app._clear_context()
    yield
    mcp_app._clear_context()


def oauth(user, **extra):
    # A valid binding-only grant explicitly consents to no tool groups.
    payload = {"type": "mcp_oauth", "user_id": str(user.pk), "scope": [], **extra}
    with patch(
        "mcp_server.oauth_utils.decrypt_oauth_token", return_value=payload
    ) as decrypt:
        result = mcp_app._authenticate_via_oauth("synthetic-token")
    decrypt.assert_called_once_with("synthetic-token")
    return result


def test_oauth_landing_uses_membership_org_and_read_only_preference(landing):
    stale = Organization.objects.create(name="Removed legacy org")
    OrganizationMembership.no_workspace_objects.create(
        user=landing.user, organization=stale, is_active=False
    )
    User._base_manager.filter(pk=landing.user.pk).update(organization=stale)
    save_config(
        landing, {"orgWorkspaceMap": {str(landing.org.pk): str(landing.other.pk)}}
    )
    before = {
        m.__name__: list(m._base_manager.order_by("pk").values())
        for m in (Workspace, WorkspaceMembership, OrganizationMembership, OrgApiKey)
    }
    from accounts.authentication import APIKeyAuthentication
    from mcp_server.api_executor import MCPRequestContext

    # Browser consent may select a landing before it issues an explicit grant.
    request = SimpleNamespace(
        headers={}, GET={}, method="POST", path="/mcp/oauth/consent/"
    )
    APIKeyAuthentication()._set_workspace_context(
        request, User.objects.get(pk=landing.user.pk)
    )
    assert request.organization == landing.org
    assert request.workspace == landing.other
    mcp_app._clear_context()
    assert oauth(landing.user) is None
    assert mcp_app._current_context() is None
    context = oauth(
        landing.user,
        org_id=str(request.organization.pk),
        workspace_id=str(request.workspace.pk),
    )
    assert context.organization == landing.org
    assert context.workspace == landing.other
    assert {
        m.__name__: list(m._base_manager.order_by("pk").values())
        for m in (Workspace, WorkspaceMembership, OrganizationMembership, OrgApiKey)
    } == before
    mcp_app._clear_context()
    User._base_manager.filter(pk=landing.user.pk).update(is_active=False)
    assert (
        oauth(
            landing.user, org_id=str(landing.org.pk), workspace_id=str(landing.other.pk)
        )
        is None
    )
    assert mcp_app._current_context() is None
    User._base_manager.filter(pk=landing.user.pk).update(is_active=True)
    OrganizationMembership._base_manager.filter(user=landing.user).update(
        is_active=False
    )
    assert (
        oauth(
            landing.user, org_id=str(landing.org.pk), workspace_id=str(landing.other.pk)
        )
        is None
    )
    assert oauth(landing.user, org_id=str(landing.org.pk), workspace_id=None) is None
    assert mcp_app._current_context() is None
    # Keep the internal empty-scope sentinel without admitting an empty bearer.
    context = mcp_app._bind_context(
        MCPRequestContext(user=landing.user, organization=None, workspace=None)
    )
    assert context.organization is None
    assert context.workspace is None
    assert workspace_scope_is_empty()
    with patch("mcp_server.usage_helpers.get_or_create_connection") as connection:
        assert mcp_app._enabled_tools_for_context(context) == (None, set())
    connection.assert_not_called()


@pytest.mark.parametrize(
    "state",
    [
        "malformed",
        "empty",
        "unknown",
        "foreign",
        "inactive",
        "deleted",
        "unjoined",
        "revoked",
    ],
)
def test_explicit_oauth_workspace_is_credential_binding_not_hint(landing, state):
    selected = landing.other
    value = str(selected.pk)
    if state == "malformed":
        value = "not-a-workspace-id"
    elif state == "empty":
        value = ""
    elif state == "unknown":
        value = str(uuid4())
    elif state == "foreign":
        other = Organization.objects.create(name="Foreign credential org")
        Workspace._base_manager.filter(pk=selected.pk).update(organization=other)
    elif state == "inactive":
        Workspace._base_manager.filter(pk=selected.pk).update(is_active=False)
    elif state == "deleted":
        Workspace._base_manager.filter(pk=selected.pk).update(deleted=True)
    elif state == "unjoined":
        selected = Workspace.objects.create(
            name="Unjoined", organization=landing.org, created_by=landing.owner
        )
        value = str(selected.pk)
    else:
        WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).update(
            is_active=False
        )
    save_config(landing, {"defaultWorkspaceId": str(landing.default.pk)})
    with patch.object(mcp_app, "_bind_context", wraps=mcp_app._bind_context) as bind:
        assert (
            oauth(landing.user, org_id=str(landing.org.pk), workspace_id=value) is None
        )
    bind.assert_not_called()
    assert mcp_app._current_context() is None
    valid = oauth(
        landing.user, org_id=str(landing.org.pk), workspace_id=str(landing.default.pk)
    )
    assert valid.workspace == landing.default
    mcp_app._clear_context()
    with patch.object(mcp_app, "_bind_context", wraps=mcp_app._bind_context) as bind:
        assert (
            oauth(landing.user, org_id=str(landing.org.pk), workspace_id=None) is None
        )
    bind.assert_not_called()
    assert mcp_app._current_context() is None


@pytest.mark.parametrize("transport", ["oauth", "personal-key"])
def test_mcp_binding_and_cleanup_preserve_authenticated_empty_scope(landing, transport):
    dataset = Dataset.objects.create(
        name="Pre-auth data", organization=landing.org, workspace=landing.other
    )
    row = Row.objects.create(dataset=dataset, order=0)
    data = Dataset.no_workspace_objects.filter(pk=dataset.pk)
    related = Row.no_workspace_objects.filter(pk=row.pk)
    assert data.exists() and related.exists()
    from mcp_server.api_executor import MCPRequestContext
    from mcp_server.models.connection import MCPConnection

    WorkspaceMembership._base_manager.filter(user=landing.user).update(is_active=False)
    before_connections = list(MCPConnection._base_manager.order_by("pk").values())
    with patch.object(mcp_app, "_bind_context", wraps=mcp_app._bind_context) as bind:
        if transport == "oauth":
            context = oauth(landing.user, org_id=str(landing.org.pk), workspace_id=None)
        else:
            key = OrgApiKey.no_workspace_objects.create(
                name="Explicit personal test key",
                type="user",
                user=landing.user,
                organization=landing.org,
            )
            context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
    assert context is None
    bind.assert_not_called()
    assert mcp_app._current_context() is None
    assert (
        list(MCPConnection._base_manager.order_by("pk").values()) == before_connections
    )
    # Exercise manager/nested-context safety directly, not by admitting a key
    # or bearer that lacks a workspace.
    context = mcp_app._bind_context(
        MCPRequestContext(user=landing.user, organization=landing.org, workspace=None)
    )
    with patch("mcp_server.usage_helpers.get_or_create_connection") as connection:
        assert mcp_app._enabled_tools_for_context(context) == (None, set())
    connection.assert_not_called()
    assert context is not None
    assert context.workspace is None
    assert workspace_scope_is_empty()
    assert not data.filter(explicit_workspace_filter(None)).exists()
    view = BaseModelViewSetMixin()
    view.queryset = related
    view.organization_lookup = "dataset__organization"
    view.request = SimpleNamespace(
        user=landing.user, organization=landing.org, workspace=None
    )
    assert not view.get_queryset().exists()
    with pytest.raises(RuntimeError, match="nested sentinel"):
        with workspace_context(None, landing.org, landing.user):
            assert workspace_scope_is_empty()
            assert not view.get_queryset().exists()
            raise RuntimeError("nested sentinel")
    assert workspace_scope_is_empty()
    assert OrganizationMembership.no_workspace_objects.filter(
        user=landing.user, is_active=True
    ).exists()
    mcp_app._clear_context()
    assert mcp_app._current_context() is None
    assert get_current_user() is None
    assert get_current_organization() is None
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()
    assert Dataset.objects.filter(pk=dataset.pk).exists()
    assert (
        oauth(
            landing.owner,
            org_id=str(landing.org.pk),
            workspace_id=str(landing.default.pk),
        ).workspace
        == landing.default
    )
    assert not workspace_scope_is_empty()


def test_oauth_explicit_foreign_workspace_refuses_even_when_actor_can_access_it(
    landing,
):
    foreign = Organization.objects.create(
        name="Accessible but not credential organization"
    )
    original_membership = landing.other_mem.organization_membership
    membership = OrganizationMembership.no_workspace_objects.create(
        user=landing.user,
        organization=foreign,
        role=original_membership.role,
        level=original_membership.level,
    )
    workspace = Workspace.objects.create(
        name="Accessible foreign workspace",
        organization=foreign,
        created_by=landing.owner,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=landing.user,
        workspace=workspace,
        organization_membership=membership,
        role=landing.other_mem.role,
        level=landing.other_mem.level,
    )
    save_config(
        landing,
        {
            "currentOrganizationId": str(landing.org.pk),
            "orgWorkspaceMap": {str(landing.org.pk): str(landing.default.pk)},
        },
    )
    assert landing.user.can_access_workspace(workspace)
    with patch.object(mcp_app, "_bind_context", wraps=mcp_app._bind_context) as bind:
        assert (
            oauth(
                landing.user, org_id=str(landing.org.pk), workspace_id=str(workspace.pk)
            )
            is None
        )
    bind.assert_not_called()
    assert mcp_app._current_context() is None
    assert oauth(landing.user) is None
    assert (
        oauth(
            landing.user,
            org_id=str(landing.org.pk),
            workspace_id=str(landing.default.pk),
        ).workspace
        == landing.default
    )


def test_oauth_shared_resolver_ignores_accessible_foreign_preference(landing):
    foreign = Organization.objects.create(name="Accessible foreign preference")
    local = landing.other_mem.organization_membership
    membership = OrganizationMembership.no_workspace_objects.create(
        user=landing.user,
        organization=foreign,
        role=local.role,
        level=local.level,
    )
    workspace = Workspace.objects.create(
        name="Foreign preferred workspace",
        organization=foreign,
        created_by=landing.owner,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=landing.user,
        workspace=workspace,
        organization_membership=membership,
        role=landing.other_mem.role,
        level=landing.other_mem.level,
    )
    save_config(
        landing,
        {
            "currentOrganizationId": str(landing.org.pk),
            "orgWorkspaceMap": {str(landing.org.pk): str(workspace.pk)},
            "currentWorkspaceId": str(workspace.pk),
            "defaultWorkspaceId": str(workspace.pk),
        },
    )
    assert landing.user.can_access_workspace(workspace)
    before = {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (Workspace, WorkspaceMembership, OrganizationMembership, OrgApiKey)
    }
    from accounts.authentication import APIKeyAuthentication

    request = SimpleNamespace(
        headers={"X-Organization-Id": str(landing.org.pk)},
        GET={},
        method="POST",
        path="/mcp/oauth/consent/",
    )
    APIKeyAuthentication()._set_workspace_context(request, landing.user)
    assert request.organization == landing.org
    assert request.workspace == landing.default
    mcp_app._clear_context()
    assert oauth(landing.user) is None
    assert mcp_app._current_context() is None
    context = oauth(
        landing.user,
        org_id=str(request.organization.pk),
        workspace_id=str(request.workspace.pk),
    )
    assert context.organization == landing.org
    assert context.workspace == landing.default
    assert {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (Workspace, WorkspaceMembership, OrganizationMembership, OrgApiKey)
    } == before
