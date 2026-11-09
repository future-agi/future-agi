"""733 F2/F3: real-session self-service, never admin-fixture substitution."""

import hashlib
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import Organization
from accounts.models.audit_log import AuditAction, AuditLog
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey, AgentccProject
from agentcc.tests.test_agentcc_permissions import (
    _member_of,
    _user_with_level,
)
from tfc.constants.levels import Level

KEYS = "/agentcc/api-keys/"
pytestmark = pytest.mark.django_db


def session_client(user, organization, workspace):
    token = AuthToken.no_workspace_objects.create(
        user=user, auth_type=AuthTokenType.ACCESS, last_used_at=timezone.now()
    )
    bearer = generate_encrypted_message({"user_id": str(user.pk), "id": str(token.pk)})
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {bearer}",
        HTTP_X_ORGANIZATION_ID=str(organization.pk),
        HTTP_X_WORKSPACE_ID=str(workspace.pk),
    )
    return client


@pytest.fixture
def gateway(transactional_db):
    # Key mutations must own a real commit, not pytest-django's db atomic block.
    client = MagicMock()
    client.create_key.side_effect = AssertionError("Legacy remote issuance forbidden")

    def confirm_import(payload):
        key = AgentccAPIKey.unscoped_objects.get(gateway_key_id=payload["id"])
        connection = transaction.get_connection(key._state.db)
        assert connection.get_autocommit()
        assert not connection.in_atomic_block
        assert key.status == AgentccAPIKey.PENDING
        assert payload["key_hash"] == key.key_hash
        assert payload["key_prefix"] == key.key_prefix
        assert "key" not in payload
        event = AuditLog.no_workspace_objects.using(key._state.db).get(
            target_id=key.pk, action=AuditAction.GATEWAY_KEY_CREATED
        )
        assert event.organization_id == key.organization_id
        assert event.actor_id == key.user_id
        return True

    client.import_committed_key.side_effect = confirm_import
    client.revoke_key_confirmed.return_value = True
    client.revoke_key.return_value = {}  # DELETE retains the legacy revoke seam.
    # A sys.modules stand-in can evict imports while Django retains its URL
    # callback. Patch the bridge that the resolved view actually calls, not a
    # subsequently imported copy of agentcc.services.auth_bridge.
    view_globals = resolve(KEYS).func.cls.create.__globals__
    bridge = view_globals["auth_bridge"]
    with (
        patch.object(bridge, "get_gateway_client", return_value=client) as get_client,
        patch.dict(view_globals, ensure_first_org_config=MagicMock()),
    ):
        yield client
        client.create_key.assert_not_called()
        if (
            client.import_committed_key.called
            or client.revoke_key_confirmed.called
            or client.revoke_key.called
        ):
            get_client.assert_called()


@pytest.mark.parametrize(
    "level", [Level.OWNER, Level.ADMIN, Level.MEMBER, Level.VIEWER]
)
@pytest.mark.parametrize("other_org", [False, True])
@pytest.mark.parametrize("other_workspace", [False, True])
def test_active_member_creates_own_key(
    organization, workspace, level, other_org, other_workspace, gateway
):
    user = _user_with_level(organization, level)
    target = (
        Organization.objects.create(name="Other member org")
        if other_org
        else organization
    )
    if other_org:
        workspace = _member_of(user, target, level)
    if other_workspace:
        workspace = Workspace.no_workspace_objects.create(
            organization=target,
            name="Second workspace",
            is_active=True,
            created_by=user,
        )
        from accounts.models.workspace import WorkspaceMembership

        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            is_active=True,
            organization_membership=OrganizationMembership._base_manager.get(
                user=user, organization=target
            ),
        )
    client = session_client(user, target, workspace)
    response = client.post(
        KEYS,
        {
            "name": "my-explicit-key",
            "owner": "somebody else",
            "user": str(uuid.uuid4()),
            "organization": str(uuid.uuid4()),
            "workspace": str(uuid.uuid4()),
            "allowed_models": ["fixture-model"],
            "allowed_providers": ["fixture-provider"],
            "metadata": {"label": "mine"},
            "log_content": "none",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    result = response.data["result"]
    row = AgentccAPIKey.no_workspace_objects.get(pk=result["id"])
    assert row.user_id == user.pk
    assert row.organization_id == target.pk
    assert (
        row.workspace_id is None
    )  # F2: org-wide access, no workspace authority from body
    assert row.owner == user.email
    assert row.key_hash == hashlib.sha256(result["key"].encode()).hexdigest()
    assert row.status == AgentccAPIKey.ACTIVE
    gateway.import_committed_key.assert_called_once()
    sent = gateway.import_committed_key.call_args.args[0]
    assert sent["owner"] == user.email
    assert sent["metadata"]["user_id"] == str(user.pk)
    assert sent["metadata"]["org_id"] == str(target.pk)
    assert sent["models"] == ["fixture-model"]
    assert sent["providers"] == ["fixture-provider"]
    assert sent["metadata"]["log_content"] == "none"
    read = client.get(f"{KEYS}{row.pk}/")
    assert read.status_code == 200
    assert "key" not in read.data["result"]
    assert "key_hash" not in read.data["result"]


@pytest.mark.parametrize("state", ["inactive", "deleted", "departed", "inactive-user"])
def test_stale_membership_cannot_create(organization, workspace, gateway, state):
    user = _user_with_level(organization, Level.MEMBER)
    client = session_client(user, organization, workspace)
    membership = OrganizationMembership._base_manager.filter(
        user=user, organization=organization
    )
    if state == "departed":
        membership.delete()
    elif state == "inactive-user":
        type(user)._base_manager.filter(pk=user.pk).update(is_active=False)
    else:
        membership.update(
            **({"is_active": False} if state == "inactive" else {"deleted": True})
        )
    response = client.post(KEYS, {"name": "no-key"}, format="json")
    assert response.status_code in (401, 403), response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()
    assert not AgentccAPIKey.no_workspace_objects.filter(user=user).exists()


@pytest.mark.parametrize(
    "authority",
    ["user_id", "org_id", "type", "scopes", "allow_policy_override"],
)
def test_metadata_cannot_forge_authority(organization, workspace, gateway, authority):
    user = _user_with_level(organization, Level.MEMBER)
    client = session_client(user, organization, workspace)
    response = client.post(
        KEYS, {"name": "no-authority", "metadata": {authority: "forged"}}, format="json"
    )
    assert response.status_code == 400, response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()


@pytest.mark.parametrize("level", [Level.OWNER, Level.MEMBER, Level.VIEWER])
def test_api_key_authentication_cannot_mint(organization, workspace, gateway, level):
    from accounts.models import OrgApiKey

    user = _user_with_level(organization, level)
    credential = OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
        name="fixture",
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=credential.api_key,
        HTTP_X_SECRET_KEY=credential.secret_key,
        HTTP_X_ORGANIZATION_ID=str(organization.pk),
        HTTP_X_WORKSPACE_ID=str(workspace.pk),
    )
    response = client.post(KEYS, {"name": "no-recursive-mint"}, format="json")
    assert response.status_code == 403, response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()


@pytest.mark.parametrize(
    "level", [Level.OWNER, Level.ADMIN, Level.MEMBER, Level.VIEWER]
)
def test_other_org_header_cannot_create(organization, workspace, gateway, level):
    user = _user_with_level(organization, level)
    foreign = Organization.objects.create(name="Not a member")
    client = session_client(user, foreign, workspace)
    response = client.post(KEYS, {"name": "no-cross-org-mint"}, format="json")
    assert response.status_code == 403, response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()


@pytest.mark.parametrize(
    "level", [Level.OWNER, Level.ADMIN, Level.MEMBER, Level.VIEWER]
)
def test_foreign_workspace_header_cannot_fall_back(
    organization, workspace, gateway, level
):
    user = _user_with_level(organization, level)
    foreign = Workspace.no_workspace_objects.create(
        organization=Organization.objects.create(name="Foreign workspace org"),
        name="Foreign",
        is_active=True,
        created_by=user,
    )
    client = session_client(user, organization, foreign)
    response = client.post(KEYS, {"name": "no-workspace-fallback"}, format="json")
    assert response.status_code == 403, response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()


def test_user_without_session_provenance_cannot_mint(organization, workspace, gateway):
    user = _user_with_level(organization, Level.OWNER)
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    try:
        response = client.post(KEYS, {"name": "no-session"}, format="json")
        assert response.status_code == 403, response.data
        gateway.create_key.assert_not_called()
        gateway.import_committed_key.assert_not_called()
    finally:
        client.stop_workspace_injection()


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
def test_own_reads_and_revocation_not_config_authority(
    organization, workspace, gateway, level
):
    user = _user_with_level(organization, level)
    peer = _user_with_level(organization, level)
    client = session_client(user, organization, workspace)
    own = AgentccAPIKey.no_workspace_objects.create(
        organization=organization, user=user, name="own", gateway_key_id="own"
    )
    other = AgentccAPIKey.no_workspace_objects.create(
        organization=organization, user=peer, name="other", gateway_key_id="other"
    )
    assert [row["id"] for row in client.get(KEYS).data["result"]] == [str(own.pk)]
    assert client.get(f"{KEYS}{other.pk}/").status_code == 404
    for suffix in ("revoke/", ""):
        response = (client.post if suffix else client.delete)(
            f"{KEYS}{other.pk}/{suffix}"
        )
        assert response.status_code == 404
    for method in (client.put, client.patch):
        assert (
            method(f"{KEYS}{own.pk}/", {"name": "renamed"}, format="json").status_code
            == 403
        )
    assert client.post(f"{KEYS}sync/").status_code == 403
    assert (
        client.post("/agentcc/provider-credentials/", {}, format="json").status_code
        == 403
    )
    assert client.post("/agentcc/org-configs/", {}, format="json").status_code == 403
    revoked = client.post(f"{KEYS}{own.pk}/revoke/")
    assert revoked.status_code == 200
    assert revoked.data["result"]["gateway_revocation_confirmed"] is True
    gateway.revoke_key_confirmed.assert_called_once_with("own")
    gateway.revoke_key.assert_not_called()
    own.refresh_from_db()
    assert own.status == AgentccAPIKey.REVOKED
    assert client.delete(f"{KEYS}{own.pk}/").status_code == 204
    gateway.revoke_key.assert_called_once_with("own")


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
@pytest.mark.parametrize("legacy", [False, True])
def test_admin_can_revoke_member_key_but_not_cross_org(
    organization, workspace, gateway, level, legacy
):
    user = _user_with_level(organization, level)
    if legacy:
        OrganizationMembership._base_manager.filter(
            user=user, organization=organization
        ).update(level=None)
    peer = _user_with_level(organization, Level.MEMBER)
    key = AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        user=peer,
        name="Member key",
        gateway_key_id="member-key",
    )
    foreign = AgentccAPIKey.no_workspace_objects.create(
        organization=Organization.objects.create(name="Foreign"),
        user=peer,
        name="Foreign key",
        gateway_key_id="foreign-key",
    )
    client = session_client(user, organization, workspace)
    assert client.post(f"{KEYS}{foreign.pk}/revoke/").status_code == 404
    assert client.post(f"{KEYS}{key.pk}/revoke/").status_code == 200
    gateway.revoke_key_confirmed.assert_called_once_with("member-key")
    gateway.revoke_key.assert_not_called()


