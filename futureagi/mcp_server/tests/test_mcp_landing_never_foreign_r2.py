"""Grant-time landing uses live membership; bearer admission never lands.

Exercise the real browser authenticator, workspace resolver and both consent
handlers. Only cache storage is replaced with an isolated real LocMemCache;
credential decryption, membership/access decisions and code issuance are real.
Runtime RED/GREEN execution belongs to the integration runner, not this packet.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from accounts import authentication
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.workspace_landing import resolve_user_workspace
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import save_config
from mcp_server import mcp_app
from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.oauth_provider import APPROVE_PREFIX, CODE_PREFIX
from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_oauth_token,
    hash_client_secret,
)
from mcp_server.views import oauth, oauth_approve

landing = _landing_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_scope():
    mcp_app._clear_context()
    yield
    mcp_app._clear_context()


def _scope_rows():
    return {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (
            Workspace,
            WorkspaceMembership,
            OrganizationMembership,
            OrgApiKey,
            MCPConnection,
            MCPToolGroupConfig,
        )
    }


@pytest.fixture(params=["consent", "approve"])
def browser_grant(landing, monkeypatch, request):
    """A real local browser session and the two production grant entry points."""
    cache = LocMemCache(f"mcp-landing-r2-{uuid4().hex}", {})
    monkeypatch.setattr(authentication, "cache", cache)
    monkeypatch.setattr(oauth_approve, "cache", cache)
    session = AuthToken.objects.create(
        user=landing.user,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
    )
    token = authentication.generate_encrypted_message(
        {"id": str(session.pk), "user_id": str(landing.user.pk)}
    )
    client = MCPOAuthClient.objects.create(
        client_id=f"landing-r2-{uuid4().hex}",
        client_secret_hash=hash_client_secret(uuid4().hex),
        name="Landing boundary regression",
        redirect_uris=["http://localhost/callback"],
    )
    request_id = uuid4().hex
    pending_key = f"{APPROVE_PREFIX}{request_id}"
    pending = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "code_challenge": "local-test-challenge",
        "redirect_uri_provided_explicitly": True,
        "scopes": ["context"],
        "state": "landing-r2",
    }
    if request.param == "approve":
        cache.set(pending_key, pending, 600)
        view = oauth_approve.MCPOAuthApproveView
        path = "/mcp/oauth/approve/"
        body = {"request_id": request_id, "approved": True}
    else:
        view = oauth.MCPOAuthConsentView
        path = "/mcp/oauth/consent/"
        body = {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "approved": True,
            "selected_groups": ["context"],
        }

    def issue(organization, workspace=None):
        mcp_app._clear_context()
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_X_ORGANIZATION_ID": str(organization.pk),
        }
        if workspace is not None:
            headers["HTTP_X_WORKSPACE_ID"] = str(workspace.pk)
        raw = APIRequestFactory().post(path, body, format="json", **headers)
        # Do not force-authenticate or inject a workspace: admission must run.
        with patch.object(cache, "set", wraps=cache.set) as cache_writes:
            response = view.as_view(
                authentication_classes=[authentication.APIKeyAuthentication]
            )(raw)
        code_writes = [
            call
            for call in cache_writes.call_args_list
            if str(call.args[0]).startswith(CODE_PREFIX)
        ]
        mcp_app._clear_context()
        if response.status_code != 200:
            assert code_writes == []
            assert not MCPOAuthCode.objects.filter(client=client).exists()
            if request.param == "approve":
                assert cache.get(pending_key) == pending
            return response, None
        code = parse_qs(urlsplit(response.data["result"]["redirect_url"]).query)[
            "code"
        ][0]
        if request.param == "approve":
            assert len(code_writes) == 1
            grant = cache.get(f"{CODE_PREFIX}{code}")
            assert grant is not None
            assert cache.get(pending_key) is None
        else:
            stored = MCPOAuthCode.objects.get(code=code, client=client)
            grant = {
                "user_id": str(stored.user_id),
                "organization_id": str(stored.organization_id),
                "workspace_id": str(stored.workspace_id),
            }
        return response, grant

    return issue


@pytest.mark.parametrize("membership_state", ["absent", "inactive", "deleted"])
@pytest.mark.parametrize("explicit_workspace", [False, True])
def test_browser_grant_never_lands_in_foreign_org(
    landing, browser_grant, membership_state, explicit_workspace
):
    foreign = Organization.objects.create(name="No active membership")
    workspace = Workspace.objects.create(
        name="Foreign default",
        organization=foreign,
        created_by=landing.owner,
        is_default=True,
    )
    if membership_state != "absent":
        local = landing.other_mem.organization_membership
        membership = OrganizationMembership.no_workspace_objects.create(
            user=landing.user,
            organization=foreign,
            role=local.role,
            level=local.level,
            is_active=membership_state != "inactive",
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=landing.user,
            workspace=workspace,
            organization_membership=membership,
            role=landing.other_mem.role,
            level=landing.other_mem.level,
        )
        if membership_state == "deleted":
            OrganizationMembership._base_manager.filter(pk=membership.pk).update(
                deleted=True
            )
    save_config(
        landing,
        {
            "currentOrganizationId": str(foreign.pk),
            "orgWorkspaceMap": {str(landing.org.pk): str(workspace.pk)},
            "currentWorkspaceId": str(workspace.pk),
            "defaultWorkspaceId": str(workspace.pk),
        },
    )
    before = _scope_rows()
    assert resolve_user_workspace(landing.user, foreign) is None
    assert resolve_user_workspace(landing.user, landing.org) == landing.default
    assert _scope_rows() == before

    response, grant = browser_grant(foreign, workspace if explicit_workspace else None)
    if explicit_workspace:
        assert response.status_code == 403
        assert grant is None
    else:
        # An inaccessible browser preference may land only in an authorized org;
        # this is not permission to repair claims of an already-issued bearer.
        assert response.status_code == 200, response.data
        assert grant["user_id"] == str(landing.user.pk)
        assert grant["organization_id"] == str(landing.org.pk)
        assert grant["workspace_id"] == str(landing.default.pk)
    assert _scope_rows() == before
    assert not MCPOAuthCode.objects.filter(organization=foreign).exists()
    assert mcp_app._current_context() is None


def test_browser_grant_own_membership_mints_explicit_scope(landing, browser_grant):
    save_config(
        landing, {"orgWorkspaceMap": {str(landing.org.pk): str(landing.other.pk)}}
    )
    before = _scope_rows()
    response, grant = browser_grant(landing.org)
    assert response.status_code == 200, response.data
    assert grant["user_id"] == str(landing.user.pk)
    assert grant["organization_id"] == str(landing.org.pk)
    assert grant["workspace_id"] == str(landing.other.pk)
    assert _scope_rows() == before

    token, _ = generate_oauth_token(
        grant["user_id"],
        grant["organization_id"],
        grant["workspace_id"],
        "landing-r2",
        "context",
    )
    payload = decrypt_oauth_token(token)
    assert payload["org_id"] == str(landing.org.pk)
    assert payload["workspace_id"] == str(landing.other.pk)
    context = mcp_app._authenticate_via_oauth(token)
    assert context.user == landing.user
    assert context.organization == landing.org
    assert context.workspace == landing.other
    assert _scope_rows() == before


@pytest.mark.parametrize("missing", ["organization", "workspace"])
def test_bearer_never_reuses_browser_landing_for_missing_claims(landing, missing):
    before = _scope_rows()
    valid, _ = generate_oauth_token(
        landing.user.pk, landing.org.pk, landing.other.pk, "landing-r2", "context"
    )
    assert mcp_app._authenticate_via_oauth(valid).workspace == landing.other
    mcp_app._clear_context()
    token, _ = generate_oauth_token(
        landing.user.pk,
        None if missing == "organization" else landing.org.pk,
        None if missing == "workspace" else landing.other.pk,
        "landing-r2",
        "context",
    )
    with patch.object(mcp_app, "_bind_context", wraps=mcp_app._bind_context) as bind:
        assert mcp_app._authenticate_via_oauth(token) is None
    bind.assert_not_called()
    assert mcp_app._current_context() is None
    assert _scope_rows() == before


def test_browser_grant_refuses_without_active_membership(landing, browser_grant):
    OrganizationMembership._base_manager.filter(user=landing.user).update(
        is_active=False
    )
    before = _scope_rows()
    assert resolve_user_workspace(landing.user, landing.org) is None
    response, grant = browser_grant(landing.org)
    assert response.status_code == 403
    assert grant is None
    assert _scope_rows() == before
    assert mcp_app._current_context() is None
