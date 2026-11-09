"""Workspace-reader self-service must not widen configuration or scoped sessions."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts import authentication
from accounts.models import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import AgentccAPIKey
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_personal_key_self_service import gateway as gateway
from agentcc.tests.test_personal_key_self_service import session_client
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
def test_admin_configuration_authority_is_unchanged(
    organization, workspace, gateway, level
):
    user = _user_with_level(organization, level)
    reader(user, workspace)
    # Org admins retain their pre-existing global workspace-write authority.
    assert user.can_write_to_workspace(workspace)
    key = own_key(user, organization)
    client = session_client(user, organization, workspace)
    response = client.patch(
        f"/agentcc/api-keys/{key.pk}/", {"name": "renamed"}, format="json"
    )
    assert response.status_code == 200, response.data
    gateway.update_key.assert_called_once_with(key.gateway_key_id, name="renamed")
    gateway.list_keys.return_value = {"data": []}
    response = client.post("/agentcc/api-keys/sync/")
    assert response.status_code == 200, response.data
    gateway.list_keys.assert_called_once()


@pytest.mark.parametrize(
    "action,method,allowed",
    [
        ("create", "POST", True),
        ("revoke", "POST", True),
        ("destroy", "DELETE", True),
        ("update", "PUT", False),
        ("partial_update", "PATCH", False),
        ("sync", "POST", False),
        ("revoke", "PATCH", False),
        ("create", "DELETE", False),
        (None, "POST", False),
        ("future_mutation", "POST", False),
    ],
)
def test_only_exact_personal_actions_defer_workspace_write(
    organization, workspace, action, method, allowed
):
    from agentcc.key_permissions import PersonalGatewayKeyAccess

    user = _user_with_level(organization, Level.MEMBER)
    reader(user, workspace)
    request = SimpleNamespace(
        user=user,
        organization=organization,
        workspace=workspace,
        headers={},
        query_params={},
        method=method,
        successful_authenticator=authentication.APIKeyAuthentication(),
        auth="verified-session",
    )
    assert (
        PersonalGatewayKeyAccess().has_permission(
            request, SimpleNamespace(action=action)
        )
        is allowed
    )


def reader(user, workspace):
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)


def own_key(user, organization):
    key = AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        name="own",
        gateway_key_id=f"own-{user.pk}",
    )
    AgentccAPIKey.no_workspace_objects.filter(pk=key.pk).update(workspace=None)
    return key


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
def test_reader_own_lifecycle_keeps_peer_and_configuration_denials(
    organization, workspace, gateway, level
):
    user = _user_with_level(organization, level)
    peer = _user_with_level(organization, level)
    reader(user, workspace)
    assert user.can_access_workspace(workspace)
    assert not user.can_write_to_workspace(workspace)
    key, other = own_key(user, organization), own_key(peer, organization)
    client = session_client(user, organization, workspace)
    assert client.post(f"/agentcc/api-keys/{other.pk}/revoke/").status_code == 404
    assert client.delete(f"/agentcc/api-keys/{other.pk}/").status_code == 404
    for method in (client.put, client.patch):
        response = method(
            f"/agentcc/api-keys/{key.pk}/", {"name": "not-renamed"}, format="json"
        )
        assert response.status_code == 403, response.data
    for path in (
        "api-keys/sync",
        "org-configs",
        "provider-credentials",
        "routing-policies",
        "guardrail-policies",
    ):
        assert client.post(f"/agentcc/{path}/", {}, format="json").status_code == 403
    gateway.revoke_key.assert_not_called()
    gateway.update_key.assert_not_called()
    gateway.list_keys.assert_not_called()
    key.refresh_from_db()
    assert key.name == "own"


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("operation", ["create", "revoke", "delete"])
@pytest.mark.parametrize(
    "boundary",
    [
        "foreign-org",
        "foreign-workspace",
        "departed",
        # The legacy authenticator catches a default-workspace uniqueness race.
        # Match request autocommit (ATOMIC_REQUESTS is off), rather than leaving
        # its caught IntegrityError inside pytest's unrelated outer transaction.
        pytest.param(
            "inactive-workspace", marks=pytest.mark.django_db(transaction=True)
        ),
    ],
)
def test_reader_requires_live_explicit_scope(
    organization, workspace, gateway, level, operation, boundary
):
    user = _user_with_level(organization, level)
    reader(user, workspace)
    key = own_key(user, organization)
    org, ws = organization, workspace
    if boundary == "foreign-org":
        org = Organization.objects.create(name="Foreign")
    elif boundary == "foreign-workspace":
        ws = Workspace.no_workspace_objects.create(
            organization=Organization.objects.create(name="Other"),
            name="Other",
            is_active=True,
            created_by=user,
        )
    client = session_client(user, org, ws)
    if boundary == "departed":
        OrganizationMembership._base_manager.filter(
            user=user, organization=organization
        ).update(is_active=False)
    elif boundary == "inactive-workspace":
        from django.db import transaction

        assert transaction.get_autocommit()
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_active=False)
    if operation == "create":
        response = client.post("/agentcc/api-keys/", {"name": "denied"}, format="json")
    elif operation == "revoke":
        response = client.post(f"/agentcc/api-keys/{key.pk}/revoke/")
    else:
        response = client.delete(f"/agentcc/api-keys/{key.pk}/")
    assert response.status_code in (401, 403), response.data
    gateway.create_key.assert_not_called()
    gateway.revoke_key.assert_not_called()


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("operation", ["create", "revoke", "delete", "patch", "sync"])
@pytest.mark.parametrize("has_workspace", [True, False])
def test_auth_scope_keeps_original_write_boundary(
    organization, workspace, gateway, level, operation, has_workspace
):
    user = _user_with_level(organization, level)
    reader(user, workspace)
    key = own_key(user, organization)
    client = session_client(user, organization, workspace)
    if not has_workspace:
        WorkspaceMembership.no_workspace_objects.filter(user=user).delete()
        client._credentials.pop("HTTP_X_WORKSPACE_ID")
    decode = authentication.decode_token

    def scoped(token):
        # Real access-session validation first. Only the auth_scope value is
        # modeled here; this is not a SAML login/retirement integration proof.
        actor, auth, _ = decode(token)
        return actor, auth, SimpleNamespace(org_id=organization.pk)

    with patch.object(authentication, "decode_token", side_effect=scoped) as decoded:
        if operation == "create":
            response = client.post(
                "/agentcc/api-keys/", {"name": "denied"}, format="json"
            )
        elif operation == "revoke":
            response = client.post(f"/agentcc/api-keys/{key.pk}/revoke/")
        elif operation == "delete":
            response = client.delete(f"/agentcc/api-keys/{key.pk}/")
        elif operation == "sync":
            response = client.post("/agentcc/api-keys/sync/")
        else:
            response = client.patch(
                f"/agentcc/api-keys/{key.pk}/", {"name": "denied"}, format="json"
            )
    assert response.status_code == 403, response.data
    decoded.assert_called_once()
    gateway.create_key.assert_not_called()
    gateway.revoke_key.assert_not_called()
    gateway.update_key.assert_not_called()
