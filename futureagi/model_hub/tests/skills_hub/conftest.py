"""Skills fixtures reuse the root's real user/org/workspace memberships."""

import pytest

from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.versions import create_skill, publish_files


@pytest.fixture
def hub_actor(user):
    return Actor(user=user, api_key=None)


@pytest.fixture
def make_skill_files():
    def make(name="help-desk", body="Read the request and explain the next safe step.", extra=None):
        files = [{
            "path": "SKILL.md",
            "content": (
                f"---\nname: {name}\n"
                "description: Use when a support request needs a clear and verifiable next step.\n"
                f"---\n\n# Instructions\n\n{body}\n"
            ),
        }]
        return files + (extra or [])
    return make


@pytest.fixture
def hub_skill(organization, workspace, hub_actor):
    return create_skill(
        organization=organization, workspace=workspace, actor=hub_actor,
        name="help-desk", description="Use when a support request needs a clear and verifiable next step.",
        tags=["support"],
    )


@pytest.fixture
def publish_skill(hub_actor, make_skill_files):
    def publish(skill, *, files=None, base_version="latest", labels=None, **kwargs):
        skill.refresh_from_db(fields=["latest_version_number"])
        if base_version == "latest":
            base_version = skill.latest_version_number or None
        return publish_files(
            skill=skill, actor=hub_actor,
            files=files if files is not None else make_skill_files(skill.name),
            note=kwargs.pop("note", ""), base_version=base_version,
            labels=labels or [], acknowledge_warnings=kwargs.pop("acknowledge_warnings", True),
            source={"type": "api"}, **kwargs,
        )
    return publish
