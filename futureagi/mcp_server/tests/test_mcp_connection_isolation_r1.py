"""Connection isolation regressions, ported onto IR FINAL 651ab5bb (test file only).

This is the IR port of the dev-line MCP E module (r6). Every isolation assertion
of the dev module is kept; only fixtures, model-field values, refusal literals
and helper calls are adapted to the IR sources. Per-test docstrings still name
the dev-line expectation; the IR prediction and every adaptation, with IR
file:line evidence, are recorded in the packet STEP-RECORD (MCP-IR-RED-R3).
Registry/execution/rate-limit services are stubbed and approval uses an isolated
LocMemCache; connection queries, serializers, OAuth client/code validation, API
key sealing/lookup/admission and persistence use the real database. No
application runtime was executed in authoring.
"""

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.cache.backends.locmem import LocMemCache
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from mcp_server import usage_helpers
from mcp_server.admin import (
    MCPConnectionAdmin,
    MCPSessionAdmin,
    MCPToolGroupConfigAdmin,
)
from mcp_server.constants import DEFAULT_TOOL_GROUPS
from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.oauth_utils import generate_refresh_token, hash_client_secret
from mcp_server.serializers.connection import (
    MCPConnectionSerializer,
    MCPConnectionUpdateSerializer,
)
from mcp_server.serializers.contracts import (
    MCPConnectionResponseSerializer,
    MCPToolGroupsResponseSerializer,
)
from mcp_server.serializers.tool_config import (
    MCPToolGroupConfigSerializer,
    MCPToolGroupConfigUpdateSerializer,
)
from mcp_server.views import oauth, oauth_approve, transport
from mcp_server.views.config import MCPConfigView, MCPToolGroupsView
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_scope():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture
def peer(user):
    return type(user).objects.create(
        email=f"mcp-peer-{uuid4().hex}@example.invalid",
        organization=user.organization,
        is_active=True,
    )


@pytest.fixture
def other_org(user):
    return type(user.organization).objects.create(name=f"MCP other {uuid4().hex}")


@pytest.fixture
def other_workspace(user, workspace):
    return type(workspace).no_workspace_objects.create(
        name=f"MCP workspace {uuid4().hex}",
        organization=user.organization,
        created_by=user,
        is_default=False,
    )


@pytest.fixture
def tool(monkeypatch):
    value = SimpleNamespace(
        name="isolation_probe",
        group=DEFAULT_TOOL_GROUPS[0],
        is_available=lambda: True,
        to_discovery_dict=lambda: {"name": "isolation_probe"},
    )
    monkeypatch.setattr(transport.registry, "list_all", lambda: [value])
    monkeypatch.setattr(
        transport.registry, "get", lambda name: value if name == value.name else None
    )
    monkeypatch.setattr(transport, "get_rate_limit_tier", lambda organization: "test")
    monkeypatch.setattr(transport, "check_rate_limit", lambda *args: None)
    monkeypatch.setattr(
        transport.executor, "execute_sync", Mock(return_value={"ok": True})
    )
    return value


@pytest.fixture
def oauth_client(monkeypatch):
    # Generated local fixtures, never real credentials or values from configuration.
    secret = uuid4().hex
    client = MCPOAuthClient.objects.create(
        client_id=f"isolation-{uuid4().hex}",
        client_secret_hash=hash_client_secret(secret),
        name="Isolation regression",
        redirect_uris=["http://localhost/callback"],
    )
    monkeypatch.setattr(oauth, "record_invalid_token_attempt", lambda request: 0)
    monkeypatch.setattr(oauth, "clear_invalid_token_attempts", lambda request: None)
    return client, secret


def _request(method, path, user, workspace, data=None, organization=None):
    organization = organization if organization is not None else user.organization
    request = getattr(APIRequestFactory(), method)(path, data or {}, format="json")
    request.organization = organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    # IR adaptation: IR authentication always binds an explicit scope requirement
    # (accounts/authentication.py:434,464,511); force_authenticate skips that class,
    # so bind the same context here. A None workspace is then an empty scope.
    set_workspace_context(
        user=user,
        organization=organization,
        workspace=workspace,
        require_workspace=True,
    )
    return request


def _counts():
    # The base manager deliberately observes inserts hidden by an empty scope.
    return (
        MCPConnection._base_manager.count(),
        MCPToolGroupConfig._base_manager.count(),
    )


def _connection(user, workspace, organization=None):
    connection = MCPConnection._base_manager.create(
        user=user,
        organization=organization if organization is not None else user.organization,
        workspace=workspace,
    )
    if workspace is None:
        # Dev BaseModel.save may fill an ambient workspace during fixture creation.
        # This is fixture setup only: never repair the row after the tested action.
        updated = MCPConnection.no_workspace_objects.filter(pk=connection.pk).update(
            workspace=None
        )
        assert updated == 1
    persisted = MCPConnection._base_manager.get(pk=connection.pk)
    assert persisted.workspace_id == (workspace.pk if workspace is not None else None)
    assert persisted.organization_id == connection.organization_id
    assert persisted.user_id == user.pk
    return persisted


def _config(connection, **kwargs):
    return MCPToolGroupConfig._base_manager.create(connection=connection, **kwargs)


def _snapshot(connection):
    return (
        MCPConnection._base_manager.filter(pk=connection.pk)
        .values(
            "user_id",
            "organization_id",
            "workspace_id",
            "oauth_token_encrypted",
            "oauth_refresh_token_encrypted",
            "api_key_id",
        )
        .get()
    )


def _exchange(principal, workspace, client_pair, organization=None):
    client, secret = client_pair
    clear_workspace_context()  # Token exchange is anonymous, not workspace middleware auth.
    code = MCPOAuthCode.objects.create(
        code=uuid4().hex,
        client=client,
        user=principal,
        organization=organization
        if organization is not None
        else principal.organization,
        workspace=workspace,
        redirect_uri=client.redirect_uris[0],
        scope=list(DEFAULT_TOOL_GROUPS),
        state="",
    )
    code.refresh_from_db()
    assert code.workspace_id == (workspace.pk if workspace is not None else None)
    response = oauth.MCPOAuthTokenView()._handle_authorization_code(
        APIRequestFactory().post("/mcp/oauth/token/"),
        {
            "code": code.code,
            "client_id": client.client_id,
            "client_secret": secret,
            "redirect_uri": client.redirect_uris[0],
        },
    )
    return response, code


def _refresh(token, client_pair, *, ambient_workspace=None):
    client, secret = client_pair
    if ambient_workspace is None:
        clear_workspace_context()
    request = APIRequestFactory().post("/mcp/oauth/token/")
    view = oauth.MCPOAuthTokenView()
    if ambient_workspace is not None:
        # TF-001: check B immediately before entering the actual A-grant handler.
        assert get_current_workspace().pk == ambient_workspace.pk
        assert get_current_organization().pk == ambient_workspace.organization_id
        assert get_current_user() is not None
    return view._handle_refresh_token(
        request,
        {
            "refresh_token": token,
            "client_id": client.client_id,
            "client_secret": secret,
        },
    )


@pytest.mark.parametrize("has_workspace", [False, True])
def test_tool_list_get_never_creates_rows(user, workspace, tool, has_workspace):
    """RED at head: GET inserts a connection and tool config in both cases."""
    before = _counts()
    response = transport.MCPToolListView.as_view()(
        _request(
            "get", "/mcp/internal/tools/", user, workspace if has_workspace else None
        )
    )
    assert response.status_code == (200 if has_workspace else 403)
    assert _counts() == before


def test_tool_list_missing_config_does_not_backfill(user, workspace, tool):
    """RED at head: GET/get_enabled_tools backfills the missing config."""
    connection = _connection(user, workspace)
    before = _counts()
    response = transport.MCPToolListView.as_view()(
        _request("get", "/mcp/internal/tools/", user, workspace)
    )
    assert response.status_code == 200
    assert response.data["result"]["total"] == 1
    assert _counts() == before
    assert not MCPToolGroupConfig._base_manager.filter(connection=connection).exists()


def test_enabled_tools_is_read_only(user, workspace, tool):
    """RED at head: the shared reader inserts MCPToolGroupConfig."""
    connection = _connection(user, workspace)
    before = _counts()
    assert tool.name in usage_helpers.get_enabled_tools(connection)
    assert _counts() == before


def test_config_get_creates_only_owned_default_once(user, workspace):
    """R3 RED: dashboard GET must initialize the caller's exact scoped default."""
    before = _counts()
    response = MCPConfigView.as_view()(_request("get", "/mcp/config/", user, workspace))
    assert response.status_code == 200
    connection = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace, deleted=False
    )
    assert str(response.data["result"]["id"]) == str(connection.pk)
    assert connection.tool_config.enabled_groups == list(DEFAULT_TOOL_GROUPS)
    assert _counts() == (before[0] + 1, before[1] + 1)
    snapshot = _snapshot(connection)
    repeated = MCPConfigView.as_view()(_request("get", "/mcp/config/", user, workspace))
    assert repeated.status_code == 200
    assert str(repeated.data["result"]["id"]) == str(connection.pk)
    assert _counts() == (before[0] + 1, before[1] + 1)
    assert _snapshot(connection) == snapshot


@pytest.mark.parametrize("invalid_scope", ["workspace", "organization"])
def test_config_default_requires_consistent_scope(
    user, workspace, other_org, invalid_scope
):
    """Default initialization must not admit absent or cross-organization scope."""
    before = _counts()
    response = MCPConfigView.as_view()(
        _request(
            "get",
            "/mcp/config/",
            user,
            None if invalid_scope == "workspace" else workspace,
            organization=other_org if invalid_scope == "organization" else None,
        )
    )
    assert response.status_code == 403
    assert _counts() == before


def test_config_default_never_adopts_owners_legacy_row(user, workspace):
    """Dashboard initialization creates a scoped row without rewriting legacy NULL."""
    legacy = _connection(user, None)
    before = _snapshot(legacy)
    counts = _counts()
    response = MCPConfigView.as_view()(_request("get", "/mcp/config/", user, workspace))
    assert response.status_code == 200
    scoped = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace, deleted=False
    )
    assert scoped.pk != legacy.pk
    assert str(response.data["result"]["id"]) == str(scoped.pk)
    assert _snapshot(legacy) == before
    assert _counts() == (counts[0] + 1, counts[1] + 1)


@pytest.mark.parametrize("missing", ["user", "organization", "workspace"])
def test_creation_requires_all_three_scope_fields(user, workspace, missing):
    """RED at head: absent workspace inserts; absent FK is an unhandled DB error."""
    scope = {"user": user, "organization": user.organization, "workspace": workspace}
    scope[missing] = None
    before = _counts()
    with pytest.raises(PermissionDenied):
        usage_helpers.get_or_create_connection(**scope)
    assert _counts() == before


def test_creation_rejects_workspace_organization_mismatch(user, workspace, other_org):
    """RED at head: explicit conflicting workspace/organization is persisted."""
    before = _counts()
    with pytest.raises(PermissionDenied):
        usage_helpers.get_or_create_connection(user, other_org, workspace)
    assert _counts() == before


def test_helper_never_reuses_wrong_organization_row(user, workspace, other_org):
    """RED at head: organization is only a creation default, not a lookup key."""
    connection = _connection(user, workspace, other_org)
    before = _snapshot(connection)
    with pytest.raises(PermissionDenied):
        usage_helpers.get_or_create_connection(user, user.organization, workspace)
    assert _snapshot(connection) == before


def test_owner_put_creates_then_lists_and_uses_own_connection(user, workspace, tool):
    """RED at head: PUT returns 404 without a prior mutating GET; positive use control."""
    response = MCPConfigView.as_view()(
        _request("put", "/mcp/config/", user, workspace, {"connection_mode": "stdio"})
    )
    assert response.status_code == 200
    connection = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace, deleted=False
    )
    assert connection.workspace_id == workspace.pk
    assert connection.organization_id == user.organization_id
    assert connection.user_id == user.pk
    read = MCPConfigView.as_view()(_request("get", "/mcp/config/", user, workspace))
    assert read.status_code == 200
    assert str(read.data["result"]["id"]) == str(connection.pk)
    listed = transport.MCPToolListView.as_view()(
        _request("get", "/mcp/internal/tools/", user, workspace)
    )
    assert listed.data["result"]["total"] == 1
    called = transport.MCPToolCallView.as_view()(
        _request(
            "post",
            "/mcp/internal/tool-call/",
            user,
            workspace,
            {"tool_name": tool.name},
        )
    )
    assert called.status_code == 200
    session = MCPSession.objects.get(pk=called.data["session_id"])
    assert session.connection_id == connection.pk
    assert session.user_id == user.pk
    assert session.organization_id == user.organization_id
    assert session.workspace_id == workspace.pk


@pytest.mark.parametrize(
    "view,path",
    [
        (MCPConfigView, "/mcp/config/"),
        (MCPToolGroupsView, "/mcp/config/tool-groups/"),
    ],
)
def test_management_reads_do_not_cross_organizations(
    user, workspace, other_org, view, path
):
    """RED at head: user/workspace-only queries expose the conflicting-org row."""
    connection = _connection(user, workspace, other_org)
    _config(connection, enabled_groups=["owner-only-marker"])
    before = _snapshot(connection)
    counts = _counts()
    response = view.as_view()(_request("get", path, user, workspace))
    if view is MCPConfigView:
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert "owner-only-marker" not in response.data["result"]["enabled_groups"]
    assert _snapshot(connection) == before
    assert _counts() == counts


@pytest.mark.parametrize("legacy", [False, True])
def test_same_org_peer_cannot_read_list_or_use_owner_connection(
    user, peer, workspace, tool, legacy
):
    """Peer defaults and calls use only U2's row; U1's scoped/legacy state is immutable."""
    type(workspace).no_workspace_objects.filter(pk=workspace.pk).update(is_default=True)
    workspace.refresh_from_db()
    assert workspace.is_default
    owner = _connection(user, None if legacy else workspace)
    _config(owner, disabled_tools=[tool.name])
    own_state = _snapshot(owner)
    counts = _counts()
    read = MCPConfigView.as_view()(_request("get", "/mcp/config/", peer, workspace))
    assert read.status_code == 200
    peer_connection = MCPConnection._base_manager.get(
        user=peer, organization=peer.organization, workspace=workspace, deleted=False
    )
    assert peer_connection.pk != owner.pk
    assert str(read.data["result"]["id"]) == str(peer_connection.pk)
    assert peer_connection.tool_config.disabled_tools == []
    assert _snapshot(owner) == own_state
    assert _counts() == (counts[0] + 1, counts[1] + 1)
    counts = _counts()
    listed = transport.MCPToolListView.as_view()(
        _request("get", "/mcp/internal/tools/", peer, workspace)
    )
    assert listed.status_code == 200
    assert listed.data["result"]["total"] == 1  # Not U1's disabled config.
    assert _counts() == counts
    response = transport.MCPToolCallView.as_view()(
        _request(
            "post",
            "/mcp/internal/tool-call/",
            peer,
            workspace,
            {"tool_name": tool.name},
        )
    )
    assert response.status_code == 200
    session = MCPSession.objects.get(pk=response.data["session_id"])
    assert session.connection_id != owner.pk
    assert session.connection_id == peer_connection.pk
    assert session.user_id == peer.pk
    assert session.organization_id == peer.organization_id
    assert session.workspace_id == workspace.pk
    assert _snapshot(owner) == own_state


def test_owner_disabled_tool_control(mcp_connection, user, workspace, tool):
    """Head PASS control: U1's own config still controls discovery and execution."""
    config = mcp_connection.tool_config
    config.disabled_tools = [tool.name]
    config.save(update_fields=["disabled_tools"])
    listed = transport.MCPToolListView.as_view()(
        _request("get", "/mcp/internal/tools/", user, workspace)
    )
    assert listed.data["result"]["total"] == 0
    called = transport.MCPToolCallView.as_view()(
        _request(
            "post",
            "/mcp/internal/tool-call/",
            user,
            workspace,
            {"tool_name": tool.name},
        )
    )
    assert called.status_code == 403
    transport.executor.execute_sync.assert_not_called()


def test_streamable_discovery_does_not_create_connection(user, workspace, tool):
    """RED at head: streamable tools/list's shared context helper creates rows."""
    from mcp_server import mcp_app
    from mcp_server.api_executor import MCPRequestContext

    set_workspace_context(
        user=user, organization=user.organization, workspace=workspace
    )
    before = _counts()
    connection, names = mcp_app._enabled_tools_for_context(
        MCPRequestContext(
            user=user, organization=user.organization, workspace=workspace
        )
    )
    assert connection is None
    assert tool.name in names
    assert _counts() == before


@pytest.mark.parametrize(
    "handler",
    ["_handle_authorization_code", "_handle_refresh_token"],
)
def test_oauth_registry_failure_precedes_transaction(
    monkeypatch, django_db_blocker, handler
):
    """R3 RED for code exchange: registry failure must not open a transaction first."""
    from django.db import DatabaseError

    registry_get = Mock(side_effect=DatabaseError("registry unavailable"))
    monkeypatch.setattr(oauth.MCPOAuthClient.objects, "get", registry_get)
    clear_attempts = Mock()
    monkeypatch.setattr(oauth, "clear_invalid_token_attempts", clear_attempts)
    data = {
        "code": uuid4().hex,
        "refresh_token": uuid4().hex,
        "client_id": uuid4().hex,
        "client_secret": uuid4().hex,
    }
    request = APIRequestFactory().post("/mcp/oauth/token/")
    with django_db_blocker.block():
        response = getattr(oauth.MCPOAuthTokenView(), handler)(request, data)
    registry_get.assert_called_once_with(client_id=data["client_id"], is_active=True)
    clear_attempts.assert_not_called()
    assert response.status_code == 503
    assert response.data == {
        "error": "server_error",
        "error_description": "OAuth client registry unavailable",
    }


def test_oauth_rejects_null_workspace_without_overwriting_legacy(user, oauth_client):
    """RED at head: NULL-workspace auth code reuses the existing legacy row."""
    legacy = _connection(user, None)
    before = _snapshot(legacy)
    counts = _counts()
    response, code = _exchange(user, None, oauth_client)
    assert response.status_code == 400
    assert _snapshot(legacy) == before
    assert _counts() == counts
    code.refresh_from_db()
    assert code.used is False


def test_oauth_never_reuses_conflicting_organization(
    user, workspace, other_org, oauth_client
):
    """RED at head: matching user/workspace row in another org is overwritten."""
    connection = _connection(user, workspace, other_org)
    before = _snapshot(connection)
    response, _ = _exchange(user, workspace, oauth_client)
    assert response.status_code == 400
    assert _snapshot(connection) == before


def test_oauth_rejects_code_with_workspace_from_another_org(
    user, workspace, other_org, oauth_client
):
    """RED at head: code's workspace and organization are not checked for consistency."""
    before = _counts()
    response, _ = _exchange(user, workspace, oauth_client, organization=other_org)
    assert response.status_code == 400
    assert _counts() == before


def test_u2_oauth_does_not_overwrite_u1_or_null_rows(
    user, peer, workspace, oauth_client
):
    """Head PASS ownership control: user discriminator must survive the org fix."""
    owner = _connection(user, workspace)
    legacy = _connection(peer, None)
    snapshots = [_snapshot(owner), _snapshot(legacy)]
    response, _ = _exchange(peer, workspace, oauth_client)
    assert response.status_code == 200
    bound = MCPConnection._base_manager.get(
        user=peer, organization=peer.organization, workspace=workspace, deleted=False
    )
    assert bound.pk not in (owner.pk, legacy.pk)
    assert bound.oauth_token_encrypted == response.data["access_token"]
    assert bound.oauth_refresh_token_encrypted == response.data["refresh_token"]
    assert [_snapshot(owner), _snapshot(legacy)] == snapshots


def test_owner_oauth_and_refresh_remain_bound_to_exact_row(
    user, workspace, oauth_client
):
    """Head PASS positive control: code and refresh update U1's exact scoped row."""
    owner = _connection(user, workspace)
    response, _ = _exchange(user, workspace, oauth_client)
    assert response.status_code == 200
    refreshed = _refresh(response.data["refresh_token"], oauth_client)
    assert refreshed.status_code == 200
    persisted = MCPConnection._base_manager.get(pk=owner.pk)
    assert persisted.oauth_token_encrypted == refreshed.data["access_token"]
    assert (persisted.user_id, persisted.organization_id, persisted.workspace_id) == (
        user.pk,
        user.organization_id,
        workspace.pk,
    )


@pytest.mark.parametrize("mismatch", ["organization", "null_workspace", "user"])
def test_refresh_rejects_foreign_or_unbound_connection(
    user, peer, workspace, other_org, oauth_client, mismatch
):
    """Head RED for organization/NULL workspace; user mismatch is a PASS control."""
    client, _ = oauth_client
    token = generate_refresh_token(user.pk, user.organization_id, client.client_id)
    connection = _connection(
        peer if mismatch == "user" else user,
        None if mismatch == "null_workspace" else workspace,
        other_org if mismatch == "organization" else user.organization,
    )
    MCPConnection._base_manager.filter(pk=connection.pk).update(
        oauth_refresh_token_encrypted=token
    )
    before = _snapshot(connection)
    response = _refresh(token, oauth_client)
    assert response.status_code == 400
    assert _snapshot(connection) == before


def test_refresh_uses_stored_token_binding_not_current_workspace(
    user, workspace, other_workspace, oauth_client
):
    """Head PASS control: a refresh for workspace A must not alter workspace B."""
    first, _ = _exchange(user, workspace, oauth_client)
    second, _ = _exchange(user, other_workspace, oauth_client)
    assert first.status_code == second.status_code == 200
    other = MCPConnection._base_manager.get(user=user, workspace=other_workspace)
    before = _snapshot(other)
    set_workspace_context(
        user=user, organization=user.organization, workspace=other_workspace
    )
    refreshed = _refresh(
        first.data["refresh_token"], oauth_client, ambient_workspace=other_workspace
    )
    assert refreshed.status_code == 200
    assert get_current_workspace().pk == other_workspace.pk
    assert get_current_user().pk == user.pk
    assert _snapshot(other) == before
    target = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace
    )
    assert target.oauth_token_encrypted == refreshed.data["access_token"]
    assert oauth.decrypt_oauth_token(refreshed.data["access_token"])[
        "workspace_id"
    ] == str(workspace.pk)


def _assert_no_credentials(value):
    forbidden = {
        "oauth_token_encrypted",
        "oauth_refresh_token_encrypted",
        "api_key",
        "api_key_id",
    }
    if isinstance(value, dict):
        assert not forbidden.intersection(value)
        for nested in value.values():
            _assert_no_credentials(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_credentials(nested)


def test_connection_and_config_serializers_never_expose_credentials(mcp_connection):
    """Head PASS allowlist control, including nested response and update serializers."""
    MCPConnection._base_manager.filter(pk=mcp_connection.pk).update(
        oauth_token_encrypted=uuid4().hex,
        oauth_refresh_token_encrypted=uuid4().hex,
    )
    mcp_connection = MCPConnection._base_manager.get(pk=mcp_connection.pk)
    config = mcp_connection.tool_config
    for serializer in (
        MCPConnectionSerializer(mcp_connection),
        MCPConnectionUpdateSerializer(mcp_connection),
        MCPToolGroupConfigSerializer(config),
        MCPToolGroupConfigUpdateSerializer(config),
        MCPConnectionResponseSerializer({"status": True, "result": mcp_connection}),
        MCPToolGroupsResponseSerializer({"status": True, "result": config}),
    ):
        _assert_no_credentials(serializer.data)
        _assert_no_credentials(dict.fromkeys(serializer.fields))


@pytest.mark.parametrize("legacy", [False, True])
def test_admin_connection_and_related_readers_are_owner_only(
    user, peer, workspace, legacy
):
    """RED at head: default-workspace admin lists U1's connection/config/session."""
    type(workspace).no_workspace_objects.filter(pk=workspace.pk).update(is_default=True)
    workspace.refresh_from_db()
    owner = _connection(user, None if legacy else workspace)
    config = _config(owner)
    session = MCPSession.objects.create(
        connection=owner,
        user=user,
        organization=user.organization,
        workspace=owner.workspace,
    )
    request = _request("get", "/admin/", peer, workspace)
    request.user = peer
    site = AdminSite()
    for model, model_admin, row in (
        (MCPConnection, MCPConnectionAdmin, owner),
        (MCPToolGroupConfig, MCPToolGroupConfigAdmin, config),
        (MCPSession, MCPSessionAdmin, session),
    ):
        bound = model_admin(model, site)
        assert not bound.get_queryset(request).filter(pk=row.pk).exists()
        assert bound.get_object(request, str(row.pk)) is None
        owner_request = _request("get", "/admin/", user, workspace)
        owner_request.user = user
        assert bound.get_queryset(owner_request).filter(pk=row.pk).exists()


def test_admin_hides_credentials_and_forbids_reassignment(
    mcp_connection, user, workspace
):
    """RED at head: autogenerated admin form includes credentials and editable ownership."""
    request = _request("get", "/admin/", user, workspace)
    request.user = user
    bound = MCPConnectionAdmin(MCPConnection, AdminSite())
    fields = bound.get_fields(request, mcp_connection)
    assert not {
        "oauth_token_encrypted",
        "oauth_refresh_token_encrypted",
        "api_key",
    }.intersection(fields)
    assert bound.has_add_permission(request) is False
    assert bound.has_change_permission(request, mcp_connection) is False
    assert bound.has_delete_permission(request, mcp_connection) is False


def test_group_put_rejects_foreign_org_connection(user, workspace, other_org):
    """RED at head: tool-group PUT mutates a same-user/workspace foreign-org config."""
    connection = _connection(user, workspace, other_org)
    config = _config(connection)
    before = list(config.disabled_tools)
    response = MCPToolGroupsView.as_view()(
        _request(
            "put",
            "/mcp/config/tool-groups/",
            user,
            workspace,
            {"disabled_tools": ["foreign-change"]},
        )
    )
    assert response.status_code == 403
    config.refresh_from_db()
    assert config.disabled_tools == before


def test_tool_call_without_workspace_creates_nothing(user, tool):
    """RED at head when handler reached directly: POST creates a NULL connection."""
    before = _counts()
    response = transport.MCPToolCallView.as_view()(
        _request(
            "post", "/mcp/internal/tool-call/", user, None, {"tool_name": tool.name}
        )
    )
    assert response.status_code == 403
    assert _counts() == before
    transport.executor.execute_sync.assert_not_called()


def test_streamable_oauth_cannot_rebind_token_to_another_org(
    user, workspace, other_org
):
    """RED at head: OAuth payload org_id is ignored when binding the current tenant."""
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    token, _ = generate_oauth_token(
        user.pk, other_org.pk, workspace.pk, "isolation", list(DEFAULT_TOOL_GROUPS)
    )
    try:
        assert mcp_app._authenticate_via_oauth(token) is None
    finally:
        mcp_app._clear_context()


def test_admin_rejects_same_user_wrong_org(user, workspace, other_org):
    """RED at head: staff default manager does not enforce connection.organization."""
    connection = _connection(user, workspace, other_org)
    request = _request("get", "/admin/", user, workspace)
    request.user = user
    bound = MCPConnectionAdmin(MCPConnection, AdminSite())
    assert not bound.get_queryset(request).filter(pk=connection.pk).exists()


def test_owner_legacy_row_is_not_reassigned_on_put(user, workspace):
    """Head PASS on tool-group PUT: a workspace-specific write must leave NULL rows alone."""
    legacy = _connection(user, None)
    before = _snapshot(legacy)
    response = MCPToolGroupsView.as_view()(
        _request(
            "put",
            "/mcp/config/tool-groups/",
            user,
            workspace,
            {"disabled_tools": ["owner-only"]},
        )
    )
    assert response.status_code == 200
    scoped = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace, deleted=False
    )
    assert scoped.pk != legacy.pk
    assert scoped.workspace_id == workspace.pk
    assert _snapshot(legacy) == before


