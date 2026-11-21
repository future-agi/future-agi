"""Explicit two-dimensional tenancy, live filtering and workspace-wide stats."""

from datetime import timedelta

import pytest
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.selectors.skills_hub import (
    get_skill_for_scope,
    list_skills,
    skills_for_request_scope,
    workspace_stats,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.resolve import resolve
from model_hub.services.skills_hub.usage import record_fetch
from model_hub.services.skills_hub.versions import archive_skill, create_skill
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_other_workspace_and_organization_are_invisible(
    hub_skill,
    publish_skill,
    organization,
    workspace,
    hub_other_workspace,
    hub_other_organization,
    hub_other_org_workspace,
):
    publish_skill(hub_skill, labels=["production"])
    for org, ws in [
        (organization, hub_other_workspace),
        (hub_other_organization, hub_other_org_workspace),
        (hub_other_organization, workspace),
    ]:
        assert not skills_for_request_scope(org, ws).exists()
        assert not list_skills(org, ws).exists()
        with pytest.raises(SkillsHubError) as exc:
            get_skill_for_scope(org, ws, hub_skill.pk)
        assert exc.value.code == "skill_not_found" and exc.value.status == 404
        with pytest.raises(SkillsHubError) as exc:
            resolve(organization=org, workspace=ws, name=hub_skill.name)
        assert exc.value.code == "skill_not_found" and exc.value.status == 404


def test_no_scope_fails_closed(organization, workspace):
    for org, ws in [(None, workspace), (organization, None), (None, None)]:
        with pytest.raises(SkillsHubError) as exc:
            skills_for_request_scope(org, ws)
        assert exc.value.code == "workspace_required" and exc.value.status == 403


def test_ambient_context_never_broadens_or_hides_explicit_scope(
    hub_skill, organization, workspace, hub_other_workspace
):
    set_workspace_context(workspace=hub_other_workspace, organization=organization)
    assert list(skills_for_request_scope(organization, workspace)) == [hub_skill]
    clear_workspace_context()
    assert list(skills_for_request_scope(organization, workspace)) == [hub_skill]
    assert not skills_for_request_scope(organization, hub_other_workspace).exists()


def test_same_name_in_other_workspace_is_independent(
    hub_skill, organization, hub_other_workspace, hub_actor
):
    other = create_skill(
        organization=organization,
        workspace=hub_other_workspace,
        actor=hub_actor,
        name=hub_skill.name,
        description=hub_skill.description,
        tags=[],
    )
    assert other.pk != hub_skill.pk
    assert (
        get_skill_for_scope(organization, hub_other_workspace, other.pk).pk == other.pk
    )


def test_blob_deduplication_is_organization_local(
    hub_skill,
    publish_skill,
    actor_for_workspace,
    hub_other_organization,
    hub_other_org_workspace,
):
    first = publish_skill(hub_skill).version
    foreign_actor = actor_for_workspace(hub_other_org_workspace)
    foreign = create_skill(
        organization=hub_other_organization,
        workspace=hub_other_org_workspace,
        actor=foreign_actor,
        name=hub_skill.name,
        description=hub_skill.description,
        tags=[],
    )
    second = publish_skill(foreign, actor=foreign_actor).version
    assert first.digest == second.digest
    first_file = first.files.get(path="SKILL.md")
    second_file = second.files.get(path="SKILL.md")
    assert first_file.sha256 == second_file.sha256
    assert first_file.blob_id != second_file.blob_id
    assert second_file.blob.organization_id == hub_other_organization.pk


def test_inconsistent_tenant_row_is_never_visible(
    organization, workspace, hub_other_organization
):
    row = HubSkill.no_workspace_objects.create(
        organization=hub_other_organization,
        workspace=workspace,
        name="inconsistent",
        description="Invalid tenant fixture",
    )
    assert (
        not skills_for_request_scope(organization, workspace).filter(pk=row.pk).exists()
    )
    assert (
        not skills_for_request_scope(hub_other_organization, workspace)
        .filter(pk=row.pk)
        .exists()
    )


def test_library_filters_and_workspace_stats_are_independent(
    hub_skill,
    organization,
    workspace,
    user,
    hub_actor,
    publish_skill,
    hub_other_workspace,
):
    version = publish_skill(hub_skill, labels=["production"]).version
    other = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="release-review",
        description="Use when reviewing a release before publication.",
        tags=["review"],
    )
    foreign = create_skill(
        organization=organization,
        workspace=hub_other_workspace,
        actor=hub_actor,
        name="foreign-skill",
        description=hub_skill.description,
        tags=["support"],
    )
    foreign_version = publish_skill(
        foreign, labels=["production"], expected_release_revision=0
    ).version
    for item in [version, version, foreign_version]:
        record_fetch(
            version=item,
            channel="api",
            consumer_key=f"user:{user.pk}",
            consumer_label="Synthetic author",
        )
    # A historical bucket outside the seven calendar-day window is excluded.
    hub_skill.usage_days.create(
        version=version,
        day=timezone.now().date() - timedelta(days=7),
        channel="download",
        consumer_key=f"user:{user.pk}",
        consumer_label="Synthetic author",
        count=50,
        last_at=timezone.now() - timedelta(days=7),
    )
    assert list(list_skills(organization, workspace, search="ReLeAsE")) == [other]
    assert list(list_skills(organization, workspace, search="SUPPORT")) == [hub_skill]
    assert list(list_skills(organization, workspace, tag="review")) == [other]
    assert list(list_skills(organization, workspace, label="production")) == [hub_skill]
    assert list(list_skills(organization, workspace, label="latest")) == [hub_skill]
    assert list(list_skills(organization, workspace, sort="name", order="asc")) == [
        hub_skill,
        other,
    ]
    assert workspace_stats(organization, workspace, user) == {
        "total": 2,
        "in_production": 1,
        "fetches_7d": 2,
        "my_drafts": 2,
    }
    archive_skill(skill=hub_skill, actor=hub_actor)
    assert workspace_stats(organization, workspace, user) == {
        "total": 1,
        "in_production": 0,
        "fetches_7d": 0,
        "my_drafts": 1,
    }
