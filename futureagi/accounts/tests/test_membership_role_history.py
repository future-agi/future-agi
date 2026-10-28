"""Organization role resolution respects explicit removal and legacy history."""

from types import SimpleNamespace

import pytest

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.workspace_membership import create_workspace_membership
from accounts.tests.key_scope_helpers import session_client
from accounts.utils import resolve_org, resolve_org_role
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.mark.parametrize("state", ["model_delete", "tombstone_update", "inactive"])
@pytest.mark.parametrize("keep_workspace", [False, True])
def test_removed_owner_has_no_role_or_private_workspace(
    key_scope, state, keep_workspace
):
    scope = key_scope[0]
    removed = scope.users[Level.OWNER]
    private = Workspace.no_workspace_objects.create(
        name="Private workspace",
        organization=scope.org,
        created_by=scope.users[Level.ADMIN],
    )
    membership = removed.get_membership(scope.org)
    if state == "model_delete":
        membership.delete()
    else:
        OrganizationMembership.all_objects.filter(pk=membership.pk).update(
            **(
                {"deleted": True}
                if state == "tombstone_update"
                else {"is_active": False}
            )
        )
    if not keep_workspace:
        WorkspaceMembership.no_workspace_objects.filter(user=removed).update(
            is_active=False
        )
    clear_workspace_context()
    # Mint a real bearer AFTER removal, so revoked-token refusal cannot mask a
    # stale-role fallback. Six public-view cases.
    client = session_client(removed, scope.org)
    response = client.get("/accounts/workspaces/")
    clear_workspace_context()
    if response.status_code == 200:
        assert str(private.pk) not in {
            row["id"] for row in response.data["result"]["workspaces"]
        }
    else:
        assert response.status_code in (400, 403)
    assert resolve_org_role(removed, scope.org) is None
    assert client.get("/accounts/keys/").status_code == 403
    clear_workspace_context()
    assert not OrganizationMembership.no_workspace_objects.filter(
        user=removed, organization=scope.org, is_active=True
    ).exists()
    assert (
        OrganizationMembership.all_objects.filter(
            user=removed, organization=scope.org
        ).count()
        == 1
    )


def test_resolved_no_organization_does_not_fall_back_to_home_org(key_scope):
    user = key_scope[0].users[Level.OWNER]
    assert resolve_org(SimpleNamespace(user=user, organization=None)) is None


def test_unresolved_legacy_request_can_use_home_org(key_scope):
    scope = key_scope[0]
    assert resolve_org(SimpleNamespace(user=scope.users[Level.OWNER])) == scope.org


def test_role_resolver_accepts_no_organization(key_scope):
    assert resolve_org_role(key_scope[0].users[Level.OWNER], None) is None


def test_truly_legacy_role_still_resolves(key_scope):
    scope = key_scope[0]
    user = User.objects.create_user(
        email="legacy-role@example.test",
        password="test-only-password",
        name="Legacy",
        organization=scope.org,
        organization_role="Owner",
    )
    assert not OrganizationMembership.all_objects.filter(user=user).exists()
    assert resolve_org_role(user, scope.org) == "Owner"


@pytest.mark.parametrize("through_view", [False, True])
def test_workspace_add_does_not_readmit_removed_org_member(key_scope, through_view):
    scope = key_scope[0]
    user = scope.users[Level.VIEWER]
    # The public add-user endpoint canonicalizes emails before its lookup.
    # Keep this test on the existing removed user, not a case-distinct new user.
    user.email = user.email.lower()
    user.save(update_fields=["email"])
    membership = user.get_membership(scope.org)
    membership.delete()
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        name="Workspace grant",
        organization=scope.org,
        created_by=scope.users[Level.OWNER],
    )
    if through_view:
        response = session_client(scope.users[Level.OWNER], scope.org).post(
            f"/accounts/workspaces/{workspace.pk}/members/",
            {"users": [{"email": user.email, "role": "workspace_member"}]},
            format="json",
        )
        assert response.status_code == 201
        clear_workspace_context()
        grant = WorkspaceMembership.no_workspace_objects.get(
            user=user, workspace=workspace
        )
    else:
        grant = create_workspace_membership(
            user=user, workspace=workspace, role="workspace_member"
        )
    # A workspace grant alone is NOT an organization re-admission. Explicit
    # RBAC invitation/reactivation is required, even when an admin adds the user.
    assert grant.organization_membership_id is None
    assert not user.can_access_organization(scope.org)
    assert session_client(user, scope.org).get("/accounts/keys/").status_code == 403
    clear_workspace_context()
    membership.refresh_from_db()
    assert membership.deleted
    assert (
        OrganizationMembership.all_objects.filter(
            user=user, organization=scope.org
        ).count()
        == 1
    )
