"""Team visibility and explicit re-admission of retained tenant identities."""

from django.db.models import Exists, OuterRef, Q

from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.services.offboarding import (
    readmit_retained_member as readmit_retained_member,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


def team_members_for_organization(organization):
    """Queryset equivalent of counts_as_member, before counting/pagination.

    Any membership history defeats the legacy FK fallback. Keep the historical
    pending-invite exception for inactive accounts invited by this organization;
    a retained *active* person with an inactive membership is not an invitee.
    """
    all_rows = OrganizationMembership._base_manager.filter(user_id=OuterRef("pk"))
    org_rows = all_rows.filter(organization=organization, deleted=False)
    return User.objects.alias(
        _team_has_history=Exists(all_rows),
        _team_active=Exists(org_rows.filter(is_active=True)),
        _team_invitation=Exists(
            OrganizationInvite.objects.filter(
                organization=organization,
                target_email__iexact=OuterRef("email"),
                status=InviteStatus.PENDING,
            )
        ),
        _team_inactive=Exists(org_rows.filter(is_active=False)),
        _team_pending=Exists(
            org_rows.filter(is_active=False, invited_by_id=OuterRef("invited_by_id"))
        ),
    ).filter(
        Q(_team_active=True)
        | Q(organization=organization, _team_has_history=False)
        | Q(is_active=False, _team_invitation=True, _team_inactive=True)
        | Q(
            organization=organization,
            is_active=False,
            invited_by__isnull=False,
            _team_pending=True,
        )
    )


def active_owner_count(organization):
    """Count authoritative owners plus truly legacy owners, not removed FKs."""
    owners = OrganizationMembership._base_manager.filter(
        user_id=OuterRef("pk"), organization=organization, is_active=True, deleted=False
    ).filter(
        Q(level__gte=Level.OWNER) | Q(level__isnull=True, role=OrganizationRoles.OWNER)
    )
    return (
        team_members_for_organization(organization)
        .filter(is_active=True)
        .alias(_team_owner=Exists(owners))
        .filter(
            Q(_team_owner=True)
            | Q(_team_has_history=False, organization_role=OrganizationRoles.OWNER)
        )
        .count()
    )
