"""Publication binds reviewed drafts and imported snapshots to their provenance."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, transaction

from model_hub.selectors.skills_hub import scoped_skill
from model_hub.services.skills_hub import drafts
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.versions import publish_by_name

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.mark.parametrize("force", [False, True])
def test_publish_revision_mismatch_retains_draft_and_labels(
    hub_skill, user, make_skill_files, force
):
    saved = drafts.save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(body="Changed in another tab"),
        note="Not reviewed",
        base_version=None,
        revision=1,
    )
    with pytest.raises(SkillsHubError) as error:
        drafts.publish_draft(
            skill=hub_skill,
            user=user,
            revision=1,
            labels=["production"],
            acknowledge_warnings=True,
            force=force,
        )
    assert error.value.code == "draft_conflict" and error.value.status == 409
    assert error.value.detail == {
        "revision": saved.revision,
        "updated_at": saved.updated_at.isoformat(),
    }
    assert drafts.get_draft(skill=hub_skill, user=user).files == saved.files
    assert not hub_skill.versions.exists()
    assert not hub_skill.labels.exists()
    assert not hub_skill.label_events.exists()


@pytest.mark.parametrize("revision", [None, 1])
def test_publish_revision_match_and_omission(hub_skill, user, revision):
    result = drafts.publish_draft(
        skill=hub_skill,
        user=user,
        revision=revision,
        labels=[],
        acknowledge_warnings=True,
    )
    assert result.version.number == 1
    assert not hub_skill.drafts.exists()


@pytest.mark.django_db(transaction=True)
def test_publish_checks_revision_after_waiting_for_concurrent_save(
    hub_skill, user, make_skill_files
):
    """A publisher waiting on the row lock must see the save that commits first."""
    waiting = Event()

    def lock_for_publish(skill, **kwargs):
        waiting.set()
        return scoped_skill(skill, **kwargs)

    def publish():
        close_old_connections()
        try:
            with pytest.raises(SkillsHubError) as error:
                drafts.publish_draft(
                    skill=hub_skill,
                    user=user,
                    revision=1,
                    labels=["production"],
                    acknowledge_warnings=True,
                )
            return error.value.code, error.value.detail["revision"]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            scoped_skill(hub_skill, lock=True)
            with patch.object(drafts, "scoped_skill", side_effect=lock_for_publish):
                pending = pool.submit(publish)
                assert waiting.wait(timeout=10)
            saved = drafts.save_draft(
                skill=hub_skill,
                user=user,
                files=make_skill_files(body="Concurrent save"),
                note="",
                base_version=None,
                revision=1,
            )
        assert pending.result(timeout=10) == ("draft_conflict", 2)
    assert drafts.get_draft(skill=hub_skill, user=user).files == saved.files
    assert not hub_skill.versions.exists() and not hub_skill.labels.exists()


def test_import_source_on_creation_update_and_unchanged_push(
    organization, workspace, hub_actor, make_skill_files
):
    args = {
        "organization": organization,
        "workspace": workspace,
        "actor": hub_actor,
        "files": make_skill_files(),
        "note": "",
        "labels": [],
        "tags": [],
        "acknowledge_warnings": True,
    }
    original = {"type": "import", "filename": "first.zip", "root": "support"}
    first = publish_by_name(**args, source=original)
    skill = first.version.skill
    assert skill.source == original and first.version.source == original
    next_source = {"type": "import", "filename": "second.zip", "root": "desk"}
    unchanged = publish_by_name(**args, source=next_source)
    assert unchanged.unchanged and unchanged.version.pk == first.version.pk
    assert unchanged.version.source == original
    args["files"] = make_skill_files(body="Updated imported instructions")
    second = publish_by_name(**args, source=next_source)
    assert second.version.number == 2 and second.version.source == next_source
    skill.refresh_from_db()
    assert skill.source == original  # identity origin is immutable provenance
    args["files"] = make_skill_files(body="An API update")
    third = publish_by_name(**args)
    assert third.version.source == {"type": "api"}
    skill.refresh_from_db()
    assert skill.source == original


def test_default_push_source_is_api(
    organization, workspace, hub_actor, make_skill_files
):
    result = publish_by_name(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        files=make_skill_files(),
        note="",
        labels=[],
        tags=[],
        acknowledge_warnings=True,
    )
    assert result.version.source == result.version.skill.source == {"type": "api"}