def _assert_local_workspace_refusal(view_class, request):
    # Enter the real decorated handler without global auth/permission guards.
    # The separate as_view calls below also exercise the route's DRF dispatch.
    view = view_class()
    drf_request = view.initialize_request(request)
    with pytest.raises(PermissionDenied) as caught:
        getattr(view, request.method.lower())(drf_request)
    assert caught.value.get_codes() == "workspace_required"
    assert str(caught.value.detail) == "Workspace context is required"


@pytest.mark.parametrize("omit_attribute", [False, True])
@pytest.mark.parametrize(
    "view_class,method,path",
    [
        (transport.MCPToolCallView, "post", "/mcp/internal/tool-call/"),
        (transport.MCPToolListView, "get", "/mcp/internal/tools/"),
    ],
)
def test_transport_no_workspace_refuses_before_connection_use(
    user, tool, omit_attribute, view_class, method, path
):
    """Dev RED: NULL-workspace requests create/use a legacy connection, not a typed refusal."""
    legacy = _connection(user, None)
    _config(legacy, disabled_tools=[tool.name])
    before = _snapshot(legacy)
    counts = _counts()
    sessions = MCPSession._base_manager.count()

    def request():
        value = _request(method, path, user, None, {"tool_name": tool.name})
        if omit_attribute:
            del value.workspace
        return value

    response = view_class.as_view()(request())
    assert response.status_code == 403
    _assert_local_workspace_refusal(view_class, request())
    assert _snapshot(legacy) == before
    assert _counts() == counts
    assert MCPSession._base_manager.count() == sessions
    transport.executor.execute_sync.assert_not_called()


@pytest.mark.parametrize("omit_attribute", [False, True])
@pytest.mark.parametrize("approved", [False, True])
def test_consent_no_workspace_refuses_before_code_issue(
    user, oauth_client, omit_attribute, approved
):
    """Dev RED: consent issues an unbound code or accepts denial without local scope refusal."""
    client, _ = oauth_client
    legacy = _connection(user, None)
    before = _snapshot(legacy)
    counts = _counts()
    codes = MCPOAuthCode._base_manager.count()
    data = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "approved": approved,
        "selected_groups": list(DEFAULT_TOOL_GROUPS),
    }

    def request():
        value = _request("post", "/mcp/oauth/consent/", user, None, data)
        if omit_attribute:
            del value.workspace
        return value

    response = oauth.MCPOAuthConsentView.as_view()(request())
    assert response.status_code == 403
    _assert_local_workspace_refusal(oauth.MCPOAuthConsentView, request())
    assert MCPOAuthCode._base_manager.count() == codes
    assert _counts() == counts
    assert _snapshot(legacy) == before


def test_owner_consent_issues_exact_scope_code_then_exchanges(
    user, workspace, oauth_client
):
    """Dev PASS control: real consent and exchange bind the same owner/org/workspace."""
    client, secret = oauth_client
    counts = _counts()
    response = oauth.MCPOAuthConsentView.as_view()(
        _request(
            "post",
            "/mcp/oauth/consent/",
            user,
            workspace,
            {
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "approved": True,
                "selected_groups": list(DEFAULT_TOOL_GROUPS),
            },
        )
    )
    assert response.status_code == 200
    code_value = parse_qs(urlsplit(response.data["result"]["redirect_url"]).query)[
        "code"
    ][0]
    code = MCPOAuthCode._base_manager.get(code=code_value)
    assert (code.user_id, code.organization_id, code.workspace_id) == (
        user.pk,
        user.organization_id,
        workspace.pk,
    )
    assert code.used is False
    assert _counts() == counts  # Consent issues a code, not a connection.
    clear_workspace_context()
    exchanged = oauth.MCPOAuthTokenView()._handle_authorization_code(
        APIRequestFactory().post("/mcp/oauth/token/"),
        {
            "code": code_value,
            "client_id": client.client_id,
            "client_secret": secret,
            "redirect_uri": client.redirect_uris[0],
        },
    )
    assert exchanged.status_code == 200
    bound = MCPConnection._base_manager.get(
        user=user, organization=user.organization, workspace=workspace, deleted=False
    )
    assert bound.oauth_token_encrypted == exchanged.data["access_token"]
    code.refresh_from_db()
    assert code.used is True


@pytest.fixture
def pending_approval(monkeypatch):
    # Exercise real cached approval/code data without any shared cache backend.
    store = LocMemCache(f"mcp-isolation-{uuid4().hex}", {})
    monkeypatch.setattr(oauth_approve, "cache", store)
    request_id = uuid4().hex
    key = f"{oauth_approve.APPROVE_PREFIX}{request_id}"
    pending = {
        "client_id": f"approval-{uuid4().hex}",
        "redirect_uri": "http://localhost/callback",
        "code_challenge": "a" * 43,
        "scopes": list(DEFAULT_TOOL_GROUPS),
        "state": "isolation-control",
    }
    store.set(key, pending, 60)
    monkeypatch.setattr(store, "set", Mock(wraps=store.set))
    monkeypatch.setattr(store, "delete", Mock(wraps=store.delete))
    yield store, request_id, key, pending
    store.clear()


@pytest.mark.parametrize("omit_attribute", [False, True])
@pytest.mark.parametrize("approved", [False, True])
def test_approval_never_falls_back_to_global_workspace(
    user, pending_approval, omit_attribute, approved
):
    """Dev RED: a non-empty global dummy workspace substitutes for absent caller scope."""
    store, request_id, key, pending = pending_approval
    dummy = SimpleNamespace(id=uuid4(), organization_id=user.organization_id)
    counts = _counts()
    codes = MCPOAuthCode._base_manager.count()

    def request():
        value = _request(
            "post",
            "/mcp/oauth/approve/",
            user,
            None,
            {
                "request_id": request_id,
                "approved": approved,
            },
        )
        if omit_attribute:
            del value.workspace
        return value

    # Never inspect any deployment setting; this is a locally constructed value.
    with override_settings(CURRENT_WORKSPACE=dummy):
        response = oauth_approve.MCPOAuthApproveView.as_view()(request())
        assert response.status_code == 403
        _assert_local_workspace_refusal(oauth_approve.MCPOAuthApproveView, request())
    assert store.get(key) == pending
    store.set.assert_not_called()
    store.delete.assert_not_called()
    assert MCPOAuthCode._base_manager.count() == codes
    assert _counts() == counts


def test_approval_uses_only_callers_resolved_workspace(
    user, workspace, pending_approval
):
    """Dev PASS control: a conflicting global dummy never overrides caller workspace."""
    store, request_id, key, _ = pending_approval
    dummy = SimpleNamespace(id=uuid4(), organization_id=user.organization_id)
    assert dummy.id != workspace.pk
    counts = _counts()
    with override_settings(CURRENT_WORKSPACE=dummy):
        response = oauth_approve.MCPOAuthApproveView.as_view()(
            _request(
                "post",
                "/mcp/oauth/approve/",
                user,
                workspace,
                {
                    "request_id": request_id,
                    "approved": True,
                    "selected_groups": list(DEFAULT_TOOL_GROUPS),
                },
            )
        )
    assert response.status_code == 200
    code = parse_qs(urlsplit(response.data["result"]["redirect_url"]).query)["code"][0]
    bound = store.get(f"{oauth_approve.CODE_PREFIX}{code}")
    assert bound["user_id"] == str(user.pk)
    assert bound["organization_id"] == str(user.organization_id)
    assert bound["workspace_id"] == str(workspace.pk)
    assert bound["workspace_id"] != str(dummy.id)
    assert store.get(key) is None
    store.set.assert_called_once()
    store.delete.assert_called_once_with(key)
    assert _counts() == counts


