"""Recover an archived original identity, never an identity selected by reused name."""

from __future__ import annotations

from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import HubSkillContextOperation
from model_hub.selectors.skills_hub_v12 import get_resource, resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.safety import require_safety_admin
from model_hub.services.skills_hub.v12.types import ResourceKind


def _summary(skill: HubSkill) -> dict:
    return {
        "id": str(skill.pk),
        "kind": skill.kind,
        "name": skill.name,
        "description": skill.description,
        "tags": skill.tags,
        "latest_version": skill.latest_version_number or None,
        "release_revision": skill.release_revision,
        "security_revision": skill.security_revision,
        "protected_label_names": skill.protected_label_names,
        "archived": skill.deleted,
    }


def list_archived_skills(
    *,
    principal: SkillPrincipal,
    page: int = 1,
    page_size: int = 25,
    kind: ResourceKind = "skill",
) -> dict:
    require_safety_admin(principal=principal)
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError("invalid_selector", "Invalid page.", status=400)
    rows = resources_for_scope(
        scope=principal.scope, kind=kind, archived=True
    ).order_by("name", "id")
    count = rows.count()
    return {
        "results": [
            _summary(row) for row in rows[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def review_unarchive(
    *, principal: SkillPrincipal, resource_id: UUID, kind: ResourceKind = "skill"
) -> dict:
    require_safety_admin(principal=principal)
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind=kind, archived=True
    )
    labels = [
        {
            "name": row.name,
            "version": row.version.number,
            "version_id": str(row.version_id),
            "digest": row.version.digest,
            "is_protected": row.name in skill.protected_label_names,
        }
        for row in skill.labels.select_related("version").order_by("name")
    ]
    consequence = {"resource": _summary(skill), "retained_labels": labels}
    return {
        **consequence,
        "review_fingerprint": canonical_digest(consequence),
        "name_collision": HubSkill._base_manager.filter(
            workspace_id=skill.workspace_id, name=skill.name, deleted=False
        ).exists(),
    }


def unarchive_resource(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    expected_release_revision: int,
    expected_review_fingerprint: str,
    note: str,
    idempotency_key: UUID,
    kind: ResourceKind = "skill",
) -> dict:
    require_safety_admin(principal=principal)
    if (
        type(expected_release_revision) is not int
        or expected_release_revision < 0
        or not isinstance(note, str)
        or len(note) > 2000
    ):
        raise SkillsHubError(
            "invalid_context_definition",
            "Invalid recovery precondition or note.",
            status=400,
        )
    body = {
        "resource_id": str(resource_id),
        "kind": kind,
        "expected_release_revision": expected_release_revision,
        "expected_review_fingerprint": expected_review_fingerprint,
        "note": note,
    }
    key = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "principal_key": f"{principal.kind}:{principal.api_key_id or principal.user_id}",
        "operation": "unarchive",
        "idempotency_key": idempotency_key,
    }
    digest = canonical_digest(body)
    try:
        with transaction.atomic():
            # Serializes retries even after the original row becomes live.
            skill = (
                HubSkill._base_manager.select_for_update()
                .filter(
                    pk=resource_id,
                    organization_id=principal.scope.organization_id,
                    workspace_id=principal.scope.workspace_id,
                    kind=kind,
                )
                .first()
            )
            if skill is None:
                raise SkillsHubError(
                    "resource_not_found", "Resource not found.", status=404
                )
            require_safety_admin(principal=principal)
            operation, created = HubSkillContextOperation.objects.get_or_create(
                **key, defaults={"request_digest": digest}
            )
            if operation.request_digest != digest:
                raise SkillsHubError(
                    "idempotency_conflict",
                    "This key belongs to another recovery.",
                    status=409,
                )
            if not created:
                return dict(operation.response["resource"])
            review = review_unarchive(
                principal=principal, resource_id=resource_id, kind=kind
            )
            if skill.release_revision != expected_release_revision:
                raise SkillsHubError(
                    "release_conflict",
                    "Release state changed. Review again.",
                    status=409,
                )
            if review["review_fingerprint"] != expected_review_fingerprint:
                raise SkillsHubError(
                    "impact_changed",
                    "Retained label consequences changed. Review again.",
                    status=409,
                )
            if review["name_collision"]:
                raise SkillsHubError(
                    "name_taken", "A live resource already uses this name.", status=409
                )
            # Unarchive is not quarantine clearance. Blocked labels remain blocked.
            skill.deleted = False
            skill.release_revision += 1
            skill.updated_by_id = principal.user_id
            skill.save(
                update_fields=[
                    "deleted",
                    "release_revision",
                    "updated_by",
                    "updated_at",
                ]
            )
            result = _summary(skill)
            HubSkillContextOperation.objects.filter(pk=operation.pk).update(
                result_resource_id=skill.pk,
                response={
                    "resource": result,
                    "note": note,
                    "review_fingerprint": expected_review_fingerprint,
                    "retained_labels": review["retained_labels"],
                },
                completed_at=timezone.now(),
            )
            return result
    except IntegrityError as exc:
        if "uniq_hub_skill_ws_name_live" not in str(exc):
            raise
        raise SkillsHubError(
            "name_taken", "A live resource already uses this name.", status=409
        ) from exc
