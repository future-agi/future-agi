"""Context authoring on the shared drafts, immutable persistence and operation log."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import cast
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillDraft, HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextDependency,
    HubSkillContextOperation,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.authoring_foundation import (
    acknowledge_head_locked,
    head_review,
    require_reviewed_head_locked,
    save_owned_draft,
    start_selected_draft,
)
from model_hub.services.skills_hub.drafts import (
    _check_revision as _check_draft_revision,
)
from model_hub.services.skills_hub.drafts import _tombstone, _validate_payload
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.snapshots import version_definition
from model_hub.services.skills_hub.types import Actor, FileInput, PublishResult
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.compiler import (
    check_context_files,
    compile_context,
    validate_links,
)
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)
from model_hub.services.skills_hub.v12.types import (
    CompiledContext,
    LinkInput,
    LockedLink,
    VersionSelector,
)
from model_hub.services.skills_hub.versions import (
    _validate_identity,
    persist_immutable_version_locked,
)


def actor_for_principal(principal: SkillPrincipal) -> Actor:
    """The caller first rechecks the real principal's current scope and role."""
    return Actor(
        user=User.objects.get(pk=principal.user_id) if principal.user_id else None,
        api_key=(
            OrgApiKey.no_workspace_objects.get(pk=principal.api_key_id)
            if principal.api_key_id
            else None
        ),
    )


def operation_replay(
    *, principal: SkillPrincipal, operation: str, key: UUID, request: dict
) -> dict | None:
    row = HubSkillContextOperation.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        principal_key=f"{principal.kind}:{principal.user_id}:{principal.api_key_id}",
        operation=operation,
        idempotency_key=key,
    ).first()
    if row is None:
        return None
    if row.request_digest != canonical_digest(request):
        raise SkillsHubError(
            "idempotency_conflict",
            "This operation key belongs to another request.",
            status=409,
        )
    response = deepcopy(row.response)
    if not isinstance(response, dict):
        raise SkillsHubError(
            "invalid_context_definition",
            "The operation receipt is invalid.",
            status=422,
        )
    return response


def record_operation(
    *,
    principal: SkillPrincipal,
    operation: str,
    key: UUID,
    request: dict,
    resource_id: UUID,
    response: dict,
) -> None:
    """Only small identity receipts: never another copy of draft or evidence text."""
    HubSkillContextOperation.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        principal_key=f"{principal.kind}:{principal.user_id}:{principal.api_key_id}",
        operation=operation,
        idempotency_key=key,
        request_digest=canonical_digest(request),
        result_resource_id=resource_id,
        response=response,
        completed_at=timezone.now(),
    )


def require_draft_revision(draft: HubSkillDraft | None, revision: int) -> HubSkillDraft:
    _check_draft_revision(draft, revision)
    if draft is None:
        raise SkillsHubError(
            "draft_not_found", "Start your own draft explicitly.", status=404
        )
    return draft


def _owned(principal: SkillPrincipal, context: HubSkill) -> HubSkillDraft | None:
    assert principal.user_id is not None  # Private principal checked before lookup.
    return HubSkillDraft.objects.filter(
        skill=context, user_id=principal.user_id
    ).first()


def _link_ids(draft: HubSkillDraft | None) -> set[UUID]:
    if draft is None:
        return set()
    return {UUID(link["skill_id"]) for link in draft.resolved_lock.get("links", [])}


@contextmanager
def locked_context_graph(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    extra_ids: set[UUID] | None = None,
    private: bool = True,
    write: bool = True,
) -> Iterator[tuple[HubSkill, HubSkillDraft | None, dict[UUID, HubSkill]]]:
    """Prepare IDs before locking; never acquire a child after its parent.

    A concurrent draft topology change fails CAS rather than acquiring a newly
    discovered child in inverted order. The client can renew its preview safely.
    """
    if private:
        require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=write)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    preliminary = _owned(principal, context) if private else None
    ids = {context.pk, *_link_ids(preliminary), *(extra_ids or set())}
    with transaction.atomic():
        rows = list(
            HubSkill._base_manager.filter(
                pk__in=ids,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                workspace__organization_id=principal.scope.organization_id,
            )
            .order_by("id")
            .select_for_update(of=("self",))
        )
        resources = {row.pk: row for row in rows}
        locked = resources.get(context.pk)
        if locked is None or locked.deleted or locked.kind != "agent_context":
            raise SkillsHubError("resource_not_found", "Context not found.", status=404)
        context = locked
        require_scope_access(principal=principal, write=write)
        draft = _owned(principal, context) if private else None
        if not _link_ids(draft).issubset(ids):
            raise SkillsHubError(
                "draft_conflict",
                "The accepted dependency set changed; reload the draft.",
                status=409,
            )
        yield context, draft, resources