def test_approval_rejects_workspace_from_another_org(
    user, workspace, other_org, pending_approval
):
    """Dev RED: approval persists an inconsistent organization/workspace grant."""
    store, request_id, key, pending = pending_approval
    response = oauth_approve.MCPOAuthApproveView.as_view()(
        _request(
            "post",
            "/mcp/oauth/approve/",
            user,
            workspace,
            {
                "request_id": request_id,
                "approved": True,
            },
            organization=other_org,
        )
    )
    assert response.status_code == 403
    assert store.get(key) == pending
    store.set.assert_not_called()
    store.delete.assert_not_called()


@pytest.mark.parametrize("binding", ["missing", "nonexistent"])
def test_streamable_oauth_never_falls_back_from_token_workspace(
    user, workspace, binding
):
    """Dev RED: missing/unresolved bearer workspace is rebound to the default workspace."""
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    type(workspace).no_workspace_objects.filter(pk=workspace.pk).update(is_default=True)
    workspace.refresh_from_db()
    token, _ = generate_oauth_token(
        user.pk,
        user.organization_id,
        None if binding == "missing" else uuid4(),
        "isolation",
        list(DEFAULT_TOOL_GROUPS),
    )
    counts = _counts()
    # Match the ASGI entry point: fixture scope is not an authenticated request.
    mcp_app._clear_context()
    assert mcp_app._current_context() is None
    try:
        assert mcp_app._authenticate_via_oauth(token) is None
        assert mcp_app._current_context() is None
        assert _counts() == counts
    finally:
        mcp_app._clear_context()


def test_streamable_owner_oauth_control(user, workspace):
    """Dev PASS control: an exact owner/org/workspace bearer authenticates without inserts."""
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    token, _ = generate_oauth_token(
        user.pk,
        user.organization_id,
        workspace.pk,
        "isolation",
        list(DEFAULT_TOOL_GROUPS),
    )
    counts = _counts()
    try:
        context = mcp_app._authenticate_via_oauth(token)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            user.pk,
            user.organization_id,
            workspace.pk,
        )
        assert _counts() == counts
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("missing", ["user", "organization", "workspace"])
def test_streamable_context_without_scope_exposes_no_tools(
    user, workspace, tool, missing
):
    """Dev RED: no local discovery guard; missing scope reaches creation or a DB error."""
    from mcp_server import mcp_app
    from mcp_server.api_executor import MCPRequestContext

    fields = {"user": user, "organization": user.organization, "workspace": workspace}
    fields[missing] = None
    before = _counts()
    connection, names = mcp_app._enabled_tools_for_context(MCPRequestContext(**fields))
    assert connection is None
    assert names == set()
    assert _counts() == before


@pytest.fixture
def r2_secondary_workspace(user, workspace, other_org):
    assert user.organization_id != other_org.pk
    creator = type(user).objects.create(
        email=f"mcp-workspace-creator-{uuid4().hex}@example.invalid",
        organization=other_org,
        is_active=True,
    )
    assert creator.organization_id == other_org.pk
    return type(workspace).no_workspace_objects.create(
        name=f"MCP secondary workspace {uuid4().hex}",
        organization=other_org,
        created_by=creator,
        is_default=False,
        is_active=True,
    )


def _r2_membership(user, organization, *, is_active):
    from accounts.models.organization_membership import OrganizationMembership

    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        deleted=False,
        defaults={"is_active": is_active},
    )
    return membership


def _ir_workspace_access(user, workspace, *, membership=None):
    """IR fixture adaptation: the actor's live access to one workspace.

    IR admits a workspace-pinned key only with a live WorkspaceMembership
    (accounts/api_key_policy.py:75-86) and binds a bearer workspace only through
    User.can_access_workspace (mcp_server/mcp_app.py:147,
    accounts/models/user.py:217-233). Granting it in refusal tests too keeps each
    refusal attributable to the guard under test, never to a missing row.
    """
    from accounts.models.workspace import WorkspaceMembership

    defaults = {"is_active": True, "deleted": False}
    if membership is not None:
        defaults["organization_membership"] = membership
    access, _ = WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user, workspace=workspace, defaults=defaults
    )
    assert access.is_active is True
    return access


# IR adaptation of dev key types, by admission role. IR stores and admits only
# the personal "user" type (accounts/models/user.py:331-335,
# accounts/api_key_policy.py:59-67) and retires system/mcp/ingest
# (accounts/api_key_constants.py:3-4; mcp_server/migrations/
# 0005_personal_key_reconnect.py:25). Dev "mcp" keys and dev "system" keys with
# an explicit actor therefore map to IR's personal key, so IR's real actor,
# membership and workspace guards are exercised instead of the retirement
# short-circuit. The dev wrong type ("ingest") stays a retired stored type.
_IR_KEY_TYPE = {"mcp": "user", "system": "user", "ingest": "ingest"}


def _ir_key_type(key_type):
    return _IR_KEY_TYPE[key_type]


def _ir_stored_organization_id(user):
    # IR admit_api_key realigns the in-memory actor's organization to the key's
    # (accounts/api_key_policy.py:96); the persisted legacy FK is the home org.
    return (
        type(user)
        ._base_manager.filter(pk=user.pk)
        .values_list("organization_id", flat=True)
        .get()
    )


def _r2_api_key(user, organization, workspace, *, key_type="mcp"):
    from accounts.models.user import OrgApiKey

    ir_type = _ir_key_type(key_type)
    key = OrgApiKey.objects.create(
        name="MCP secondary organization regression",
        api_key=uuid4().hex,
        secret_key=uuid4().hex,
        organization=organization,
        workspace=workspace,
        user=user,
        type=ir_type,
        enabled=True,
    )
    persisted = OrgApiKey.no_workspace_objects.get(pk=key.pk)
    assert persisted.type == ir_type
    assert persisted.user_id == user.pk
    assert persisted.organization_id == organization.pk
    assert persisted.workspace_id == (workspace.pk if workspace is not None else None)
    assert persisted.enabled is True
    # IR seals the secret at save and the row keeps only its hash
    # (accounts/models/user.py:383-396, accounts/api_key_lookup.py:108-129); the
    # issuing instance keeps the plaintext. Carry it in memory for presentation.
    persisted.secret_key = key.secret_key
    return persisted


def _r2_key_snapshot(key):
    return (
        type(key)
        .no_workspace_objects.filter(pk=key.pk)
        .values("type", "user_id", "organization_id", "workspace_id", "enabled")
        .get()
    )


@pytest.fixture
def r2_streamable_services(monkeypatch, tool):
    from unittest.mock import AsyncMock

    from mcp.types import Tool

    from mcp_server import mcp_app, rate_limiter

    blocked = SimpleNamespace(
        name="r2_disabled_probe",
        group=tool.group,
        is_available=lambda: True,
    )
    for candidate in (tool, blocked):
        candidate.to_mcp_tool = lambda candidate=candidate: Tool(
            name=candidate.name, inputSchema={"type": "object"}
        )
    from mcp_server import generated_registry
    from mcp_server.generated_registry import GeneratedToolRegistry

    # Discovery, lookup and the OAuth grant intersection must share one catalog.
    registry = GeneratedToolRegistry([tool, blocked])
    monkeypatch.setattr(mcp_app, "registry", registry)
    monkeypatch.setattr(generated_registry, "registry", registry)
    execute = AsyncMock(return_value={"ok": True})
    # Patch the executor where the streamable handler imports and invokes it.
    monkeypatch.setattr(mcp_app.executor, "execute", execute)
    monkeypatch.setattr(
        rate_limiter, "get_rate_limit_tier", lambda organization: "test"
    )
    monkeypatch.setattr(rate_limiter, "check_rate_limit", lambda *args: None)
    return SimpleNamespace(tool=tool, blocked=blocked, execute=execute)


def _r2_configured_connection(user, organization, workspace, services):
    connection = _connection(user, workspace, organization)
    _config(
        connection,
        enabled_groups=[services.tool.group],
        disabled_tools=[services.blocked.name],
    )
    return connection


def _r2_assert_owner_tools(context, connection, services):
    from asgiref.sync import async_to_sync

    from mcp_server import mcp_app

    counts = _counts()
    sessions = MCPSession._base_manager.count()
    snapshot = _snapshot(connection)
    listed = async_to_sync(mcp_app.list_generated_tools)()
    assert {value.name for value in listed} == {services.tool.name}
    assert _counts() == counts
    assert MCPSession._base_manager.count() == sessions
    assert _snapshot(connection) == snapshot
    services.execute.assert_not_awaited()

    result = async_to_sync(mcp_app.call_generated_tool)(services.tool.name, {})
    assert result.isError is False
    assert result.structuredContent == {"ok": True}
    services.execute.assert_awaited_once_with(services.tool, {}, context)
    assert _counts() == counts
    assert _snapshot(connection) == snapshot
    session = MCPSession._base_manager.get(connection=connection)
    assert (session.user_id, session.organization_id, session.workspace_id) == (
        context.user.pk,
        context.organization.pk,
        context.workspace.pk,
    )


