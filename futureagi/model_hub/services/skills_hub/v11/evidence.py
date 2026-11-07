"""Visible exact-content evidence; identity, freshness and quality stay independent."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillLabelEvent, HubSkillVersion
from model_hub.models.skills_hub_v11 import (
    HubSkillTestEvidence,
    HubSkillTestRun,
    HubSkillTestSuite,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import (
    TERMINAL_JOB_STATUSES,
    SkillPrincipal,
)


def currentness(
    *, run: HubSkillTestRun, skill: HubSkill, candidate_digest: str
) -> dict:
    label = skill.labels.filter(name="production").first()
    event = (
        skill.label_events.filter(label="production")
        .order_by("-created_at", "-id")
        .first()
    )
    suite = HubSkillTestSuite.objects.filter(
        skill=skill,
        organization_id=skill.organization_id,
        workspace_id=skill.workspace_id,
    ).first()
    competitors = run.competitors
    available = HubSkillVersion.objects.filter(
        id__in=[item["version_id"] for item in competitors],
        skill__organization_id=skill.organization_id,
        skill__workspace_id=skill.workspace_id,
        skill__deleted=False,
    ).count()
    return {
        "content": run.candidate_digest == candidate_digest,
        "baseline": None
        if run.baseline_version_id is None
        else bool(
            label
            and label.version_id == run.baseline_version_id
            and (event.id if event else None) == run.baseline_event_id
        ),
        "suite": None
        if run.suite_revision is None
        else bool(suite and suite.revision == run.suite_revision),
        "competitors": None if not competitors else available == len(competitors),
        "payload_available": run.snapshot is not None
        and run.payload_purged_at is None
        and (run.payload_expires_at is None or run.payload_expires_at > timezone.now()),
    }


def _skill(principal: SkillPrincipal, skill_id: UUID) -> HubSkill:
    skill = HubSkill.no_workspace_objects.filter(
        id=skill_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if skill is None:
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    return skill


def list_test_evidence(
    *, principal: SkillPrincipal, skill_id: UUID, candidate: dict
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    skill = _skill(principal, skill_id)
    if candidate.get("kind") == "draft":
        draft = skill.drafts.filter(user_id=principal.user_id).first()
        if draft is None or draft.revision != candidate.get("revision"):
            raise SkillsHubError(
                "draft_conflict", "Choose the current saved draft revision.", status=409
            )
        digest = manifest_digest(draft.files)
    elif candidate.get("kind") == "version":
        version = skill.versions.filter(number=candidate.get("version")).first()
        if version is None:
            raise SkillsHubError("version_not_found", "Version not found.", status=404)
        digest = version.digest
    else:
        raise SkillsHubError(
            "invalid_selector", "Choose a draft or published version.", status=400
        )
    runs = (
        HubSkillTestRun.objects.filter(
            Q(visibility="workspace") | Q(job__owner_user_id=principal.user_id),
            job__skill=skill,
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
            candidate_digest=digest,
        )
        .select_related("job")
        .order_by("-job__created_at")[:100]
    )
    rows = []
    for run in runs:
        freshness = currentness(run=run, skill=skill, candidate_digest=digest)
        attempts = list(
            run.hub_skill_attempts.values(
                "routing_verdict", "delivery_verdict", "status"
            )
        )
        rows.append(
            {
                "run_id": str(run.pk),
                "status": run.job.status,
                "visibility": run.visibility,
                "currentness": freshness,
                "validity": run.validity,
                "selected_cases": len(run.selected_case_ids),
                "suite_cases": run.snapshot.get("suite_case_count")
                if run.snapshot
                else run.summary.get("suite_case_count"),
                "repetitions": run.repetitions,
                "answer_regressions": run.summary.get("decision", {}).get(
                    "regressed_cases", 0
                ),
                "routing_failures": sum(
                    item["routing_verdict"] == "fail" for item in attempts
                ),
                "delivery_failures": sum(
                    item["delivery_verdict"] == "fail" for item in attempts
                ),
                "error_attempts": sum(
                    item["status"] in {"error", "unknown"} for item in attempts
                ),
                "created_at": run.job.created_at,
                "can_attach": run.visibility == "workspace"
                and run.job.status in TERMINAL_JOB_STATUSES
                and freshness["payload_available"],
            }
        )
    return {
        "candidate_digest": digest,
        "current_release_revision": getattr(skill, "release_revision", 0),
        "runs": rows,
        "reason_if_none": None if rows else "No visible test evidence for these files.",
    }


def attach_test_evidence_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    version: HubSkillVersion,
    run_id: UUID,
    label_event: HubSkillLabelEvent | None = None,
) -> HubSkillTestEvidence:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Evidence attachment requires the release transaction and skill lock"
        )
    if (
        skill.organization_id != principal.scope.organization_id
        or skill.workspace_id != principal.scope.workspace_id
        or skill.deleted
        or version.skill_id != skill.id
    ):
        raise SkillsHubError(
            "evidence_mismatch", "Evidence does not belong to this release.", status=409
        )
    if label_event is not None and (
        label_event.skill_id != skill.id or label_event.to_version != version.number
    ):
        raise SkillsHubError(
            "evidence_mismatch", "Evidence does not match the label event.", status=409
        )
    run = (
        HubSkillTestRun.objects.select_for_update()
        .select_related("job")
        .filter(
            job_id=run_id,
            job__skill=skill,
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
        )
        .first()
    )
    if run is None or (
        run.visibility == "private" and run.job.owner_user_id != principal.user_id
    ):
        raise SkillsHubError(
            "evidence_mismatch", "Evidence does not match this release.", status=409
        )
    if run.visibility != "workspace":
        raise SkillsHubError(
            "evidence_share_required",
            "Explicitly share this evidence before attaching it.",
            status=403,
        )
    if (
        run.candidate_digest != version.digest
        or run.job.status not in TERMINAL_JOB_STATUSES
    ):
        raise SkillsHubError(
            "evidence_mismatch",
            "Attach terminal evidence for this exact content.",
            status=409,
        )
    freshness = currentness(run=run, skill=skill, candidate_digest=version.digest)
    if not freshness["payload_available"]:
        raise SkillsHubError(
            "evidence_expired",
            "Detailed evidence expired; it cannot be newly attached.",
            status=410,
        )
    evidence, _ = HubSkillTestEvidence.objects.get_or_create(
        version=version,
        run=run,
        label_event=label_event,
        defaults={
            "organization_id": principal.scope.organization_id,
            "workspace_id": principal.scope.workspace_id,
            "skill": skill,
            "candidate_digest": version.digest,
            "execution_fingerprint": run.execution_fingerprint,
            "attached_by_id": principal.user_id,
            "attached_by_api_key_id": principal.api_key_id,
            "currentness_at_action": freshness,
        },
    )
    return evidence
