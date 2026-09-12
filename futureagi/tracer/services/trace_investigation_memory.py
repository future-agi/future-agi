from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from django.db import transaction

from accounts.models.organization_membership import OrganizationMembership
from tfc.constants.levels import Level
from tracer.models.trace_investigation import (
    TraceInvestigationFeedback,
    TraceInvestigationMemoryAction,
    TraceInvestigationMemoryEvaluation,
    TraceInvestigationMemoryPromotion,
    TraceInvestigationMemorySnapshot,
    TraceInvestigationMemoryStatus,
    TraceInvestigationReport,
)
from tracer.models.trace_scan import TraceScanConfig, TraceScanEngine
from tracer.services.trace_investigation import (
    InvestigationConflict,
    InvestigationNotFound,
    _digest,
    get_or_create_active_memory,
    validate_investigation_memory,
)

_MAX_FEEDBACK_EVENTS = 100
_MAX_EVALUATION_BYTES = 32 * 1024


def _require_project_access(
    *,
    actor,
    organization_id: uuid.UUID,
    project,
    require_admin: bool = False,
):
    membership = OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.id,
        organization_id=organization_id,
        is_active=True,
    ).first()
    if membership is None:
        raise InvestigationNotFound("project scope was not found")
    if project.organization_id != organization_id:
        raise InvestigationNotFound("project scope was not found")
    if require_admin:
        if membership.level_or_legacy < Level.ADMIN:
            raise InvestigationNotFound("project scope was not found")
    elif project.workspace_id is not None:
        if not actor.can_write_to_workspace(project.workspace):
            raise InvestigationNotFound("project scope was not found")
    elif membership.level_or_legacy < Level.MEMBER:
        raise InvestigationNotFound("project scope was not found")
    return membership


def _locked_config(
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
) -> TraceScanConfig:
    try:
        return (
            TraceScanConfig.no_workspace_objects.select_for_update()
            .select_related("project")
            .get(
                project_id=project_id,
                project__organization_id=organization_id,
                project__workspace_id=workspace_id,
                project__trace_type="observe",
                engine=TraceScanEngine.OMEGA,
            )
        )
    except TraceScanConfig.DoesNotExist as exc:
        raise InvestigationNotFound("Omega project scope was not found") from exc


def submit_reviewed_feedback(
    *,
    actor,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    occurrence_id: uuid.UUID,
    finding_id: str,
    idempotency_key: str,
    feedback_type: str,
    comment: str,
) -> dict[str, object]:
    content = {
        "report_id": report_id,
        "occurrence_id": occurrence_id,
        "finding_id": finding_id,
        "feedback_type": feedback_type,
        "comment": comment,
    }
    content_digest = _digest(content)
    with transaction.atomic():
        try:
            report = (
                TraceInvestigationReport.no_workspace_objects.select_for_update(
                    of=("self",)
                )
                .select_related("project__workspace__organization")
                .get(
                    id=report_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    active_projection_updated=True,
                )
            )
        except TraceInvestigationReport.DoesNotExist as exc:
            raise InvestigationNotFound("investigation report was not found") from exc
        _require_project_access(
            actor=actor,
            organization_id=organization_id,
            project=report.project,
        )

        occurrence = next(
            (
                row
                for row in report.occurrences
                if str(row.get("occurrence_id")) == str(occurrence_id)
            ),
            None,
        )
        if occurrence is None or occurrence.get("finding_id") != finding_id:
            raise InvestigationNotFound("investigation finding was not found")

        existing = TraceInvestigationFeedback.no_workspace_objects.filter(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if (
                existing.content_digest != content_digest
                or existing.reviewer_id != actor.id
            ):
                raise InvestigationConflict(
                    "feedback identity was reused with different content"
                )
            feedback = existing
        else:
            feedback = TraceInvestigationFeedback.no_workspace_objects.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                report=report,
                reviewer=actor,
                occurrence_id=occurrence_id,
                finding_id=finding_id,
                idempotency_key=idempotency_key,
                feedback_type=feedback_type,
                comment=comment,
                content_digest=content_digest,
            )
    return {
        "feedback_id": feedback.id,
        "review_state": feedback.review_state,
        "learning_event_id": feedback.learning_event_id,
        "active_memory_unchanged": True,
    }


