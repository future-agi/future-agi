"""Private, expiring trace previews; explicit redaction and reviewed suite acceptance."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.models.skills_hub_v11 import HubSkillTestSuite
from model_hub.models.skills_hub_v12 import (
    HubSkillContextCapture,
    HubSkillContextOperation,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256
from model_hub.services.skills_hub.v11.inventory import bind_findings, secret_findings
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import (
    canonical_json,
    get_suite,
    save_suite,
    validate_cases,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.trace_source import (
    authorize_trace_source,
    read_trace_source,
)
from tracer.models.project import Project

CASE_FIELDS = {
    "id",
    "title",
    "source_group_id",
    "prompt",
    "fixtures",
    "expected_trigger",
    "checks",
    "reviewed",
}


def _findings(case: dict) -> list[dict]:
    # Fixture names, titles and check configuration are disclosed too, not just bodies.
    return secret_findings(canonical_json(case), location={"case_id": case["id"]})


def preview_trace_capture(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    trace_ref: dict,
    selected_segment_ids: list[str],
) -> dict:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    require_scope_access(principal=principal, write=True)
    context = get_resource(
        principal=principal, resource_id=context_id, kind="agent_context"
    )
    source = read_trace_source(principal=principal, trace_ref=trace_ref)
    if (
        not isinstance(selected_segment_ids, list)
        or not selected_segment_ids
        or len(selected_segment_ids) > 200
        or any(not isinstance(item, str) for item in selected_segment_ids)
        or len(set(selected_segment_ids)) != len(selected_segment_ids)
    ):
        raise SkillsHubError(
            "capture_unsafe",
            "Explicitly select distinct request or tool-result segments.",
            status=422,
        )
    by_id = {item["id"]: item for item in source["segments"]}
    if set(selected_segment_ids) - set(by_id):
        raise SkillsHubError(
            "capture_unsafe",
            "A selected source segment changed or is not supported.",
            status=422,
        )
    segments = [by_id[key] for key in selected_segment_ids]
    recorded = source["recorded_context"]
    version = None
    try:
        if str(UUID(str(recorded["id"]))) == str(context.pk):
            version = HubSkillVersion.objects.filter(
                pk=UUID(str(recorded["version_id"])),
                skill=context,
                digest=recorded["digest"],
            ).first()
    except (TypeError, ValueError, KeyError):
        version = None
    with transaction.atomic():
        # A source deletion/retention sweep increments this fence before clearing projections.
        project = (
            Project.no_workspace_objects.select_for_update()
            .filter(
                pk=source["trace_ref"]["project_id"],
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .first()
        )
        if (
            project is None
            or project.analysis_source_generation != source["project_generation"]
        ):
            raise SkillsHubError(
                "capture_unsafe", "The source changed. Preview it again.", status=422
            )
        context = get_resource(
            principal=principal, resource_id=context_id, kind="agent_context", lock=True
        )
        capture = HubSkillContextCapture.objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            context=context,
            owner_id=principal.user_id,
            trace_ref=source["trace_ref"],
            source_fingerprint=source["source_fingerprint"],
            source_hashes={
                "segments": source["source_hashes"],
                "physical": source["physical"],
                "external": source["external"],
            },
            selected_text=segments,
            recorded_version=version,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
    findings = []
    for item in segments:
        findings += secret_findings(item["text"], location={"path": item["id"]})
    return {
        "capture_id": str(capture.pk),
        "expires_at": capture.expires_at,
        "source_fingerprint": capture.source_fingerprint,
        "recorded_context": (
            {"version_id": str(version.pk), "digest": version.digest}
            if version
            else None
        ),
        "segments": segments,
        "omitted": [
            {
                "reason": "Only selected request and tool-result text is included; system, assistant and reasoning content is excluded."
            }
        ],
        "outbound_findings": bind_findings(findings, capture.source_fingerprint),
        "replay_scope": "text_only",
    }


def accept_trace_case(
    *,
    principal: SkillPrincipal,
    context_id: UUID,
    request: dict,
    idempotency_key: UUID,
    capture_id: UUID | None = None,
) -> dict:
    """capture_id is a route argument; legacy service callers may include it in request."""
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    request = dict(request)
    if capture_id is None:
        capture_id = request.pop("capture_id", None)
    if (
        set(request)
        != {
            "source_fingerprint",
            "suite_revision",
            "case",
            "acknowledge_workspace_sharing",
        }
        or request["acknowledge_workspace_sharing"] is not True
    ):
        raise SkillsHubError(
            "capture_unsafe",
            "Explicitly review and acknowledge workspace sharing.",
            status=422,
        )
    get_resource(principal=principal, resource_id=context_id, kind="agent_context")
    capture = HubSkillContextCapture.objects.filter(
        pk=capture_id,
        context_id=context_id,
        owner_id=principal.user_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if capture is None:
        raise SkillsHubError("capture_not_found", "Capture not found.", status=404)
    # Reauthorization precedes replay. Lost responses do not require retained raw payload.
    authorize_trace_source(principal=principal, trace_ref=capture.trace_ref)
    lookup = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "principal_key": f"{principal.kind}:{principal.api_key_id or principal.user_id}",
        "operation": "accept_trace_case",
        "idempotency_key": idempotency_key,
    }
    digest = canonical_digest(
        {"context_id": str(context_id), "capture_id": str(capture_id), **request}
    )
    prior = HubSkillContextOperation.objects.filter(**lookup).first()
    if prior:
        if prior.request_digest != digest:
            raise SkillsHubError(
                "idempotency_conflict",
                "The operation key belongs to a different capture acceptance.",
                status=409,
            )
        return get_suite(scope=principal.scope, skill_id=context_id)
    if capture.expires_at <= timezone.now() or capture.selected_text is None:
        raise SkillsHubError(
            "capture_expired", "Capture preview expired. Preview again.", status=410
        )
    if request["source_fingerprint"] != capture.source_fingerprint:
        raise SkillsHubError(
            "capture_unsafe",
            "The selected source does not match this preview.",
            status=422,
        )
    source = read_trace_source(principal=principal, trace_ref=capture.trace_ref)
    if source["source_fingerprint"] != capture.source_fingerprint:
        raise SkillsHubError(
            "capture_unsafe", "The source span changed. Preview again.", status=422
        )
    case = validate_cases([request["case"]], require_reviewed=True)[0]
    if case["expected_trigger"] is not None:
        raise SkillsHubError(
            "invalid_test_config",
            "Standing context has no activation expectation.",
            status=400,
        )
    if _findings(case):
        raise SkillsHubError(
            "capture_unsafe",
            "Redact possible secrets before workspace disclosure.",
            status=422,
        )
    case["source_group_id"] = f"trace:{capture.source_fingerprint}"
    with transaction.atomic():
        project = (
            Project.no_workspace_objects.select_for_update()
            .filter(
                pk=source["trace_ref"]["project_id"],
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .first()
        )
        if (
            project is None
            or project.analysis_source_generation != source["project_generation"]
        ):
            raise SkillsHubError(
                "capture_unsafe", "The source changed before acceptance.", status=422
            )
        get_resource(
            principal=principal, resource_id=context_id, kind="agent_context", lock=True
        )
        suite = (
            HubSkillTestSuite.objects.select_for_update()
            .filter(
                skill_id=context_id,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .first()
        )
        capture = HubSkillContextCapture.objects.select_for_update().get(pk=capture.pk)
        prior, created = HubSkillContextOperation.objects.get_or_create(
            **lookup, defaults={"request_digest": digest}
        )
        if prior.request_digest != digest:
            raise SkillsHubError(
                "idempotency_conflict",
                "The operation key belongs to a different acceptance.",
                status=409,
            )
        if not created:
            return get_suite(scope=principal.scope, skill_id=context_id)
        if capture.accepted_receipt is not None:
            raise SkillsHubError(
                "capture_unsafe", "This preview was already accepted.", status=409
            )
        if capture.expires_at <= timezone.now():
            raise SkillsHubError(
                "capture_expired", "Capture expired before acceptance.", status=410
            )
        cases = (
            [
                {key: value for key, value in item.items() if key in CASE_FIELDS}
                for item in suite.cases
            ]
            if suite
            else []
        )
        if any(item["id"] == case["id"] for item in cases):
            raise SkillsHubError(
                "test_suite_conflict",
                "This case already exists. Do not replace it through capture.",
                status=409,
            )
        saved = save_suite(
            principal=principal,
            skill_id=context_id,
            revision=request["suite_revision"],
            cases=[*cases, case],
        )
        receipt = {
            "capture_id": str(capture.pk),
            "case_id": case["id"],
            "suite_revision": saved.revision,
            "source_fingerprint": capture.source_fingerprint,
            "sanitized_digest": canonical_digest(case),
            "sanitized_prompt_sha256": file_sha256(case["prompt"]),
            "recorded_version_id": (
                str(capture.recorded_version_id)
                if capture.recorded_version_id
                else None
            ),
            "replay_scope": "text_only",
            "exact_context_available": capture.recorded_version_id is not None,
        }
        capture.accepted_receipt = receipt
        capture.selected_text = None
        capture.save(update_fields=["accepted_receipt", "selected_text"])
        HubSkillContextOperation.objects.filter(pk=prior.pk).update(
            result_resource_id=capture.pk,
            response=receipt,
            completed_at=timezone.now(),
        )
        return get_suite(scope=principal.scope, skill_id=context_id)


def purge_expired_captures(*, batch_size: int = 100) -> int:
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Invalid retention batch size")
    with transaction.atomic():
        ids = list(
            HubSkillContextCapture.objects.select_for_update(skip_locked=True)
            .filter(expires_at__lte=timezone.now(), selected_text__isnull=False)
            .order_by("expires_at")
            .values_list("pk", flat=True)[:batch_size]
        )
        return HubSkillContextCapture.objects.filter(pk__in=ids).update(
            selected_text=None
        )
