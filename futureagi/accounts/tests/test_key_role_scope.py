"""API-key bootstrap preserves the caller's organization and role."""

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.key_scope_helpers import (
    ROLES,
    key_client,
    make_key,
    session_client,
)
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
KEYS_URL = "/accounts/keys/"


def returned_key(response):
    assert response.status_code == 200, response.data
    assert response.data["status"] == "success"
    assert set(response.data["data"]) == {"id", "api_key", "secret_key"}
    clear_workspace_context()
    key = OrgApiKey.no_workspace_objects.get(id=response.data["data"]["id"])
    assert response.data["data"]["api_key"] == key.api_key
    assert response.data["data"]["secret_key"] == key.secret_key
    return key


@pytest.mark.parametrize("tenant", [0, 1])
@pytest.mark.parametrize("level", ROLES)
@pytest.mark.parametrize("legacy", [False, True])
def test_bootstrap_role_and_authenticated_identity(key_scope, tenant, level, legacy):
    scope = key_scope[tenant]
    user = scope.users[level]
    if legacy:
        OrganizationMembership.no_workspace_objects.filter(user=user).update(level=None)
    key = returned_key(session_client(user, scope.org).get(KEYS_URL))
    expected_type = "system" if level >= Level.ADMIN else "user"
    assert key.type == expected_type
    assert key.organization_id == scope.org.id
    assert key.user_id == (None if expected_type == "system" else user.id)

    request = Request(
        APIRequestFactory().get(
            KEYS_URL, HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key
        )
    )
    authenticated, _ = APIKeyAuthentication().authenticate(request)
    expected_user = scope.users[Level.OWNER] if expected_type == "system" else user
    assert authenticated.pk == expected_user.pk
    assert request.organization.pk == scope.org.pk
    clear_workspace_context()

    # SDK init accepts either key type and must return the same credential.
    assert returned_key(key_client(key).get(KEYS_URL)).pk == key.pk
    target = make_key(scope.org, scope.users[Level.OWNER], name="owner-action")
    response = key_client(key).delete(
        "/accounts/key/delete_secret_key/", {"key_id": str(target.id)}, format="json"
    )
    assert response.status_code == (
        200 if expected_type == "system" else 400 if level == Level.MEMBER else 403
    )
    target.refresh_from_db()
    assert target.deleted is (expected_type == "system")


@pytest.mark.parametrize("tenant", [0, 1])
def test_workspace_admin_without_org_admin_receives_personal_key(key_scope, tenant):
    scope = key_scope[tenant]
    user = scope.users[Level.VIEWER]
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        level=Level.WORKSPACE_ADMIN, role="workspace_admin"
    )
    key = returned_key(session_client(user, scope.org).get(KEYS_URL))
    assert key.type == "user"
    assert key.user_id == user.id
    target = make_key(scope.org, scope.users[Level.OWNER])
    response = key_client(key).delete(
        "/accounts/key/delete_secret_key/", {"key_id": str(target.id)}, format="json"
    )
    assert response.status_code == 400
    target.refresh_from_db()
    assert not target.deleted


@pytest.mark.parametrize("membership_state", ["inactive", "deleted", "missing"])
def test_no_active_membership_cannot_bootstrap_with_legacy_owner_field(
    key_scope, membership_state
):
    scope = key_scope[0]
    user = scope.users[Level.OWNER]
    membership = user.get_membership(scope.org)
    if membership_state == "missing":
        membership.delete()
    else:
        OrganizationMembership.no_workspace_objects.filter(pk=membership.pk).update(
            **(
                {"is_active": False}
                if membership_state == "inactive"
                else {"deleted": True}
            )
        )
    before = OrgApiKey.no_workspace_objects.count()
    response = session_client(user, scope.org).get(KEYS_URL)
    assert response.status_code == 403
    assert OrgApiKey.no_workspace_objects.count() == before


@pytest.mark.parametrize("tenant", [0, 1])
@pytest.mark.parametrize("level", ROLES)
def test_foreign_org_header_never_returns_foreign_keys(key_scope, tenant, level):
    own, foreign = key_scope[tenant], key_scope[1 - tenant]
    foreign_key = make_key(foreign.org, key_type="system")
    client = session_client(own.users[level], foreign.org)
    key = returned_key(client.get(KEYS_URL))
    assert key.organization_id == own.org.id
    assert key.pk != foreign_key.pk
    assert key.api_key != foreign_key.api_key
    assert key.secret_key != foreign_key.secret_key

    # API keys remain bound to their own org even with a different org header.
    response = key_client(key, HTTP_X_ORGANIZATION_ID=str(foreign.org.id)).post(
        "/accounts/key/disable_key/", {"key_id": str(foreign_key.pk)}, format="json"
    )
    assert response.status_code in (400, 403)
    foreign_key.refresh_from_db()
    assert foreign_key.enabled
    mixed = key_client(key)
    mixed.credentials(
        HTTP_X_API_KEY=foreign_key.api_key, HTTP_X_SECRET_KEY=key.secret_key
    )
    assert mixed.get(KEYS_URL).status_code == 401


@pytest.mark.parametrize("level", ROLES)
def test_current_membership_not_legacy_org_role_selects_key(key_scope, level):
    home, current = key_scope
    user = home.users[Level.OWNER]
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=current.org, role="Owner", level=level
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=current.workspace,
        role="workspace_member",
        level=Level.MEMBER,
    )
    key = returned_key(session_client(user, current.org).get(KEYS_URL))
    assert key.organization_id == current.org.id
    assert key.type == ("system" if level >= Level.ADMIN else "user")
    if level < Level.ADMIN:
        assert key.user_id == user.id


@pytest.mark.parametrize("key_type", ["user", "mcp", "system"])
def test_key_authenticated_bootstrap_keeps_exact_key(key_scope, key_type):
    scope = key_scope[0]
    user = scope.users[Level.OWNER]
    key = make_key(scope.org, user if key_type != "system" else None, key_type)
    assert returned_key(key_client(key).get(KEYS_URL)).pk == key.pk
    if key_type != "system":
        assert not OrgApiKey.no_workspace_objects.filter(
            organization=scope.org, type="system"
        ).exists()


def test_extra_key_header_does_not_change_bearer_role(key_scope):
    scope = key_scope[0]
    user = scope.users[Level.VIEWER]
    key = returned_key(
        session_client(user, scope.org).get(KEYS_URL, HTTP_X_API_KEY="unused")
    )
    assert key.type == "user"
    assert key.user_id == user.id


@pytest.mark.parametrize("unusable", ["disabled", "deleted", "other-user", "other-org"])
def test_personal_bootstrap_only_reuses_own_enabled_key(key_scope, unusable):
    scope, other = key_scope
    user = scope.users[Level.MEMBER]
    candidate = make_key(
        other.org if unusable == "other-org" else scope.org,
        scope.users[Level.OWNER] if unusable == "other-user" else user,
        enabled=unusable != "disabled",
        deleted=unusable == "deleted",
    )
    client = session_client(user, scope.org)
    key = returned_key(client.get(KEYS_URL))
    assert key.pk != candidate.pk
    assert key.type == "user"
    assert key.user_id == user.id
    assert key.organization_id == scope.org.id
    assert returned_key(client.get(KEYS_URL)).pk == key.pk


def test_disabled_system_key_is_not_reenabled_by_bootstrap(key_scope):
    scope = key_scope[0]
    key = make_key(scope.org, key_type="system", enabled=False)
    response = session_client(scope.users[Level.OWNER], scope.org).get(KEYS_URL)
    assert response.status_code == 403
    key.refresh_from_db()
    assert not key.enabled
