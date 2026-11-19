"""Publication, immutable numbering and atomic lifecycle regressions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import close_old_connections

from accounts.models.user import OrgApiKey
from model_hub.models.skills_hub import HubSkillBlob, HubSkillFile, HubSkillVersion
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.versions import (
    archive_skill,
    create_skill,
    publish_by_name,
    publish_files,
    restore_version,
    update_tags,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_create_seeds_only_author_draft(hub_skill, user):
    assert hub_skill.latest_version_number == 0
    assert hub_skill.source == {"type": "blank"}
    draft = hub_skill.drafts.get(user=user)
    assert draft.revision == 1 and draft.base_version is None
    assert draft.files[0]["path"] == "SKILL.md"
    assert hub_skill.name in draft.files[0]["content"]
    assert hub_skill.description in draft.files[0]["content"]
    assert not hub_skill.versions.exists()
    assert not hub_skill.labels.exists()


@pytest.mark.parametrize("name", ["", "Upper", "a--b", "-start", "tail-", "a" * 65])
def test_invalid_name(organization, workspace, hub_actor, name):
    with pytest.raises(SkillsHubError) as exc:
        create_skill(
            organization=organization,
            workspace=workspace,
            actor=hub_actor,
            name=name,
            description="A description",
            tags=[],
        )
    assert (exc.value.code, exc.value.status) == ("invalid_name", 400)


def test_name_unique_until_archived(hub_skill, organization, workspace, hub_actor):
    args = {
        "organization": organization,
        "workspace": workspace,
        "actor": hub_actor,
        "name": hub_skill.name,
        "description": hub_skill.description,
        "tags": [],
    }
    with pytest.raises(SkillsHubError, match="already exists") as exc:
        create_skill(**args)
    assert exc.value.code == "name_taken"
    archive_skill(skill=hub_skill, actor=hub_actor)
    replacement = create_skill(**args)
    assert replacement.pk != hub_skill.pk


@pytest.mark.parametrize(
    "tags", [["bad tag"], ["UPPER"], ["x" * 41], [str(i) for i in range(21)]]
)
def test_tag_limits(hub_skill, hub_actor, tags):
    with pytest.raises(SkillsHubError) as exc:
        update_tags(skill=hub_skill, actor=hub_actor, tags=tags)
    assert exc.value.code == "invalid_tags"


def test_numbering_blob_dedup_and_immutable_previous(
    hub_skill, publish_skill, make_skill_files
):
    first_files = make_skill_files(
        extra=[{"path": "references/shared.txt", "content": "Shared reference"}]
    )
    first = publish_skill(hub_skill, files=first_files).version
    second_files = make_skill_files(body="A revised procedure.", extra=first_files[1:])
    second = publish_skill(hub_skill, files=second_files).version
    assert (first.number, second.number, second.parent_number) == (1, 2, 1)
    assert second.digest != first.digest
    assert HubSkillBlob.objects.count() == 3
    assert HubSkillFile.objects.count() == 4
    assert first.files.get(path="SKILL.md").blob.content == first_files[0]["content"]
    assert (
        first.files.get(path="references/shared.txt").blob_id
        == second.files.get(path="references/shared.txt").blob_id
    )
    hub_skill.refresh_from_db()
    assert hub_skill.latest_version_number == 2
    assert hub_skill.description == second.frontmatter["description"]
    assert second.total_bytes == sum(
        len(file["content"].encode("utf-8")) for file in second_files
    )


def test_unchanged_content_can_promote_without_new_version(hub_skill, publish_skill):
    first = publish_skill(hub_skill)
    repeated = publish_skill(hub_skill, labels=["production"])
    assert repeated.unchanged is True and repeated.version.pk == first.version.pk
    assert hub_skill.versions.count() == 1
    assert hub_skill.labels.get(name="production").version_id == first.version.pk
    assert hub_skill.label_events.count() == 1


def test_stale_base_and_force(hub_skill, publish_skill, make_skill_files):
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        publish_skill(
            hub_skill, base_version=None, files=make_skill_files(body="New content")
        )
    assert exc.value.code == "stale_base"
    assert exc.value.detail == {"base_version": None, "latest_version": 1}
    assert hub_skill.versions.count() == 1
    forced = publish_skill(
        hub_skill,
        base_version=None,
        force=True,
        files=make_skill_files(body="New content"),
    )
    assert forced.version.number == 2 and forced.version.parent_number is None


def test_publish_errors_and_warning_acknowledgment(
    hub_skill, publish_skill, make_skill_files
):
    with pytest.raises(SkillsHubError) as exc:
        publish_skill(hub_skill, files=[])
    assert exc.value.code == "checks_failed" and exc.value.status == 422
    assert exc.value.detail["summary"]["errors"] > 0
    files = make_skill_files(extra=[{"path": "empty.txt", "content": ""}])
    with pytest.raises(SkillsHubError) as exc:
        publish_skill(hub_skill, files=files, acknowledge_warnings=False)
    assert exc.value.code == "warnings_unacknowledged"
    assert not hub_skill.versions.exists()
    assert publish_skill(hub_skill, files=files).checks["summary"]["warnings"] > 0


def test_publish_note_limit(hub_skill, publish_skill):
    with pytest.raises(SkillsHubError) as exc:
        publish_skill(hub_skill, note="x" * 2001)
    assert exc.value.code == "payload_too_large"
    assert not hub_skill.versions.exists()


def test_label_failure_rolls_back_entire_publication(hub_skill, publish_skill):
    with pytest.raises(SkillsHubError) as exc:
        publish_skill(hub_skill, labels=["production", "latest"])
    assert exc.value.code == "invalid_label"
    hub_skill.refresh_from_db()
    assert hub_skill.latest_version_number == 0
    assert not hub_skill.versions.exists()
    assert not hub_skill.labels.exists()
    assert not hub_skill.label_events.exists()
    assert not HubSkillBlob.objects.exists()


def test_restore_is_new_even_when_restoring_latest(
    hub_skill, publish_skill, hub_actor, make_skill_files
):
    first = publish_skill(hub_skill, labels=["production"]).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="Changed instructions")
    ).version
    restored = restore_version(skill=hub_skill, actor=hub_actor, number=1)
    assert restored.version.number == 3 and not restored.unchanged
    assert restored.version.digest == first.digest
    assert restored.version.parent_number == second.number
    assert restored.version.note == "Restored from v1"
    assert hub_skill.labels.get(name="production").version_id == first.pk
    restored_again = restore_version(
        skill=hub_skill, actor=hub_actor, number=3, note="Restore again"
    )
    assert restored_again.version.number == 4
    assert restored_again.version.note == "Restore again"


def test_restore_missing_version(hub_skill, hub_actor):
    with pytest.raises(SkillsHubError) as exc:
        restore_version(skill=hub_skill, actor=hub_actor, number=123)
    assert exc.value.code == "version_not_found"


def test_push_by_name_is_idempotent(
    organization, workspace, hub_actor, make_skill_files
):
    args = {
        "organization": organization,
        "workspace": workspace,
        "actor": hub_actor,
        "files": make_skill_files(),
        "note": "CI push",
        "labels": ["staging"],
        "tags": ["help"],
        "acknowledge_warnings": True,
    }
    first = publish_by_name(**args)
    repeated = publish_by_name(**args)
    assert repeated.unchanged is True
    assert repeated.version.pk == first.version.pk
    assert first.version.source == {"type": "api"}
    assert first.version.skill.source == {"type": "api"}
    assert not first.version.skill.drafts.exists()
    assert first.version.skill.tags == ["help"]


def test_api_key_actor_is_recorded_on_version_and_label(
    hub_skill, organization, workspace, user, make_skill_files
):
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic publish key",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
    )
    result = publish_files(
        skill=hub_skill,
        actor=Actor(user=user, api_key=key),
        files=make_skill_files(),
        note="API publish",
        base_version=None,
        labels=["production"],
        acknowledge_warnings=True,
        source={"type": "api"},
    )
    assert result.version.created_by_id == user.pk
    assert result.version.created_by_api_key_id == key.pk
    assert hub_skill.labels.get(name="production").updated_by_api_key_id == key.pk
    event = hub_skill.label_events.get()
    assert event.actor_id == user.pk and event.actor_api_key_id == key.pk


def test_metadata_updates_do_not_mutate_versions(hub_skill, publish_skill, hub_actor):
    version = publish_skill(hub_skill, labels=["production"]).version
    updated = update_tags(skill=hub_skill, actor=hub_actor, tags=["new", "new"])
    assert updated.tags == ["new"]
    assert updated.versions.count() == 1
    archive_skill(skill=updated, actor=hub_actor)
    assert HubSkillVersion.objects.get(pk=version.pk).digest == version.digest
    assert version.labels.get(name="production").version_id == version.pk


@pytest.mark.django_db(transaction=True)
def test_concurrent_publishes_allocate_distinct_monotonic_numbers(
    hub_skill, hub_actor, make_skill_files
):
    """Two real connections race after checks, not a mocked database lock."""
    from model_hub.services.skills_hub import versions

    barrier = Barrier(2, timeout=15)
    real_checks = versions.run_checks

    def synchronized_checks(*args, **kwargs):
        result = real_checks(*args, **kwargs)
        barrier.wait()
        return result

    def publish(body):
        close_old_connections()
        try:
            return publish_files(
                skill=hub_skill,
                actor=hub_actor,
                files=make_skill_files(body=body),
                note="Concurrent publish",
                base_version=None,
                labels=[],
                acknowledge_warnings=True,
                force=True,
                source={"type": "api"},
            ).version.number
        finally:
            close_old_connections()

    with patch.object(versions, "run_checks", side_effect=synchronized_checks):
        with ThreadPoolExecutor(max_workers=2) as pool:
            numbers = list(
                pool.map(publish, ["First parallel change", "Second parallel change"])
            )
    assert sorted(numbers) == [1, 2]
    hub_skill.refresh_from_db()
    assert hub_skill.latest_version_number == 2
    assert list(
        hub_skill.versions.order_by("number").values_list("number", flat=True)
    ) == [1, 2]
