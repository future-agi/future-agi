"""
Per-invite accept links (TH-4079).

Every invite email's link names the invite it was sent for, so a user
invited by two organizations can accept each one separately. Before this,
the link only carried the user and the backend checked the invite of
``user.organization`` (the first org that invited them).
"""

import pytest
from django.contrib.auth.tokens import default_token_generator

from accounts.invite_tokens import make_invite_token, read_invite_token
from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

INVITED_EMAIL = "per-invite-target@futureagi.com"


def _make_owner(org, email):
    clear_workspace_context()
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=email,
        password="ownerpass123",
        name="Owner",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    Workspace.objects.create(
        name=f"{org.name} default",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return user


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="PerInviteOrgA")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="PerInviteOrgB")


@pytest.fixture
def owner_a(org_a):
    return _make_owner(org_a, "per-invite-owner-a@futureagi.com")


@pytest.fixture
def owner_b(org_b):
    return _make_owner(org_b, "per-invite-owner-b@futureagi.com")


@pytest.fixture
def invited_user(org_a):
    return User.objects.create_user(
        email=INVITED_EMAIL,
        password="unused-Pass-123",
        name="Invited",
        organization=org_a,
        is_active=False,
    )


@pytest.fixture
def pending_invite(org_a, owner_a, invited_user):
    return OrganizationInvite.objects.create(
        organization=org_a,
        target_email=INVITED_EMAIL,
        level=Level.MEMBER,
        workspace_access=[],
        invited_by=owner_a,
        status=InviteStatus.PENDING,
    )


@pytest.fixture
def other_invite(org_b, owner_b, invited_user):
    return OrganizationInvite.objects.create(
        organization=org_b,
        target_email=INVITED_EMAIL,
        level=Level.MEMBER,
        workspace_access=[],
        invited_by=owner_b,
        status=InviteStatus.PENDING,
    )


class TestInviteTokens:
    def test_invite_token_round_trips(self, pending_invite):
        token = make_invite_token(pending_invite)
        assert "." in token
        assert read_invite_token(token) == pending_invite.id

    def test_tampered_invite_token_reads_none(self, pending_invite, other_invite):
        token = make_invite_token(pending_invite)
        forged = f"{other_invite.id}.{token.split('.', 1)[1]}"
        assert read_invite_token(forged) is None

    def test_legacy_django_token_reads_none(self, invited_user):
        assert (
            read_invite_token(default_token_generator.make_token(invited_user)) is None
        )


def _token_from_link(link):
    return link.rstrip("/").rsplit("/", 1)[1]


def _owner_client(owner):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=owner)
    client.set_workspace(
        Workspace.objects.get(organization=owner.organization, is_default=True)
    )
    return client


def _invite_via_api(owner, email):
    response = _owner_client(owner).post(
        "/accounts/organization/invite/",
        {"emails": [email], "org_level": Level.MEMBER, "workspace_access": []},
        format="json",
    )
    assert response.status_code == 200, response.data
    return OrganizationInvite.objects.get(
        organization=owner.organization,
        target_email__iexact=email,
        status=InviteStatus.PENDING,
    )


@pytest.fixture
def sent_emails(monkeypatch):
    import accounts.utils as accounts_utils
    import accounts.views.workspace_management as wm

    sent = []

    def _capture(subject, template, context, recipients, *args, **kwargs):
        sent.append({"template": template, "context": context, "to": recipients})

    monkeypatch.setattr(accounts_utils, "email_helper", _capture)
    monkeypatch.setattr(wm, "email_helper", _capture)
    monkeypatch.setattr(
        wm, "log_and_deduct_cost_for_resource_request", None, raising=False
    )
    return sent


class TestInviteEmailLinks:
    def test_invite_create_email_link_names_that_invite(
        self, owner_a, owner_b, sent_emails
    ):
        email = "two-org-new@futureagi.com"
        invite_a = _invite_via_api(owner_a, email)
        invite_b = _invite_via_api(owner_b, email)

        links = [m["context"]["invite_link"] for m in sent_emails]
        assert [read_invite_token(_token_from_link(link)) for link in links] == [
            invite_a.id,
            invite_b.id,
        ]

    def test_resend_to_active_user_with_pending_invite_sends_link(
        self, owner_b, invited_user, other_invite, sent_emails
    ):
        invited_user.is_active = True
        invited_user.save(update_fields=["is_active"])

        response = _owner_client(owner_b).post(
            "/accounts/organization/invite/resend/",
            {"invite_id": str(other_invite.id)},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert sent_emails[-1]["template"] == "invite_user.html"
        link = sent_emails[-1]["context"]["invite_link"]
        assert read_invite_token(_token_from_link(link)) == other_invite.id

    def test_workspace_invite_email_token_names_invite(
        self, auth_client, workspace, sent_emails
    ):
        email = "ws-per-invite@futureagi.com"
        response = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": [email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.data

        invite = OrganizationInvite.objects.get(target_email__iexact=email)
        assert read_invite_token(sent_emails[-1]["context"]["token"]) == invite.id

    def test_manage_team_invite_email_token_names_invite(
        self, auth_client, workspace, sent_emails
    ):
        email = "team-per-invite@futureagi.com"
        response = auth_client.post(
            "/accounts/team/users/",
            {
                "members": [
                    {
                        "email": email,
                        "name": "Team Per Invite",
                        "organization_role": OrganizationRoles.MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.data

        invite = OrganizationInvite.objects.get(target_email__iexact=email)
        assert read_invite_token(sent_emails[-1]["context"]["token"]) == invite.id
