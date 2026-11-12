"""Invitation lifecycle contracts using synthetic, isolated tenants only."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from accounts.utils import send_invite_email
from accounts.views.rbac_views import InviteCreateAPIView, InviteResendAPIView, MemberListAPIView
from accounts.views.signup import _accept_invitation_mail
from accounts.views.workspace import WorkspaceManagementView
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context, set_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants():
    clear_workspace_context()
    rows = []
    for label in ("a", "b"):
        org = Organization.objects.create(name=f"invitation-fixture-{label}")
        set_workspace_context(organization=org)
        owner = User.objects.create_user(
            email=f"owner-{label}@example.invalid", name=f"Owner {label}",
            organization=org, organization_role="Owner", password="Synthetic-fixture-784!",
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=owner, organization=org, role="Owner", level=Level.OWNER, is_active=True,
        )
        workspace = Workspace.objects.create(
            organization=org, name=f"Workspace {label}", created_by=owner, is_active=True,
        )
        rows.append(SimpleNamespace(org=org, owner=owner, membership=membership, workspace=workspace))
    clear_workspace_context()
    recipient = User.objects.create_user(
        email="recipient@example.invalid", name="Recipient", organization=rows[0].org,
        is_active=False, password="Synthetic-fixture-784!", config={},
    )
    invites = [OrganizationInvite.objects.create(
        organization=row.org, target_email=recipient.email, invited_by=row.owner,
        level=Level.ADMIN, workspace_access=[],
    ) for row in rows]
    yield rows, recipient, invites
    clear_workspace_context()


def emailed_link(user, row):
    with patch("accounts.utils.email_helper") as mail:
        send_invite_email(user.email, row.org, row.owner)
    return mail.call_args.args[2]["invite_link"]


def preview(link):
    uid, token = urlsplit(link).path.rstrip("/").split("/")[-2:]
    return _accept_invitation_mail(SimpleNamespace(method="GET", headers={}, user=AnonymousUser()), uid, token)


def invoke(view, row, payload):
    set_workspace_context(organization=row.org)
    request = APIRequestFactory().post("/accounts/organization/invite/", payload, format="json")
    request.organization = row.org
    force_authenticate(request, user=row.owner)
    return view.as_view()(request)


def test_email_links_are_distinct_for_independent_organization_invitations(tenants):
    rows, user, _ = tenants
    assert emailed_link(user, rows[0]) != emailed_link(user, rows[1])


def test_email_preview_names_the_inviting_organization(tenants):
    rows, user, _ = tenants
    response = preview(emailed_link(user, rows[1]))
    assert response.status_code == 200
    assert response.data["org_name"] == rows[1].org.name


def test_manual_link_list_omits_identity_owned_by_another_organization(tenants):
    rows, _, _ = tenants
    with patch("tfc.ee_gating.is_oss", return_value=True):
        result = MemberListAPIView()._get_invites(rows[1].org, viewer_org_level=Level.OWNER)
    assert len(result) == 1
    assert "invite_link" not in result[0]


def test_expired_invitation_has_no_copy_link(tenants):
    rows, _, invites = tenants
    OrganizationInvite.objects.filter(pk=invites[0].pk).update(created_at=timezone.now() - timedelta(days=9))
    with patch("tfc.ee_gating.is_oss", return_value=True):
        result = MemberListAPIView()._get_invites(rows[0].org, viewer_org_level=Level.OWNER)
    assert result[0]["status"] == InviteStatus.EXPIRED
    assert "invite_link" not in result[0]


def test_resend_demotion_requires_workspace_grants_before_mutating(tenants):
    rows, _, invites = tenants
    with patch("accounts.views.rbac_views.send_invite_email", return_value=True) as mail:
        response = invoke(InviteResendAPIView, rows[0], {"invite_id": str(invites[0].id), "org_level": Level.MEMBER})
    assert response.status_code == 400
    invites[0].refresh_from_db()
    assert invites[0].level == Level.ADMIN
    mail.assert_not_called()


def test_resend_demotion_persists_authorized_workspace_grants(tenants):
    rows, _, invites = tenants
    grants = [{"workspace_id": str(rows[0].workspace.id), "level": Level.WORKSPACE_MEMBER}]
    with patch("accounts.views.rbac_views.send_invite_email", return_value=True):
        response = invoke(InviteResendAPIView, rows[0], {
            "invite_id": str(invites[0].id), "org_level": Level.MEMBER, "workspace_access": grants,
        })
    assert response.status_code == 200
    invites[0].refresh_from_db()
    assert invites[0].workspace_access == grants
    assert invites[0].level == Level.MEMBER


def test_oss_renew_changes_generation_without_sending_mail(tenants):
    rows, user, invites = tenants
    old_link = emailed_link(user, rows[0])
    with patch("tfc.ee_gating.is_oss", return_value=True), patch("accounts.views.rbac_views.send_invite_email") as mail:
        response = invoke(InviteResendAPIView, rows[0], {"invite_id": str(invites[0].id), "send_email": False})
    assert response.status_code == 200
    mail.assert_not_called()
    assert preview(old_link).status_code == 400
    assert preview(emailed_link(user, rows[0])).status_code == 200


def test_delivery_failure_is_an_explicit_sanitized_outcome(tenants):
    rows, user, _ = tenants
    with patch("accounts.utils.email_helper", side_effect=RuntimeError("synthetic transport failure")):
        assert send_invite_email(user.email, rows[0].org, rows[0].owner) is False


def test_resend_does_not_claim_delivery_on_mail_failure(tenants):
    rows, _, invites = tenants
    with patch("accounts.utils.email_helper", side_effect=RuntimeError("synthetic transport failure")):
        response = invoke(InviteResendAPIView, rows[0], {"invite_id": str(invites[0].id)})
    assert response.status_code == 400
    assert "synthetic transport failure" not in str(response.data)
    invites[0].refresh_from_db()
    assert invites[0].status == InviteStatus.PENDING


def test_create_reports_delivery_warning_and_keeps_pending_invite(tenants):
    rows, _, _ = tenants
    with patch("accounts.utils.email_helper", side_effect=RuntimeError("synthetic transport failure")):
        response = invoke(InviteCreateAPIView, rows[0], {"emails": ["new-recipient@example.invalid"], "org_level": Level.ADMIN})
    assert response.status_code == 200
    assert response.data["result"]["delivery_failed"] == ["new-recipient@example.invalid"]
    assert OrganizationInvite.objects.filter(organization=rows[0].org, target_email="new-recipient@example.invalid", status=InviteStatus.PENDING).exists()


def test_workspace_creation_persists_the_invitation_before_email(tenants):
    rows, _, _ = tenants
    email = "workspace-recipient@example.invalid"
    observed = []
    def delivered(*args, **kwargs):
        observed.append(OrganizationInvite.objects.filter(organization=rows[0].org, target_email=email, status=InviteStatus.PENDING).exists())
    with patch("accounts.views.workspace.email_helper", side_effect=delivered):
        response = invoke(WorkspaceManagementView, rows[0], {"name": "New invitation workspace", "emails": [email], "role": "Member"})
    assert response.status_code == 201
    assert observed == [True]


def test_active_recipient_gets_an_accept_only_oss_link(tenants):
    rows, user, _ = tenants
    user.is_active = True
    user.save(update_fields=["is_active"])
    with patch("tfc.ee_gating.is_oss", return_value=True):
        result = MemberListAPIView()._get_invites(rows[1].org, viewer_org_level=Level.OWNER)
    assert "invite_link" in result[0]
    response = preview(result[0]["invite_link"])
    assert response.status_code == 200
    assert response.data["requires_password"] is False
    assert response.data["requires_login"] is True


def test_accept_only_link_cannot_become_an_activation_link(tenants):
    rows, user, _ = tenants
    user.is_active = True
    user.save(update_fields=["is_active"])
    with patch("tfc.ee_gating.is_oss", return_value=True):
        result = MemberListAPIView()._get_invites(rows[0].org, viewer_org_level=Level.OWNER)
    assert "invite_link" in result[0]
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert preview(result[0]["invite_link"]).status_code == 400