def create_memory_candidate(
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    expected_parent_snapshot_id: uuid.UUID,
    idempotency_key: str,
    source_feedback_ids: list[uuid.UUID],
    entries: list[Mapping[str, object]],
) -> dict[str, object]:
    if len(source_feedback_ids) > _MAX_FEEDBACK_EVENTS:
        raise InvestigationConflict("memory candidate exceeds 100 feedback events")
    normalized, candidate_digest = validate_investigation_memory(entries)
    normalized_feedback_ids = sorted({str(item) for item in source_feedback_ids})
    with transaction.atomic():
        config = _locked_config(
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        parent = get_or_create_active_memory(config)
        existing = TraceInvestigationMemorySnapshot.no_workspace_objects.filter(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if (
                existing.digest != candidate_digest
                or existing.parent_id != expected_parent_snapshot_id
                or existing.source_feedback_ids != normalized_feedback_ids
            ):
                raise InvestigationConflict(
                    "memory candidate identity was reused with different content"
                )
            candidate = existing
        else:
            if parent.id != expected_parent_snapshot_id:
                raise InvestigationConflict("active memory parent changed")
            if parent.digest == candidate_digest:
                raise InvestigationConflict(
                    "memory candidate is identical to its parent"
                )
            feedback = list(
                TraceInvestigationFeedback.no_workspace_objects.filter(
                    id__in=source_feedback_ids,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    review_state="reviewed",
                )
            )
            if len(feedback) != len(normalized_feedback_ids):
                raise InvestigationNotFound("reviewed feedback scope was not found")

            inherited = {row["id"]: row for row in parent.entries}
            allowed_feedback = set(normalized_feedback_ids)
            for entry in normalized:
                if inherited.get(entry["id"]) == entry:
                    continue
                if str(entry.get("source_feedback_id")) not in allowed_feedback:
                    raise InvestigationConflict(
                        "each new or changed memory entry needs reviewed provenance"
                    )
            candidate = TraceInvestigationMemorySnapshot.no_workspace_objects.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                parent=parent,
                idempotency_key=idempotency_key,
                status=TraceInvestigationMemoryStatus.CANDIDATE,
                digest=candidate_digest,
                entries=normalized,
                source_feedback_ids=normalized_feedback_ids,
            )
    return {
        "candidate_id": candidate.id,
        "candidate_digest": candidate.digest,
        "parent_snapshot_id": candidate.parent_id,
        "status": candidate.status,
        "active_memory_unchanged": True,
    }


def record_memory_evaluation(
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    candidate_digest: str,
    idempotency_key: str,
    cohort_id: str,
    metrics: Mapping[str, object],
    passed: bool,
    holdout_disjoint: bool,
) -> dict[str, object]:
    if len(json.dumps(metrics, separators=(",", ":")).encode()) > _MAX_EVALUATION_BYTES:
        raise InvestigationConflict("memory evaluation metrics exceed 32 KiB")
    content_digest = _digest(
        {
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "cohort_id": cohort_id,
            "metrics": metrics,
            "passed": passed,
            "holdout_disjoint": holdout_disjoint,
        }
    )
    with transaction.atomic():
        try:
            candidate = TraceInvestigationMemorySnapshot.no_workspace_objects.select_for_update().get(
                id=candidate_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        except TraceInvestigationMemorySnapshot.DoesNotExist as exc:
            raise InvestigationNotFound("memory candidate was not found") from exc
        if candidate.digest != candidate_digest:
            raise InvestigationConflict("memory candidate digest changed")
        existing = TraceInvestigationMemoryEvaluation.no_workspace_objects.filter(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if existing.content_digest != content_digest:
                raise InvestigationConflict(
                    "evaluation identity was reused with different content"
                )
            evaluation = existing
        else:
            if candidate.status != TraceInvestigationMemoryStatus.CANDIDATE:
                raise InvestigationConflict(
                    f"memory snapshot is already {candidate.status}"
                )
            evaluation = TraceInvestigationMemoryEvaluation.no_workspace_objects.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                candidate=candidate,
                idempotency_key=idempotency_key,
                candidate_digest=candidate_digest,
                cohort_id=cohort_id,
                metrics=dict(metrics),
                passed=passed,
                holdout_disjoint=holdout_disjoint,
                content_digest=content_digest,
            )
    return {
        "evaluation_id": evaluation.id,
        "status": "passed" if evaluation.passed else "failed",
        "candidate_digest": evaluation.candidate_digest,
        "promotion_allowed": evaluation.passed and evaluation.holdout_disjoint,
    }


def change_active_memory(
    *,
    actor,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    action: str,
    idempotency_key: str,
    expected_current_snapshot_id: uuid.UUID,
    target_snapshot_id: uuid.UUID,
    target_digest: str,
    evaluation_id: uuid.UUID | None,
) -> dict[str, object]:
    content_digest = _digest(
        {
            "action": action,
            "expected_current": expected_current_snapshot_id,
            "target": target_snapshot_id,
            "target_digest": target_digest,
            "evaluation": evaluation_id,
        }
    )
    with transaction.atomic():
        config = _locked_config(
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        _require_project_access(
            actor=actor,
            organization_id=organization_id,
            project=config.project,
            require_admin=True,
        )
        current = get_or_create_active_memory(config)
        existing = TraceInvestigationMemoryPromotion.no_workspace_objects.filter(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if (
                existing.content_digest != content_digest
                or existing.actor_id != actor.id
            ):
                raise InvestigationConflict(
                    "memory change identity was reused with different content"
                )
            promotion = existing
            active = promotion.to_snapshot
        else:
            if current.id != expected_current_snapshot_id:
                raise InvestigationConflict("active memory snapshot changed")
            try:
                target = TraceInvestigationMemorySnapshot.no_workspace_objects.select_for_update().get(
                    id=target_snapshot_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
            except TraceInvestigationMemorySnapshot.DoesNotExist as exc:
                raise InvestigationNotFound(
                    "target memory snapshot was not found"
                ) from exc
            if target.digest != target_digest:
                raise InvestigationConflict("target memory digest changed")

            evaluation = None
            if action == TraceInvestigationMemoryAction.PROMOTE:
                if target.status != TraceInvestigationMemoryStatus.CANDIDATE:
                    raise InvestigationConflict("target is not a memory candidate")
                if target.parent_id != current.id or evaluation_id is None:
                    raise InvestigationConflict("memory promotion parent is stale")
                try:
                    evaluation = (
                        TraceInvestigationMemoryEvaluation.no_workspace_objects.get(
                            id=evaluation_id,
                            candidate=target,
                            candidate_digest=target.digest,
                            organization_id=organization_id,
                            workspace_id=workspace_id,
                            project_id=project_id,
                        )
                    )
                except TraceInvestigationMemoryEvaluation.DoesNotExist as exc:
                    raise InvestigationNotFound(
                        "memory evaluation was not found"
                    ) from exc
                if not evaluation.passed or not evaluation.holdout_disjoint:
                    raise InvestigationConflict(
                        "memory evaluation does not permit promotion"
                    )
            else:
                if evaluation_id is not None:
                    raise InvestigationConflict(
                        "rollback does not accept an evaluation"
                    )
                if target.status != TraceInvestigationMemoryStatus.SUPERSEDED:
                    raise InvestigationConflict(
                        "rollback target is not retained history"
                    )

            current.status = TraceInvestigationMemoryStatus.SUPERSEDED
            current.save(update_fields=["status", "updated_at"])
            target.status = TraceInvestigationMemoryStatus.ACTIVE
            target.save(update_fields=["status", "updated_at"])
            config.omega_memory = target.entries
            config.save(update_fields=["omega_memory", "updated_at"])
            promotion = TraceInvestigationMemoryPromotion.no_workspace_objects.create(
                organization_id=organization_id,
                workspace_id=workspace_id,
                project_id=project_id,
                actor=actor,
                from_snapshot=current,
                to_snapshot=target,
                evaluation=evaluation,
                action=action,
                idempotency_key=idempotency_key,
                content_digest=content_digest,
            )
            active = target
    return {
        "status": action,
        "change_id": promotion.id,
        "active_snapshot_id": active.id,
        "active_digest": active.digest,
        "previous_snapshot_id": promotion.from_snapshot_id,
        "next_attempt_uses": active.id,
    }
