"""Owner-private native evidence linked to existing Simulation jobs.

All ordinary Simulation projections must apply ordinary_simulation_jobs before
native launch is enabled. Sharing here is never public-object publication.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from model_hub.models import HubSkillNativeEvidence
from model_hub.models.skills_hub_r3 import (
    HubSkillEvidenceAnnotation,
    HubSkillSpendReservation,
)
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.native_reducer import validate_matrix
from model_hub.services.skills_hub.v11.inventory import fingerprint
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.types import ResourceKind
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.services.skills_context_adapter import (
    NativeExecutionSpec,
    validate_native_configuration,
)

TERMINAL = {"completed": "completed", "failed": "failed", "canceled": "cancelled"}


def ordinary_simulation_jobs(path: str = "") -> Q:
    """Use before list/count/compare/export. Shared native jobs stay Skills-only."""
    return Q(**{f"{path}hub_skill_native_evidence__isnull": True})


def visible_native_evidence(
    *, principal: SkillPrincipal, resource_id: UUID, kind: ResourceKind
) -> QuerySet[HubSkillNativeEvidence]:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    get_resource(principal=principal, resource_id=resource_id, kind=kind)
    return HubSkillNativeEvidence.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        skill_id=resource_id,
        simulation_job__deleted=False,
    ).filter(Q(owner_id=principal.user_id) | Q(visibility="workspace"))


def get_native_evidence(
    *,
    principal: SkillPrincipal,
    evidence_id: UUID,
    resource_id: UUID,
    kind: ResourceKind,
    payload: bool = False,
    lock: bool = False,
) -> HubSkillNativeEvidence:
    rows = visible_native_evidence(
        principal=principal, resource_id=resource_id, kind=kind
    )
    if lock:
        if not connection.in_atomic_block:
            raise RuntimeError("Evidence locks require an enclosing transaction.")
        rows = rows.select_for_update(of=("self",))
    evidence = (
        rows.select_related("simulation_job", "skill").filter(pk=evidence_id).first()
    )
    if evidence is None:
        raise SkillsHubError(
            "resource_not_found", "Native evidence not found.", status=404
        )
    if payload and (
        evidence.payload_purged_at is not None
        or evidence.payload_expires_at <= timezone.now()
    ):
        raise SkillsHubError(
            "evidence_expired", "Detailed native evidence expired.", status=410
        )
    return evidence


def bind_native_evidence_locked(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    job: HostedHarnessJob,
    spec: NativeExecutionSpec,
    payload_expires_at: datetime,
    reservation_id: UUID,
) -> HubSkillNativeEvidence:
    """The existing hosted creation transaction calls this before exposing its job.

    The shared spending authority must have reserved admission for this job. This
    does not reserve twice or dispatch work. It is not safe to attach private
    metadata to a job whose ordinary projections have already been materialized.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Bind native evidence in the hosted job creation transaction."
        )
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    require_scope_access(principal=principal, write=True)
    skill = get_resource(principal=principal, resource_id=resource_id, kind=kind)
    scope = principal.scope
    if (
        job.organization_id != scope.organization_id
        or job.workspace_id != scope.workspace_id
    ):
        raise SkillsHubError(
            "resource_not_found", "Simulation job not found.", status=404
        )
    if (
        job.state != "queued"
        or job.current_attempt_number
        or job.test_execution_id
        or job.run_test_id
    ):
        raise SkillsHubError(
            "runtime_incompatible",
            "Private ownership must precede Simulation materialization.",
            status=422,
        )
    if timezone.is_naive(payload_expires_at) or payload_expires_at <= timezone.now():
        raise SkillsHubError(
            "invalid_test_config",
            "Native evidence requires a future bounded expiry.",
            status=400,
        )
    if (payload_expires_at - timezone.now()).total_seconds() > 90 * 86400:
        raise SkillsHubError(
            "invalid_test_config",
            "Native evidence retention cannot exceed 90 days.",
            status=400,
        )
    reservation = HubSkillSpendReservation.objects.filter(
        pk=reservation_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        source="simulation_native",
        source_id=job.pk,
        parent__isnull=True,
        state="prepared",
        reserved_usd__gt=0,
    ).first()
    if reservation is None:
        raise SkillsHubError(
            "skills_unavailable",
            "The shared workspace allowance must reserve native admission before launch.",
            status=503,
        )
    if validate_native_configuration(spec.configuration) != spec.configuration:
        raise SkillsHubError(
            "runtime_incompatible",
            "Use the canonical reviewed native configuration.",
            status=422,
        )
    candidate = spec.snapshots["candidate"]
    if candidate is None or candidate["skill_id"] != str(resource_id):
        raise SkillsHubError(
            "evidence_mismatch",
            "Native candidate does not belong to this subject.",
            status=409,
        )
    if job.payload.get("skills_context") != spec.guest_payload():
        raise SkillsHubError(
            "evidence_mismatch",
            "Simulation must execute the exact reviewed native payload.",
            status=409,
        )
    return HubSkillNativeEvidence.objects.create(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        skill=skill,
        owner_id=principal.user_id,
        simulation_job=job,
        candidate_digest=candidate["digest"],
        execution_fingerprint=spec.fingerprint,
        configuration={
            "schema": 1,
            "digest": fingerprint(spec.configuration),
            "native": spec.configuration,
        },
        planned_matrix=validate_matrix(spec.matrix),
        payload_expires_at=payload_expires_at,
        protected_locations=[{"kind": "simulation_job", "id": str(job.pk)}],
    )


