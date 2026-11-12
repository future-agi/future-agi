"""Removed members remain manageable without exposing cancelled invitations."""

from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture(autouse=True)
def local_only():
    with (
        patch("accounts.views.rbac_views.send_invite_email"),
        patch("accounts.views.workspace_management.email_helper"),
        patch("accounts.views.workspace_management.track_mixpanel_event"),
        patch(
            "accounts.views.workspace_management.log_and_deduct_cost_for_resource_request",
            None,
        ),
    ):
        yield


@pytest.fixture
def member_user(user, organization, workspace):
    member = User.objects.create_user(
        email="removed-marker@futureagi.com",
        password="member-test-password",
        name="Removed Member",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.objects.create(
        user=member,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        organization_membership=membership,
        role="Workspace Viewer",
        level=Level.WORKSPACE_VIEWER,
        is_active=True,
    )
    return member


def remove_member(client, member):
    response = client.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(member.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data


def member_rows(client, filter_status=None):
    params = {"limit": 100}
    if filter_status:
        params["filter_status"] = [filter_status]
    response = client.get("/accounts/organization/members/", params)
    assert response.status_code == 200, response.data
    return response.data["result"]["results"]


def assert_removed_row(rows, member):
    matches = [row for row in rows if row["id"] == str(member.pk)]
    assert len(matches) == 1
    assert matches[0]["status"] == "Deactivated"
    assert matches[0]["type"] == "member"
    assert matches[0]["ws_level"] is None
    assert matches[0]["workspaces"] == []
    return matches[0]


def team_invite(client, email):
    response = client.post(
        "/accounts/team/users/",
        {
            "members": [
                {"email": email, "name": "Pending Member", "role": "Member"}
            ]
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    return User.objects.get(email=email)


def test_removed_member_listed_as_deactivated(auth_client, member_user, organization):
    remove_member(auth_client, member_user)
    assert_removed_row(member_rows(auth_client), member_user)
    membership = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    assert membership.deleted and not membership.is_active
    assert membership.removed_at is not None
    assert membership.removed_at == membership.deleted_at


def test_removed_member_in_deactivated_filter(auth_client, member_user):
    remove_member(auth_client, member_user)
    rows = member_rows(auth_client, "Deactivated")
    assert_removed_row(rows, member_user)
    assert all(row["status"] == "Deactivated" for row in rows)


@pytest.mark.parametrize("filter_status", [None, "Deactivated"])
def test_cancelled_invitee_hidden_while_removed_member_remains(
    auth_client, member_user, organization, filter_status
):
    pending = team_invite(auth_client, "cancelled-marker@futureagi.com")
    invitation = OrganizationInvite.objects.get(
        organization=organization,
        target_email=pending.email,
        status=InviteStatus.PENDING,
    )
    response = auth_client.delete(
        "/accounts/organization/invite/cancel/",
        {"invite_id": str(invitation.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data
    remove_member(auth_client, member_user)

    rows = member_rows(auth_client, filter_status)
    assert_removed_row(rows, member_user)
    assert pending.email not in {row["email"] for row in rows}
    membership = OrganizationMembership.all_objects.get(
        user=pending, organization=organization
    )
    assert membership.deleted and not membership.is_active
    assert membership.removed_at is None
    assert membership.deleted_at is not None
    assert not WorkspaceMembership._base_manager.filter(
        user=pending, workspace__organization=organization, is_active=True
    ).exists()
    invitation.refresh_from_db()
    assert invitation.status == InviteStatus.CANCELLED


def test_provisional_team_membership_is_not_marked(auth_client, organization):
    pending = team_invite(auth_client, "provisional-marker@futureagi.com")
    membership = OrganizationMembership.all_objects.get(
        user=pending, organization=organization
    )
    assert not pending.is_active
    assert membership.is_active and not membership.deleted
    assert membership.removed_at is None
    assert OrganizationInvite.objects.filter(
        organization=organization,
        target_email=pending.email,
        status=InviteStatus.PENDING,
    ).exists()


def test_reactivation_clears_marker_and_lists_active(
    auth_client, member_user, organization
):
    membership = OrganizationMembership.objects.get(
        user=member_user, organization=organization
    )
    remove_member(auth_client, member_user)
    assert_removed_row(member_rows(auth_client), member_user)
    response = auth_client.post(
        "/accounts/organization/members/reactivate/",
        {"user_id": str(member_user.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data
    membership = OrganizationMembership.all_objects.get(pk=membership.pk)
    assert membership.removed_at is None
    assert membership.deleted_at is None
    assert membership.is_active and not membership.deleted
    rows = [row for row in member_rows(auth_client) if row["id"] == str(member_user.pk)]
    assert len(rows) == 1 and rows[0]["status"] == "Active"
    assert str(member_user.pk) not in {
        row["id"] for row in member_rows(auth_client, "Deactivated")
    }


def test_reinvite_clears_marker(auth_client, member_user, organization, workspace):
    remove_member(auth_client, member_user)
    membership = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    assert membership.removed_at is not None
    response = auth_client.post(
        "/accounts/organization/invite/",
        {
            "emails": [member_user.email],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.pk), "level": Level.WORKSPACE_VIEWER}
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert member_user.email in response.data["result"]["invited"]
    membership = OrganizationMembership.all_objects.get(pk=membership.pk)
    assert membership.removed_at is None
    assert membership.deleted_at is None
    assert membership.is_active and not membership.deleted


def test_team_readmission_clears_marker(auth_client, member_user, organization):
    remove_member(auth_client, member_user)
    membership = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    assert membership.removed_at is not None
    restored_user = team_invite(auth_client, member_user.email)
    assert restored_user.pk == member_user.pk
    membership = OrganizationMembership.all_objects.get(pk=membership.pk)
    assert membership.removed_at is None
    assert membership.deleted_at is None
    assert membership.is_active and not membership.deleted


def test_foreign_removed_members_never_listed(auth_client, member_user, organization):
    remove_member(auth_client, member_user)
    # This positive control fails on the old list, not just on the new field.
    assert_removed_row(member_rows(auth_client), member_user)
    removed = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    foreign_org = Organization.objects.create(name="Other Organization")
    foreign_user = User.objects.create_user(
        email="foreign-marker@futureagi.com",
        password="foreign-test-password",
        organization=foreign_org,
    )
    for person in (member_user, foreign_user):
        OrganizationMembership.all_objects.create(
            user=person,
            organization=foreign_org,
            role=OrganizationRoles.OWNER,
            level=Level.OWNER,
            is_active=False,
            deleted=True,
            deleted_at=removed.deleted_at,
            removed_at=removed.removed_at,
        )
    for filter_status in (None, "Deactivated"):
        rows = member_rows(auth_client, filter_status)
        own_row = assert_removed_row(rows, member_user)
        assert own_row["org_level"] == Level.MEMBER
        assert foreign_user.email not in {row["email"] for row in rows}


def test_one_row_per_user_with_multiple_removed_memberships(
    auth_client, member_user, organization
):
    remove_member(auth_client, member_user)
    assert_removed_row(member_rows(auth_client), member_user)
    removed = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    OrganizationMembership.all_objects.create(
        user=member_user,
        organization=organization,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=False,
        deleted=True,
        deleted_at=removed.deleted_at,
        removed_at=removed.removed_at,
    )
    for filter_status in (None, "Deactivated"):
        row = assert_removed_row(member_rows(auth_client, filter_status), member_user)
        assert row["org_level"] == Level.VIEWER


def test_live_membership_wins_over_removed_history(
    auth_client, member_user, organization
):
    remove_member(auth_client, member_user)
    assert_removed_row(member_rows(auth_client), member_user)
    removed = OrganizationMembership.all_objects.get(
        user=member_user, organization=organization
    )
    OrganizationMembership.objects.create(
        user=member_user,
        organization=organization,
        role=OrganizationRoles.MEMBER_VIEW_ONLY,
        level=Level.VIEWER,
        is_active=True,
    )
    # Even a newer tombstone must not override the current live membership.
    OrganizationMembership.all_objects.create(
        user=member_user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=False,
        deleted=True,
        deleted_at=removed.deleted_at,
        removed_at=removed.removed_at,
    )
    rows = [row for row in member_rows(auth_client) if row["id"] == str(member_user.pk)]
    assert len(rows) == 1
    assert rows[0]["status"] == "Active"
    assert rows[0]["org_level"] == Level.VIEWER
    assert str(member_user.pk) not in {
        row["id"] for row in member_rows(auth_client, "Deactivated")
    }
