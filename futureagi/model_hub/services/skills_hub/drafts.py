"""Per-author drafts with optimistic revisions and serialized publication."""

from __future__ import annotations

import unicodedata

from django.db import transaction
from django.utils import timezone

from accounts.models.user import User
from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.selectors.skills_hub import scoped_skill
from model_hub.services.skills_hub.checks import path_directory_collisions
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_FRONTMATTER_BYTES,
    MAX_NOTE,
    MAX_PATH,
    MAX_PATH_SEGMENTS,
    MAX_VERSION_BYTES,
)
from model_hub.services.skills_hub.types import Actor, FileInput, PublishResult
from model_hub.services.skills_hub.versions import publish_files


def _validate_payload(files: list[FileInput], note: str) -> None:
    """Bound storage without requiring a publishable SKILL.md or frontmatter."""
    if len(files) > MAX_FILES or len(note) > MAX_NOTE:
        raise SkillsHubError(
            "payload_too_large",
            "Use at most 200 files and a note of 2000 characters.",
            status=413,
        )
    if path_directory_collisions([file["path"] for file in files]):
        raise SkillsHubError(
            "invalid_selector", "A file path cannot also be a directory.", status=400
        )
    total = 0
    paths = set()
    for file in files:
        path, content = file["path"], file["content"]
        segments = path.split("/")
        if len(path) > MAX_PATH or len(segments) > MAX_PATH_SEGMENTS:
            raise SkillsHubError(
                "payload_too_large",
                "File paths can have up to 255 characters and 8 segments.",
                status=413,
            )
        if (
            "\\" in path
            or any(
                part in {"", ".", ".."} or part.endswith((" ", "."))
                for part in segments
            )
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in path)
            or path.casefold() in paths
        ):
            raise SkillsHubError(
                "invalid_selector", "Use unique relative file paths.", status=400
            )
        paths.add(path.casefold())
        if any(
            unicodedata.category(char) == "Cs"
            or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
            for char in content
        ):
            raise SkillsHubError(
                "invalid_selector", "Files must contain plain UTF-8 text.", status=400
            )
        try:
            size = len(content.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SkillsHubError(
                "invalid_selector", "Files must contain valid UTF-8 text.", status=400
            ) from exc
        if path == "SKILL.md":
            lines = content.splitlines(keepends=True)
            if lines and lines[0].rstrip("\r\n") == "---":
                end = next(
                    (
                        index
                        for index in range(1, len(lines))
                        if lines[index].rstrip("\r\n") == "---"
                    ),
                    len(lines),
                )
                if len("".join(lines[1:end]).encode("utf-8")) > MAX_FRONTMATTER_BYTES:
                    raise SkillsHubError(
                        "payload_too_large",
                        "Frontmatter can have up to 16 KiB of text.",
                        status=413,
                    )
        total += size
        if size > MAX_FILE_BYTES or total > MAX_VERSION_BYTES:
            raise SkillsHubError(
                "payload_too_large",
                "Use files up to 512 KiB and a draft up to 2 MiB.",
                status=413,
            )


def get_draft(*, skill: HubSkill, user: User) -> HubSkillDraft:
    """Read only this author's draft; a read never creates a draft."""
    skill = scoped_skill(skill)
    try:
        return skill.drafts.get(user=user)
    except HubSkillDraft.DoesNotExist as exc:
        raise SkillsHubError("draft_not_found", "Draft not found.", status=404) from exc


@transaction.atomic
def save_draft(
    *,
    skill: HubSkill,
    user: User,
    files: list[FileInput],
    note: str,
    base_version: int | None,
    revision: int,
) -> HubSkillDraft:
    """Save if the supplied revision still matches, including first-create races."""
    skill = scoped_skill(skill, lock=True)
    draft = HubSkillDraft.all_objects.filter(skill=skill, user=user).first()
    _check_revision(draft, revision)
    current_revision = draft.revision if draft else 0
    _validate_payload(files, note)
    if (
        base_version is not None
        and not skill.versions.filter(number=base_version).exists()
    ):
        raise SkillsHubError("version_not_found", "Base version not found.", status=404)
    if draft is None:
        draft = HubSkillDraft(skill=skill, user=user)
    draft.files = files
    draft.note = note
    draft.base_version = base_version
    draft.revision = current_revision + 1
    draft.discarded_at = None
    draft.save()
    return draft


def _check_revision(draft: HubSkillDraft | None, revision: int) -> None:
    # Zero means no live draft, not a reset of the persisted revision sequence.
    current_revision = (
        draft.revision if draft is not None and draft.discarded_at is None else 0
    )
    if revision != current_revision:
        raise SkillsHubError(
            "draft_conflict",
            "This draft changed. Reload it before saving or publishing.",
            status=409,
            detail={
                "revision": current_revision,
                "updated_at": draft.updated_at.isoformat() if draft else None,
            },
        )


def _tombstone(draft: HubSkillDraft) -> None:
    """Erase authoring content but retain the CAS sequence under the skill lock."""
    draft.files = []
    draft.note = ""
    draft.base_version = None
    draft.discarded_at = timezone.now()
    draft.revision += 1
    draft.save(
        update_fields=[
            "files",
            "note",
            "base_version",
            "discarded_at",
            "revision",
            "updated_at",
        ]
    )


@transaction.atomic
def discard_draft(*, skill: HubSkill, user: User) -> None:
    """Discard only this author's draft; repeating discard is harmless."""
    skill = scoped_skill(skill, lock=True)
    draft = skill.drafts.filter(user=user).first()
    if draft is not None:
        _tombstone(draft)


@transaction.atomic
def publish_draft(
    *,
    skill: HubSkill,
    user: User,
    note: str | None = None,
    labels: list[str],
    acknowledge_warnings: bool,
    force: bool = False,
    revision: int | None = None,
) -> PublishResult:
    """Publish and tombstone the locked draft, retaining it on any failure."""
    skill = scoped_skill(skill, lock=True)
    draft = HubSkillDraft.all_objects.filter(skill=skill, user=user).first()
    if revision is not None:
        _check_revision(draft, revision)
    if draft is None or draft.discarded_at is not None:
        raise SkillsHubError("draft_not_found", "Draft not found.", status=404)
    result = publish_files(
        skill=skill,
        actor=Actor(user=user, api_key=None),
        files=draft.files,
        note=draft.note if note is None else note,
        base_version=draft.base_version,
        labels=labels,
        acknowledge_warnings=acknowledge_warnings,
        force=force,
        source={"type": "draft"},
    )
    _tombstone(draft)
    return result