@pytest.mark.parametrize("ambient", ["none", "legacy"])
def test_r2_secondary_org_api_key_lists_and_calls_read_only(
    user, workspace, other_org, r2_secondary_workspace, r2_streamable_services, ambient
):
    """R1 FAIL: legacy-FK gate hides B's tools; R2 admits active B membership."""
    from mcp_server import mcp_app

    membership = _r2_membership(user, other_org, is_active=True)
    _ir_workspace_access(user, r2_secondary_workspace, membership=membership)
    connection = _r2_configured_connection(
        user, other_org, r2_secondary_workspace, r2_streamable_services
    )
    key = _r2_api_key(user, other_org, r2_secondary_workspace)
    key_snapshot = _r2_key_snapshot(key)
    mcp_app._clear_context()
    if ambient == "legacy":
        set_workspace_context(
            user=user, organization=user.organization, workspace=workspace
        )
    try:
        context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            user.pk,
            other_org.pk,
            r2_secondary_workspace.pk,
        )
        assert _ir_stored_organization_id(context.user) != context.organization.pk
        assert context.api_key.pk == key.pk
        _r2_assert_owner_tools(context, connection, r2_streamable_services)
        assert _r2_key_snapshot(key) == key_snapshot
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("ambient", ["none", "legacy"])
def test_r2_secondary_org_bearer_lists_and_calls_read_only(
    user, workspace, other_org, r2_secondary_workspace, r2_streamable_services, ambient
):
    """R1 FAIL: bearer chooses legacy A; R2 authenticates and uses exact B/W_B."""
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    membership = _r2_membership(user, other_org, is_active=True)
    _ir_workspace_access(user, r2_secondary_workspace, membership=membership)
    connection = _r2_configured_connection(
        user, other_org, r2_secondary_workspace, r2_streamable_services
    )
    token, _ = generate_oauth_token(
        user.pk,
        other_org.pk,
        r2_secondary_workspace.pk,
        "isolation",
        list(DEFAULT_TOOL_GROUPS),
    )
    counts = _counts()
    mcp_app._clear_context()
    if ambient == "legacy":
        set_workspace_context(
            user=user, organization=user.organization, workspace=workspace
        )
    try:
        context = mcp_app._authenticate_via_oauth(token)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            user.pk,
            other_org.pk,
            r2_secondary_workspace.pk,
        )
        assert _ir_stored_organization_id(context.user) != context.organization.pk
        assert context.api_key is None
        assert _counts() == counts
        _r2_assert_owner_tools(context, connection, r2_streamable_services)
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize(
    "scope,membership_state",
    [("secondary", "absent"), ("secondary", "inactive"), ("primary", "inactive")],
)
def test_r2_bearer_requires_active_membership(
    user, workspace, other_org, r2_secondary_workspace, scope, membership_state
):
    """R1 FAIL for inactive A, PASS refusal for B; R2 refuses all three cases."""
    from accounts.models.organization_membership import OrganizationMembership
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    organization = other_org if scope == "secondary" else user.organization
    selected_workspace = r2_secondary_workspace if scope == "secondary" else workspace
    # Workspace access alone must not stand in for the organization membership.
    _ir_workspace_access(user, selected_workspace)
    if membership_state == "inactive":
        _r2_membership(user, organization, is_active=False)
    else:
        assert not OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).exists()
    token, _ = generate_oauth_token(
        user.pk,
        organization.pk,
        selected_workspace.pk,
        "isolation",
        list(DEFAULT_TOOL_GROUPS),
    )
    counts = _counts()
    mcp_app._clear_context()
    try:
        assert mcp_app._authenticate_via_oauth(token) is None
        assert mcp_app._current_context() is None
        assert _counts() == counts
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("scope", ["primary", "secondary"])
def test_r2_api_key_inactive_membership_cannot_list_or_call(
    user, workspace, other_org, r2_secondary_workspace, r2_streamable_services, scope
):
    """R1 FAIL for inactive A, PASS refusal for B; R2 denies before execution/writes."""
    from asgiref.sync import async_to_sync

    from mcp_server import mcp_app

    organization = other_org if scope == "secondary" else user.organization
    selected_workspace = r2_secondary_workspace if scope == "secondary" else workspace
    membership = _r2_membership(user, organization, is_active=True)
    _ir_workspace_access(user, selected_workspace, membership=membership)
    connection = _r2_configured_connection(
        user, organization, selected_workspace, r2_streamable_services
    )
    key = _r2_api_key(user, organization, selected_workspace)
    counts = _counts()
    sessions = MCPSession._base_manager.count()
    snapshot = _snapshot(connection)
    mcp_app._clear_context()
    try:
        context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            user.pk,
            organization.pk,
            selected_workspace.pk,
        )
        # Revoke after authentication: admission must consult current authority.
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        assert async_to_sync(mcp_app.list_generated_tools)() == []
        result = async_to_sync(mcp_app.call_generated_tool)(
            r2_streamable_services.tool.name, {}
        )
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "HTTP_403"
        # IR literal for the same refusal (mcp_server/mcp_app.py:250-252).
        assert (
            result.structuredContent["error"]["message"]
            == "Access denied to this organization"
        )
        r2_streamable_services.execute.assert_not_awaited()
        assert _counts() == counts
        assert MCPSession._base_manager.count() == sessions
        assert _snapshot(connection) == snapshot
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("binding", ["foreign", "inactive", "missing", "nonexistent"])
def test_r2_secondary_org_bearer_requires_exact_active_workspace(
    user, workspace, other_org, r2_secondary_workspace, binding
):
    """R1 PASS (B denied early); R2 must retain these refusals after admitting B."""
    from mcp_server import mcp_app
    from mcp_server.oauth_utils import generate_oauth_token

    membership = _r2_membership(user, other_org, is_active=True)
    _ir_workspace_access(user, r2_secondary_workspace, membership=membership)
    workspace_id = r2_secondary_workspace.pk
    if binding == "foreign":
        workspace_id = workspace.pk
    elif binding == "inactive":
        type(workspace).no_workspace_objects.filter(pk=workspace_id).update(
            is_active=False
        )
    elif binding == "missing":
        workspace_id = None
    elif binding == "nonexistent":
        workspace_id = uuid4()
    token, _ = generate_oauth_token(
        user.pk, other_org.pk, workspace_id, "isolation", list(DEFAULT_TOOL_GROUPS)
    )
    counts = _counts()
    mcp_app._clear_context()
    try:
        assert mcp_app._authenticate_via_oauth(token) is None
        assert mcp_app._current_context() is None
        assert _counts() == counts
    finally:
        mcp_app._clear_context()


def test_r2_unknown_api_key_refusal_log_is_value_free(monkeypatch):
    """R1 and dev FAIL: log includes attempted key prefix; R2 emits only the event."""
    from mcp_server import mcp_app

    # Uppercase hex has no characters in common with the lowercase event name,
    # so this checks even a single supplied key byte, not only the full key.
    attempted_key = uuid4().hex.upper()
    attempted_secret = uuid4().hex.upper()
    captured_logger = Mock()
    monkeypatch.setattr(mcp_app, "logger", captured_logger)
    counts = _counts()
    mcp_app._clear_context()
    try:
        assert (
            mcp_app._authenticate_and_set_context(attempted_key, attempted_secret)
            is None
        )
        captured_logger.warning.assert_called_once_with("mcp_auth_failed")
        assert len(captured_logger.mock_calls) == 1
        arguments = repr(
            (
                captured_logger.warning.call_args.args,
                captured_logger.warning.call_args.kwargs,
            )
        )
        assert not set(attempted_key).intersection(arguments)
        assert not set(attempted_secret).intersection(arguments)
        assert mcp_app._current_context() is None
        assert _counts() == counts
    finally:
        mcp_app._clear_context()


def _r3_api_key(
    user,
    organization,
    workspace,
    *,
    key_type="mcp",
    unbound_user=False,
    unbound_workspace=False,
):
    from accounts.api_key_constants import RECOVERABLE_TYPES
    from accounts.api_secrets import seal

    key = _r2_api_key(user, organization, workspace, key_type=key_type)
    ir_type = _ir_key_type(key_type)
    bound_user = None if unbound_user else user
    bound_workspace = None if unbound_workspace else workspace
    # Setup only: bypass ambient filtering and save-time workspace auto-assignment.
    # Uppercase generated material is disjoint from the allowed log arguments.
    presented_secret = uuid4().hex.upper()
    # IR adaptation: once a row has a hash the hash decides
    # (accounts/api_secrets.py:163-203), so re-key through IR's own sealing.
    updated = (
        type(key)
        .no_workspace_objects.filter(pk=key.pk)
        .update(
            type=ir_type,
            user=bound_user,
            workspace=bound_workspace,
            api_key=uuid4().hex.upper(),
            **seal(presented_secret, recoverable=ir_type in RECOVERABLE_TYPES),
        )
    )
    assert updated == 1
    key = type(key).no_workspace_objects.get(pk=key.pk)
    assert key.type == ir_type
    assert key.organization_id == organization.pk
    assert key.user_id == (bound_user.pk if bound_user is not None else None)
    assert key.workspace_id == (
        bound_workspace.pk if bound_workspace is not None else None
    )
    key.secret_key = presented_secret  # In memory only; the row keeps the hash.
    return key


