"""Exact context release review, scoped impact and transactional pointer movement."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillLabelEvent, HubSkillVersion
from model_hub.models.skills_hub_v11 import HubSkillTestRun
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextDependency,
    HubSkillContextReceipt,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.authoring_foundation import head_review
from model_hub.services.skills_hub.compare import compare_file_sets
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import _set_label_locked, _validate_name
from model_hub.services.skills_hub.snapshots import version_definition
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.evidence import (
    attach_test_evidence_locked,
    currentness,
)
from model_hub.services.skills_hub.v11.governance import (
    advance_release_revision_locked,
    authorize_label_change_locked,
)
from model_hub.services.skills_hub.v11.principals import (
    effective_scope_level,
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import (
    TERMINAL_JOB_STATUSES,
    SkillPrincipal,
)
from model_hub.services.skills_hub.v12.authoring import (
    actor_for_principal,
    compile_owned_draft,
    dependency_changes,
    locked_context_graph,
    operation_replay,
    record_operation,
    require_draft_revision,
    version_files,
)
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)
from model_hub.services.skills_hub.v12.types import LockedLink
from tfc.constants.levels import Level


def compare_context(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    from_ref: int | str,
    to_ref: int | str,
    revision: int | None = None,
    page: int = 1,
) -> dict:
    """Revision-bound paginated diff metadata; chunk routes carry exact text."""
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    if type(page) is not int or page < 1:
        raise SkillsHubError(
            "invalid_selector", "Choose a positive comparison page.", status=400
        )
    versions: dict[int, HubSkillVersion] = {}
    ids: set[UUID] = set()
    private = "draft" in (from_ref, to_ref)
    if private:
        require_private_principal(principal=principal)
        if revision is None:
            raise SkillsHubError(
                "context_precondition_required",
                "Supply the saved draft revision.",
                status=428,
            )
    for ref in (from_ref, to_ref):
        if ref == "draft":
            continue
        if type(ref) is not int or ref < 1:
            raise SkillsHubError(
                "invalid_selector",
                "Choose exact versions or your saved draft.",
                status=400,
            )
        version = context.versions.filter(number=ref).first()
        if version is None:
            raise SkillsHubError("version_not_found", "Version not found.", status=404)
        versions[ref] = version
        ids.update(
            HubSkillContextDependency.objects.filter(
                parent_version=version
            ).values_list("child_skill_id", flat=True)
        )
    with locked_context_graph(
        principal=principal,
        context_id=context_id,
        extra_ids=ids,
        private=private,
        write=False,
    ) as (context, draft, _):
        snapshots: dict[int | str, tuple[list[FileInput], list[LockedLink], dict]] = {}
        for ref, version in versions.items():
            require_distribution_eligibility(version)
            snapshots[ref] = (
                version_files(version),
                version_definition(version)["links"],
                {"kind": "version", "version": ref, "digest": version.digest},
            )
        if private:
            assert revision is not None  # Validated before locking the graph.
            draft = require_draft_revision(draft, revision)
            compiled = compile_owned_draft(context=context, draft=draft)
            snapshots["draft"] = (
                compiled.files,
                compiled.lock["links"],
                {"kind": "draft", "revision": revision, "digest": compiled.digest},
            )
        before, after = snapshots[from_ref], snapshots[to_ref]
        diff = compare_file_sets(before[0], after[0])
        rows = sorted(
            diff["files"],
            key=lambda row: (
                row["path"] != "AGENTS.md",
                row["status"] == "unchanged",
                row["path"],
            ),
        )
        output = []
        for row in rows[(page - 1) * 25 : page * 25]:
            item = {
                key: value
                for key, value in row.items()
                if key not in {"from_text", "to_text"}
            }
            item["from_text_url"] = (
                _file_url(context.pk, before[2], row["path"])
                if row["from_sha256"]
                else None
            )
            item["to_text_url"] = (
                _file_url(context.pk, after[2], row["path"])
                if row["to_sha256"]
                else None
            )
            output.append(item)
        return {
            "from": from_ref,
            "to": to_ref,
            "files": output,
            "summary": diff["summary"],
            "dependency_changes": dependency_changes(before[1], after[1]),
            "page": page,
            "page_size": 25,
            "count": len(rows),
            "total_pages": (len(rows) + 24) // 25,
        }


def validate_context_evidence(
    *, principal: SkillPrincipal, context: HubSkill, digest: str, run_id: UUID | None
) -> dict | None:
    """Validate the legacy singleton text reference before allocation or no-op.

    Joint native/text packet resolution belongs to r3.release_requests. This seam
    does not silently interpret native IDs as text or make one evidence kind mandatory.
    """
    if run_id is None:
        return None
    require_private_principal(principal=principal)
    run = (
        HubSkillTestRun.objects.select_for_update()
        .select_related("job")
        .filter(
            job_id=run_id,
            job__skill=context,
            job__organization_id=context.organization_id,
            job__workspace_id=context.workspace_id,
        )
        .first()
    )
    if run is None or (
        run.visibility != "workspace" and run.job.owner_user_id != principal.user_id
    ):
        raise SkillsHubError(
            "evidence_mismatch", "Evidence does not match this release.", status=409
        )
    if run.visibility != "workspace":
        raise SkillsHubError(
            "evidence_share_required",
            "Share the evidence explicitly first.",
            status=403,
        )
    if run.candidate_digest != digest or run.job.status not in TERMINAL_JOB_STATUSES:
        raise SkillsHubError(
            "evidence_mismatch",
            "Select terminal evidence for this exact candidate.",
            status=409,
        )
    freshness = currentness(run=run, skill=context, candidate_digest=digest)
    if not freshness["payload_available"]:
        raise SkillsHubError(
            "evidence_expired", "The selected evidence payload expired.", status=410
        )
    return {"run_id": str(run.pk), "currentness": freshness, "validity": run.validity}


def attach_context_evidence(
    *,
    principal: SkillPrincipal,
    context: HubSkill,
    version: HubSkillVersion,
    prepared: dict | None,
    label_event: HubSkillLabelEvent | None = None,
) -> dict | None:
    if prepared is None:
        return None
    attached = attach_test_evidence_locked(
        principal=principal,
        skill=context,
        version=version,
        run_id=UUID(prepared["run_id"]),
        label_event=label_event,
    )
    # Both writes are inside the same release transaction; no post-move freshness
    # is externally visible as pre-move evidence. Preserve an older no-op association.
    if label_event is not None:
        attached.currentness_at_action = prepared["currentness"]
        attached.save(update_fields=["currentness_at_action"])
    return {
        "run_id": str(attached.run_id),
        "currentness": attached.currentness_at_action,
        "validity": attached.run.validity,
    }


def context_release_state(*, principal: SkillPrincipal, context_id: UUID) -> dict:
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    level = (
        effective_scope_level(principal=principal)
        if principal.kind != "system_key"
        else 0
    )
    writer, admin = level >= Level.WORKSPACE_MEMBER, level >= Level.WORKSPACE_ADMIN
    return {
        "latest_version": context.latest_version_number or None,
        "release_revision": context.release_revision,
        "security_revision": context.security_revision,
        "protected_label_names": context.protected_label_names,
        "labels": [
            {
                "name": row.name,
                "version": row.version.number,
                "version_id": str(row.version_id),
                "updated_at": row.updated_at.isoformat(),
                "is_protected": row.name in context.protected_label_names,
                "can_move": (
                    admin if row.name in context.protected_label_names else writer
                ),
                "can_remove": (
                    admin if row.name in context.protected_label_names else writer
                ),
                "release_revision": context.release_revision,
            }
            for row in context.labels.select_related("version").order_by("name")
        ],
        "permissions": {
            "can_publish": writer,
            "can_promote": writer,
            "can_manage_protection": admin,
            "can_quarantine": admin,
            "can_issue_credentials": admin,
        },
    }


def release_impact(
    *, context: HubSkill, label: str | None, target_version: int | None, operation: str
) -> dict:
    """Stream the full relevant binding identity; show only the first 25 rows."""
    rows = HubSkillContextBinding.objects.filter(
        context=context,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        retired_at__isnull=True,
    )
    if label is not None:
        # Other environments are not part of this label transition. Pins remain
        # visible as intentional non-followers but do not change their target.
        rows = rows.filter(Q(selector__label=label) | Q(selector__has_key="version"))
    identity = {
        "context_id": str(context.pk),
        "operation": operation,
        "label": label,
        "target_version": target_version,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    )
    rows = rows.annotate(
        observed=Exists(
            HubSkillContextReceipt.objects.filter(
                binding_id=OuterRef("pk"),
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
                expires_at__gt=timezone.now(),
            )
        )
    )
    bindings: list[dict] = []
    following, pinned, count, unobserved = 0, 0, 0, 0
    for row in rows.order_by("id").iterator(chunk_size=100):
        value = {
            "id": str(row.pk),
            "revision": row.revision,
            "mode": row.update_mode,
            "selector": row.selector,
            "configuration_digest": row.configuration_digest,
        }
        digest.update(
            b"\n" + json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )
        count += 1
        unobserved += int(not row.observed)
        pinned += int("version" in row.selector)
        following += int("label" in row.selector)
        if len(bindings) < 25:
            bindings.append(value)
    return {
        "as_of": timezone.now().isoformat(),
        "scope": "known_references",
        "bindings": bindings,
        "following": following,
        "pinned": pinned,
        "unobserved": unobserved,
        "more_available": count > len(bindings),
        "fingerprint": "sha256:" + digest.hexdigest(),
    }


def _file_url(context_id: UUID, candidate: dict, path: str) -> str:
    prefix = f"/model-hub/skills/contexts/{context_id}/"
    if candidate["kind"] == "draft":
        return (
            prefix
            + "draft/files/?"
            + urlencode({"revision": candidate["revision"], "path": path})
        )
    return (
        prefix + f"versions/{candidate['version']}/files/?" + urlencode({"path": path})
    )


def _diff_page(
    *,
    context: HubSkill,
    before: HubSkillVersion | None,
    after_files: list[FileInput],
    candidate: dict,
    page: int = 1,
) -> dict:
    diff = compare_file_sets(version_files(before) if before else [], after_files)
    rows = sorted(
        diff["files"],
        key=lambda row: (
            row["path"] != "AGENTS.md",
            row["status"] == "unchanged",
            row["path"],
        ),
    )
    output = []
    for row in rows[(page - 1) * 25 : page * 25]:
        item = {
            key: value
            for key, value in row.items()
            if key not in {"from_text", "to_text"}
        }
        item["from_text_url"] = (
            _file_url(
                context.pk, {"kind": "version", "version": before.number}, row["path"]
            )
            if before and row["from_sha256"]
            else None
        )
        item["to_text_url"] = (
            _file_url(context.pk, candidate, row["path"]) if row["to_sha256"] else None
        )
        output.append(item)
    return {
        "from": before.number if before else None,
        "to": candidate,
        "files": output,
        "summary": diff["summary"],
        "page": page,
        "page_size": 25,
        "count": len(rows),
        "total_pages": (len(rows) + 24) // 25,
        "context_id": str(context.pk),
    }


def review_context_release(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    operation: str,
    target_version: int | None = None,
    label: str | None = None,
    evidence_run_id: UUID | None = None,
    revision: int | None = None,
    page: int = 1,
) -> dict:
    if type(page) is not int or page < 1:
        raise SkillsHubError(
            "invalid_selector", "Choose a positive review page.", status=400
        )
    if operation not in {"publish", "move", "restore", "remove", "archive"}:
        raise SkillsHubError(
            "invalid_selector", "Choose a supported release operation.", status=400
        )
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    version = (
        context.versions.filter(number=target_version).first()
        if target_version is not None
        else None
    )
    if target_version is not None and version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    ids = (
        set(
            HubSkillContextDependency.objects.filter(
                parent_version=version
            ).values_list("child_skill_id", flat=True)
        )
        if version
        else set()
    )
    with locked_context_graph(
        principal=principal,
        context_id=context_id,
        extra_ids=ids,
        private=target_version is None,
        write=False,
    ) as (context, draft, _):
        if version is None:
            require_private_principal(principal=principal)
            if revision is None:
                raise SkillsHubError(
                    "context_precondition_required",
                    "Review the exact saved draft revision.",
                    status=428,
                )
            draft = require_draft_revision(draft, revision)
            compiled = compile_owned_draft(context=context, draft=draft)
            files, lock, checks, digest = (
                compiled.files,
                compiled.lock,
                compiled.checks,
                compiled.digest,
            )
            candidate = {"kind": "draft", "revision": revision, "digest": digest}
            origin = draft.base_version
            reviewed_head = head_review(
                skill=context, draft=draft, candidate_digest=digest
            )
        else:
            require_distribution_eligibility(version)
            definition = version_definition(version)
            files, lock, checks, digest = (
                version_files(version),
                {"schema": 1, "links": definition["links"]},
                version.checks,
                version.digest,
            )
            candidate = {"kind": "version", "version": version.number, "digest": digest}
            origin, reviewed_head = version.origin_number, None
        if label is not None:
            _validate_name(label)
            pointer = (
                context.labels.select_related("version").filter(name=label).first()
            )
            before = pointer.version if pointer else None
        else:
            before = context.versions.filter(number=origin).first() if origin else None
        latest = context.versions.filter(number=context.latest_version_number).first()
        old_links = version_definition(before)["links"] if before else []
        evidence = validate_context_evidence(
            principal=principal, context=context, digest=digest, run_id=evidence_run_id
        )
        state = context_release_state(principal=principal, context_id=context.pk)
        impact = release_impact(
            context=context,
            label=label,
            target_version=(
                None if operation in {"remove", "archive"} else target_version
            ),
            operation=operation,
        )
        identity = {
            "candidate": candidate,
            "operation": operation,
            "label": label,
            "release_revision": context.release_revision,
            "security_revision": context.security_revision,
            "impact": impact["fingerprint"],
            "evidence": evidence,
            "head": reviewed_head,
        }
        return {
            "candidate": candidate,
            "base_version": origin,
            "latest_version": context.latest_version_number or None,
            "release_state": state,
            "changes": _diff_page(
                context=context,
                before=before,
                after_files=files,
                candidate=candidate,
                page=page,
            ),
            "head_changes": _diff_page(
                context=context,
                before=latest,
                after_files=files,
                candidate=candidate,
                page=page,
            ),
            "dependency_changes": dependency_changes(old_links, lock["links"]),
            "checks": checks,
            "evidence": evidence,
            "impact": impact,
            "head_review": reviewed_head,
            "review_fingerprint": canonical_digest(identity),
        }


def _move_preconditions(
    *,
    principal: SkillPrincipal,
    context: HubSkill,
    label: str,
    request: dict,
    operation: str,
) -> dict:
    required = {
        "expected_version",
        "expected_release_revision",
        "expected_security_revision",
        "expected_impact_fingerprint",
    }
    if not required.issubset(request):
        raise SkillsHubError(
            "context_precondition_required",
            "Read pointer, release, safety and impact preconditions first.",
            status=428,
        )
    authorize_label_change_locked(
        principal=principal,
        skill=context,
        name=label,
        expected_version=request["expected_version"],
        expected_release_revision=request["expected_release_revision"],
    )
    if (
        type(request["expected_security_revision"]) is not int
        or request["expected_security_revision"] != context.security_revision
    ):
        raise SkillsHubError(
            "release_conflict", "The security state changed; review again.", status=409
        )
    impact = release_impact(
        context=context,
        label=label,
        target_version=request.get("version"),
        operation=operation,
    )
    if impact["fingerprint"] != request["expected_impact_fingerprint"]:
        raise SkillsHubError(
            "impact_changed", "The relevant target configuration changed.", status=409
        )
    return impact


def move_context_label(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    label: str,
    request: dict,
    idempotency_key: UUID,
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    _validate_name(label)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    if len(request.get("note", "")) > 500:
        raise SkillsHubError(
            "payload_too_large",
            "Label notes can have up to 500 characters.",
            status=413,
        )
    allowed = {
        "version",
        "expected_version",
        "expected_release_revision",
        "expected_security_revision",
        "expected_impact_fingerprint",
        "expected_digest",
        "note",
        "evidence_run_id",
    }
    if set(request) - allowed:
        raise SkillsHubError(
            "invalid_selector",
            "Use only the supported exact release preconditions.",
            status=400,
        )
    body = {"context_id": str(context_id), "label": label, **request}
    # Reauthorization of protected authority precedes retry receipts, but mutable
    # pointer CAS must not invalidate the receipt for a previously completed move.
    if label in context.protected_label_names:
        from model_hub.services.skills_hub.v11.principals import require_skill_admin

        require_skill_admin(principal=principal, skill=context)
    replay = operation_replay(
        principal=principal,
        operation="context.label",
        key=idempotency_key,
        request=body,
    )
    if replay is not None:
        require_distribution_eligibility(
            context.versions.get(pk=replay["label"]["version_id"])
        )
        return replay
    number = request.get("version")
    if type(number) is not int or number < 1:
        raise SkillsHubError(
            "invalid_selector", "Choose a positive target version.", status=400
        )
    version = context.versions.filter(number=number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    ids = set(
        HubSkillContextDependency.objects.filter(parent_version=version).values_list(
            "child_skill_id", flat=True
        )
    )
    with locked_context_graph(
        principal=principal, context_id=context_id, extra_ids=ids, private=False
    ) as (context, _, _):
        replay = operation_replay(
            principal=principal,
            operation="context.label",
            key=idempotency_key,
            request=body,
        )
        if replay is not None:
            if label in context.protected_label_names:
                from model_hub.services.skills_hub.v11.principals import (
                    require_skill_admin,
                )

                require_skill_admin(principal=principal, skill=context)
            require_distribution_eligibility(version)
            return replay
        _move_preconditions(
            principal=principal,
            context=context,
            label=label,
            request=request,
            operation="move",
        )
        if not request.get("expected_digest"):
            raise SkillsHubError(
                "context_precondition_required",
                "Supply the reviewed target digest.",
                status=428,
            )
        if request["expected_digest"] != version.digest:
            raise SkillsHubError(
                "candidate_changed",
                "The reviewed target digest does not match.",
                status=409,
            )
        require_distribution_eligibility(version)
        prepared = validate_context_evidence(
            principal=principal,
            context=context,
            digest=version.digest,
            run_id=request.get("evidence_run_id"),
        )
        previous_revision = context.release_revision
        moved = _set_label_locked(
            skill=context,
            actor=actor_for_principal(principal),
            name=label,
            version_number=version.number,
            note=request.get("note", ""),
            expected_digest=version.digest,
        )
        event = (
            context.label_events.get(release_revision=context.release_revision)
            if previous_revision != context.release_revision
            else None
        )
        evidence = attach_context_evidence(
            principal=principal,
            context=context,
            version=version,
            prepared=prepared,
            label_event=event,
        )
        response = {
            "label": {
                "name": moved.name,
                "version": version.number,
                "version_id": str(version.pk),
                "digest": version.digest,
            },
            "event_id": str(event.pk) if event else None,
            "release_revision": context.release_revision,
            "evidence": evidence,
            "currentness_at_action": prepared["currentness"] if prepared else None,
            "post_move_state": {
                "label": label,
                "version": version.number,
                "release_revision": context.release_revision,
            },
        }
        record_operation(
            principal=principal,
            operation="context.label",
            key=idempotency_key,
            request=body,
            resource_id=context.pk,
            response=response,
        )
        return response


def remove_context_label(
    *, principal: SkillPrincipal, context_id: UUID, label: str, request: dict
) -> None:
    require_private_principal(principal=principal)
    _validate_name(label)
    if len(request.get("note", "")) > 500:
        raise SkillsHubError(
            "payload_too_large",
            "Label notes can have up to 500 characters.",
            status=413,
        )
    with locked_context_graph(
        principal=principal, context_id=context_id, private=False
    ) as (context, _, _):
        _move_preconditions(
            principal=principal,
            context=context,
            label=label,
            request=request,
            operation="remove",
        )
        pointer = context.labels.select_related("version").filter(name=label).first()
        if pointer is None:
            raise SkillsHubError("label_not_found", "Label not found.", status=404)
        previous = pointer.version.number
        pointer.delete()
        HubSkillLabelEvent.objects.create(
            skill=context,
            label=label,
            from_version=previous,
            to_version=None,
            actor_id=principal.user_id,
            actor_api_key_id=principal.api_key_id,
            release_revision=advance_release_revision_locked(skill=context),
            note=request.get("note", ""),
        )


def context_dependencies(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    number: int,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    """One immutable-version page with authorized current update facts."""
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 25
    ):
        raise SkillsHubError(
            "invalid_selector", "Choose a bounded dependency page.", status=400
        )
    version = context.versions.filter(number=number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    require_distribution_eligibility(version)
    links = version_definition(version)["links"]
    results = []
    for link in links[(page - 1) * page_size : page * page_size]:
        child = HubSkill.no_workspace_objects.filter(
            pk=link["skill_id"],
            kind="skill",
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
        ).first()
        selector = link["requested"]
        if child is None:
            upstream, state = None, "unavailable"
        elif "version" in selector:
            upstream, state = selector["version"], "pinned_as_intended"
        else:
            upstream = (
                child.latest_version_number
                if selector["label"] == "latest"
                else child.labels.filter(name=selector["label"])
                .values_list("version__number", flat=True)
                .first()
            )
            state = (
                "unavailable"
                if upstream is None
                else "current" if upstream == link["version"] else "update_available"
            )
        results.append({**link, "upstream_version": upstream, "update_state": state})
    return {
        "context_id": str(context.pk),
        "version": number,
        "version_id": str(version.pk),
        "digest": version.digest,
        "results": results,
        "count": len(links),
        "page": page,
        "page_size": page_size,
        "total_pages": (len(links) + page_size - 1) // page_size,
    }


def context_dependents(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    page: int = 1,
    page_size: int = 25,
    candidate_version: int | None = None,
    view: str = "active",
) -> dict:
    child = get_resource(principal=principal, resource_id=skill_id, kind="skill")
    if page < 1 or not 1 <= page_size <= 100 or view not in {"active", "history"}:
        raise SkillsHubError(
            "invalid_selector", "Choose a valid dependency page and view.", status=400
        )
    if (
        candidate_version is not None
        and not child.versions.filter(number=candidate_version).exists()
    ):
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    rows = HubSkillContextDependency.objects.filter(
        child_skill=child,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        parent_version__skill__organization_id=principal.scope.organization_id,
        parent_version__skill__workspace_id=principal.scope.workspace_id,
        parent_version__skill__kind="agent_context",
    )
    if view == "active":
        from django.db.models import F, Func, JSONField

        rows = (
            rows.filter(parent_version__skill__deleted=False)
            .filter(
                Q(
                    parent_version__number=F(
                        "parent_version__skill__latest_version_number"
                    )
                )
                | Q(parent_version__labels__isnull=False)
                | Q(
                    parent_version__skill__hub_skill_bindings__selector__version=Func(
                        F("parent_version__number"),
                        function="to_jsonb",
                        output_field=JSONField(),
                    ),
                    parent_version__skill__hub_skill_bindings__retired_at__isnull=True,
                )
            )
            .distinct()
        )
    count = rows.count()
    selected = (
        rows.select_related("parent_version__skill", "child_version")
        .prefetch_related("parent_version__labels")
        .order_by("parent_version__skill_id", "parent_version__number")[
            (page - 1) * page_size : page * page_size
        ]
    )
    upstream: dict[str, int | None] = dict(
        child.labels.values_list("name", "version__number")
    )
    upstream["latest"] = child.latest_version_number or None
    result = []
    for row in selected:
        selector = row.requested_selector
        number = (
            upstream.get(selector["label"])
            if "label" in selector
            else selector["version"]
        )
        state = (
            "pinned_as_intended"
            if "version" in selector
            else (
                "unavailable"
                if number is None
                else (
                    "update_available"
                    if number != row.child_version.number
                    else "current"
                )
            )
        )
        result.append(
            {
                "context_id": str(row.parent_version.skill_id),
                "name": row.parent_version.skill.name,
                "context_version": row.parent_version.number,
                "labels": sorted(
                    label.name for label in row.parent_version.labels.all()
                ),
                "requested": selector,
                "locked_version": row.child_version.number,
                "update_state": state,
            }
        )
    return {
        "as_of": timezone.now().isoformat(),
        "scope": "published_context_references",
        "results": result,
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }
