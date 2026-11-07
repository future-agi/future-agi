"""Skill creation and immutable publication under a per-skill row lock."""

from __future__ import annotations

import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import (
    HubSkill,
    HubSkillBlob,
    HubSkillDraft,
    HubSkillFile,
)
from model_hub.selectors.skills_hub import scoped_skill, skills_for_request_scope
from model_hub.services.skills_hub.checks import estimate_tokens, run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.limits import (
    MAX_DESCRIPTION,
    MAX_NAME,
    MAX_NOTE,
    MAX_TAGS,
    NAME_RE,
    TAG_RE,
)
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.templates_registry import get_template_files
from model_hub.services.skills_hub.types import Actor, FileInput, PublishResult


def _validate_tags(tags: list[str]) -> None:
    if (
        not isinstance(tags, list)
        or len(tags) > MAX_TAGS
        or any(
            not isinstance(tag, str) or not re.fullmatch(TAG_RE, tag) for tag in tags
        )
    ):
        raise SkillsHubError(
            "invalid_tags",
            "Use up to 20 lowercase tags of at most 40 characters.",
            status=400,
        )


def _validate_identity(name: str, description: str, tags: list[str]) -> None:
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= MAX_NAME
        or not re.fullmatch(NAME_RE, name)
    ):
        raise SkillsHubError(
            "invalid_name",
            "Use 1–64 lowercase letters, numbers and single hyphens for the name.",
            status=400,
        )
    if not isinstance(description, str) or not 1 <= len(description) <= MAX_DESCRIPTION:
        raise SkillsHubError(
            "invalid_name", "Add a description of 1–1024 characters.", status=400
        )
    _validate_tags(tags)


def _validate_note(note: str) -> None:
    if len(note) > MAX_NOTE:
        raise SkillsHubError(
            "payload_too_large",
            "Version notes can have up to 2000 characters.",
            status=413,
        )


def _checked(
    files: list[FileInput], *, name: str | None, acknowledge_warnings: bool
) -> dict:
    checks = run_checks(files, expected_name=name)
    if checks["summary"]["errors"]:
        raise SkillsHubError(
            "checks_failed",
            "Fix the errors before publishing.",
            status=422,
            detail=checks,
        )
    if checks["summary"]["warnings"] and not acknowledge_warnings:
        raise SkillsHubError(
            "warnings_unacknowledged",
            "Review and acknowledge the warnings before publishing.",
            status=422,
            detail=checks,
        )
    return checks


def _create_identity(
    *,
    organization: Organization,
    workspace: Workspace,
    actor: Actor,
    name: str,
    description: str,
    tags: list[str],
    source: dict,
) -> HubSkill:
    _validate_identity(name, description, tags)
    scope = skills_for_request_scope(organization, workspace)
    if workspace.organization_id != organization.pk:
        raise SkillsHubError(
            "workspace_required", "Choose a workspace in this organization.", status=403
        )
    try:
        # The savepoint keeps an expected unique-name collision recoverable.
        with transaction.atomic():
            return HubSkill.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                name=name,
                description=description,
                tags=list(dict.fromkeys(tags)),
                source=source,
                created_by=actor.user,
                updated_by=actor.user,
            )
    except IntegrityError as exc:
        if scope.filter(name=name).exists():
            raise SkillsHubError(
                "name_taken", "A skill with this name already exists.", status=409
            ) from exc
        raise


@transaction.atomic
def create_skill(
    *,
    organization: Organization,
    workspace: Workspace,
    actor: Actor,
    name: str,
    description: str,
    tags: list[str],
    template_id: str | None = None,
) -> HubSkill:
    """Create a stable identity and the creator's first revisioned draft."""
    source = (
        {"type": "template", "template_id": template_id}
        if template_id
        else {"type": "blank"}
    )
    skill = _create_identity(
        organization=organization,
        workspace=workspace,
        actor=actor,
        name=name,
        description=description,
        tags=tags,
        source=source,
    )
    files = get_template_files(
        template_id or "blank", name=name, description=description
    )
    if actor.user is not None:
        HubSkillDraft.objects.create(
            skill=skill,
            user=actor.user,
            files=files,
            note="",
            base_version=None,
            revision=1,
        )
    return skill


def _publish_checked(
    *,
    skill: HubSkill,
    actor: Actor,
    files: list[FileInput],
    note: str,
    base_version: int | None,
    labels: list[str],
    checks: dict,
    source: dict,
    force: bool,
    always_new: bool = False,
) -> PublishResult:
    """Caller owns an atomic transaction; the lock serializes all publishers."""
    skill = scoped_skill(skill, lock=True)
    latest_number = skill.latest_version_number or None
    if not force and base_version != latest_number:
        raise SkillsHubError(
            "stale_base",
            "A newer version was published. Compare it before publishing.",
            status=409,
            detail={"base_version": base_version, "latest_version": latest_number},
        )
    hashed = [
        {
            "path": file["path"],
            "content": file["content"],
            "sha256": file_sha256(file["content"]),
        }
        for file in sorted(files, key=lambda file: file["path"])
    ]
    digest = manifest_digest(hashed)
    latest = (
        skill.versions.filter(number=latest_number).first() if latest_number else None
    )
    unchanged = bool(latest is not None and latest.digest == digest and not always_new)
    if unchanged and latest is not None:
        version = latest
    else:
        version = skill.versions.create(
            number=skill.latest_version_number + 1,
            digest=digest,
            note=note,
            created_by=actor.user,
            created_by_api_key=actor.api_key,
            file_count=len(files),
            total_bytes=sum(len(file["content"].encode("utf-8")) for file in files),
            frontmatter=checks["frontmatter"],
            checks=checks,
            parent_number=base_version,
            source=source,
        )
        rows = []
        for file in hashed:
            size = len(file["content"].encode("utf-8"))
            blob, _ = HubSkillBlob.objects.get_or_create(
                organization_id=skill.organization_id,
                sha256=file["sha256"],
                defaults={"content": file["content"], "size_bytes": size},
            )
            rows.append(
                HubSkillFile(
                    version=version,
                    path=file["path"],
                    blob=blob,
                    size_bytes=size,
                    sha256=file["sha256"],
                    token_estimate=estimate_tokens(file["content"]),
                )
            )
        HubSkillFile.objects.bulk_create(rows)
        skill.description = checks["frontmatter"]["description"]
        skill.latest_version_number = version.number
        skill.updated_by = actor.user
        skill.save(
            update_fields=[
                "description",
                "latest_version_number",
                "updated_by",
                "updated_at",
            ]
        )
    for label in dict.fromkeys(labels):
        set_label(skill=skill, actor=actor, name=label, version_number=version.number)
    return PublishResult(version=version, unchanged=unchanged, checks=checks)


@transaction.atomic
def publish_files(
    *,
    skill: HubSkill,
    actor: Actor,
    files: list[FileInput],
    note: str,
    base_version: int | None,
    labels: list[str],
    acknowledge_warnings: bool,
    force: bool = False,
    source: dict,
) -> PublishResult:
    """Check and atomically publish content, optionally moving requested labels."""
    _validate_note(note)
    checks = _checked(files, name=skill.name, acknowledge_warnings=acknowledge_warnings)
    return _publish_checked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        base_version=base_version,
        labels=labels,
        checks=checks,
        source=source,
        force=force,
    )


@transaction.atomic
def publish_by_name(
    *,
    organization: Organization,
    workspace: Workspace,
    actor: Actor,
    files: list[FileInput],
    note: str,
    labels: list[str],
    tags: list[str],
    acknowledge_warnings: bool,
) -> PublishResult:
    """Push from API/MCP without a stale-base check; identical pushes are no-ops."""
    _validate_note(note)
    _validate_tags(tags)
    checks = _checked(files, name=None, acknowledge_warnings=acknowledge_warnings)
    frontmatter = checks["frontmatter"]
    scope = skills_for_request_scope(organization, workspace)
    skill = scope.filter(name=frontmatter["name"]).first()
    if skill is None:
        try:
            skill = _create_identity(
                organization=organization,
                workspace=workspace,
                actor=actor,
                name=frontmatter["name"],
                description=frontmatter["description"],
                tags=tags,
                source={"type": "api"},
            )
        except SkillsHubError as exc:
            if exc.code != "name_taken":
                raise
            skill = scope.get(name=frontmatter["name"])
    skill = scoped_skill(skill, lock=True)
    normalized_tags = list(dict.fromkeys(tags))
    if skill.tags != normalized_tags:
        skill.tags = normalized_tags
        skill.updated_by = actor.user
        skill.save(update_fields=["tags", "updated_by", "updated_at"])
    return _publish_checked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        base_version=skill.latest_version_number or None,
        labels=labels,
        checks=checks,
        source={"type": "api"},
        force=True,
    )


@transaction.atomic
def restore_version(
    *, skill: HubSkill, actor: Actor, number: int, note: str = ""
) -> PublishResult:
    """Restore a snapshot as a new version without moving any labels."""
    skill = scoped_skill(skill, lock=True)
    version = skill.versions.filter(number=number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    files: list[FileInput] = [
        {"path": row.path, "content": row.blob.content}
        for row in version.files.select_related("blob").order_by("path")
    ]
    note = note or f"Restored from v{number}"
    _validate_note(note)
    checks = _checked(files, name=skill.name, acknowledge_warnings=True)
    return _publish_checked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        base_version=skill.latest_version_number or None,
        labels=[],
        checks=checks,
        source={"type": "restore", "version": number},
        force=False,
        always_new=True,
    )


@transaction.atomic
def update_tags(*, skill: HubSkill, actor: Actor, tags: list[str]) -> HubSkill:
    """Change discovery tags without changing any immutable version."""
    _validate_tags(tags)
    skill = scoped_skill(skill, lock=True)
    skill.tags = list(dict.fromkeys(tags))
    skill.updated_by = actor.user
    skill.save(update_fields=["tags", "updated_by", "updated_at"])
    return skill


@transaction.atomic
def archive_skill(*, skill: HubSkill, actor: Actor) -> None:
    """Hide an identity while retaining versions, labels and their history."""
    skill = scoped_skill(skill, lock=True)
    skill.deleted = True
    skill.deleted_at = timezone.now()
    skill.updated_by = actor.user
    skill.save(update_fields=["deleted", "deleted_at", "updated_by", "updated_at"])
