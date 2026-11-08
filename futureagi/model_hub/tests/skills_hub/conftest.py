"""Skills fixtures reuse the root's real user/org/workspace memberships."""

from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.versions import create_skill, publish_files
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def actor_for_workspace():
    """Create foreign fixtures with their own authorized author, never the reader."""

    def make(workspace):
        author = User.objects.create_user(
            email=f"skills-fixture-{uuid4()}@example.test",
            name="Scoped fixture author",
            organization=workspace.organization,
        )
        OrganizationMembership.no_workspace_objects.update_or_create(
            user=author,
            organization=workspace.organization,
            defaults={
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
                "is_active": True,
            },
        )
        return Actor(user=author, api_key=None)

    return make


@pytest.fixture
def hub_actor(user):
    return Actor(user=user, api_key=None)


@pytest.fixture
def make_skill_files():
    def make(
        name="help-desk",
        body="Read the request and explain the next safe step.",
        extra=None,
    ):
        files = [
            {
                "path": "SKILL.md",
                "content": (
                    f"---\nname: {name}\n"
                    "description: Use when a support request needs a clear and verifiable next step.\n"
                    f"---\n\n# Instructions\n\n{body}\n"
                ),
            }
        ]
        return files + (extra or [])

    return make


@pytest.fixture
def hub_skill(organization, workspace, hub_actor):
    # The v1 regression suite exercises migrated identities, whose labels remain
    # unprotected. New-identity protection is covered by v1.1 integration tests.
    skill = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="help-desk",
        description="Use when a support request needs a clear and verifiable next step.",
        tags=["support"],
    )
    skill.protected_label_names = []
    skill.save(update_fields=["protected_label_names"])
    return skill


@pytest.fixture(autouse=True)
def enable_test_slice(settings):
    # Synthetic provider fixtures exercise the enabled service path explicitly.
    # Tests of the operational disabled state override this setting themselves.
    settings.SKILLS_TESTING_ENABLED = True
    # Synthetic qualification tests opt in independently of the production default.
    settings.SKILLS_ASSIST_ENABLED = True


@pytest.fixture
def publish_skill(hub_actor, make_skill_files):
    def publish(skill, *, files=None, base_version="latest", labels=None, **kwargs):
        skill.refresh_from_db(fields=["latest_version_number"])
        if base_version == "latest":
            base_version = skill.latest_version_number or None
        return publish_files(
            skill=skill,
            actor=kwargs.pop("actor", hub_actor),
            files=files if files is not None else make_skill_files(skill.name),
            note=kwargs.pop("note", ""),
            base_version=base_version,
            labels=labels or [],
            acknowledge_warnings=kwargs.pop("acknowledge_warnings", True),
            source={"type": "api"},
            **kwargs,
        )

    return publish


@pytest.fixture
def hub_other_user(organization):
    return User.objects.create_user(
        email="skills-other@example.test",
        name="Other author",
        organization=organization,
    )


@pytest.fixture
def hub_other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        name="Other workspace",
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )


@pytest.fixture
def hub_other_organization(db):
    return Organization.objects.create(name="Other Skills organization")


@pytest.fixture
def hub_other_org_workspace(hub_other_organization, user):
    return Workspace.no_workspace_objects.create(
        name="Other organization workspace",
        organization=hub_other_organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
