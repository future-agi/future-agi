"""Draft storage accepts incomplete writing but never silently overwrites it."""

import pytest

from model_hub.models.skills_hub import HubSkillDraft
from model_hub.services.skills_hub.drafts import (
    discard_draft,
    get_draft,
    publish_draft,
    save_draft,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import MAX_FILE_BYTES, MAX_FILES, MAX_NOTE
from model_hub.services.skills_hub.types import FileInput

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def other_writer(hub_other_user, organization, workspace):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    membership = OrganizationMembership.no_workspace_objects.create(
        organization=organization,
        user=hub_other_user,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        workspace=workspace,
        user=hub_other_user,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    return hub_other_user


def test_save_denies_user_without_current_membership(hub_skill, hub_other_user):
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill,
            user=hub_other_user,
            files=[],
            note="",
            base_version=None,
            revision=0,
        )
    assert exc.value.code == "access_revoked"
    assert not HubSkillDraft.all_objects.filter(
        skill=hub_skill, user=hub_other_user
    ).exists()


def test_save_incomplete_content_and_revision_conflict(hub_skill, user):
    files: list[FileInput] = [
        {"path": "SKILL.md", "content": "An unfinished draft without frontmatter"}
    ]
    draft = save_draft(
        skill=hub_skill,
        user=user,
        files=files,
        note="In progress",
        base_version=None,
        revision=1,
    )
    assert draft.revision == 2 and draft.files == files
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill, user=user, files=[], note="", base_version=None, revision=1
        )
    assert exc.value.code == "draft_conflict"
    assert exc.value.detail == {
        "revision": 2,
        "updated_at": draft.updated_at.isoformat(),
    }
    assert get_draft(skill=hub_skill, user=user).files == files
    assert not hub_skill.versions.exists() and not hub_skill.labels.exists()


def test_get_is_read_only_and_drafts_are_per_user(
    hub_skill, user, hub_other_user, make_skill_files, other_writer
):
    with pytest.raises(SkillsHubError) as exc:
        get_draft(skill=hub_skill, user=hub_other_user)
    assert exc.value.code == "draft_not_found"
    assert hub_skill.drafts.count() == 1
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill,
            user=hub_other_user,
            files=[],
            note="",
            base_version=None,
            revision=1,
        )
    assert exc.value.detail == {"revision": 0, "updated_at": None}
    theirs = save_draft(
        skill=hub_skill,
        user=hub_other_user,
        files=make_skill_files(),
        note="Other author",
        base_version=None,
        revision=0,
    )
    assert theirs.revision == 1 and hub_skill.drafts.count() == 2
    discard_draft(skill=hub_skill, user=user)
    discard_draft(skill=hub_skill, user=user)
    assert get_draft(skill=hub_skill, user=hub_other_user).pk == theirs.pk


@pytest.mark.parametrize(
    "files,note",
    [
        (
            [
                {"path": f"{index}.txt", "content": "x"}
                for index in range(MAX_FILES + 1)
            ],
            "",
        ),
        ([{"path": "SKILL.md", "content": "x" * (MAX_FILE_BYTES + 1)}], ""),
        (
            [
                {"path": f"{index}.txt", "content": "x" * MAX_FILE_BYTES}
                for index in range(5)
            ],
            "",
        ),
        ([{"path": "a" * 256, "content": "x"}], ""),
        ([{"path": "/".join(["part"] * 9), "content": "x"}], ""),
        ([], "x" * (MAX_NOTE + 1)),
        (
            [
                {
                    "path": "SKILL.md",
                    "content": "---\n" + "x" * (16 * 1024) + "\n---\nBody",
                }
            ],
            "",
        ),
    ],
)
def test_draft_storage_limits(hub_skill, user, files, note):
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill,
            user=user,
            files=files,
            note=note,
            base_version=None,
            revision=1,
        )
    assert exc.value.code == "payload_too_large"
    assert get_draft(skill=hub_skill, user=user).revision == 1