def share_native_evidence(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    evidence_id: UUID,
    acknowledge_snapshot_sharing: bool,
) -> HubSkillNativeEvidence:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    require_scope_access(principal=principal, write=True)
    if acknowledge_snapshot_sharing is not True:
        raise SkillsHubError(
            "invalid_test_config",
            "Explicitly acknowledge sharing native inputs and results with the workspace.",
            status=400,
        )
    with transaction.atomic():
        evidence = get_native_evidence(
            principal=principal,
            evidence_id=evidence_id,
            resource_id=resource_id,
            kind=kind,
            payload=True,
            lock=True,
        )
        if evidence.owner_id != principal.user_id:
            raise SkillsHubError(
                "resource_not_found", "Owned native evidence not found.", status=404
            )
        if evidence.visibility == "private":
            evidence.visibility, evidence.shared_at = "workspace", timezone.now()
            evidence.save(update_fields=["visibility", "shared_at"])
        return evidence


def resolve_native_evidence_ref(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    evidence_id: UUID,
    candidate_digest: str,
    annotation_revision: int,
    configuration_digest: str | None = None,
    lock: bool = False,
) -> dict:
    evidence = get_native_evidence(
        principal=principal,
        evidence_id=evidence_id,
        resource_id=resource_id,
        kind=kind,
        payload=True,
        lock=lock,
    )
    if evidence.visibility != "workspace":
        raise SkillsHubError(
            "evidence_share_required",
            "Share the selected native evidence before attaching it.",
            status=403,
        )
    if evidence.candidate_digest != candidate_digest:
        raise SkillsHubError(
            "evidence_mismatch",
            "Native evidence is for different candidate bytes.",
            status=409,
        )
    status = TERMINAL.get(evidence.simulation_job.state)
    if status is None:
        raise SkillsHubError(
            "evidence_mismatch",
            "Native work must be terminal before release review.",
            status=409,
        )
    configuration = evidence.configuration
    validate_native_configuration(configuration.get("native"))
    if configuration.get("digest") != fingerprint(configuration.get("native")) or (
        configuration_digest is not None
        and configuration_digest != configuration["digest"]
    ):
        raise SkillsHubError(
            "evidence_mismatch",
            "The native environment no longer matches the release target.",
            status=409,
        )
    revision = (
        HubSkillEvidenceAnnotation.objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            source="simulation_native",
            source_id=evidence.id,
        ).aggregate(value=Max("revision"))["value"]
        or 0
    )
    revision = max(revision, evidence.annotation_revision)
    if type(annotation_revision) is not int or annotation_revision != revision:
        raise SkillsHubError(
            "evidence_mismatch",
            "Human annotations changed; review the new revision.",
            status=409,
        )
    return {
        "schema": 1,
        "source": "simulation_native",
        "source_id": str(evidence.id),
        "subject_kind": kind,
        "subject_id": str(resource_id),
        "candidate_digest": evidence.candidate_digest,
        "execution_fingerprint": evidence.execution_fingerprint,
        "status": status,
        "visibility": evidence.visibility,
        "payload_expires_at": evidence.payload_expires_at.isoformat(),
        "configuration": configuration,
        "annotation_revision": revision,
    }


def authorize_native_job_payload(
    *, principal: SkillPrincipal, job_id: UUID
) -> HubSkillNativeEvidence:
    """Mandatory guard for explicitly routed artifact, trace, call and guest reads."""
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    evidence = (
        HubSkillNativeEvidence.objects.filter(
            simulation_job_id=job_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        .select_related("skill")
        .first()
    )
    if evidence is None:
        raise SkillsHubError("resource_not_found", "Native job not found.", status=404)
    kind: ResourceKind
    if evidence.skill.kind == "agent_context":
        kind = "agent_context"
    elif evidence.skill.kind == "skill":
        kind = "skill"
    else:
        raise SkillsHubError(
            "resource_not_found", "Native subject not found.", status=404
        )
    return get_native_evidence(
        principal=principal,
        evidence_id=evidence.id,
        resource_id=evidence.skill_id,
        kind=kind,
        payload=True,
    )


def purge_native_control_payload(*, evidence_id: UUID) -> bool:
    """Clear known control-plane payloads; retain an explicit incomplete purge.

    External artifacts and downstream projections need the integrated retention
    worker. A nonempty protected_locations inventory prevents a false purged flag.
    Expiry independently denies every guarded read even before physical deletion.
    """
    from simulate.models.hosted_harness import (
        HostedHarnessEvent,
        HostedHarnessManifest,
        HostedHarnessReceipt,
    )

    with transaction.atomic():
        evidence = (
            HubSkillNativeEvidence.objects.select_for_update()
            .filter(pk=evidence_id)
            .first()
        )
        if evidence is None or evidence.payload_expires_at > timezone.now():
            return False
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            pk=evidence.simulation_job_id
        )
        if job.state not in TERMINAL:
            return False
        job.payload, job.stage_outputs, job.failure = {}, [], None
        job.save(update_fields=["payload", "stage_outputs", "failure"])
        HostedHarnessEvent.no_workspace_objects.filter(attempt__job=job).update(
            payload=None, rejection_message=None
        )
        HostedHarnessManifest.no_workspace_objects.filter(attempt__job=job).update(
            body={}
        )
        HostedHarnessReceipt.no_workspace_objects.filter(job=job).update(body={})
        # No source/prompts survive here. Do not mark all projections purged while
        # another storage location can still hold private inputs or answers.
        return not evidence.protected_locations
