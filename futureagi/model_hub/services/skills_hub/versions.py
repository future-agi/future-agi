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
    HubSkillVersion,
)
from model_hub.selectors.skills_hub import scoped_skill, skills_for_request_scope
from model_hub.services.skills_hub.checks import (
    estimate_tokens,
    parse_frontmatter,
    run_checks,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import _set_label_locked, _validate_name
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
from model_hub.services.skills_hub.types import MISSING, Actor, FileInput, PublishResult
from model_hub.services.skills_hub.v11.evidence import attach_test_evidence_locked
from model_hub.services.skills_hub.v11.governance import (
    authorize_archive_locked,
    authorize_release_locked,
    principal_for_actor,
)
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)


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
) -> tuple[dict, dict]:
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
    # Checks are redacted diagnostics, never the authoritative publication data.
    root = next(file["content"] for file in files if file["path"] == "SKILL.md")
    frontmatter, _, _ = parse_frontmatter(root)
    assert frontmatter is not None  # Successful checks guarantee a valid root mapping.
    return checks, frontmatter


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
        if HubSkill.no_workspace_objects.filter(
            organization_id=organization.pk, workspace_id=workspace.pk, name=name
        ).exists():
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
    principal = principal_for_actor(skill=skill, actor=actor)
    require_scope_access(principal=principal, write=True)
    require_private_principal(principal=principal)
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


