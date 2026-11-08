"""Origin-preserving draft seams used by safe skill and context authoring.

Context compilation stays in v12/compiler. Head helpers accept its exact digest;
only trusted services call them, never an unverified caller-supplied digest.
"""

from __future__ import annotations

from uuid import UUID

from django.db import transaction

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.drafts import _check_revision, _validate_payload
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.snapshots import version_definition
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.principals import require_private_principal
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.types import ResourceKind


@transaction.atomic
def start_selected_draft(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    revision: int,
    from_version: int | None,
) -> HubSkillDraft:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind=kind, lock=True
    )
    draft = HubSkillDraft.all_objects.filter(
        skill=skill, user_id=principal.user_id
    ).first()
    _check_revision(draft, revision)
    if revision != 0 or (draft is not None and draft.discarded_at is None):
        raise SkillsHubError(
            "draft_conflict",
            "Preserve and suspend your existing draft before switching origin.",
            status=409,
        )
    if from_version is None and skill.latest_version_number:
        raise SkillsHubError(
            "version_not_found", "Choose the immutable version to edit.", status=404
        )
    version = (
        skill.versions.filter(number=from_version).first()
        if from_version is not None
        else None
    )
    if from_version is not None and version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    files: list[FileInput] = (
        [
            {"path": row.path, "content": row.blob.content}
            for row in version.files.select_related("blob").order_by("path")
        ]
        if version
        else []
    )
    spec, lock = {}, {}
    if kind == "agent_context":
        if version:
            descriptor = version_definition(version)
            owned = set(descriptor["owned_paths"])
            files = [file for file in files if file["path"] in owned]
            spec = {
                "schema": 1,
                "description": descriptor["description"],
                "links": [
                    {"skill_id": link["skill_id"], "selector": link["requested"]}
                    for link in descriptor["links"]
                ],
            }
            lock = {"schema": 1, "links": descriptor["links"]}
        else:
            files = [{"path": "AGENTS.md", "content": "# Instructions\n"}]
            spec = {"schema": 1, "description": skill.description, "links": []}
            lock = {"schema": 1, "links": []}
    elif version is None:
        from model_hub.services.skills_hub.templates_registry import get_template_files

        files = get_template_files(
            "blank", name=skill.name, description=skill.description
        )
    if draft is None:
        draft = HubSkillDraft(skill=skill, user_id=principal.user_id, revision=0)
    draft.files = files
    draft.context_spec, draft.resolved_lock = spec, lock
    draft.source = dict(version.source) if version else {}
    draft.base_version = from_version
    draft.expected_head_version = skill.latest_version_number or None
    draft.head_review_fingerprint = ""
    draft.note = ""
    draft.revision += 1
    draft.discarded_at = None
    draft.save()
    return draft


@transaction.atomic
def save_owned_draft(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    revision: int,
    base_version: int | None,
    files: list[FileInput],
    note: str,
    description: str | None = None,
) -> HubSkillDraft:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind=kind, lock=True
    )
    draft = skill.drafts.filter(user_id=principal.user_id).first()
    _check_revision(draft, revision)
    if draft is None:
        raise SkillsHubError("draft_not_found", "Start a draft explicitly.", status=404)
    if base_version != draft.base_version:
        raise SkillsHubError(
            "stale_base", "Ordinary saves cannot change the content origin.", status=409
        )
    _validate_payload(files, note)
    if kind == "agent_context":
        if not isinstance(description, str) or not 1 <= len(description) <= 1024:
            raise SkillsHubError(
                "invalid_context_definition",
                "Use a purpose of 1–1024 characters.",
                status=400,
            )
        draft.context_spec = {**draft.context_spec, "description": description}
    elif description is not None:
        raise SkillsHubError(
            "invalid_context_definition",
            "Edit skill descriptions in SKILL.md.",
            status=400,
        )
    draft.files, draft.note = files, note
    draft.head_review_fingerprint = ""
    draft.revision += 1
    draft.save()
    return draft


def head_review(
    *, skill: HubSkill, draft: HubSkillDraft, candidate_digest: str
) -> dict:
    head = skill.versions.filter(number=skill.latest_version_number).first()
    identity = {
        "subject_id": str(skill.pk),
        "kind": skill.kind,
        "draft_revision": draft.revision,
        "candidate_digest": candidate_digest,
        "base_version": draft.base_version,
        "expected_head_version": head.number if head else None,
        "head_digest": head.digest if head else None,
    }
    return {**identity, "review_fingerprint": canonical_digest(identity)}


def acknowledge_head_locked(
    *,
    skill: HubSkill,
    draft: HubSkillDraft,
    candidate_digest: str,
    expected_head_version: int | None,
    review_fingerprint: str,
) -> HubSkillDraft:
    """Caller holds current parent/draft locks after authorization and compilation."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Head acknowledgment requires an atomic caller.")
    reviewed = head_review(skill=skill, draft=draft, candidate_digest=candidate_digest)
    if (
        reviewed["review_fingerprint"] != review_fingerprint
        or reviewed["expected_head_version"] != expected_head_version
    ):
        raise SkillsHubError(
            "stale_base",
            "The publication head or candidate changed; review again.",
            status=409,
        )
    draft.expected_head_version = expected_head_version
    draft.revision += 1
    draft.head_review_fingerprint = head_review(
        skill=skill, draft=draft, candidate_digest=candidate_digest
    )["review_fingerprint"]
    draft.save(
        update_fields=[
            "expected_head_version",
            "head_review_fingerprint",
            "revision",
            "updated_at",
        ]
    )
    return draft


def require_reviewed_head_locked(
    *,
    skill: HubSkill,
    draft: HubSkillDraft,
    candidate_digest: str,
    review_fingerprint: str,
) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Publication head validation requires an atomic caller.")
    reviewed = head_review(skill=skill, draft=draft, candidate_digest=candidate_digest)
    if (
        not draft.head_review_fingerprint
        or draft.head_review_fingerprint != review_fingerprint
        or reviewed["review_fingerprint"] != review_fingerprint
        or draft.expected_head_version != reviewed["expected_head_version"]
    ):
        raise SkillsHubError(
            "stale_base",
            "Review and acknowledge the current head without replacing the draft origin.",
            status=409,
        )
