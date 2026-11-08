"""Bounded owner-private checkpoints; restoration never rewinds draft revisions."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_r3 import HubSkillDraftCheckpoint
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.compare import compare_file_sets
from model_hub.services.skills_hub.drafts import (
    _check_revision,
    _tombstone,
    _validate_payload,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.import_plans import (
    complete_operation,
    lock_intake_workspace,
    operation_replay,
)
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.principals import require_private_principal
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.limits import CHECKPOINT_DAYS, MAX_CHECKPOINTS
from model_hub.services.skills_hub.v12.types import ResourceKind

_PAYLOAD_FIELDS = (
    "files",
    "note",
    "base_version",
    "source",
    "context_spec",
    "resolved_lock",
)
_REASONS = {"deletion", "replacement", "suspension", "manual"}


def draft_recovery_digest(draft: HubSkillDraft) -> str:
    """Identity of all recoverable authoring state, not a compiled release digest."""
    return canonical_digest({field: getattr(draft, field) for field in _PAYLOAD_FIELDS})


def checkpoint_metadata(checkpoint: HubSkillDraftCheckpoint) -> dict:
    return {
        "id": str(checkpoint.pk),
        "resource_id": str(checkpoint.skill_id),
        "draft_revision": checkpoint.draft_revision,
        "reason": checkpoint.reason,
        "digest": checkpoint.digest,
        "created_at": checkpoint.created_at.isoformat(),
        "expires_at": checkpoint.expires_at.isoformat(),
        "payload_expired": checkpoint.expires_at <= timezone.now()
        or checkpoint.payload is None,
        "restored_revision": checkpoint.restored_revision,
    }


def checkpoint_draft_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    draft: HubSkillDraft,
    reason: str,
) -> HubSkillDraftCheckpoint:
    """Integration hook before destructive saves. Caller holds parent/draft locks."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Checkpointing requires a locked transaction.")
    require_private_principal(principal=principal)
    if (
        skill.organization_id != principal.scope.organization_id
        or skill.workspace_id != principal.scope.workspace_id
        or draft.skill_id != skill.pk
        or draft.user_id != principal.user_id
        or draft.discarded_at is not None
    ):
        raise SkillsHubError("resource_not_found", "Draft not found.", status=404)
    if reason not in _REASONS:
        raise SkillsHubError(
            "invalid_selector", "Choose a supported checkpoint reason.", status=400
        )
    rows = HubSkillDraftCheckpoint.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill=skill,
        owner_id=principal.user_id,
    )
    if (
        rows.filter(expires_at__gt=timezone.now(), payload__isnull=False).count()
        >= MAX_CHECKPOINTS
    ):
        raise SkillsHubError(
            "context_rate_limited",
            "The 20 retained checkpoints are full. Export or wait for expiry.",
            status=429,
        )
    _validate_payload(draft.files, draft.note)
    payload = {field: copy.deepcopy(getattr(draft, field)) for field in _PAYLOAD_FIELDS}
    return rows.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill=skill,
        owner_id=principal.user_id,
        draft_revision=draft.revision,
        reason=reason,
        payload=payload,
        digest=canonical_digest(payload),
        expires_at=timezone.now() + timedelta(days=CHECKPOINT_DAYS),
    )


@transaction.atomic
def checkpoint_draft(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    revision: int,
    expected_digest: str,
    reason: str,
    suspend: bool,
    idempotency_key: UUID,
) -> dict:
    lock_intake_workspace(principal)
    assert principal.user_id is not None
    request = {
        "resource_id": str(resource_id),
        "kind": kind,
        "revision": revision,
        "expected_digest": expected_digest,
        "reason": reason,
        "suspend": suspend,
    }
    replay = operation_replay(
        principal=principal,
        operation="draft.checkpoint",
        idempotency_key=idempotency_key,
        request=request,
    )
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind=kind, lock=True
    )
    if replay is not None:
        return replay
    draft = HubSkillDraft.all_objects.filter(
        skill=skill, user_id=principal.user_id
    ).first()
    _check_revision(draft, revision)
    if draft is None or draft.discarded_at is not None:
        raise SkillsHubError("draft_not_found", "Draft not found.", status=404)
    if draft_recovery_digest(draft) != expected_digest:
        raise SkillsHubError(
            "candidate_changed", "The recoverable draft changed.", status=409
        )
    checkpoint = checkpoint_draft_locked(
        principal=principal, skill=skill, draft=draft, reason=reason
    )
    if suspend:
        _tombstone(draft)
    response = {
        "checkpoint": checkpoint_metadata(checkpoint),
        "suspended": suspend,
        "draft_revision": 0 if suspend else draft.revision,
        "retained_revision": draft.revision,
    }
    complete_operation(
        principal=principal,
        operation="draft.checkpoint",
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        resource_id=skill.pk,
    )
    return response


def list_checkpoints(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    require_private_principal(principal=principal)
    skill = get_resource(principal=principal, resource_id=resource_id, kind=kind)
    if (
        type(page) is not int
        or type(page_size) is not int
        or not 1 <= page <= 10000
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid bounded page.", status=400
        )
    rows = (
        HubSkillDraftCheckpoint.objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            skill=skill,
            owner_id=principal.user_id,
        )
        .defer("payload")
        .order_by("-created_at", "-id")
    )
    count = rows.count()
    # Metadata must not load expired/private payload text merely to list a receipt.
    result = []
    for row in rows[(page - 1) * page_size : page * page_size]:
        result.append(
            {
                "id": str(row.pk),
                "resource_id": str(row.skill_id),
                "draft_revision": row.draft_revision,
                "reason": row.reason,
                "digest": row.digest,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "payload_expired": row.expires_at <= timezone.now(),
                "restored_revision": row.restored_revision,
            }
        )
    return {
        "results": result,
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def get_checkpoint(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    checkpoint_id: UUID,
) -> HubSkillDraftCheckpoint:
    require_private_principal(principal=principal)
    skill = get_resource(principal=principal, resource_id=resource_id, kind=kind)
    checkpoint = HubSkillDraftCheckpoint.objects.filter(
        pk=checkpoint_id,
        skill=skill,
        owner_id=principal.user_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if checkpoint is None:
        raise SkillsHubError("resource_not_found", "Checkpoint not found.", status=404)
    if checkpoint.expires_at <= timezone.now() or checkpoint.payload is None:
        raise SkillsHubError(
            "evidence_expired", "The recovery payload expired.", status=410
        )
    return checkpoint


@transaction.atomic
def restore_checkpoint(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    checkpoint_id: UUID,
    revision: int,
    expected_digest: str,
    idempotency_key: UUID,
) -> dict:
    lock_intake_workspace(principal)
    assert principal.user_id is not None
    request = {
        "resource_id": str(resource_id),
        "kind": kind,
        "checkpoint_id": str(checkpoint_id),
        "revision": revision,
        "expected_digest": expected_digest,
    }
    replay = operation_replay(
        principal=principal,
        operation="draft.restore",
        idempotency_key=idempotency_key,
        request=request,
    )
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind=kind, lock=True
    )
    if replay is not None:
        return replay
    checkpoint = get_checkpoint(
        principal=principal,
        resource_id=resource_id,
        kind=kind,
        checkpoint_id=checkpoint_id,
    )
    if checkpoint.digest != expected_digest:
        raise SkillsHubError(
            "candidate_changed",
            "Review the exact checkpoint before restoring.",
            status=409,
        )
    draft = HubSkillDraft.all_objects.filter(
        skill=skill, user_id=principal.user_id
    ).first()
    _check_revision(draft, revision)
    alternate = None
    if draft is not None and draft.discarded_at is None:
        alternate = checkpoint_draft_locked(
            principal=principal, skill=skill, draft=draft, reason="replacement"
        )
    if draft is None:
        draft = HubSkillDraft(skill=skill, user_id=principal.user_id, revision=0)
    assert checkpoint.payload is not None
    for field in _PAYLOAD_FIELDS:
        setattr(draft, field, copy.deepcopy(checkpoint.payload[field]))
    draft.revision += 1
    draft.discarded_at = None
    draft.head_review_fingerprint = ""
    draft.expected_head_version = skill.latest_version_number or None
    draft.save()
    checkpoint.restored_revision = draft.revision
    checkpoint.save(update_fields=["restored_revision"])
    response = {
        "resource_id": str(skill.pk),
        "kind": skill.kind,
        "checkpoint_id": str(checkpoint.pk),
        "draft_revision": draft.revision,
        "digest": checkpoint.digest,
        "preserved_checkpoint_id": str(alternate.pk) if alternate else None,
    }
    complete_operation(
        principal=principal,
        operation="draft.restore",
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        resource_id=skill.pk,
    )
    return response


def compare_recovery_files(
    *,
    base: list[FileInput],
    local: list[FileInput],
    server: list[FileInput],
) -> dict:
    """No semantic merge; each column is the complete explicitly supplied snapshot."""
    for files in (base, local, server):
        _validate_payload(files, "")
    return {
        "base_local": compare_file_sets(base, local),
        "base_server": compare_file_sets(base, server),
        "local_server": compare_file_sets(local, server),
    }


def export_unsent_draft(*, files: list[FileInput], note: str) -> bytes:
    """Pure download content for a failed save; does not persist browser/private text."""
    _validate_payload(files, note)
    return json.dumps(
        {"schema": 1, "files": files, "note": note}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")


@transaction.atomic
def purge_expired_checkpoints(*, batch_size: int = 100) -> int:
    if not 1 <= batch_size <= 100:
        raise ValueError("Use a batch size of 1–100.")
    ids = list(
        HubSkillDraftCheckpoint.objects.select_for_update(skip_locked=True)
        .filter(expires_at__lte=timezone.now(), payload__isnull=False)
        .order_by("expires_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    return HubSkillDraftCheckpoint.objects.filter(pk__in=ids).update(payload=None)
