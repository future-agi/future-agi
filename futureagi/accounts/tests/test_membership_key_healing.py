"""Bearer sessions distinguish legacy membership repair from explicit removal."""

import pytest

from accounts.models import OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.workspace_membership import ensure_org_membership
from accounts.tests.key_scope_helpers import key_client, make_key, session_client
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
KEYS_URL = "/accounts/keys/"


@pytest.mark.parametrize("state", ["inactive", "deleted", "delete"])
@pytest.mark.parametrize("workspace_present", [False, True])
def test_removed_membership_is_not_healed_by_existing_bearer(
    key_scope, state, workspace_present
):
    scope = key_scope[0]
    user = scope.users[Level.VIEWER]
    client = session_client(user, scope.org)
    # Prime authentication caches before the membership changes.
    assert client.get(KEYS_URL).status_code == 200
    clear_workspace_context()
    membership = user.get_membership(scope.org)
    if state == "delete":
        membership.delete()
    else:
        OrganizationMembership.all_objects.filter(pk=membership.pk).update(
            **({"is_active": False} if state == "inactive" else {"deleted": True})
        )
    if not workspace_present:
        WorkspaceMembership.no_workspace_objects.filter(user=user).delete()
    key_count = OrgApiKey.no_workspace_objects.count()
    memberships = OrganizationMembership.all_objects.filter(user=user).count()
    workspace_count = WorkspaceMembership.all_objects.filter(user=user).count()
    for _ in range(2):
        assert client.get(KEYS_URL).status_code == 403
        clear_workspace_context()
        assert not user.can_access_organization(scope.org)
        assert user.get_membership_level(scope.org) is None
        assert user.get_organization_role(scope.org) is None
        assert ensure_org_membership(user, scope.org) is None
        assert (
            OrganizationMembership.all_objects.filter(user=user).count() == memberships
        )
        assert (
            WorkspaceMembership.all_objects.filter(user=user).count() == workspace_count
        )
        assert OrgApiKey.no_workspace_objects.count() == key_count


def test_legacy_bearer_repairs_missing_membership_at_viewer_level(key_scope):
    scope = key_scope[0]
    # No prior membership or removal record: this is the documented legacy case,
    # not a deletion disguised as an account that never joined.
    user = User.objects.create_user(
        email="legacy-key-reader@example.test",
        password="test-only-password",
        name="Legacy reader",
        organization=scope.org,
        organization_role="Owner",
    )
    assert not OrganizationMembership.all_objects.filter(user=user).exists()
    assert not WorkspaceMembership.all_objects.filter(user=user).exists()
    client = session_client(user, scope.org)
    response = client.get(KEYS_URL)
    assert response.status_code == 200
    clear_workspace_context()
    membership = user.get_membership(scope.org)
    assert membership.level_or_legacy == Level.VIEWER
    key = OrgApiKey.no_workspace_objects.get(pk=response.data["data"]["id"])
    assert key.type == "user"
    assert key.user_id == user.pk
    assert key_client(key).get(KEYS_URL).status_code == 200
    clear_workspace_context()
    target = make_key(scope.org, scope.users[Level.OWNER])
    denied = key_client(key).delete(
        "/accounts/key/delete_secret_key/", {"key_id": str(target.pk)}, format="json"
    )
    assert denied.status_code == 400
    target.refresh_from_db()
    assert not target.deleted
    assert client.get(KEYS_URL).data["data"]["id"] == str(key.pk)
    clear_workspace_context()
    assert OrganizationMembership.all_objects.filter(user=user).count() == 1


def test_explicit_reinvitation_can_follow_a_removed_membership(key_scope):
    scope = key_scope[0]
    user = scope.users[Level.VIEWER]
    client = session_client(user, scope.org)
    user.get_membership(scope.org).delete()
    assert client.get(KEYS_URL).status_code == 403
    clear_workspace_context()
    # Only an explicit membership grant, not a resolver read, restores access.
    OrganizationMembership.no_workspace_objects.create(
        user=user, organization=scope.org, role="Viewer", level=Level.VIEWER
    )
    response = client.get(KEYS_URL)
    assert response.status_code == 200
    clear_workspace_context()
    key = OrgApiKey.no_workspace_objects.get(pk=response.data["data"]["id"])
    assert key.type == "user"
    assert key.user_id == user.pk