def test_draft_accepts_exact_byte_limits(hub_skill, user):
    files: list[FileInput] = [
        {"path": f"{index}.txt", "content": "é" * (MAX_FILE_BYTES // 2)}
        for index in range(4)
    ]
    draft = save_draft(
        skill=hub_skill,
        user=user,
        files=files,
        note="x" * MAX_NOTE,
        base_version=None,
        revision=1,
    )
    assert draft.revision == 2 and draft.files == files


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a\\b", "a//b", "a./b"])
def test_draft_paths_are_relative(hub_skill, user, path):
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill,
            user=user,
            files=[{"path": path, "content": "x"}],
            note="",
            base_version=None,
            revision=1,
        )
    assert exc.value.code == "invalid_selector"


def test_publish_tombstones_only_its_author_draft(
    hub_skill, user, hub_other_user, make_skill_files, other_writer
):
    save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(),
        note="Saved note",
        base_version=None,
        revision=1,
    )
    save_draft(
        skill=hub_skill,
        user=hub_other_user,
        files=[],
        note="Other draft",
        base_version=None,
        revision=0,
    )
    result = publish_draft(
        skill=hub_skill, user=user, labels=["staging"], acknowledge_warnings=True
    )
    assert result.version.number == 1 and result.version.note == "Saved note"
    assert hub_skill.labels.get(name="staging").version_id == result.version.pk
    assert not hub_skill.drafts.filter(user=user).exists()
    tombstone = HubSkillDraft.all_objects.get(skill=hub_skill, user=user)
    assert tombstone.revision == 3 and tombstone.discarded_at is not None
    assert tombstone.files == [] and tombstone.note == ""
    assert get_draft(skill=hub_skill, user=hub_other_user).note == "Other draft"


def test_failed_publish_retains_draft_and_force_can_resolve_stale_base(
    hub_skill, user, publish_skill, make_skill_files
):
    save_draft(
        skill=hub_skill, user=user, files=[], note="", base_version=None, revision=1
    )
    with pytest.raises(SkillsHubError) as exc:
        publish_draft(skill=hub_skill, user=user, labels=[], acknowledge_warnings=True)
    assert exc.value.code == "checks_failed"
    assert get_draft(skill=hub_skill, user=user).revision == 2
    save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(body="Draft change"),
        note="",
        base_version=None,
        revision=2,
    )
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        publish_draft(skill=hub_skill, user=user, labels=[], acknowledge_warnings=True)
    assert exc.value.code == "stale_base"
    assert get_draft(skill=hub_skill, user=user).revision == 3
    result = publish_draft(
        skill=hub_skill, user=user, labels=[], acknowledge_warnings=True, force=True
    )
    assert result.version.number == 2
    assert not hub_skill.drafts.filter(user=user).exists()


def test_unchanged_publish_also_discards_draft(
    hub_skill, user, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill)
    save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(),
        note="",
        base_version=1,
        revision=1,
    )
    result = publish_draft(
        skill=hub_skill, user=user, labels=[], acknowledge_warnings=True
    )
    assert result.unchanged and result.version.pk == first.version.pk
    assert not hub_skill.drafts.exists()
    tombstone = HubSkillDraft.all_objects.get(skill=hub_skill, user=user)
    assert tombstone.revision == 3 and tombstone.discarded_at is not None
    assert tombstone.files == []


@pytest.mark.parametrize("content", ["\x00", "\x01", "\x85", "\ud800"])
def test_draft_rejects_nontext_before_database_write(hub_skill, user, content):
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill,
            user=user,
            files=[{"path": "SKILL.md", "content": content}],
            note="",
            base_version=None,
            revision=1,
        )
    assert exc.value.code == "invalid_selector"
    assert get_draft(skill=hub_skill, user=user).revision == 1


def test_save_rejects_nonexistent_base(hub_skill, user):
    with pytest.raises(SkillsHubError) as exc:
        save_draft(
            skill=hub_skill, user=user, files=[], note="", base_version=99, revision=1
        )
    assert exc.value.code == "version_not_found"
