"""r3 regressions for sibling routes and the retained identity's team lifecycle.

Behavioral Django/PostgreSQL tests; do not substitute static tripwires for these.
The independent-connection interleaving also covers both sibling routes in
``test_offboarding_concurrency``.
"""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.api_key_policy import admit_api_key
from accounts.authentication import decode_token
from accounts.models.audit_log import AuditLog
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import test_offboarding_cross_org as support
from accounts.views.workspace_management import ManageTeamView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.audit import mute_audit_signals
from tfc.utils.tenant_scope import acts_in_organization

pytestmark = pytest.mark.django_db
local_only = support.local_only
target = support.target
other_org_data = support.other_org_data
ALL_ENDPOINTS = support.ENDPOINTS + ["member_remove", "deactivate"]


@pytest.mark.parametrize("endpoint", ["member_remove", "deactivate"])
@pytest.mark.parametrize("other_state", ["active", "absent", "inactive", "deleted"])
def test_sibling_removal_preserves_foreign_credentials(
    auth_client,
    organization,
    target,
    other_org_data,
    endpoint,
    other_state,
    django_capture_on_commit_callbacks,
):
    # A1/A2: actual token decoding, API-key admission and tenant grants/data,
    # including zero-membership retention rather than a global is_active write.
    support.test_offboarding_preserves_foreign_data_and_credentials(
        auth_client,
        organization,
        target,
        other_org_data,
        endpoint,
        other_state,
        django_capture_on_commit_callbacks,
    )


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
def test_offboarding_audits_only_acting_org_with_actor_and_prior_roles(
    auth_client, user, organization, target, other_org_data, endpoint
):
    """B1: queryset updates must preserve organization AND workspace audit rows."""
    prior_workspaces = list(
        WorkspaceMembership._base_manager.filter(
            user=target, workspace__organization=organization, is_active=True
        ).values_list("workspace_id", "role", "level")
    )
    before = set(AuditLog._base_manager.values_list("pk", flat=True))
    response = support.remove_member(auth_client, endpoint, target.pk)
    support.assert_success(response, endpoint, target.pk)
    rows = list(
        AuditLog._base_manager.filter(
            action="member.removed", target_id=target.pk
        ).exclude(pk__in=before)
    )
    assert len(rows) == 1 + len(prior_workspaces)
    assert all(row.organization_id == organization.pk for row in rows)
    assert all(row.actor_id == user.pk for row in rows)
    org_row = next(row for row in rows if row.scope == "organization")
    assert org_row.changes == {"role": OrganizationRoles.MEMBER, "level": Level.MEMBER}
    workspace_rows = {
        row.changes["workspace_id"]: row.changes
        for row in rows
        if row.scope == "workspace"
    }
    assert workspace_rows == {
        str(pk): {"workspace_id": str(pk), "role": role, "level": level}
        for pk, role, level in prior_workspaces
    }


def test_offboarding_respects_audit_muting(target, organization, user):
    from accounts.services.offboarding import offboard_organization_member

    before = AuditLog._base_manager.count()
    with mute_audit_signals():
        offboard_organization_member(target, organization)
    assert AuditLog._base_manager.count() == before
    support.assert_removed(target, organization)


def team_get(actor, organization, member_id=None, *, is_active="true"):
    request = APIRequestFactory().get("/accounts/team/users/", {"is_active": is_active})
    request.organization = organization
    force_authenticate(request, user=actor)
    return ManageTeamView.as_view()(request, member_id=member_id)


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
def test_team_list_and_detail_exclude_removed_identity_but_keep_other_org(
    auth_client, user, organization, target, other_org_data, endpoint
):
    """B2: count, detail, role and status use the selected org membership."""
    membership, ws, *_ = other_org_data
    before = team_get(user, organization).data["result"]["total"]
    support.assert_success(
        support.remove_member(auth_client, endpoint, target.pk), endpoint, target.pk
    )
    listing = team_get(user, organization)
    assert listing.status_code == 200, listing.data
    assert listing.data["result"]["total"] == before - 1
    assert str(target.pk) not in {
        row["id"] for row in listing.data["result"]["results"]
    }
    assert team_get(user, organization, target.pk).status_code == 404
    foreign = team_get(target, ws.organization, target.pk)
    assert foreign.status_code == 200, foreign.data
    row = foreign.data["result"]["results"][0]
    assert row["status"] == "Active"
    assert row["role"] == membership.role


