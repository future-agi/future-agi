"""Retain exact upload candidates; acceptance never re-reads an upload or a label.

Plans are owner-private, bounded and short lived. Item receipts contain identities,
not file text, and survive payload expiry. A workspace lock serializes name creation,
source mapping and operation-key lookup before taking any resource locks.
"""

from __future__ import annotations

import copy
import hashlib
import posixpath
from collections import Counter
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan, HubSkillIntakeReceipt
from model_hub.models.skills_hub_v12 import HubSkillContextOperation
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.checks import (
    parse_frontmatter,
    path_is_valid,
    run_checks,
)
from model_hub.services.skills_hub.drafts import _validate_payload
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import (
    MAX_IMPORT_SKILLS,
    MAX_LOOSE_FILES,
    MAX_UNZIPPED_BYTES,
    MAX_ZIP_ENTRIES,
)
from model_hub.services.skills_hub.packages import (
    _bundles,
    _zip_files,
    decode_text_file,
    file_sha256,
    is_ignored_path,
    manifest_digest,
)
from model_hub.services.skills_hub.types import MISSING, Actor, FileInput
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.versions import _validate_identity, _validate_tags

PLAN_MINUTES = 15
MAX_ACTIVE_PLANS = 20


def lock_intake_workspace(principal: SkillPrincipal) -> Workspace:
    """The caller owns a transaction; take this before resource/plan/draft locks."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Intake mutation requires a transaction.")
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    return Workspace.no_workspace_objects.select_for_update(no_key=True).get(
        pk=principal.scope.workspace_id, organization_id=principal.scope.organization_id
    )


def intake_actor(principal: SkillPrincipal) -> Actor:
    assert principal.user_id is not None
    return Actor(
        user=User.objects.get(pk=principal.user_id),
        api_key=(
            OrgApiKey.no_workspace_objects.get(pk=principal.api_key_id)
            if principal.api_key_id
            else None
        ),
    )


def operation_replay(
    *, principal: SkillPrincipal, operation: str, idempotency_key: UUID, request: dict
) -> dict | None:
    """Called after workspace lock and current authorization, before mutable reads."""
    if not isinstance(idempotency_key, UUID):
        raise SkillsHubError(
            "invalid_selector", "Supply an operation UUID.", status=400
        )
    row = HubSkillContextOperation.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        principal_key=f"user:{principal.user_id}",
        operation=operation,
        idempotency_key=idempotency_key,
    ).first()
    if row is None:
        return None
    if row.request_digest != canonical_digest(request):
        raise SkillsHubError(
            "idempotency_conflict",
            "This operation key has different inputs.",
            status=409,
        )
    response: dict = row.response
    return copy.deepcopy(response)


def complete_operation(
    *,
    principal: SkillPrincipal,
    operation: str,
    idempotency_key: UUID,
    request: dict,
    response: dict,
    resource_id: UUID,
) -> HubSkillContextOperation:
    return HubSkillContextOperation.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        principal_key=f"user:{principal.user_id}",
        operation=operation,
        idempotency_key=idempotency_key,
        request_digest=canonical_digest(request),
        response=response,
        result_resource_id=resource_id,
        completed_at=timezone.now(),
    )


def file_inventory(files: list[FileInput]) -> list[dict]:
    return [
        {
            "path": file["path"],
            "sha256": file_sha256(file["content"]),
            "size_bytes": len(file["content"].encode("utf-8")),
        }
        for file in sorted(files, key=lambda row: row["path"])
    ]


def retained_plan(
    *,
    principal: SkillPrincipal,
    kind: str,
    items: list[dict],
    rejected: list[dict],
    skipped: list,
    upload_digest: str,
) -> HubSkillIntakePlan:
    """Persist a bounded trusted parser result. The caller already holds the workspace lock."""
    if len(items) > MAX_IMPORT_SKILLS:
        raise SkillsHubError(
            "payload_too_large", "Select at most 50 packages.", status=413
        )
    rows = HubSkillIntakePlan.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_id=principal.user_id,
    )
    if (
        rows.filter(expires_at__gt=timezone.now(), payload__isnull=False).count()
        >= MAX_ACTIVE_PLANS
    ):
        raise SkillsHubError(
            "context_rate_limited",
            "Wait for an intake plan to expire before creating another.",
            status=429,
        )
    total = sum(
        len(file["content"].encode("utf-8")) for item in items for file in item["files"]
    )
    if total > MAX_UNZIPPED_BYTES:
        raise SkillsHubError(
            "payload_too_large", "Keep retained input within 20 MiB.", status=413
        )
    payload = {"schema": 1, "items": items, "upload_digest": upload_digest}
    inventory = {
        "schema": 1,
        "items": [
            {
                **{
                    key: copy.deepcopy(value)
                    for key, value in item.items()
                    if key != "files"
                },
                "files": file_inventory(item["files"]),
            }
            for item in items
        ],
        "rejected": rejected,
        "skipped": skipped,
    }
    assert principal.user_id is not None
    return HubSkillIntakePlan.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_id=principal.user_id,
        kind=kind,
        fingerprint=canonical_digest(
            {"kind": kind, "payload": payload, "inventory": inventory}
        ),
        inventory=inventory,
        payload=payload,
        expires_at=timezone.now() + timedelta(minutes=PLAN_MINUTES),
    )


def get_import_plan(*, principal: SkillPrincipal, plan_id: UUID) -> HubSkillIntakePlan:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    row = HubSkillIntakePlan.objects.filter(
        pk=plan_id,
        owner_id=principal.user_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if row is None:
        raise SkillsHubError("resource_not_found", "Intake plan not found.", status=404)
    if row.expires_at <= timezone.now() or row.payload is None:
        raise SkillsHubError(
            "import_preview_expired",
            "The retained input expired. Preview it again.",
            status=410,
        )
    return row


def checked_plan_item(
    *,
    principal: SkillPrincipal,
    plan_id: UUID,
    expected_fingerprint: str,
    item_key: str,
    kind: str,
) -> tuple[HubSkillIntakePlan, dict]:
    plan = get_import_plan(principal=principal, plan_id=plan_id)
    if plan.kind != kind:
        raise SkillsHubError("resource_not_found", "Intake plan not found.", status=404)
    if (
        plan.fingerprint != expected_fingerprint
        or plan.fingerprint
        != canonical_digest(
            {"kind": plan.kind, "payload": plan.payload, "inventory": plan.inventory}
        )
    ):
        raise SkillsHubError(
            "candidate_changed",
            "Review the retained input before accepting it.",
            status=409,
        )
    assert plan.payload is not None
    item = next((row for row in plan.payload["items"] if row["key"] == item_key), None)
    if item is None:
        raise SkillsHubError("resource_not_found", "Package not found.", status=404)
    if item["blockers"]:
        raise SkillsHubError(
            "import_invalid",
            "This package contains unsupported or rejected members.",
            status=422,
            detail={"blockers": item["blockers"]},
        )
    return plan, item


def head_identity(skill: HubSkill | None) -> dict:
    head = (
        skill.versions.filter(number=skill.latest_version_number).first()
        if skill
        else None
    )
    return {
        "expected_head_version": head.number if head else None,
        "expected_head_digest": head.digest if head else None,
        "metadata_revision": skill.metadata_revision if skill else 0,
        "security_revision": skill.security_revision if skill else 0,
        "tags_digest": canonical_digest(skill.tags if skill else []),
    }


def require_plan_head(skill: HubSkill, item: dict) -> None:
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    current = head_identity(skill)
    if current != {key: item[key] for key in current}:
        raise SkillsHubError(
            "stale_base",
            "The destination changed after preview. Review a new plan.",
            status=409,
        )
    head = skill.versions.filter(number=skill.latest_version_number).first()
    if head:
        require_distribution_eligibility(head)


def read_upload_groups(
    uploads: list[tuple[str, bytes]],
) -> tuple[list[dict], list[dict], list]:
    """Bounded shared extraction, before kind-specific root discovery; never executes files."""
    if len(uploads) > MAX_ZIP_ENTRIES:
        raise SkillsHubError(
            "payload_too_large", "Use at most 1000 upload parts.", status=413
        )
    rejected: list[dict] = []
    skipped: list[str] = []
    groups: list[dict] = []
    loose: list[tuple[str, bytes]] = []
    budget = {"entries": 0, "bytes": 0}
    for index, (filename, raw) in enumerate(uploads):
        if not path_is_valid(filename):
            rejected.append({"path": filename, "reason": "path_invalid"})
        elif is_ignored_path(filename):
            skipped.append(filename)
        elif filename.lower().endswith(".zip") or raw.startswith(
            (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
        ):
            start = len(rejected)
            source = f"{index}:{filename}"
            groups.append(
                {
                    "files": _zip_files(filename, raw, rejected, skipped, budget),
                    "source": source,
                    "filename": filename,
                }
            )
            for finding in rejected[start:]:
                finding["source"] = source
        else:
            loose.append((filename, raw))
    if (
        len(loose) > MAX_LOOSE_FILES
        or budget["bytes"] + sum(len(raw) for _, raw in loose) > MAX_UNZIPPED_BYTES
    ):
        raise SkillsHubError(
            "payload_too_large",
            "Keep uploads within the shared file and byte limits.",
            status=413,
        )
    counts = Counter(path.casefold() for path, _ in loose)
    files = {}
    for path, raw in loose:
        if counts[path.casefold()] > 1:
            rejected.append(
                {"path": path, "reason": "path_duplicate", "source": "loose"}
            )
            continue
        start = len(rejected)
        content = decode_text_file(path, raw, rejected)
        for finding in rejected[start:]:
            finding["source"] = "loose"
        if content is not None:
            files[path] = content
    if loose:
        groups.append({"files": files, "source": "loose", "filename": None})
    return groups, rejected, skipped


def unassigned_members(files: dict[str, str], bundles: list[dict]) -> list[dict]:
    return [
        {"path": path, "reason": "unassigned_member"}
        for path in files
        if not any(
            not item["root"] or path.startswith(item["root"] + "/") for item in bundles
        )
    ]


def rejection_blocks(finding: dict, item: dict, items: list[dict]) -> bool:
    """Unsafe archive structure blocks all; a rejected member blocks its whole package."""
    if finding["reason"] in {
        "path_invalid",
        "path_duplicate",
        "symlink",
        "special_file",
        "encrypted",
        "archive_invalid",
        "compression_ratio",
    }:
        return True
    source = finding.get("source")
    if source is None:
        return True
    if source != item["upload_source"]:
        return False
    affected = [
        row
        for row in items
        if row["upload_source"] == source
        and (not row["root"] or finding["path"].startswith(row["root"] + "/"))
    ]
    return not affected or any(row["key"] == item["key"] for row in affected)


def _upload_items(
    uploads: list[tuple[str, bytes]],
) -> tuple[list[dict], list[dict], list]:
    groups, rejected, skipped = read_upload_groups(uploads)
    items: list[dict] = []
    for group in groups:
        start = len(rejected)
        bundles = _bundles(
            group["files"],
            group["source"],
            posixpath.splitext(posixpath.basename(group["filename"] or "skill"))[0],
            rejected,
            filename=group["filename"],
        )
        rejected.extend(unassigned_members(group["files"], bundles))
        for finding in rejected[start:]:
            finding["source"] = group["source"]
        items.extend(
            {
                **bundle,
                "kind": "skill",
                "upload_source": group["source"],
                "blockers": [],
            }
            for bundle in bundles
        )
    for item in items:
        item["blockers"] = [
            copy.deepcopy(finding)
            for finding in rejected
            if rejection_blocks(finding, item, items)
        ]
        if item["checks"]["summary"]["errors"]:
            item["blockers"].append({"path": item["root"], "reason": "checks_failed"})
    return items, rejected, skipped


@transaction.atomic
def preview_import(
    *,
    principal: SkillPrincipal,
    uploads: list[tuple[str, bytes]],
    source_identities: dict[str, dict] | None = None,
) -> HubSkillIntakePlan:
    """Local-folder/ZIP preview. Source identities are versioned, client-reported receipts."""
    from model_hub.services.skills_hub.r3.migration_receipts import (
        migration_comparison,
        source_destination,
        validate_source,
    )

    lock_intake_workspace(principal)
    items, rejected, skipped = _upload_items(uploads)
    identities = source_identities or {}
    unknown = set(identities) - {item["key"] for item in items}
    if unknown:
        raise SkillsHubError(
            "import_invalid",
            "Source identity must name an inventoried package.",
            status=400,
        )
    for item in items:
        source = identities.get(item["key"])
        if source is not None:
            source = validate_source(source, item["files"])
        skill, basis = source_destination(principal=principal, source=source)
        item.update(head_identity(skill))
        item.update(
            destination_id=str(skill.pk) if skill else None,
            source=source,
            source_manifest=file_inventory(item["files"]),
            migration=migration_comparison(
                basis=basis, source_files=item["files"], skill=skill
            ),
        )
        if skill and skill.name != item["name"]:
            item["blockers"].append({"path": "SKILL.md", "reason": "name_mismatch"})
    return retained_plan(
        principal=principal,
        kind="skill_import",
        items=items,
        rejected=rejected,
        skipped=skipped,
        upload_digest=canonical_digest(
            [[path, hashlib.sha256(raw).hexdigest()] for path, raw in uploads]
        ),
    )


@transaction.atomic
def preview_manual_reconciliation(
    *,
    principal: SkillPrincipal,
    plan_id: UUID,
    item_key: str,
    expected_fingerprint: str,
    files: list[FileInput],
) -> HubSkillIntakePlan:
    lock_intake_workspace(principal)
    original, item = checked_plan_item(
        principal=principal,
        plan_id=plan_id,
        expected_fingerprint=expected_fingerprint,
        item_key=item_key,
        kind="skill_import",
    )
    if not item["source"] or not item["destination_id"]:
        raise SkillsHubError(
            "import_invalid",
            "Manual source reconciliation needs an existing mapped destination.",
            status=400,
        )
    skill = get_resource(
        principal=principal,
        resource_id=UUID(item["destination_id"]),
        kind="skill",
        lock=True,
    )
    require_plan_head(skill, item)
    _validate_payload(files, "")
    checks = run_checks(files, expected_name=skill.name)
    reconciled = {
        **copy.deepcopy(item),
        "files": copy.deepcopy(files),
        "checks": checks,
        "manual_reconciliation": True,
        "parent_plan_id": str(original.pk),
        "blockers": (
            [{"path": "SKILL.md", "reason": "checks_failed"}]
            if checks["summary"]["errors"]
            else []
        ),
    }
    return retained_plan(
        principal=principal,
        kind="skill_import",
        items=[reconciled],
        rejected=[],
        skipped=[],
        upload_digest=canonical_digest(
            {"parent": original.fingerprint, "files": files}
        ),
    )


def _create_import_identity(
    principal: SkillPrincipal, item: dict, tags: list[str]
) -> HubSkill:
    description = item.get("description")
    if item["kind"] == "skill":
        frontmatter, _, _ = parse_frontmatter(
            next(
                file["content"] for file in item["files"] if file["path"] == "SKILL.md"
            )
        )
        if frontmatter is None:
            raise SkillsHubError(
                "checks_failed", "Valid skill frontmatter is required.", status=422
            )
        description = frontmatter["description"]
    if not isinstance(description, str):
        raise SkillsHubError("invalid_name", "A text purpose is required.", status=400)
    _validate_identity(item["name"], description, tags)
    if HubSkill._base_manager.filter(
        workspace_id=principal.scope.workspace_id, name=item["name"], deleted=False
    ).exists():
        raise SkillsHubError(
            "name_taken",
            "This name already exists. Do not overwrite it by name.",
            status=409,
        )
    try:
        with transaction.atomic():
            return HubSkill._base_manager.create(
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                name=item["name"],
                description=description,
                tags=tags,
                kind=item["kind"],
                source={"type": "import"},
                created_by_id=principal.user_id,
                updated_by_id=principal.user_id,
            )
    except IntegrityError as exc:
        # Legacy creation does not take the new intake workspace lock. Preserve
        # the unique-name winner instead of returning an internal error or retrying
        # as an overwrite of the identity created while this plan was reviewed.
        if HubSkill._base_manager.filter(
            workspace_id=principal.scope.workspace_id, name=item["name"], deleted=False
        ).exists():
            raise SkillsHubError(
                "name_taken",
                "This name was created concurrently. Review the destination.",
                status=409,
            ) from exc
        raise


def validated_intake_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        raise SkillsHubError("invalid_tags", "Supply a tag list, not null.", status=400)
    _validate_tags(value)
    return value


def stage_import_item(
    *,
    principal: SkillPrincipal,
    plan: HubSkillIntakePlan,
    item: dict,
    tags: object = MISSING,
    decision: str = "replace",
) -> dict:
    """Caller holds workspace lock; never replaces a live personal draft."""
    from model_hub.services.skills_hub.r3.migration_receipts import (
        record_migration_locked,
    )

    if decision not in {"keep", "replace", "manual"}:
        raise SkillsHubError(
            "import_invalid",
            "Choose keep, reviewed replacement or a retained manual reconciliation.",
            status=400,
        )
    if (decision == "manual") != bool(item.get("manual_reconciliation")):
        raise SkillsHubError(
            "import_invalid",
            "Manual reconciliation requires its own reviewed file plan.",
            status=400,
        )
    provided_tags = None if tags is MISSING else validated_intake_tags(tags)
    assert principal.user_id is not None
    skill = (
        get_resource(
            principal=principal,
            resource_id=UUID(item["destination_id"]),
            kind=item["kind"],
            lock=True,
        )
        if item["destination_id"]
        else None
    )
    if skill:
        require_plan_head(skill, item)
    elif decision == "keep":
        raise SkillsHubError(
            "import_invalid", "There is no existing destination to keep.", status=400
        )
    if decision == "keep":
        assert skill is not None
        return {
            "resource_id": str(skill.pk),
            "kind": skill.kind,
            "decision": "keep",
            "published": False,
            "draft_revision": None,
            "digest": item["expected_head_digest"],
        }
    if skill is None:
        skill = _create_import_identity(
            principal, item, [] if provided_tags is None else provided_tags
        )
    draft = HubSkillDraft.all_objects.filter(
        skill=skill, user_id=principal.user_id
    ).first()
    if draft is not None and draft.discarded_at is None:
        raise SkillsHubError(
            "draft_conflict",
            "Preserve and suspend your draft before importing.",
            status=409,
        )
    _validate_payload(item["files"], "")
    if skill.kind == "skill":
        checks = run_checks(item["files"], expected_name=skill.name)
        if checks["summary"]["errors"]:
            raise SkillsHubError(
                "checks_failed",
                "Fix package errors before staging.",
                status=422,
                detail=checks,
            )
    if draft is None:
        draft = HubSkillDraft(skill=skill, user_id=principal.user_id, revision=0)
    draft.files = copy.deepcopy(item["files"])
    draft.note = ""
    draft.base_version = item["expected_head_version"]
    draft.expected_head_version = item["expected_head_version"]
    draft.head_review_fingerprint = ""
    draft.context_spec = (
        {"schema": 1, "description": item["description"], "links": []}
        if skill.kind == "agent_context"
        else {}
    )
    draft.resolved_lock = (
        {"schema": 1, "links": []} if skill.kind == "agent_context" else {}
    )
    draft.source = {"type": "import", "plan_id": str(plan.pk), "item_key": item["key"]}
    draft.revision += 1
    draft.discarded_at = None
    if item.get("source"):
        receipt = record_migration_locked(
            principal=principal,
            skill=skill,
            source=item["source"],
            files=item["files"],
            source_manifest=item["source_manifest"],
            draft_revision=draft.revision,
        )
        draft.source.update(
            provider="migration",
            migration_receipt_id=str(receipt.pk),
            upstream_digest=receipt.source_digest,
        )
    draft.save()
    if provided_tags is not None and skill.tags != provided_tags:
        skill.tags = list(dict.fromkeys(provided_tags))
        skill.metadata_revision += 1
        skill.updated_by_id = principal.user_id
        skill.save(
            update_fields=["tags", "metadata_revision", "updated_by", "updated_at"]
        )
    return {
        "resource_id": str(skill.pk),
        "kind": skill.kind,
        "decision": decision,
        "published": False,
        "draft_revision": draft.revision,
        "digest": manifest_digest(draft.files),
    }


@transaction.atomic
def accept_import(
    *,
    principal: SkillPrincipal,
    plan_id: UUID,
    item_key: str,
    expected_fingerprint: str,
    expected_head_version: int | None,
    expected_head_digest: str | None,
    rights_confirmed: bool,
    idempotency_key: UUID,
    decision: str = "replace",
    mode: str = "snapshot",
    tags: object = MISSING,
) -> dict:
    lock_intake_workspace(principal)
    request: dict = {
        "plan_id": str(plan_id),
        "item_key": item_key,
        "expected_fingerprint": expected_fingerprint,
        "expected_head_version": expected_head_version,
        "expected_head_digest": expected_head_digest,
        "rights_confirmed": rights_confirmed,
        "decision": decision,
        "mode": mode,
    }
    if mode != "snapshot":
        raise SkillsHubError(
            "import_invalid",
            "Uploads are complete snapshots; use a push plan for patches.",
            status=400,
        )
    if tags is not MISSING:
        request["tags"] = tags
    replay = operation_replay(
        principal=principal,
        operation="intake.accept",
        idempotency_key=idempotency_key,
        request=request,
    )
    if replay is not None:
        get_resource(
            principal=principal,
            resource_id=UUID(replay["resource_id"]),
            kind=replay["kind"],
        )
        return replay
    plan, item = checked_plan_item(
        principal=principal,
        plan_id=plan_id,
        expected_fingerprint=expected_fingerprint,
        item_key=item_key,
        kind="skill_import",
    )
    return accept_retained_item(
        principal=principal,
        plan=plan,
        item=item,
        request=request,
        idempotency_key=idempotency_key,
        operation="intake.accept",
        tags=tags,
    )


def accept_retained_item(
    *,
    principal: SkillPrincipal,
    plan: HubSkillIntakePlan,
    item: dict,
    request: dict,
    idempotency_key: UUID,
    operation: str,
    tags: object = MISSING,
) -> dict:
    if request["rights_confirmed"] is not True:
        raise SkillsHubError(
            "license_review_required",
            "Confirm your rights to copy these files.",
            status=422,
        )
    if any(
        request[key] != item[key]
        for key in ("expected_head_version", "expected_head_digest")
    ):
        raise SkillsHubError(
            "stale_base",
            "Accept the exact destination reviewed in this plan.",
            status=409,
        )
    prior = HubSkillIntakeReceipt.objects.filter(
        plan=plan, item_key=item["key"]
    ).first()
    if prior:
        if prior.operation.request_digest != canonical_digest(request):
            raise SkillsHubError(
                "idempotency_conflict",
                "This package was already accepted with different choices.",
                status=409,
            )
        get_resource(
            principal=principal,
            resource_id=prior.destination_id,
            kind=prior.result["kind"],
        )
        return copy.deepcopy(prior.result)
    result = stage_import_item(
        principal=principal,
        plan=plan,
        item=item,
        tags=tags,
        decision=request["decision"],
    )
    operation_row = complete_operation(
        principal=principal,
        operation=operation,
        idempotency_key=idempotency_key,
        request=request,
        response=result,
        resource_id=UUID(result["resource_id"]),
    )
    HubSkillIntakeReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        plan=plan,
        item_key=item["key"],
        operation=operation_row,
        destination_id=result["resource_id"],
        result=result,
    )
    return result


def list_import_receipts(
    *, principal: SkillPrincipal, plan_id: UUID, page: int = 1, page_size: int = 25
) -> dict:
    """Recover per-item outcomes even when retained upload text has expired."""
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    if (
        type(page) is not int
        or type(page_size) is not int
        or not 1 <= page <= 10000
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid bounded page.", status=400
        )
    plan = (
        HubSkillIntakePlan.objects.filter(
            pk=plan_id,
            owner_id=principal.user_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        .only("id")
        .first()
    )
    if plan is None:
        raise SkillsHubError("resource_not_found", "Intake plan not found.", status=404)
    rows = HubSkillIntakeReceipt.objects.filter(
        plan=plan,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        destination__deleted=False,
        destination__organization_id=principal.scope.organization_id,
        destination__workspace_id=principal.scope.workspace_id,
    ).order_by("item_key")
    count = rows.count()
    return {
        "results": [
            {"item_key": row.item_key, "result": row.result}
            for row in rows[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


@transaction.atomic
def purge_expired_intake(*, batch_size: int = 100) -> int:
    """Existing retention worker calls this; receipts and hashes survive."""
    if not 1 <= batch_size <= 100:
        raise ValueError("Use a batch size of 1–100.")
    ids = list(
        HubSkillIntakePlan.objects.select_for_update(skip_locked=True)
        .filter(expires_at__lte=timezone.now(), payload__isnull=False)
        .order_by("expires_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    # Inventory contains only manifests/check diagnostics, never retained input text.
    return HubSkillIntakePlan.objects.filter(pk__in=ids).update(payload=None)