def test_service_or_grant_provenance_never_counts_as_session():
    from types import SimpleNamespace

    from accounts.authentication import APIKeyAuthentication
    from agentcc.key_permissions import has_personal_key_session

    class ServiceAuthentication(APIKeyAuthentication):
        pass

    for authenticator in (None, ServiceAuthentication()):
        request = SimpleNamespace(
            successful_authenticator=authenticator,
            auth="opaque",
            credential_provenance="service",
        )
        assert not has_personal_key_session(request)
    for marker in ("org_api_key", "auth_scope"):
        request = SimpleNamespace(
            successful_authenticator=APIKeyAuthentication(),
            auth="opaque",
            **{marker: object()},
        )
        assert not has_personal_key_session(request)


def test_issuance_restores_browsing_workspace(organization, workspace, gateway):
    from tfc.middleware.workspace_context import get_current_workspace

    user = _user_with_level(organization, Level.MEMBER)
    client = session_client(user, organization, workspace)

    def check_restored(org, **kwargs):
        assert get_current_workspace() == workspace
        assert org == organization
        assert kwargs["user"] == user

    restored = MagicMock(side_effect=check_restored)
    with patch.dict(
        resolve(KEYS).func.cls.create.__globals__, ensure_first_org_config=restored
    ):
        response = client.post(KEYS, {"name": "context-restored"}, format="json")
    assert response.status_code == 201, response.data
    gateway.import_committed_key.assert_called_once()
    restored.assert_called_once()


def test_project_cannot_cross_workspace(organization, workspace, gateway):
    user = _user_with_level(organization, Level.MEMBER)
    other = Workspace.no_workspace_objects.create(
        organization=organization, name="Other", is_active=True, created_by=user
    )
    project = AgentccProject.no_workspace_objects.create(
        organization=organization, workspace=other, name="Not in this workspace"
    )
    client = session_client(user, organization, workspace)
    response = client.post(
        KEYS, {"name": "no-project", "project_id": str(project.pk)}, format="json"
    )
    assert response.status_code == 404, response.data
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()