def persist_immutable_version_locked(
    *,
    skill: HubSkill,
    actor: Actor,
    files: list[FileInput],
    note: str,
    parent_number: int | None,
    origin_number: int | None,
    description: str,
    frontmatter: dict,
    checks: dict,
    source: dict,
    always_new: bool = False,
    expected_digest: str | None = None,
) -> tuple[HubSkillVersion, bool]:
    """Persist validated bytes without parsing kind-specific instructions.

    The caller owns the atomic transaction and sorted parent/child locks and must
    authorize ALL effects before calling, including deduplication. No labels move.
    Context callers write the descriptor projection in the same transaction.
    """
    from model_hub.services.skills_hub.drafts import _validate_payload

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Immutable persistence requires an atomic caller.")
    _validate_payload(files, note)
    if (
        not description
        or len(description) > MAX_DESCRIPTION
        or checks["summary"]["errors"]
    ):
        raise SkillsHubError(
            "checks_failed",
            "Validate the complete candidate before publication.",
            status=422,
        )
    if skill.kind == HubSkill.Kind.AGENT_CONTEXT and frontmatter:
        raise SkillsHubError(
            "invalid_context_definition",
            "Context versions have no frontmatter.",
            status=400,
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
    if expected_digest is not None and expected_digest != digest:
        raise SkillsHubError(
            "candidate_changed", "The reviewed content changed.", status=409
        )
    latest = skill.versions.filter(number=skill.latest_version_number).first()
    if latest is not None and latest.digest == digest and not always_new:
        from model_hub.services.skills_hub.v12.resolution_leases import (
            require_distribution_eligibility,
        )

        require_distribution_eligibility(latest)
        return latest, True
    if source.get("provider") == "github":
        source = {
            **source,
            "local_modifications": digest != source.get("upstream_digest"),
        }
    version = skill.versions.create(
        number=skill.latest_version_number + 1,
        digest=digest,
        note=note,
        created_by=actor.user,
        created_by_api_key=actor.api_key,
        file_count=len(files),
        total_bytes=sum(len(file["content"].encode("utf-8")) for file in files),
        frontmatter=frontmatter,
        checks=checks,
        parent_number=parent_number,
        origin_number=origin_number,
        source=source,
    )
    # Global content-addressed keys are acquired in hash order to avoid deadlock.
    content_by_hash = {file["sha256"]: file["content"] for file in hashed}
    blobs = {}
    for sha256, content in sorted(content_by_hash.items()):
        blobs[sha256], _ = HubSkillBlob.objects.get_or_create(
            organization_id=skill.organization_id,
            sha256=sha256,
            defaults={"content": content, "size_bytes": len(content.encode("utf-8"))},
        )
    HubSkillFile.objects.bulk_create(
        [
            HubSkillFile(
                version=version,
                path=file["path"],
                blob=blobs[file["sha256"]],
                size_bytes=len(file["content"].encode("utf-8")),
                sha256=file["sha256"],
                token_estimate=estimate_tokens(file["content"]),
            )
            for file in hashed
        ]
    )
    skill.description = description
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
    return version, False


def _publish_checked(
    *,
    skill: HubSkill,
    actor: Actor,
    files: list[FileInput],
    note: str,
    base_version: int | None,
    labels: list[str],
    checks: dict,
    frontmatter: dict,
    source: dict,
    force: bool,
    always_new: bool = False,
    expected_digest: str | None = None,
    expected_release_revision: object = MISSING,
    evidence_run_id=None,
) -> PublishResult:
    """Caller owns an atomic transaction; the lock serializes all publishers."""
    skill = scoped_skill(skill, lock=True)
    principal = principal_for_actor(skill=skill, actor=actor)
    authorize_release_locked(
        principal=principal,
        skill=skill,
        label_names=labels,
        expected_release_revision=expected_release_revision,
    )
    for label in labels:
        _validate_name(label)
    latest_number = skill.latest_version_number or None
    if not force and base_version != latest_number:
        raise SkillsHubError(
            "stale_base",
            "A newer version was published. Compare it before publishing.",
            status=409,
            detail={"base_version": base_version, "latest_version": latest_number},
        )
    version, unchanged = persist_immutable_version_locked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        parent_number=base_version,
        origin_number=base_version,
        description=frontmatter["description"],
        frontmatter=frontmatter,
        checks=checks,
        source=source,
        always_new=always_new,
        expected_digest=expected_digest,
    )
    for label in dict.fromkeys(labels):
        _set_label_locked(
            skill=skill,
            actor=actor,
            name=label,
            version_number=version.number,
            evidence_run_id=evidence_run_id,
        )
    evidence = None
    if evidence_run_id is not None:
        attached = attach_test_evidence_locked(
            principal=principal,
            skill=skill,
            version=version,
            run_id=evidence_run_id,
        )
        evidence = {
            "run_id": str(attached.run_id),
            "currentness": attached.currentness_at_action,
            "validity": attached.run.validity,
        }
    version.skill = skill
    return PublishResult(
        version=version,
        unchanged=unchanged,
        checks=checks,
        release_revision=skill.release_revision,
        evidence=evidence,
    )


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
    expected_digest: str | None = None,
    expected_release_revision: object = MISSING,
    evidence_run_id=None,
) -> PublishResult:
    """Check and atomically publish content, optionally moving requested labels."""
    _validate_note(note)
    checks, frontmatter = _checked(
        files, name=skill.name, acknowledge_warnings=acknowledge_warnings
    )
    return _publish_checked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        base_version=base_version,
        labels=labels,
        checks=checks,
        frontmatter=frontmatter,
        source=source,
        force=force,
        expected_digest=expected_digest,
        expected_release_revision=expected_release_revision,
        evidence_run_id=evidence_run_id,
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
    source: dict | None = None,
    expected_digest: str | None = None,
    expected_release_revision: object = MISSING,
    evidence_run_id=None,
) -> PublishResult:
    """Push by name, preserving provenance; identical pushes are no-ops."""
    source = {"type": "api"} if source is None else source
    _validate_note(note)
    _validate_tags(tags)
    checks, frontmatter = _checked(
        files, name=None, acknowledge_warnings=acknowledge_warnings
    )
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
                source=source,
            )
        except SkillsHubError as exc:
            if exc.code != "name_taken":
                raise
            skill = scope.filter(name=frontmatter["name"]).first()
            if skill is None:
                raise
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
        frontmatter=frontmatter,
        source=source,
        force=True,
        expected_digest=expected_digest,
        expected_release_revision=expected_release_revision,
        evidence_run_id=evidence_run_id,
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
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    require_distribution_eligibility(version)
    files: list[FileInput] = [
        {"path": row.path, "content": row.blob.content}
        for row in version.files.select_related("blob").order_by("path")
    ]
    note = note or f"Restored from v{number}"
    _validate_note(note)
    checks, frontmatter = _checked(files, name=skill.name, acknowledge_warnings=True)
    return _publish_checked(
        skill=skill,
        actor=actor,
        files=files,
        note=note,
        base_version=skill.latest_version_number or None,
        labels=[],
        checks=checks,
        frontmatter=frontmatter,
        source=(
            {**version.source, "operation": "restore", "restored_from_version": number}
            if version.source.get("provider") == "github"
            else {"type": "restore", "version": number}
        ),
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
def archive_skill(
    *,
    skill: HubSkill,
    actor: Actor,
    expected_release_revision: object = MISSING,
) -> None:
    """Hide an identity while retaining versions, labels and their history."""
    skill = scoped_skill(skill, lock=True)
    authorize_archive_locked(
        principal=principal_for_actor(skill=skill, actor=actor),
        skill=skill,
        expected_release_revision=expected_release_revision,
    )
    skill.deleted = True
    skill.deleted_at = timezone.now()
    skill.updated_by = actor.user
    skill.save(update_fields=["deleted", "deleted_at", "updated_by", "updated_at"])
