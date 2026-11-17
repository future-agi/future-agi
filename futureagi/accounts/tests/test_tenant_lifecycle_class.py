"""Tenant lifecycle regressions across native tools, REST and receivers (r4)."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache

from accounts.api_key_policy import admit_api_key
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.tests import test_offboarding_cross_org as support
from accounts.tests.test_offboarding_sibling_routes import team_get
from ai_tools.base import ToolContext
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.tenant_scope import counts_as_member

pytestmark = pytest.mark.django_db
local_only = support.local_only
target = support.target
other_org_data = support.other_org_data


def foreign_credentials(target, org):
    return support.credentials(target, org), OrgApiKey._base_manager.create(
        user=target, organization=org
    )


def additional_credentials(target, org, workspace):
    from accounts.models.service_grant import ServiceGrant
    from agentcc.models import AgentccAPIKey

    gateway = AgentccAPIKey._base_manager.create(
        organization=org,
        user=target,
        gateway_key_id=uuid4().hex,
        name="offboarding-synthetic",
        key_hash=uuid4().hex + uuid4().hex,
    )
    grant = ServiceGrant.objects.create(
        jti=uuid4().hex,
        kind="sandbox",
        kid="test",
        aud="sandbox",
        org=org.pk,
        workspace=workspace.pk,
        user=target.pk,
        via="fa",
        sub="synthetic",
        scope="sandbox",
        iat=1,
        exp=2,
    )
    return gateway, grant


def assert_foreign_credentials(target, tokens, key):
    target.refresh_from_db()
    assert target.is_active
    key.refresh_from_db()
    assert key.enabled and admit_api_key(key).pk == key.pk
    for token, bearer in tokens:
        token.refresh_from_db()
        assert token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}")["token"] == bearer


@pytest.mark.parametrize("tool_name", ["remove_user", "deactivate_user"])
def test_native_tools_leave_foreign_identity_and_credentials_intact(
    tool_name,
    user,
    organization,
    workspace,
    target,
    other_org_data,
    django_capture_on_commit_callbacks,
):
    from ai_tools.tools.users.deactivate_user import (
        DeactivateUserInput,
        DeactivateUserTool,
    )
    from ai_tools.tools.users.remove_user import RemoveUserInput, RemoveUserTool

    membership, ws, grant, memory, dataset = other_org_data
    tokens, key = foreign_credentials(target, ws.organization)
    own_tokens = support.credentials(target, organization)
    own_key = OrgApiKey._base_manager.create(user=target, organization=organization)
    foreign_gateway, foreign_service = additional_credentials(
        target, ws.organization, ws
    )
    own_gateway, own_service = additional_credentials(target, organization, workspace)
    tool, model = (
        (RemoveUserTool, RemoveUserInput)
        if tool_name == "remove_user"
        else (DeactivateUserTool, DeactivateUserInput)
    )
    with django_capture_on_commit_callbacks(execute=True):
        result = tool().execute(
            model(user_id=target.pk), ToolContext(user, organization, workspace)
        )
    assert not result.is_error, result.content
    support.assert_removed(target, organization)
    assert_foreign_credentials(target, tokens, key)
    for row in (membership, grant, memory, dataset):
        row.refresh_from_db()
        assert not row.deleted
    assert membership.is_active and grant.is_active
    for row in (foreign_gateway, foreign_service, own_gateway, own_service):
        row.refresh_from_db()
    assert foreign_gateway.status != foreign_gateway.REVOKED
    assert foreign_service.revoked_at is None
    assert own_gateway.status == own_gateway.REVOKED
    assert own_service.revoked_at is not None
    own_key.refresh_from_db()
    assert not own_key.enabled
    for token, _ in own_tokens:
        token.refresh_from_db()
        assert not token.is_active


def test_org_membership_delete_signal_does_not_clear_foreign_sessions(
    target,
    organization,
    other_org_data,
    django_capture_on_commit_callbacks,
):
    _, ws, *_ = other_org_data
    tokens, key = foreign_credentials(target, ws.organization)
    with django_capture_on_commit_callbacks(execute=True):
        # Queryset deletion is the hard-delete path (instance.delete is soft).
        OrganizationMembership._base_manager.filter(
            user=target, organization=organization
        ).delete()
    assert_foreign_credentials(target, tokens, key)


def test_inactive_person_profile_save_does_not_globally_revoke_keys(
    target, other_org_data
):
    # An org-scoped profile/current-workspace save is not platform deactivation.
    # Bypass the true platform transition so a subsequently issued foreign key
    # can detect the receiver wrongly interpreting every inactive save as revoke.
    _, ws, *_ = other_org_data
    User._base_manager.filter(pk=target.pk).update(is_active=False)
    target.refresh_from_db()
    key = OrgApiKey._base_manager.create(user=target, organization=ws.organization)
    target.current_workspace = None
    target.save(update_fields=["current_workspace"])
    key.refresh_from_db()
    assert key.enabled


def test_user_post_save_with_tenant_context_revokes_only_that_tenant(
    target,
    organization,
    other_org_data,
):
    _, ws, *_ = other_org_data
    _, foreign_key = foreign_credentials(target, ws.organization)
    gateway, grant = additional_credentials(target, ws.organization, ws)
    own_key = OrgApiKey._base_manager.create(user=target, organization=organization)
    # Exercise the receiver itself, even though tenant entrypoints are forbidden
    # from performing this global write by the independent AST guard.
    with patch(
        "tfc.middleware.workspace_context.get_current_organization",
        return_value=organization,
    ):
        target.is_active = False
        target.save(update_fields=["is_active"])
    for row in (foreign_key, gateway, grant, own_key):
        row.refresh_from_db()
    assert foreign_key.enabled
    assert gateway.status != gateway.REVOKED
    assert grant.revoked_at is None
    assert not own_key.enabled


def test_reactivate_is_org_scoped(auth_client, target, organization, other_org_data):
    from accounts.services.offboarding import offboard_organization_member

    _, ws, *_ = other_org_data
    offboard_organization_member(target, organization)
    tokens, key = foreign_credentials(target, ws.organization)
    response = auth_client.post(
        "/accounts/organization/members/reactivate/",
        {"user_id": str(target.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert counts_as_member(target, organization)
    assert_foreign_credentials(target, tokens, key)


def test_reactivate_never_reenables_platform_disabled_identity(
    auth_client,
    target,
    organization,
):
    from accounts.services.offboarding import offboard_organization_member

    offboard_organization_member(target, organization)
    User._base_manager.filter(pk=target.pk).update(is_active=False)
    response = auth_client.post(
        "/accounts/organization/members/reactivate/",
        {"user_id": str(target.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data
    target.refresh_from_db()
    assert not target.is_active


def test_legacy_delete_uses_authoritative_last_owner(
    auth_client, user, organization, target
):
    support.set_actor_level(user, organization, Level.ADMIN)
    OrganizationMembership._base_manager.filter(
        user=target, organization=organization
    ).update(role=OrganizationRoles.OWNER, level=Level.OWNER)
    response = support.remove_member(auth_client, "legacy", target.pk)
    assert response.status_code in (400, 403), response.data
    assert counts_as_member(target, organization)


def invite(client, email, role=OrganizationRoles.ADMIN):
    with (
        patch("accounts.views.workspace_management.track_mixpanel_event"),
        patch("accounts.views.workspace_management.email_helper"),
        patch(
            "accounts.views.workspace_management.log_and_deduct_cost_for_resource_request",
            None,
        ),
    ):
        return client.post(
            "/accounts/team/users/",
            {"members": [{"email": email, "name": "Pending invitee", "role": role}]},
            format="json",
        )


@pytest.mark.parametrize(
    "role", [OrganizationRoles.ADMIN, OrganizationRoles.MEMBER, OrganizationRoles.OWNER]
)
def test_pending_post_displays_requested_role(auth_client, user, organization, role):
    response = invite(auth_client, "r4-pending@futureagi.com", role)
    assert response.status_code == 201, response.data
    pending = User.objects.get(email="r4-pending@futureagi.com")
    result = team_get(user, organization, pending.pk, is_active="false")
    assert result.status_code == 200, result.data
    row = result.data["result"]["results"][0]
    assert row["status"] == "Request Pending"
    assert row["role"] == role
    assert row["organization_role"] == role


def test_removed_pending_invitee_disappears_and_can_be_reinvited(
    auth_client, user, organization
):
    email = "r4-cancelled@futureagi.com"
    response = invite(auth_client, email)
    assert response.status_code == 201, response.data
    pending = User.objects.get(email=email)
    password = pending.password
    before = team_get(user, organization, is_active="false").data["result"]["total"]
    response = support.remove_member(auth_client, "team", pending.pk)
    assert response.status_code == 200, response.data
    assert not counts_as_member(pending, organization)
    assert (
        team_get(user, organization, pending.pk, is_active="false").status_code == 404
    )
    listing = team_get(user, organization, is_active="false").data["result"]
    assert listing["total"] == before - 1
    assert str(pending.pk) not in {row["id"] for row in listing["results"]}
    assert not OrganizationInvite.objects.filter(
        organization=organization, target_email=email, status=InviteStatus.PENDING
    ).exists()
    response = invite(auth_client, email, OrganizationRoles.MEMBER)
    assert response.status_code == 201, response.data
    pending.refresh_from_db()
    assert pending.password == password and not pending.is_active
    assert User.objects.filter(email=email).count() == 1
    result = team_get(user, organization, pending.pk, is_active="false")
    assert result.status_code == 200, result.data
    assert result.data["result"]["results"][0]["role"] == OrganizationRoles.MEMBER