def _r3_assert_auth_refused(
    key,
    services,
    monkeypatch,
    *,
    event,
    connection=None,
):
    from asgiref.sync import async_to_sync

    from mcp_server import mcp_app

    captured_logger = Mock()
    bind_context = Mock(wraps=mcp_app._bind_context)
    monkeypatch.setattr(mcp_app, "logger", captured_logger)
    monkeypatch.setattr(mcp_app, "_bind_context", bind_context)
    mcp_app._clear_context()
    counts = _counts()
    sessions = MCPSession._base_manager.count()
    snapshot = _snapshot(connection) if connection is not None else None
    key_snapshot = _r2_key_snapshot(key)
    try:
        assert (
            mcp_app._authenticate_and_set_context(key.api_key, key.secret_key) is None
        )
        bind_context.assert_not_called()
        assert mcp_app._current_context() is None
        assert get_current_user() is None
        assert get_current_organization() is None
        assert get_current_workspace() is None
        assert async_to_sync(mcp_app.list_generated_tools)() == []
        result = async_to_sync(mcp_app.call_generated_tool)(services.tool.name, {})
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == "UNAUTHENTICATED"
        services.execute.assert_not_called()
        services.execute.assert_not_awaited()
        assert _counts() == counts
        assert MCPSession._base_manager.count() == sessions
        if connection is not None:
            assert _snapshot(connection) == snapshot
        assert _r2_key_snapshot(key) == key_snapshot

        # IR emits one value-free refusal event with no fields for every
        # admission failure (mcp_server/mcp_app.py:90-92); dev named the reason.
        captured_logger.warning.assert_called_once_with(event)
        assert len(captured_logger.mock_calls) == 1
        arguments = repr(
            (
                captured_logger.warning.call_args.args,
                captured_logger.warning.call_args.kwargs,
            )
        )
        assert not set(key.api_key).intersection(arguments)
        assert not set(key.secret_key).intersection(arguments)
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("scope", ["primary", "secondary"])
@pytest.mark.parametrize("has_connection", [False, True])
@pytest.mark.parametrize("key_type", ["mcp", "system"])
def test_r3_api_key_without_workspace_never_uses_default(
    user,
    workspace,
    other_org,
    r2_secondary_workspace,
    r2_streamable_services,
    monkeypatch,
    scope,
    has_connection,
    key_type,
):
    """R2 FAIL: substitutes a default (or selects a system actor); R3 refuses."""
    organization = other_org if scope == "secondary" else user.organization
    selected_workspace = r2_secondary_workspace if scope == "secondary" else workspace
    membership = _r2_membership(user, organization, is_active=True)
    # The default must be one the actor could land on, so a refusal is real.
    _ir_workspace_access(user, selected_workspace, membership=membership)
    assert user.is_active
    type(workspace).no_workspace_objects.filter(organization=organization).update(
        is_default=False
    )
    type(workspace).no_workspace_objects.filter(pk=selected_workspace.pk).update(
        is_default=True, is_active=True
    )
    selected_workspace.refresh_from_db()
    assert (
        type(workspace)
        .no_workspace_objects.get(
            organization=organization, is_default=True, is_active=True
        )
        .pk
        == selected_workspace.pk
    )
    connection = (
        _r2_configured_connection(
            user, organization, selected_workspace, r2_streamable_services
        )
        if has_connection
        else None
    )
    key = _r3_api_key(
        user,
        organization,
        selected_workspace,
        key_type=key_type,
        unbound_workspace=True,
    )
    _r3_assert_auth_refused(
        key,
        r2_streamable_services,
        monkeypatch,
        # Dev: "mcp_auth_workspace_refused", reason "missing_workspace".
        event="mcp_auth_failed",
        connection=connection,
    )


@pytest.mark.parametrize("has_connection", [False, True])
def test_r3_system_key_without_user_never_selects_a_member(
    user,
    workspace,
    r2_streamable_services,
    monkeypatch,
    has_connection,
):
    """R2 FAIL: selects a legacy-FK user; R3 requires the key's explicit actor."""
    _r2_membership(user, user.organization, is_active=True)
    assert user.is_active
    connection = (
        _r2_configured_connection(
            user, user.organization, workspace, r2_streamable_services
        )
        if has_connection
        else None
    )
    key = _r3_api_key(
        user,
        user.organization,
        workspace,
        key_type="system",
        unbound_user=True,
    )
    _r3_assert_auth_refused(
        key,
        r2_streamable_services,
        monkeypatch,
        event="mcp_auth_failed",  # Dev: "mcp_auth_no_user".
        connection=connection,
    )


@pytest.mark.parametrize("scope", ["primary", "secondary"])
@pytest.mark.parametrize("ambient", ["none", "legacy"])
def test_r3_system_key_uses_only_explicit_active_member(
    user,
    peer,
    workspace,
    other_org,
    r2_secondary_workspace,
    r2_streamable_services,
    scope,
    ambient,
):
    """R2 FAIL: picks a different primary actor or no secondary actor; R3 admits."""
    from datetime import timedelta

    from mcp_server import mcp_app

    organization = other_org if scope == "secondary" else user.organization
    selected_workspace = r2_secondary_workspace if scope == "secondary" else workspace
    # A second active member must not become an implicit alternative principal.
    user_membership = _r2_membership(user, organization, is_active=True)
    peer_membership = _r2_membership(peer, organization, is_active=True)
    _ir_workspace_access(user, selected_workspace, membership=user_membership)
    _ir_workspace_access(peer, selected_workspace, membership=peer_membership)
    assert peer.is_active
    if scope == "primary":
        # Keep the older alternative deterministic even under a frozen test clock.
        type(peer).objects.all().filter(pk=peer.pk).update(
            created_at=user.created_at + timedelta(seconds=1)
        )
        assert (
            type(user)
            .objects.all()
            .filter(organization=organization)
            .order_by("created_at")
            .first()
            .pk
            != peer.pk
        )
    else:
        assert peer.organization_id != organization.pk
        # Dev requires a same-org workspace creator. That alternative legacy-FK
        # actor must never replace the explicit secondary-org key owner below.
        assert selected_workspace.created_by_id != peer.pk
        assert (
            type(user)
            .objects.all()
            .filter(pk=selected_workspace.created_by_id, organization=organization)
            .exists()
        )
    connection = _r2_configured_connection(
        peer, organization, selected_workspace, r2_streamable_services
    )
    key = _r3_api_key(peer, organization, selected_workspace, key_type="system")
    key_snapshot = _r2_key_snapshot(key)
    counts = _counts()
    sessions = MCPSession._base_manager.count()
    mcp_app._clear_context()
    if ambient == "legacy":
        set_workspace_context(
            user=user, organization=user.organization, workspace=workspace
        )
    try:
        context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        assert context is not None
        assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
            peer.pk,
            organization.pk,
            selected_workspace.pk,
        )
        assert context.api_key.pk == key.pk
        assert _counts() == counts
        assert MCPSession._base_manager.count() == sessions
        _r2_assert_owner_tools(context, connection, r2_streamable_services)
        assert _r2_key_snapshot(key) == key_snapshot
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("membership_state", ["absent", "inactive"])
def test_r3_system_key_legacy_fk_without_active_membership_is_refused(
    user,
    peer,
    workspace,
    r2_streamable_services,
    monkeypatch,
    membership_state,
):
    """R2 FAIL: chooses a legacy actor before membership admission; R3 refuses."""
    from accounts.models.organization_membership import OrganizationMembership

    organization = user.organization
    assert peer.organization_id == organization.pk
    assert peer.is_active
    _r2_membership(user, organization, is_active=True)
    # Live workspace access must not stand in for the organization membership.
    _ir_workspace_access(peer, workspace)
    if membership_state == "absent":
        OrganizationMembership.no_workspace_objects.filter(
            user=peer, organization=organization, deleted=False
        ).delete()
        assert not OrganizationMembership.no_workspace_objects.filter(
            user=peer, organization=organization, deleted=False
        ).exists()
    else:
        _r2_membership(peer, organization, is_active=False)
    assert not OrganizationMembership.no_workspace_objects.filter(
        user=peer, organization=organization, is_active=True, deleted=False
    ).exists()
    connection = _r2_configured_connection(
        peer, organization, workspace, r2_streamable_services
    )
    key = _r3_api_key(peer, organization, workspace, key_type="system")
    _r3_assert_auth_refused(
        key,
        r2_streamable_services,
        monkeypatch,
        event="mcp_auth_failed",  # Dev: "mcp_auth_no_user".
        connection=connection,
    )


