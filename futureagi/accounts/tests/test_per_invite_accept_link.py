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
            read_invite_token(default_token_generator.make_token(invited_user))
            is None
        )
