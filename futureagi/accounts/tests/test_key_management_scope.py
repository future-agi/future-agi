"""Organization and personal key management follows current memberships."""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import OrgApiKey
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.key_scope_helpers import (
    ROLES,
    make_key,
    session_client,
)
from accounts.views.keys import GetKeysView, SecretKeyAPIViewSet
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.mark.parametrize("tenant", [0, 1])
@pytest.mark.parametrize("level", ROLES)
def test_list_keys_matches_current_role(key_scope, tenant, level):
    scope, other = key_scope[tenant], key_scope[1 - tenant]
    user = scope.users[level]
    system = make_key(scope.org, key_type="system")
    personal = make_key(scope.org, user)
    colleague = make_key(
        scope.org, scope.users[Level.OWNER if level != Level.OWNER else Level.ADMIN]
    )
    foreign = make_key(other.org, key_type="system")
    ingest = make_key(scope.org, user, "ingest", workspace=scope.workspace)
    response = session_client(user, scope.org).get("/accounts/key/get_secret_keys/")
    assert response.status_code == 200
    rows = response.data["result"]["table"]
    ids = {str(row["id"]) for row in rows}
    assert str(personal.pk) in ids
    assert (str(system.pk) in ids) is (level >= Level.ADMIN)
    assert (str(colleague.pk) in ids) is (level >= Level.ADMIN)
    assert ids.isdisjoint({str(foreign.pk), str(ingest.pk)})
    assert response.data["result"]["metadata"]["total_rows"] == len(ids)
    assert all(row["secret_key"] != system.secret_key for row in rows)


@pytest.mark.parametrize("tenant", [0, 1])
@pytest.mark.parametrize("level", ROLES)
@pytest.mark.parametrize("action", ["enable_key", "disable_key", "delete_secret_key"])
def test_system_key_changes_require_current_org_admin(key_scope, tenant, level, action):
    scope = key_scope[tenant]
    user = scope.users[level]
    # Give everyone workspace write permission: only the org-key gate decides.
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        role="workspace_admin", level=Level.WORKSPACE_ADMIN
    )
    key = make_key(scope.org, key_type="system", enabled=action != "enable_key")
    client = session_client(user, scope.org)
    method = client.delete if action == "delete_secret_key" else client.post
    response = method(
        f"/accounts/key/{action}/", {"key_id": str(key.pk)}, format="json"
    )
    permitted = level >= Level.ADMIN
    assert response.status_code == (200 if permitted else 400)
    key.refresh_from_db()
    assert key.deleted is (permitted and action == "delete_secret_key")
    expected_enabled = action != "enable_key"
    if permitted and action != "delete_secret_key":
        expected_enabled = action == "enable_key"
    assert key.enabled is expected_enabled


@pytest.mark.parametrize("action", ["enable_key", "disable_key", "delete_secret_key"])
def test_foreign_system_key_cannot_be_changed_by_other_org_owner(key_scope, action):
    own, other = key_scope
    key = make_key(other.org, key_type="system", enabled=action != "enable_key")
    client = session_client(own.users[Level.OWNER], own.org)
    method = client.delete if action == "delete_secret_key" else client.post
    response = method(
        f"/accounts/key/{action}/", {"key_id": str(key.pk)}, format="json"
    )
    assert response.status_code == 400
    key.refresh_from_db()
    assert not key.deleted
    assert key.enabled is (action != "enable_key")


@pytest.mark.parametrize("action", ["enable_key", "disable_key"])
@pytest.mark.parametrize("own_key", [True, False])
def test_member_manages_only_own_personal_key(key_scope, action, own_key):
    scope = key_scope[0]
    user = scope.users[Level.MEMBER]
    key = make_key(
        scope.org,
        user if own_key else scope.users[Level.OWNER],
        enabled=action != "enable_key",
    )
    response = session_client(user, scope.org).post(
        f"/accounts/key/{action}/", {"key_id": str(key.pk)}, format="json"
    )
    assert response.status_code == (200 if own_key else 400)
    key.refresh_from_db()
    assert key.enabled is (
        (action == "enable_key") if own_key else action != "enable_key"
    )


@pytest.mark.parametrize("state", ["inactive", "deleted", "missing"])
@pytest.mark.parametrize(
    "action",
    [
        "bootstrap",
        "get_secret_keys",
        "generate_secret_key",
        "enable_key",
        "disable_key",
        "delete_secret_key",
    ],
)
def test_key_views_require_active_membership_not_legacy_role(key_scope, state, action):
    scope = key_scope[0]
    user = scope.users[Level.OWNER]
    membership = user.get_membership(scope.org)
    key = make_key(scope.org, key_type="system", enabled=action != "enable_key")
    if state == "missing":
        membership.delete()
    else:
        OrganizationMembership.no_workspace_objects.filter(pk=membership.pk).update(
            **({"is_active": False} if state == "inactive" else {"deleted": True})
        )
    # Isolate the view's membership guard from workspace authentication denials.
    factory = APIRequestFactory()
    if action in ("bootstrap", "get_secret_keys"):
        request = factory.get("/accounts/keys/")
        method = "get"
    else:
        method = "delete" if action == "delete_secret_key" else "post"
        request = getattr(factory, method)(
            "/accounts/key/",
            {"key_name": "new"}
            if action == "generate_secret_key"
            else {"key_id": str(key.pk)},
            format="json",
        )
    request.organization = scope.org
    request.workspace = scope.workspace
    force_authenticate(request, user=user)
    before = OrgApiKey.no_workspace_objects.count()
    view = (
        GetKeysView.as_view()
        if action == "bootstrap"
        else SecretKeyAPIViewSet.as_view({method: action})
    )
    response = view(request)
    assert response.status_code == 403
    clear_workspace_context()
    assert OrgApiKey.no_workspace_objects.count() == before
    key.refresh_from_db()
    assert not key.deleted
    assert key.enabled is (action != "enable_key")


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN, Level.MEMBER])
def test_key_generation_always_binds_personal_key_to_caller(key_scope, level):
    scope = key_scope[0]
    user = scope.users[level]
    response = session_client(user, scope.org).post(
        "/accounts/key/generate_secret_key/", {"key_name": "personal"}, format="json"
    )
    assert response.status_code == 200
    key = OrgApiKey.no_workspace_objects.get(pk=response.data["result"]["key_id"])
    assert key.type == "user"
    assert key.user_id == user.pk
    assert key.organization_id == scope.org.pk


@pytest.mark.parametrize("action", ["enable_key", "disable_key", "delete_secret_key"])
def test_ingest_keys_are_not_managed_by_user_key_endpoints(key_scope, action):
    scope = key_scope[0]
    key = make_key(scope.org, key_type="ingest", enabled=action != "enable_key")
    client = session_client(scope.users[Level.OWNER], scope.org)
    method = client.delete if action == "delete_secret_key" else client.post
    response = method(
        f"/accounts/key/{action}/", {"key_id": str(key.pk)}, format="json"
    )
    assert response.status_code == 400
    key.refresh_from_db()
    assert not key.deleted
    assert key.enabled is (action != "enable_key")