def compile_owned_draft(*, context: HubSkill, draft: HubSkillDraft) -> CompiledContext:
    """Compile authorized retained versions, including ordinarily retired leaves."""
    snapshots = {}
    for link in draft.resolved_lock.get("links", []):
        version = (
            HubSkillVersion.objects.select_related("skill")
            .filter(
                pk=link["version_id"],
                number=link["version"],
                digest=link["digest"],
                skill_id=link["skill_id"],
                skill__kind="skill",
                skill__organization_id=context.organization_id,
                skill__workspace_id=context.workspace_id,
            )
            .first()
        )
        if version is None:
            raise SkillsHubError(
                "dependency_unavailable", "An accepted leaf is unavailable.", status=404
            )
        if link["mount"] != f"skills/{version.skill.name}/":
            raise SkillsHubError(
                "invalid_context_definition",
                "The retained mount identity changed.",
                status=400,
            )
        require_distribution_eligibility(version)
        snapshots[version.pk] = version_files(version)
    return compile_context(
        name=context.name,
        files=draft.files,
        spec=draft.context_spec,
        lock=draft.resolved_lock,
        child_snapshots=snapshots,
    )


def version_files(version: HubSkillVersion) -> list[FileInput]:
    return [
        {"path": row.path, "content": row.blob.content}
        for row in version.files.select_related("blob").order_by("path")
    ]


