"""Membership audit guards must hold through a real transaction commit."""
import uuid

import pytest
from django.db import connection, transaction

from accounts.models.audit_log import AuditLog
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db(transaction=True)


def _member():
    org = Organization.objects.create(name="Deletion probe")
    user = User.objects.create_user(
        email=f"delete-{uuid.uuid4().hex}@example.test",
        password="deletion-probe-password", name="Deletion probe member",
        organization=org, organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user, organization=org,
        defaults={"role": OrganizationRoles.MEMBER, "level": Level.MEMBER, "is_active": True},
    )
    return org, user, membership


def test_hard_organization_delete_with_membership_commits():
    org, user, membership = _member()
    org_id, member_id = org.id, membership.id
    assert OrganizationMembership.no_workspace_objects.filter(pk=member_id).exists()
    with transaction.atomic():
        org.delete()
        # Force deferred FK checks now as well as checking the transaction exit.
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert not Organization.objects.filter(pk=org_id).exists()
    assert not OrganizationMembership.no_workspace_objects.filter(pk=member_id).exists()
    assert not AuditLog.objects.filter(organization_id=org_id).exists()


def test_ordinary_membership_removal_keeps_the_audit():
    org, user, membership = _member()
    # Model.delete() is soft-delete; the ordinary hard removal signal is
    # exercised through the queryset, not a hand-sent signal.
    with transaction.atomic():
        OrganizationMembership.no_workspace_objects.filter(pk=membership.pk).delete()
    assert Organization.objects.filter(pk=org.pk).exists()
    assert AuditLog.objects.filter(
        organization=org, action="member.removed", scope="organization",
        target_id=str(user.id),
    ).exists()
