"""Append-only human judgements and authoritative, independently typed evidence."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_r3 import (
    HubSkillEvidenceAnnotation,
    HubSkillNativeEvidence,
)
from model_hub.models.skills_hub_v11 import HubSkillTestRun
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import (
    TERMINAL_JOB_STATUSES,
    SkillPrincipal,
)
from model_hub.services.skills_hub.v12.credentials import canonical_digest


def evidence_record(
    *, principal: SkillPrincipal, source: str, source_id: UUID, lock: bool = False
) -> HubSkillTestRun | HubSkillNativeEvidence:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=lock)
    scope = principal.scope
    rows: QuerySet[HubSkillTestRun] | QuerySet[HubSkillNativeEvidence]
    if source == "skills_text":
        rows = HubSkillTestRun.objects.select_related("job", "job__skill").filter(
            Q(visibility="workspace") | Q(job__owner_user_id=principal.user_id),
            job_id=source_id,
            job__organization_id=scope.organization_id,
            job__workspace_id=scope.workspace_id,
            job__kind="test",
            job__skill__organization_id=scope.organization_id,
            job__skill__workspace_id=scope.workspace_id,
            job__skill__deleted=False,
        )
    elif source == "simulation_native":
        rows = HubSkillNativeEvidence.objects.select_related(
            "skill", "simulation_job"
        ).filter(
            Q(visibility="workspace") | Q(owner_id=principal.user_id),
            pk=source_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            simulation_job__organization_id=scope.organization_id,
            simulation_job__workspace_id=scope.workspace_id,
            skill__deleted=False,
            skill__organization_id=scope.organization_id,
            skill__workspace_id=scope.workspace_id,
        )
    else:
        raise SkillsHubError(
            "invalid_evidence", "Choose a supported evidence source.", status=400
        )
    if lock:
        rows = rows.select_for_update(of=("self",))
    record = rows.first()
    if record is None:
        raise SkillsHubError("resource_not_found", "Evidence not found.", status=404)
    return record


def annotation_revision(
    *, principal: SkillPrincipal, source: str, source_id: UUID
) -> int:
    return (
        HubSkillEvidenceAnnotation.objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            source=source,
            source_id=source_id,
        ).aggregate(revision=Max("revision"))["revision"]
        or 0
    )


def evidence_reference(
    *,
    principal: SkillPrincipal,
    source: str,
    source_id: UUID,
    shared: bool = False,
    lock: bool = False,
) -> dict:
    record = evidence_record(
        principal=principal, source=source, source_id=source_id, lock=lock
    )
    if shared and record.visibility != "workspace":
        raise SkillsHubError(
            "evidence_share_required",
            "Explicitly share the selected evidence first.",
            status=403,
        )
    if (
        record.payload_purged_at is not None
        or record.payload_expires_at is None
        or record.payload_expires_at <= timezone.now()
        or (isinstance(record, HubSkillTestRun) and record.snapshot is None)
    ):
        raise SkillsHubError(
            "evidence_expired",
            "Detailed evidence is unavailable or expired.",
            status=410,
        )
    if isinstance(record, HubSkillTestRun):
        skill = record.job.skill
        status = record.job.status
        config = record.execution_configuration
        configuration = {
            "schema": 1,
            "digest": canonical_digest(config),
            "text": config,
        }
    else:
        skill = record.skill
        status = {"canceled": "cancelled"}.get(
            record.simulation_job.state, record.simulation_job.state
        )
        config = record.configuration
        configuration = {
            "schema": 1,
            "digest": canonical_digest(config),
            "native": config,
        }
    assert skill is not None
    if status not in TERMINAL_JOB_STATUSES:
        raise SkillsHubError(
            "evidence_mismatch", "Select terminal evidence.", status=409
        )
    return {
        "schema": 1,
        "source": source,
        "source_id": str(record.pk),
        "subject_kind": skill.kind,
        "subject_id": str(skill.pk),
        "candidate_digest": record.candidate_digest,
        "execution_fingerprint": record.execution_fingerprint,
        "status": status,
        "visibility": record.visibility,
        "payload_expires_at": record.payload_expires_at.isoformat(),
        "configuration": configuration,
        "annotation_revision": annotation_revision(
            principal=principal, source=source, source_id=source_id
        ),
    }


def _validate_citation(
    record: HubSkillTestRun | HubSkillNativeEvidence,
    *,
    source: str,
    attempt_id: UUID | None,
    check_id: UUID | None,
    cited_text: str,
) -> None:
    if attempt_id is None:
        if check_id is not None or cited_text:
            raise SkillsHubError(
                "invalid_annotation",
                "Choose an attempt for a check or citation.",
                status=400,
            )
        return
    if isinstance(record, HubSkillNativeEvidence):
        # Native attempts are owned by Simulation. Do not infer IDs or quotations
        # from task messages or the ordinary Simulation relative-score response.
        raise SkillsHubError(
            "runtime_incompatible",
            "Native attempt annotations require the qualified native result reader.",
            status=422,
        )
    attempt = record.hub_skill_attempts.filter(pk=attempt_id).first()
    if attempt is None:
        raise SkillsHubError("resource_not_found", "Attempt not found.", status=404)
    checks = [
        item
        for item in attempt.check_results
        if str(item.get("check_id")) == str(check_id)
    ]
    if check_id is not None and not checks:
        raise SkillsHubError(
            "resource_not_found", "Check not found in this attempt.", status=404
        )
    texts = [attempt.answer or ""]
    for check in checks:
        texts.extend(
            value
            for key, value in check.items()
            if key in {"evidence", "reason", "actual", "expected"}
            and isinstance(value, str)
        )
    if cited_text and not any(cited_text in text for text in texts):
        raise SkillsHubError(
            "invalid_annotation",
            "Cite an exact excerpt from this attempt or check.",
            status=400,
        )


@transaction.atomic
def append_annotation(
    *,
    principal: SkillPrincipal,
    source: str,
    source_id: UUID,
    annotation_revision: int,
    verdict: str,
    check_id: UUID | None,
    attempt_id: UUID | None,
    cited_text: str,
    comment: str,
) -> HubSkillEvidenceAnnotation:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if verdict not in {"agree", "disagree", "cannot_assess"} or any(
        not isinstance(s, str) or len(s) > 2000 for s in (cited_text, comment)
    ):
        raise SkillsHubError(
            "invalid_annotation",
            "Use a supported verdict and bounded text.",
            status=400,
        )
    record = evidence_record(principal=principal, source=source, source_id=source_id)
    skill_id = (
        record.job.skill_id if isinstance(record, HubSkillTestRun) else record.skill_id
    )
    assert skill_id is not None
    assert principal.user_id is not None
    HubSkill.no_workspace_objects.select_for_update().get(
        pk=skill_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    )
    ref = evidence_reference(
        principal=principal, source=source, source_id=source_id, lock=True
    )
    if (
        type(annotation_revision) is not int
        or annotation_revision != ref["annotation_revision"]
    ):
        raise SkillsHubError(
            "annotation_conflict",
            "A review was added. Reload before appending.",
            status=409,
        )
    _validate_citation(
        record,
        source=source,
        attempt_id=attempt_id,
        check_id=check_id,
        cited_text=cited_text,
    )
    row = HubSkillEvidenceAnnotation.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source=source,
        source_id=source_id,
        revision=annotation_revision + 1,
        actor_id=principal.user_id,
        verdict=verdict,
        check_id=check_id,
        attempt_id=attempt_id,
        cited_text=cited_text,
        comment=comment,
    )
    if source == "simulation_native":
        HubSkillNativeEvidence.objects.filter(pk=source_id).update(
            annotation_revision=row.revision
        )
    return row


def list_annotations(
    *,
    principal: SkillPrincipal,
    source: str,
    source_id: UUID,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    evidence_reference(principal=principal, source=source, source_id=source_id)
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid page and page size.", status=400
        )
    rows = HubSkillEvidenceAnnotation.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source=source,
        source_id=source_id,
    ).order_by("revision")
    count = rows.count()
    return {
        "results": list(
            rows[(page - 1) * page_size : page * page_size].values(
                "id",
                "revision",
                "actor_id",
                "check_id",
                "attempt_id",
                "verdict",
                "cited_text",
                "comment",
                "created_at",
            )
        ),
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def purge_annotation_payload(
    *, source: str, source_id: UUID, organization_id: UUID, workspace_id: UUID
) -> int:
    """Retention hook, called with the evidence owner's purge in the same transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Annotation purge must join the evidence retention transaction"
        )
    return HubSkillEvidenceAnnotation.objects.filter(
        source=source,
        source_id=source_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
    ).update(cited_text="", comment="")