@transaction.atomic
def create_context(
    *,
    principal: SkillPrincipal,
    name: str,
    description: str,
    tags: list[str],
    files: list[FileInput] | None,
    idempotency_key: UUID,
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    Workspace.no_workspace_objects.select_for_update().get(
        pk=principal.scope.workspace_id, organization_id=principal.scope.organization_id
    )
    request = {"name": name, "description": description, "tags": tags, "files": files}
    replay = operation_replay(
        principal=principal,
        operation="context.create",
        key=idempotency_key,
        request=request,
    )
    if replay is not None:
        get_resource(
            principal=principal,
            resource_id=UUID(replay["context_id"]),
            kind="agent_context",
        )
        return replay
    _validate_identity(name, description, tags)
    files = (
        files
        if files is not None
        else [{"path": "AGENTS.md", "content": "# Instructions\n"}]
    )
    _validate_payload(files, "")
    checks = check_context_files(files=files, complete=False)
    # Drafts may be incomplete, but never accept generated or host-control paths.
    if any(item["code"] == "unsupported_instruction_scope" for item in checks["items"]):
        raise SkillsHubError(
            "unsupported_instruction_scope",
            "Remove reserved instruction/control paths.",
            status=422,
            detail=checks,
        )
    try:
        with transaction.atomic():
            context = HubSkill.no_workspace_objects.create(
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                kind="agent_context",
                name=name,
                description=description,
                tags=list(dict.fromkeys(tags)),
                created_by_id=principal.user_id,
                updated_by_id=principal.user_id,
                source={"type": "blank"},
            )
    except IntegrityError as exc:
        if HubSkill.no_workspace_objects.filter(
            workspace_id=principal.scope.workspace_id, name=name
        ).exists():
            raise SkillsHubError(
                "name_taken", "A live resource already uses this name.", status=409
            ) from exc
        raise
    assert principal.user_id is not None  # Creation requires a private principal.
    draft = HubSkillDraft.objects.create(
        skill=context,
        user_id=principal.user_id,
        revision=1,
        files=files,
        context_spec={"schema": 1, "description": description, "links": []},
        resolved_lock={"schema": 1, "links": []},
    )
    receipt = {
        "context_id": str(context.pk),
        "draft_id": str(draft.pk),
        "revision": draft.revision,
        "published": False,
    }
    record_operation(
        principal=principal,
        operation="context.create",
        key=idempotency_key,
        request=request,
        resource_id=context.pk,
        response=receipt,
    )
    return receipt


def start_context_draft(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    revision: int,
    from_version: int | None,
) -> HubSkillDraft:
    require_private_principal(principal=principal)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    origin = (
        context.versions.filter(number=from_version).first()
        if from_version is not None
        else None
    )
    if from_version is not None and origin is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    child_ids = (
        set(
            HubSkillContextDependency.objects.filter(parent_version=origin).values_list(
                "child_skill_id", flat=True
            )
        )
        if origin
        else set()
    )
    with locked_context_graph(
        principal=principal, context_id=context_id, extra_ids=child_ids, private=False
    ):
        if origin is not None:
            require_distribution_eligibility(origin)
        return start_selected_draft(
            principal=principal,
            resource_id=context_id,
            kind="agent_context",
            revision=revision,
            from_version=from_version,
        )


def save_context_draft(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    revision: int,
    base_version: int | None,
    files: list[FileInput],
    description: str,
    note: str,
) -> HubSkillDraft:
    require_private_principal(principal=principal)
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    checks = check_context_files(files=files, complete=False)
    if any(item["code"] == "unsupported_instruction_scope" for item in checks["items"]):
        raise SkillsHubError(
            "unsupported_instruction_scope",
            "Remove reserved instruction/control paths.",
            status=422,
            detail=checks,
        )
    return save_owned_draft(
        principal=principal,
        resource_id=context_id,
        kind="agent_context",
        revision=revision,
        base_version=base_version,
        files=files,
        description=description,
        note=note,
    )


def get_compiled_draft(
    *, principal: SkillPrincipal, context_id: UUID, revision: int
) -> CompiledContext:
    with locked_context_graph(
        principal=principal, context_id=context_id, write=False
    ) as (context, draft, _):
        draft = require_draft_revision(draft, revision)
        return compile_owned_draft(context=context, draft=draft)


def dependency_changes(before: list[LockedLink], after: list[LockedLink]) -> list[dict]:
    old, new = {row["skill_id"]: row for row in before}, {
        row["skill_id"]: row for row in after
    }
    rows = []
    for identity in sorted(old.keys() | new.keys()):
        left, right = old.get(identity), new.get(identity)
        retained = right if right is not None else left
        assert retained is not None  # Identity comes from the union of both maps.
        change = (
            "added"
            if left is None
            else (
                "removed"
                if right is None
                else (
                    "selector_changed"
                    if left["requested"] != right["requested"]
                    else (
                        "version_changed"
                        if left["version_id"] != right["version_id"]
                        else "unchanged"
                    )
                )
            )
        )
        rows.append(
            {
                "skill_id": identity,
                "mount": retained["mount"],
                "change": change,
                "before": left,
                "after": right,
                "upstream_version": None,
            }
        )
    return rows


def _dependency_plan(
    *,
    context: HubSkill,
    draft: HubSkillDraft,
    resources: dict[UUID, HubSkill],
    links: list[LinkInput],
    refresh_skill_ids: list[UUID],
) -> dict:
    links = validate_links(links)
    try:
        refresh = {str(UUID(str(value))) for value in refresh_skill_ids}
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillsHubError(
            "invalid_dependency_selector", "Refresh IDs must be UUIDs.", status=400
        ) from exc
    if len(refresh) != len(refresh_skill_ids) or not refresh.issubset(
        {row["skill_id"] for row in links}
    ):
        raise SkillsHubError(
            "invalid_dependency_selector",
            "Refresh only unique selected followers.",
            status=400,
        )
    previous = {row["skill_id"]: row for row in draft.resolved_lock.get("links", [])}
    accepted, sources = [], []
    for link in links:
        identity, selector = link["skill_id"], link["selector"]
        if identity in refresh and "version" in selector:
            raise SkillsHubError(
                "invalid_dependency_selector",
                "Change a pin explicitly instead of refreshing it.",
                status=400,
            )
        old = previous.get(identity)
        if old and old["requested"] == selector and identity not in refresh:
            accepted.append(deepcopy(old))
            continue
        child = resources.get(UUID(identity))
        if child is None or child.deleted:
            raise SkillsHubError(
                "dependency_unavailable",
                "The selected leaf is unavailable.",
                status=404,
            )
        if child.kind != "skill":
            raise SkillsHubError(
                "unsupported_dependency_kind",
                "Only leaf skills may be linked.",
                status=400,
            )
        number: int | None
        if "version" in selector:
            number = cast(VersionSelector, selector)["version"]
        elif selector["label"] == "latest":
            number = child.latest_version_number
        else:
            number = (
                child.labels.filter(name=selector["label"])
                .values_list("version__number", flat=True)
                .first()
            )
            if number is None:
                raise SkillsHubError(
                    "label_not_found",
                    "The selected leaf label is not assigned.",
                    status=404,
                )
        version = child.versions.filter(number=number).first()
        if version is None:
            raise SkillsHubError(
                "dependency_unavailable",
                "The selected leaf version is unavailable.",
                status=404,
            )
        require_distribution_eligibility(version)
        accepted.append(
            {
                "mount": f"skills/{child.name}/",
                "skill_id": identity,
                "requested": selector,
                "version": version.number,
                "version_id": str(version.pk),
                "digest": version.digest,
            }
        )
        sources.append(
            {
                "skill_id": identity,
                "release_revision": child.release_revision,
                "security_revision": child.security_revision,
                "version_id": str(version.pk),
                "latest_version": (
                    child.latest_version_number
                    if selector == {"label": "latest"}
                    else None
                ),
            }
        )
    proposed = deepcopy(draft)
    proposed.context_spec = {**draft.context_spec, "links": links}
    proposed.resolved_lock = {"schema": 1, "links": accepted}
    compiled = compile_owned_draft(context=context, draft=proposed)
    plan_identity = {
        "context_id": str(context.pk),
        "revision": draft.revision,
        "links": links,
        "refresh_skill_ids": sorted(refresh),
        "candidate_digest": compiled.digest,
        "resolved_lock": compiled.lock,
        "sources": sources,
    }
    return {
        "revision": draft.revision,
        "fingerprint": canonical_digest(plan_identity),
        "candidate_digest": compiled.digest,
        "resolved_lock": compiled.lock,
        "changes": dependency_changes(
            draft.resolved_lock["links"], compiled.lock["links"]
        ),
        "checks": compiled.checks,
    }


def preview_dependencies(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    revision: int,
    links: list[LinkInput],
    refresh_skill_ids: list[UUID],
) -> dict:
    require_private_principal(principal=principal)
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    links = validate_links(links)
    with locked_context_graph(
        principal=principal,
        context_id=context_id,
        extra_ids={UUID(row["skill_id"]) for row in links},
        write=False,
    ) as (context, draft, resources):
        draft = require_draft_revision(draft, revision)
        return _dependency_plan(
            context=context,
            draft=draft,
            resources=resources,
            links=links,
            refresh_skill_ids=refresh_skill_ids,
        )


def apply_dependencies(
    *, principal: SkillPrincipal, context_id: UUID, request: dict, idempotency_key: UUID
) -> dict:
    """Return a durable revision receipt; replays never serialize a later draft as it.

    The HTTP layer can read DraftOut at this exact revision; if it has advanced,
    return this receipt and request a reload, never overwrite later edits.
    """
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    if set(request) != {
        "revision",
        "links",
        "refresh_skill_ids",
        "expected_fingerprint",
    }:
        raise SkillsHubError(
            "invalid_dependency_selector",
            "Use only the reviewed dependency fields.",
            status=400,
        )
    body = {"context_id": str(context_id), **request}
    replay = operation_replay(
        principal=principal,
        operation="context.dependencies",
        key=idempotency_key,
        request=body,
    )
    if replay is not None:
        return replay
    links = validate_links(request["links"])
    with locked_context_graph(
        principal=principal,
        context_id=context_id,
        extra_ids={UUID(row["skill_id"]) for row in links},
    ) as (context, draft, resources):
        replay = operation_replay(
            principal=principal,
            operation="context.dependencies",
            key=idempotency_key,
            request=body,
        )
        if replay is not None:
            return replay
        draft = require_draft_revision(draft, request["revision"])
        plan = _dependency_plan(
            context=context,
            draft=draft,
            resources=resources,
            links=links,
            refresh_skill_ids=request["refresh_skill_ids"],
        )
        if plan["fingerprint"] != request["expected_fingerprint"]:
            raise SkillsHubError(
                "dependency_plan_changed",
                "The dependency preview changed; review it again.",
                status=409,
            )
        draft.context_spec = {**draft.context_spec, "links": links}
        draft.resolved_lock = plan["resolved_lock"]
        draft.head_review_fingerprint = ""
        draft.revision += 1
        draft.save(
            update_fields=[
                "context_spec",
                "resolved_lock",
                "head_review_fingerprint",
                "revision",
                "updated_at",
            ]
        )
        receipt = {
            "context_id": str(context.pk),
            "draft_id": str(draft.pk),
            "revision": draft.revision,
            "digest": plan["candidate_digest"],
        }
        record_operation(
            principal=principal,
            operation="context.dependencies",
            key=idempotency_key,
            request=body,
            resource_id=context.pk,
            response=receipt,
        )
        return receipt


def review_context_head(
    *, principal: SkillPrincipal, context_id: UUID, revision: int
) -> dict:
    with locked_context_graph(
        principal=principal, context_id=context_id, write=False
    ) as (context, draft, _):
        draft = require_draft_revision(draft, revision)
        compiled = compile_owned_draft(context=context, draft=draft)
        return head_review(skill=context, draft=draft, candidate_digest=compiled.digest)


def acknowledge_context_head(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    revision: int,
    expected_digest: str,
    expected_head_version: int | None,
    review_fingerprint: str,
) -> HubSkillDraft:
    with locked_context_graph(principal=principal, context_id=context_id) as (
        context,
        draft,
        _,
    ):
        draft = require_draft_revision(draft, revision)
        compiled = compile_owned_draft(context=context, draft=draft)
        if compiled.digest != expected_digest:
            raise SkillsHubError(
                "candidate_changed", "The candidate changed; review again.", status=409
            )
        return acknowledge_head_locked(
            skill=context,
            draft=draft,
            candidate_digest=compiled.digest,
            expected_head_version=expected_head_version,
            review_fingerprint=review_fingerprint,
        )


def persist_projection(
    *,
    context: HubSkill,
    version: HubSkillVersion,
    compiled: CompiledContext,
    unchanged: bool,
) -> None:
    expected = [
        {
            "child_skill_id": UUID(link["skill_id"]),
            "child_version_id": UUID(link["version_id"]),
            "mount": link["mount"],
            "requested_selector": link["requested"],
        }
        for link in compiled.lock["links"]
    ]
    if unchanged:
        actual = list(
            HubSkillContextDependency.objects.filter(parent_version=version)
            .order_by("mount")
            .values("child_skill_id", "child_version_id", "mount", "requested_selector")
        )
        if actual != expected:
            raise SkillsHubError(
                "invalid_context_definition",
                "The retained dependency projection is inconsistent.",
                status=422,
            )
    else:
        HubSkillContextDependency.objects.bulk_create(
            [
                HubSkillContextDependency(
                    organization_id=context.organization_id,
                    workspace_id=context.workspace_id,
                    parent_version=version,
                    **row,
                )
                for row in expected
            ]
        )


def _publish_replay(context: HubSkill, response: dict) -> PublishResult:
    version = context.versions.get(pk=response["version_id"])
    require_distribution_eligibility(version)
    return PublishResult(
        version,
        response["unchanged"],
        version.checks,
        response["release_revision"],
        response["evidence"],
    )


def _publication_receipt(result: PublishResult) -> dict:
    return {
        "version_id": str(result.version.pk),
        "unchanged": result.unchanged,
        "release_revision": result.release_revision,
        "evidence": result.evidence,
    }


def publish_context_draft(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    revision: int,
    base_version: int | None,
    expected_digest: str,
    acknowledge_warnings: bool,
    note: str,
    idempotency_key: UUID,
    expected_head_version: int | None,
    review_fingerprint: str,
    evidence_run_id: UUID | None = None,
) -> PublishResult:
    from model_hub.services.skills_hub.v12.releases import (
        attach_context_evidence,
        validate_context_evidence,
    )

    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    request = {
        "context_id": str(context_id),
        "revision": revision,
        "base_version": base_version,
        "expected_digest": expected_digest,
        "acknowledge_warnings": acknowledge_warnings,
        "note": note,
        "expected_head_version": expected_head_version,
        "review_fingerprint": review_fingerprint,
        "evidence_run_id": evidence_run_id,
    }
    replay = operation_replay(
        principal=principal,
        operation="context.publish",
        key=idempotency_key,
        request=request,
    )
    if replay is not None:
        return _publish_replay(context, replay)
    with locked_context_graph(principal=principal, context_id=context_id) as (
        context,
        draft,
        _,
    ):
        replay = operation_replay(
            principal=principal,
            operation="context.publish",
            key=idempotency_key,
            request=request,
        )
        if replay is not None:
            return _publish_replay(context, replay)
        draft = require_draft_revision(draft, revision)
        if (
            base_version != draft.base_version
            or expected_head_version != draft.expected_head_version
        ):
            raise SkillsHubError(
                "stale_base",
                "The reviewed origin or publication head changed.",
                status=409,
            )
        compiled = compile_owned_draft(context=context, draft=draft)
        if compiled.digest != expected_digest:
            raise SkillsHubError(
                "candidate_changed", "The saved candidate changed.", status=409
            )
        require_reviewed_head_locked(
            skill=context,
            draft=draft,
            candidate_digest=compiled.digest,
            review_fingerprint=review_fingerprint,
        )
        if compiled.checks["summary"]["warnings"] and not acknowledge_warnings:
            raise SkillsHubError(
                "warnings_unacknowledged",
                "Review the complete tree warnings.",
                status=422,
                detail=compiled.checks,
            )
        evidence = validate_context_evidence(
            principal=principal,
            context=context,
            digest=compiled.digest,
            run_id=evidence_run_id,
        )
        version, unchanged = persist_immutable_version_locked(
            skill=context,
            actor=actor_for_principal(principal),
            files=compiled.files,
            note=note,
            parent_number=context.latest_version_number or None,
            origin_number=draft.base_version,
            description=compiled.spec["description"],
            frontmatter={},
            checks=compiled.checks,
            source=draft.source or {"type": "draft"},
            expected_digest=expected_digest,
        )
        persist_projection(
            context=context, version=version, compiled=compiled, unchanged=unchanged
        )
        attached = attach_context_evidence(
            principal=principal, context=context, version=version, prepared=evidence
        )
        result = PublishResult(
            version, unchanged, compiled.checks, context.release_revision, attached
        )
        _tombstone(draft)
        record_operation(
            principal=principal,
            operation="context.publish",
            key=idempotency_key,
            request=request,
            resource_id=context.pk,
            response=_publication_receipt(result),
        )
        return result


def restore_context(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    number: int,
    base_version: int | None,
    expected_digest: str,
    note: str,
    idempotency_key: UUID,
) -> PublishResult:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    request = {
        "context_id": str(context_id),
        "number": number,
        "base_version": base_version,
        "expected_digest": expected_digest,
        "note": note,
    }
    replay = operation_replay(
        principal=principal,
        operation="context.restore",
        key=idempotency_key,
        request=request,
    )
    if replay is not None:
        return _publish_replay(context, replay)
    original = context.versions.filter(number=number).first()
    if original is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    definition = version_definition(original)
    ids = {UUID(row["skill_id"]) for row in definition["links"]}
    with locked_context_graph(
        principal=principal, context_id=context_id, extra_ids=ids, private=False
    ) as (context, _, _):
        replay = operation_replay(
            principal=principal,
            operation="context.restore",
            key=idempotency_key,
            request=request,
        )
        if replay is not None:
            return _publish_replay(context, replay)
        if base_version != (context.latest_version_number or None):
            raise SkillsHubError(
                "stale_base",
                "Review the current publication head before restoring.",
                status=409,
            )
        require_distribution_eligibility(original)
        temporary = HubSkillDraft(
            skill=context,
            files=[
                file
                for file in version_files(original)
                if file["path"] in definition["owned_paths"]
            ],
            context_spec={
                "schema": 1,
                "description": definition["description"],
                "links": [
                    {"skill_id": link["skill_id"], "selector": link["requested"]}
                    for link in definition["links"]
                ],
            },
            resolved_lock={"schema": 1, "links": definition["links"]},
        )
        compiled = compile_owned_draft(context=context, draft=temporary)
        if compiled.digest != original.digest or original.digest != expected_digest:
            raise SkillsHubError(
                "candidate_changed",
                "The retained complete snapshot does not match.",
                status=409,
            )
        persist_projection(
            context=context, version=original, compiled=compiled, unchanged=True
        )
        version, unchanged = persist_immutable_version_locked(
            skill=context,
            actor=actor_for_principal(principal),
            files=compiled.files,
            note=note or f"Restored from v{number}",
            parent_number=base_version,
            origin_number=number,
            description=compiled.spec["description"],
            frontmatter={},
            checks=compiled.checks,
            source={
                **original.source,
                "operation": "restore",
                "restored_from_version": number,
            },
            always_new=True,
            expected_digest=expected_digest,
        )
        persist_projection(
            context=context, version=version, compiled=compiled, unchanged=False
        )
        result = PublishResult(
            version, unchanged, compiled.checks, context.release_revision
        )
        record_operation(
            principal=principal,
            operation="context.restore",
            key=idempotency_key,
            request=request,
            resource_id=context.pk,
            response=_publication_receipt(result),
        )
        return result
