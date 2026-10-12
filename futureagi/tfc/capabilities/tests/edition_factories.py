"""Small factories for edition-rule tests (organizations, members, invites)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.utils import timezone

from tfc.constants.levels import INVITE_VALIDITY_DAYS, Level
from tfc.constants.roles import OrganizationRoles


def make_org(name: str | None = None):
    from accounts.models.organization import Organization

    return Organization.objects.create(name=name or f"Org {uuid.uuid4().hex[:6]}")


def make_user(email: str | None = None, organization=None, **extra):
    from accounts.models.user import User

    return User.objects.create_user(
        email=email or f"user-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=extra.pop("name", "Edition Test User"),
        organization=organization,
        organization_role=extra.pop("organization_role", OrganizationRoles.MEMBER),
        **extra,
    )


def make_member(
    organization,
    email: str | None = None,
    *,
    active: bool = True,
    level: int = Level.MEMBER,
):
    from accounts.models.organization_membership import OrganizationMembership

    user = make_user(email, organization)
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=Level.to_org_string(level),
        level=level,
        is_active=active,
    )
    return user


def make_owner(organization, email: str | None = None):
    return make_member(organization, email, level=Level.OWNER)


def make_invite(
    organization,
    email: str,
    *,
    expired: bool = False,
    status: str = "Pending",
):
    from accounts.models.organization_invite import OrganizationInvite

    invite = OrganizationInvite.objects.create(
        organization=organization,
        target_email=email,
        level=Level.MEMBER,
        status=status,
    )
    if expired:
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            created_at=timezone.now() - timedelta(days=INVITE_VALIDITY_DAYS + 1)
        )
        invite.refresh_from_db()
    return invite


def make_workspace(organization, created_by, name: str | None = None, **extra):
    from accounts.models.workspace import Workspace

    return Workspace.objects.create(
        name=name or f"WS {uuid.uuid4().hex[:6]}",
        organization=organization,
        created_by=created_by,
        is_default=extra.pop("is_default", False),
        is_active=extra.pop("is_active", True),
        **extra,
    )