def _r6_key_state(key):
    # Observe soft-deleted rows without ambient filtering; never include key material.
    return (
        type(key)
        ._base_manager.filter(pk=key.pk)
        .values(
            "type", "user_id", "organization_id", "workspace_id", "enabled", "deleted"
        )
        .get()
    )


@pytest.mark.parametrize("ambient_default", [False, True])
@pytest.mark.parametrize("key_type", ["mcp", "system"])
@pytest.mark.parametrize(
    "key_state", ["valid", "disabled", "soft_deleted", "wrong_type"]
)
def test_r6_key_lookup_under_foreign_scope_preserves_binding_and_refusals(
    user,
    peer,
    workspace,
    other_org,
    r2_secondary_workspace,
    monkeypatch,
    ambient_default,
    key_type,
    key_state,
):
    """R5 RED for valid keys: ambient filtering hides the exact secondary key."""
    from accounts.api_key_constants import RETIRED_KEY_TYPES
    from accounts.api_key_policy import APIKeyRetired
    from mcp_server import mcp_app

    membership = _r2_membership(peer, other_org, is_active=True)
    _ir_workspace_access(peer, r2_secondary_workspace, membership=membership)
    assert peer.is_active
    assert peer.pk != user.pk
    assert workspace.organization_id == user.organization_id != other_org.pk
    assert r2_secondary_workspace.organization_id == other_org.pk
    assert workspace.pk != r2_secondary_workspace.pk
    key = _r2_api_key(peer, other_org, r2_secondary_workspace, key_type=key_type)
    expected_type = (
        _ir_key_type("ingest") if key_state == "wrong_type" else _ir_key_type(key_type)
    )
    updated = (
        type(key)
        .no_workspace_objects.filter(pk=key.pk)
        .update(
            type=expected_type,
            enabled=key_state != "disabled",
            deleted=key_state == "soft_deleted",
        )
    )
    assert updated == 1
    key_snapshot = _r6_key_state(key)
    assert key_snapshot == {
        "type": expected_type,
        "user_id": peer.pk,
        "organization_id": other_org.pk,
        "workspace_id": r2_secondary_workspace.pk,
        "enabled": key_state != "disabled",
        "deleted": key_state == "soft_deleted",
    }
    # Exercise both branches of BaseModelManager's ambient workspace filter.
    updated = (
        type(workspace)
        .no_workspace_objects.filter(pk=workspace.pk)
        .update(is_default=ambient_default)
    )
    assert updated == 1
    workspace = type(workspace).no_workspace_objects.get(pk=workspace.pk)
    assert workspace.is_default is ambient_default
    counts = _counts()
    sessions = MCPSession._base_manager.count()
    bind_context = Mock(wraps=mcp_app._bind_context)
    monkeypatch.setattr(mcp_app, "_bind_context", bind_context)
    mcp_app._clear_context()
    try:
        set_workspace_context(
            user=user, organization=user.organization, workspace=workspace
        )
        assert get_current_user().pk == user.pk
        assert get_current_organization().pk == user.organization_id
        assert get_current_workspace().pk == workspace.pk
        # Neither .all() nor all_objects bypasses the foreign ambient workspace.
        assert not type(key).objects.all().filter(pk=key.pk).exists()
        assert not type(key).all_objects.filter(pk=key.pk).exists()
        assert type(key).no_workspace_objects.filter(pk=key.pk).exists() is (
            key_state != "soft_deleted"
        )

        if expected_type in RETIRED_KEY_TYPES:
            # IR refuses a proven retired-type key by raising, never by binding
            # (accounts/api_key_policy.py:114-120; mcp_server/mcp_app.py:88-89).
            with pytest.raises(APIKeyRetired):
                mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
            context = None
        else:
            context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
        if key_state == "valid":
            assert context is not None
            assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
                peer.pk,
                other_org.pk,
                r2_secondary_workspace.pk,
            )
            assert context.api_key.pk == key.pk
            assert context.api_key.type == _ir_key_type(key_type)
            assert context.api_key.enabled is True
            assert context.api_key.deleted is False
            assert _ir_stored_organization_id(context.user) != context.organization.pk
            bind_context.assert_called_once_with(context)
            assert mcp_app._current_context() is context
            assert get_current_user().pk == peer.pk
            assert get_current_organization().pk == other_org.pk
            assert get_current_workspace().pk == r2_secondary_workspace.pk
        else:
            assert context is None
            bind_context.assert_not_called()
            assert mcp_app._request_context.get() is None
            # This low-level lookup must not bind a refused key or change the caller.
            assert get_current_user().pk == user.pk
            assert get_current_organization().pk == user.organization_id
            assert get_current_workspace().pk == workspace.pk
        assert _r6_key_state(key) == key_snapshot
        assert _counts() == counts
        assert MCPSession._base_manager.count() == sessions
    finally:
        mcp_app._clear_context()


@pytest.mark.parametrize("legacy", [False, True])
def test_same_org_peer_transport_first_use_never_reuses_owner_connection(
    user, peer, workspace, tool, legacy
):
    """R4 PASS expected: transport first use cannot inherit U1's row or config."""
    from mcp_server.api_executor import MCPRequestContext

    assert peer.pk != user.pk
    assert peer.organization_id == user.organization_id == workspace.organization_id
    type(workspace).no_workspace_objects.filter(pk=workspace.pk).update(is_default=True)
    workspace.refresh_from_db()
    assert workspace.is_default
    owner = _connection(user, None if legacy else workspace)
    owner_config = _config(
        owner, enabled_groups=list(DEFAULT_TOOL_GROUPS), disabled_tools=[tool.name]
    )
    own_state = MCPConnection._base_manager.filter(pk=owner.pk).values().get()
    config_state = (
        MCPToolGroupConfig._base_manager.filter(pk=owner_config.pk).values().get()
    )
    counts = _counts()
    sessions = MCPSession._base_manager.count()

    # No dashboard request: U2 has no connection of any scope before discovery.
    assert not MCPConnection._base_manager.filter(user=peer).exists()
    listed = transport.MCPToolListView.as_view()(
        _request("get", "/mcp/internal/tools/", peer, workspace)
    )
    assert listed.status_code == 200
    # The sole registry tool is disabled for U1, but enabled by U2's defaults.
    assert listed.data["result"]["total"] == 1
    assert _counts() == counts
    assert MCPSession._base_manager.count() == sessions
    transport.executor.execute_sync.assert_not_called()
    assert MCPConnection._base_manager.filter(pk=owner.pk).values().get() == own_state
    assert (
        MCPToolGroupConfig._base_manager.filter(pk=owner_config.pk).values().get()
        == config_state
    )

    # LIST must leave the first call's missing-U2-row precondition intact.
    assert not MCPConnection._base_manager.filter(user=peer).exists()
    response = transport.MCPToolCallView.as_view()(
        _request(
            "post",
            "/mcp/internal/tool-call/",
            peer,
            workspace,
            {"tool_name": tool.name},
        )
    )
    assert response.status_code == 200
    peer_connection = MCPConnection._base_manager.get(user=peer)
    assert peer_connection.pk != owner.pk
    assert (
        peer_connection.user_id,
        peer_connection.organization_id,
        peer_connection.workspace_id,
    ) == (peer.pk, peer.organization_id, workspace.pk)
    assert peer_connection.deleted is False
    # Defaults may remain implicit; any persisted config must be U2's, not U1's.
    peer_configs = list(
        MCPToolGroupConfig._base_manager.filter(connection=peer_connection).values(
            "enabled_groups", "disabled_tools"
        )
    )
    for config in peer_configs:
        assert config["enabled_groups"] == list(DEFAULT_TOOL_GROUPS)
        assert config["disabled_tools"] == []
    assert _counts() == (counts[0] + 1, counts[1] + len(peer_configs))
    session = MCPSession._base_manager.get(pk=response.data["session_id"])
    assert session.connection_id == peer_connection.pk
    assert (session.user_id, session.organization_id, session.workspace_id) == (
        peer.pk,
        peer.organization_id,
        workspace.pk,
    )
    assert MCPSession._base_manager.count() == sessions + 1
    transport.executor.execute_sync.assert_called_once()
    invocation = transport.executor.execute_sync.call_args
    contexts = [
        value
        for value in (*invocation.args, *invocation.kwargs.values())
        if isinstance(value, MCPRequestContext)
    ]
    assert len(contexts) == 1
    context = contexts[0]
    assert (context.user.pk, context.organization.pk, context.workspace.pk) == (
        peer.pk,
        peer.organization_id,
        workspace.pk,
    )
    assert MCPConnection._base_manager.filter(pk=owner.pk).values().get() == own_state
    assert (
        MCPToolGroupConfig._base_manager.filter(pk=owner_config.pk).values().get()
        == config_state
    )
