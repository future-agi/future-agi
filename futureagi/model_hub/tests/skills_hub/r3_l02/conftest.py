"""Synthetic intake fixtures over real memberships and PostgreSQL records."""

from uuid import uuid4

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def second_principal(organization, workspace):
    user = User.objects.create_user(
        email=f"intake-{uuid4()}@example.test",
        name="Second author",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )
