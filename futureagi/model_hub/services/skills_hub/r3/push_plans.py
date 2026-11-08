"""Reviewed leaf pushes: exact snapshots/patches, head CAS, no implicit label move."""

from __future__ import annotations

import copy
from uuid import UUID

from django.db import transaction

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan, HubSkillIntakeReceipt
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.checks import (
    parse_frontmatter,
    path_is_valid,
    run_checks,
)
from model_hub.services.skills_hub.compare import compare_file_sets
from model_hub.services.skills_hub.drafts import _validate_payload
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.r3.import_plans import (
    _create_import_identity,
    checked_plan_item,
    complete_operation,
    head_identity,
    intake_actor,
    lock_intake_workspace,
    operation_replay,
    require_plan_head,
    retained_plan,
    validated_intake_tags,
)
from model_hub.services.skills_hub.types import MISSING, FileInput
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)
from model_hub.services.skills_hub.versions import (
    _checked,
    persist_immutable_version_locked,
)


def _files_at_head(skill: HubSkill | None) -> list[FileInput]:
    version = (
        skill.versions.filter(number=skill.latest_version_number).first()
        if skill
        else None
    )
    if version is None:
        return []
    require_distribution_eligibility(version)
    return [
        {"path": row.path, "content": row.blob.content}
        for row in version.files.select_related("blob").order_by("path")
    ]


@transaction.atomic
def preview_push(
    *,
    principal: SkillPrincipal,
    files: list[FileInput],
    mode: str,
    resource_id: UUID | None = None,
    delete_paths: list[str] | None = None,
    tags: object = MISSING,
    note: str = "",
) -> HubSkillIntakePlan:
    lock_intake_workspace(principal)
    if mode not in {"snapshot", "patch"}:
        raise SkillsHubError(
            "import_invalid", "Choose snapshot or patch explicitly.", status=400
        )
    _validate_payload(files, note)
    if tags is not MISSING:
        tags = validated_intake_tags(tags)
    skill = (
        get_resource(
            principal=principal, resource_id=resource_id, kind="skill", lock=True
        )
        if resource_id
        else None
    )
    before = _files_at_head(skill)
    deletions = delete_paths or []
    if (
        len(deletions) > 200
        or len(set(deletions)) != len(deletions)
        or any(not path_is_valid(path) for path in deletions)
        or (mode == "snapshot" and deletions)
    ):
        raise SkillsHubError(
            "import_invalid",
            "Only patch plans accept unique explicit deletion paths.",
            status=400,
        )
    candidate: list[FileInput]
    if mode == "patch":
        if skill is None:
            raise SkillsHubError(
                "import_invalid",
                "A patch needs an explicit existing destination.",
                status=400,
            )
        tree = {file["path"]: file["content"] for file in before}
        if set(deletions) - tree.keys() or set(deletions) & {
            file["path"] for file in files
        }:
            raise SkillsHubError(
                "import_invalid",
                "Delete only existing paths not supplied as replacements.",
                status=400,
            )
        for path in deletions:
            del tree[path]
        tree.update({file["path"]: file["content"] for file in files})
        candidate = [
            {"path": path, "content": content} for path, content in sorted(tree.items())
        ]
    else:
        candidate = copy.deepcopy(files)
    _validate_payload(candidate, note)
    checks = run_checks(candidate, expected_name=skill.name if skill else None)
    root = next((row["content"] for row in candidate if row["path"] == "SKILL.md"), "")
    frontmatter, _, _ = parse_frontmatter(root)
    item = {
        "key": "push",
        "kind": "skill",
        "name": skill.name if skill else (frontmatter or {}).get("name", ""),
        "files": candidate,
        "mode": mode,
        "note": note,
        "checks": checks,
        "destination_id": str(skill.pk) if skill else None,
        "source": None,
        "blockers": (
            [{"reason": "checks_failed", "path": "SKILL.md"}]
            if checks["summary"]["errors"]
            else []
        ),
        "changes": {
            "summary": compare_file_sets(before, candidate)["summary"],
            "delete_paths": sorted(
                {row["path"] for row in before} - {row["path"] for row in candidate}
            ),
        },
        **head_identity(skill),
    }
    if tags is not MISSING:
        item["tags"] = tags
    return retained_plan(
        principal=principal,
        kind="skill_push",
        items=[item],
        rejected=[],
        skipped=[],
        upload_digest=canonical_digest(
            {"files": files, "delete_paths": deletions, "mode": mode}
        ),
    )


