"""``counts_as_member`` in the integration ``tenant_scope`` module.

FA dev integration (SC1): the helper that decides whether an
administrator's remove, delete, rename or re-invite reaches an account joins
the integration module, whose rule is that a stand-in (an unsaved instance,
see ``_stored_account``) costs no database access.
"""

import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tenant_scope import counts_as_member


@pytest.fixture(autouse=True)
def _no_bound_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


# No ``db`` fixture: pytest-django raises on any query, so this test also
# proves that a stand-in costs no database access.
@pytest.mark.unit
def test_counts_as_member_never_counts_an_unsaved_stand_in():
    organization = Organization(name="Unsaved stand-in organization")
    stand_in = User(
        email="stand-in@example.invalid", organization=organization, is_active=False
    )
    assert stand_in.pk is not None and stand_in._state.adding

    assert counts_as_member(stand_in, organization) is False
    assert counts_as_member(stand_in, None) is False


def _account(organization, label, *, is_active=True):
    user = User.objects.create_user(
        email=f"member-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="memberpass123",
        name=f"Member {label}",
        organization=organization,
        organization_role="Member",
    )
    if user.is_active != is_active:
        user.is_active = is_active
        user.save(update_fields=["is_active"])
    return user


def _membership(user, organization, *, is_active):
    membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        defaults={"role": "Member", "level": Level.MEMBER, "is_active": is_active},
    )
    return membership


@pytest.mark.integration
def test_counts_as_member_counts_an_active_member(db):
    organization = Organization.objects.create(name="Members")
    member = _account(organization, "active")
    _membership(member, organization, is_active=True)

    assert counts_as_member(member, organization) is True


@pytest.mark.integration
def test_counts_as_member_skips_a_removed_member_whatever_the_legacy_field(db):
    organization = Organization.objects.create(name="Former members")
    removed = _account(organization, "removed")
    _membership(removed, organization, is_active=False)

    assert removed.organization_id == organization.id
    assert counts_as_member(removed, organization) is False


@pytest.mark.integration
def test_counts_as_member_counts_a_pending_invitee(db):
    organization = Organization.objects.create(name="Invited")
    inviter = _account(organization, "inviter")
    _membership(inviter, organization, is_active=True)
    invitee = _account(organization, "invitee", is_active=False)
    # A pending invitation as the invite flow records it: the inviter on the
    # account and on its inactive membership row (tenant_scope's rule).
    invitee.invited_by = inviter
    invitee.save(update_fields=["invited_by"])
    membership = _membership(invitee, organization, is_active=False)
    membership.invited_by = inviter
    membership.save(update_fields=["invited_by"])

    assert counts_as_member(invitee, organization) is True
    assert counts_as_member(invitee, Organization.objects.create(name="Other")) is (
        False
    )