@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
def test_team_readd_reuses_retained_identity_without_touching_foreign_credentials(
    auth_client, organization, workspace, target, other_org_data, endpoint
):
    """B3: re-add must not report 'already a member' for a tombstoned identity."""
    _, ws, *_ = other_org_data
    old_grant = WorkspaceMembership._base_manager.get(user=target, workspace=workspace)
    old_membership = OrganizationMembership._base_manager.get(
        user=target, organization=organization
    )
    own_key = OrgApiKey._base_manager.create(user=target, organization=organization)
    support.assert_success(
        support.remove_member(auth_client, endpoint, target.pk), endpoint, target.pk
    )
    foreign_tokens = support.credentials(target, ws.organization)
    foreign_key = OrgApiKey._base_manager.create(
        user=target, organization=ws.organization, workspace=ws
    )
    password_hash = target.password
    with (
        patch("accounts.views.workspace_management.track_mixpanel_event"),
        patch("accounts.views.workspace_management.email_helper"),
        patch(
            "accounts.views.workspace_management.log_and_deduct_cost_for_resource_request",
            None,
        ),
    ):
        response = auth_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": target.email,
                        "name": target.name,
                        "role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
    assert response.status_code == 201, response.data
    target.refresh_from_db()
    assert target.is_active and target.password == password_hash
    assert acts_in_organization(target, organization)
    assert acts_in_organization(target, ws.organization)
    old_membership.refresh_from_db()
    old_grant.refresh_from_db()
    own_key.refresh_from_db()
    assert old_membership.is_active and not old_membership.deleted
    assert (
        old_grant.is_active
        and old_grant.organization_membership_id == old_membership.pk
    )
    assert not own_key.enabled  # Re-admission never restores revoked credentials.
    assert (
        not WorkspaceMembership._base_manager.filter(
            user=target, workspace__organization=organization, is_active=True
        )
        .exclude(pk=old_grant.pk)
        .exists()
    )
    foreign_key.refresh_from_db()
    assert foreign_key.enabled and admit_api_key(foreign_key).pk == foreign_key.pk
    for token, bearer in foreign_tokens:
        token.refresh_from_db()
        assert token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}")["token"] == bearer
    assert decode_token(foreign_tokens[0][1])[0].pk == target.pk


def test_team_preserves_legacy_and_pending_invite_fallback(user, organization):
    legacy = User.objects.create_user(
        email="legacy-team@futureagi.com",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    pending = User.objects.create_user(
        email="pending-team@futureagi.com",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=False,
        invited_by=user,
    )
    OrganizationMembership._base_manager.create(
        user=pending,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=False,
        invited_by=user,
    )
    legacy_response = team_get(user, organization, legacy.pk)
    assert legacy_response.status_code == 200, legacy_response.data
    assert legacy_response.data["result"]["results"][0]["status"] == "Active"
    pending_response = team_get(user, organization, pending.pk, is_active="false")
    assert pending_response.status_code == 200, pending_response.data
    assert pending_response.data["result"]["results"][0]["status"] == "Request Pending"
    OrganizationMembership._base_manager.create(
        user=legacy,
        organization=organization,
        is_active=False,
        deleted=True,
    )
    assert team_get(user, organization, legacy.pk).status_code == 404


@pytest.mark.parametrize("endpoint", ["member_remove", "deactivate"])
def test_sibling_removal_keeps_last_owner_guard(
    auth_client, user, organization, target, endpoint
):
    support.set_actor_level(user, organization, Level.ADMIN)
    OrganizationMembership._base_manager.filter(
        user=target, organization=organization
    ).update(role=OrganizationRoles.OWNER, level=Level.OWNER)
    # Deliberately leave User.organization_role stale: membership is authoritative.
    response = support.remove_member(auth_client, endpoint, target.pk)
    assert response.status_code in (400, 403), response.data
    assert acts_in_organization(target, organization)