@transaction.atomic
def accept_push(
    *,
    principal: SkillPrincipal,
    plan_id: UUID,
    expected_fingerprint: str,
    mode: str,
    expected_head_version: int | None,
    expected_head_digest: str | None,
    acknowledge_warnings: bool,
    idempotency_key: UUID,
) -> dict:
    lock_intake_workspace(principal)
    request = {
        "plan_id": str(plan_id),
        "expected_fingerprint": expected_fingerprint,
        "mode": mode,
        "expected_head_version": expected_head_version,
        "expected_head_digest": expected_head_digest,
        "acknowledge_warnings": acknowledge_warnings,
    }
    replay = operation_replay(
        principal=principal,
        operation="push.accept",
        idempotency_key=idempotency_key,
        request=request,
    )
    if replay is not None:
        get_resource(
            principal=principal, resource_id=UUID(replay["resource_id"]), kind="skill"
        )
        return replay
    plan, item = checked_plan_item(
        principal=principal,
        plan_id=plan_id,
        expected_fingerprint=expected_fingerprint,
        item_key="push",
        kind="skill_push",
    )
    if any(
        request[key] != item[key]
        for key in ("mode", "expected_head_version", "expected_head_digest")
    ):
        raise SkillsHubError(
            "stale_base",
            "Accept the exact mode and destination reviewed in the plan.",
            status=409,
        )
    prior = HubSkillIntakeReceipt.objects.filter(plan=plan, item_key="push").first()
    if prior:
        if prior.operation.request_digest != canonical_digest(request):
            raise SkillsHubError(
                "idempotency_conflict",
                "The push was already accepted with different choices.",
                status=409,
            )
        get_resource(
            principal=principal, resource_id=prior.destination_id, kind="skill"
        )
        return copy.deepcopy(prior.result)
    skill = (
        get_resource(
            principal=principal,
            resource_id=UUID(item["destination_id"]),
            kind="skill",
            lock=True,
        )
        if item["destination_id"]
        else _create_import_identity(principal, item, item.get("tags", []))
    )
    if item["destination_id"]:
        require_plan_head(skill, item)
    checks, frontmatter = _checked(
        item["files"], name=skill.name, acknowledge_warnings=acknowledge_warnings
    )
    head = skill.versions.filter(number=skill.latest_version_number).first()
    source = {
        **(head.source if head else {}),
        "type": "api",
        "operation": "reviewed_push",
        "plan_id": str(plan.pk),
    }
    version, unchanged = persist_immutable_version_locked(
        skill=skill,
        actor=intake_actor(principal),
        files=item["files"],
        note=item["note"],
        parent_number=item["expected_head_version"],
        origin_number=item["expected_head_version"],
        description=frontmatter["description"],
        frontmatter=frontmatter,
        checks=checks,
        source=source,
        expected_digest=manifest_digest(item["files"]),
    )
    if "tags" in item and skill.tags != item["tags"]:
        skill.tags = list(dict.fromkeys(item["tags"]))
        skill.metadata_revision += 1
        skill.updated_by_id = principal.user_id
        skill.save(
            update_fields=["tags", "metadata_revision", "updated_by", "updated_at"]
        )
    result = {
        "resource_id": str(skill.pk),
        "kind": "skill",
        "version": version.number,
        "version_id": str(version.pk),
        "digest": version.digest,
        "unchanged": unchanged,
        "mode": mode,
        "labels_moved": False,
    }
    operation = complete_operation(
        principal=principal,
        operation="push.accept",
        idempotency_key=idempotency_key,
        request=request,
        response=result,
        resource_id=skill.pk,
    )
    HubSkillIntakeReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        plan=plan,
        item_key="push",
        operation=operation,
        destination=skill,
        result=result,
    )
    return result


@transaction.atomic
def restore_skill_version(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    number: int,
    expected_head_version: int | None,
    expected_digest: str,
    note: str,
    idempotency_key: UUID,
) -> dict:
    """Always-new explicit restoration, but an unknown response never allocates twice."""
    lock_intake_workspace(principal)
    request = {
        "resource_id": str(resource_id),
        "number": number,
        "expected_head_version": expected_head_version,
        "expected_digest": expected_digest,
        "note": note,
    }
    replay = operation_replay(
        principal=principal,
        operation="skill.restore",
        idempotency_key=idempotency_key,
        request=request,
    )
    skill = get_resource(
        principal=principal, resource_id=resource_id, kind="skill", lock=True
    )
    if replay is not None:
        return replay
    target = skill.versions.filter(number=number).first()
    if target is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    require_distribution_eligibility(target)
    if (skill.latest_version_number or None) != expected_head_version:
        raise SkillsHubError(
            "stale_base", "Review the current publication head.", status=409
        )
    if target.digest != expected_digest:
        raise SkillsHubError(
            "candidate_changed",
            "The restore target differs from the reviewed content.",
            status=409,
        )
    files: list[FileInput] = [
        {"path": row.path, "content": row.blob.content}
        for row in target.files.select_related("blob").order_by("path")
    ]
    checks, frontmatter = _checked(files, name=skill.name, acknowledge_warnings=True)
    version, _ = persist_immutable_version_locked(
        skill=skill,
        actor=intake_actor(principal),
        files=files,
        note=note or f"Restored from v{number}",
        parent_number=expected_head_version,
        origin_number=target.number,
        description=frontmatter["description"],
        frontmatter=frontmatter,
        checks=checks,
        source={
            **target.source,
            "operation": "restore",
            "restored_from_version": number,
        },
        expected_digest=expected_digest,
        always_new=True,
    )
    result = {
        "resource_id": str(skill.pk),
        "kind": "skill",
        "version": version.number,
        "version_id": str(version.pk),
        "digest": version.digest,
        "unchanged": False,
        "labels_moved": False,
    }
    complete_operation(
        principal=principal,
        operation="skill.restore",
        idempotency_key=idempotency_key,
        request=request,
        response=result,
        resource_id=skill.pk,
    )
    return result
