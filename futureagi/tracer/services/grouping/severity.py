"""Independent, revision-fenced Feed severity work. Never changes memberships."""

import json
import secrets
import uuid
from collections.abc import Callable
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.constants.grouping_versions import SEVERITY_POLICY_VERSION
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingIssueState,
    TraceGroupingScope,
    TraceGroupingSeverityJob,
)
from tracer.models.trace_investigation import TraceInvestigationFinding
from tracer.queries.grouping import (
    GroupingSnapshotError,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    GroupingNotFound,
    _eligible_project,
    _token_hash,
)

MAX_MEMBERS = 5
MAX_INPUT_BYTES = 200_000
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


def account_call(*, job_id: uuid.UUID, operation: Callable, data: dict) -> dict:
    attempt_id = (
        TraceGroupingSeverityJob.no_workspace_objects.filter(pk=job_id)
        .values_list("source_attempt_id", flat=True)
        .first()
    )
    if attempt_id is None:
        raise GroupingNotFound("severity job not found")
    return operation(attempt_id=attempt_id, severity_job_id=job_id, **data)


def enqueue_severity(
    *, issue: TraceGroupingIssueState, attempt: TraceGroupingAttempt
) -> None:
    """Called under the publication scope lock; coalesce unclaimed revisions."""
    cluster = issue.cluster
    if issue.retired or issue.dirty or cluster.severity_source == "manual":
        return
    now = timezone.now()
    TraceGroupingSeverityJob.no_workspace_objects.filter(
        issue=issue, state="pending"
    ).exclude(issue_revision=issue.revision).update(state="superseded", updated_at=now)
    TraceGroupingSeverityJob.no_workspace_objects.get_or_create(
        issue=issue,
        issue_revision=issue.revision,
        policy_version=SEVERITY_POLICY_VERSION,
        defaults={"source_attempt": attempt, "not_before": now + timedelta(seconds=10)},
    )
    cluster.severity_assessment_status = "pending"
    cluster.save(update_fields=["severity_assessment_status", "updated_at"])


def _snapshot(issue: TraceGroupingIssueState) -> dict:
    """Prototype-first deterministic sample, plus oldest members; no text truncation.

    Counts and selection scope are disclosed. Whole linked evidence packets are
    retained; an oversized packet fails assessment, not issue publication.
    """
    members = TraceInvestigationFinding.no_workspace_objects.filter(
        cluster=issue.cluster,
        report__is_current=True,
        report__deleted=False,
        report__project_id=issue.scope.project_id,
        report__organization_id=issue.scope.organization_id,
        report__workspace_id=issue.scope.workspace_id,
        report__execution_status="completed",
    ).select_related("report")
    chosen = list(
        members.filter(id__in=issue.prototype_occurrence_ids).order_by("id")[
            :MAX_MEMBERS
        ]
    )
    chosen += list(
        members.exclude(id__in=[row.id for row in chosen]).order_by("id")[
            : MAX_MEMBERS - len(chosen)
        ]
    )
    from tracer.services.grouping.publish import _finding_evidence

    reports, rows = {}, []
    for member in chosen:
        key = str(member.report_id)
        if key not in reports:
            reports[key] = export_grouping_snapshot(report=member.report)
        snapshot = reports[key]
        finding = next(
            row
            for row in snapshot["report"]["findings"]
            if row["finding_id"] == member.finding_id
        )
        rows.append(
            {
                "occurrence_id": str(member.id),
                "report_id": key,
                "evidence_digest": member.report.evidence_digest,
                "outcome": member.report.outcome,
                "coverage": snapshot["report"]["coverage"],
                "missing_fields": snapshot["report"]["missing_fields"],
                "finding": finding,
                "linked_requirements": [
                    check
                    for check in snapshot["report"]["requirement_checks"]
                    if check["requirement_id"] == finding["requirement_id"]
                ],
                "evidence": [
                    {"ref": ref, "text": text, "digest": digest}
                    for ref, (text, digest) in _finding_evidence(
                        snapshot, str(member.id)
                    ).items()
                    if not ref.startswith("companion:")
                ],
            }
        )
    value = {
        "policy_version": SEVERITY_POLICY_VERSION,
        "issue_id": str(issue.cluster_id),
        "issue_revision": issue.revision,
        "mechanism": issue.mechanism,
        "member_count": members.count(),
        "trace_count": members.values("report__trace_id").distinct().count(),
        "selection": "prototype-first-then-UUID; maximum five; unsampled members not assessed",
        "members": rows,
    }
    if (
        not rows
        or len(json.dumps(value, ensure_ascii=False).encode()) > MAX_INPUT_BYTES
    ):
        raise GroupingSnapshotError("severity evidence is empty or exceeds bound")
    return value


def _current(job: TraceGroupingSeverityJob) -> bool:
    issue = job.issue
    return (
        not issue.retired
        and not issue.dirty
        and not issue.deleted
        and not issue.cluster.deleted
        and not issue.scope.deleted
        and issue.revision == job.issue_revision
        and issue.cluster.severity_source != "manual"
        and _eligible_project(issue.scope.project_id)
    )


def claim_severity(*, worker_id: str, limit: int) -> dict:
    if not worker_id or not 1 <= limit <= 10:
        raise GroupingControlError("invalid severity claim")
    now, claims = timezone.now(), []
    ids = list(
        TraceGroupingSeverityJob.no_workspace_objects.filter(
            Q(state="pending", not_before__lte=now)
            | Q(state="running", lease_expires_at__lte=now)
        )
        .order_by("not_before")
        .values_list("id", "issue__scope_id")[: limit * 4]
    )
    for job_id, scope_id in ids:
        with transaction.atomic():
            if (
                not TraceGroupingScope.no_workspace_objects.select_for_update(
                    skip_locked=True
                )
                .filter(pk=scope_id)
                .first()
            ):
                continue
            job = (
                TraceGroupingSeverityJob.no_workspace_objects.select_for_update(
                    skip_locked=True, of=("self",)
                )
                .select_related("issue__cluster", "issue__scope", "source_attempt")
                .filter(pk=job_id)
                .first()
            )
            if job is None or job.state not in {"pending", "running"}:
                continue
            if job.state == "running" and job.lease_expires_at > now:
                continue
            if not _current(job):
                job.state = "superseded"
            elif job.attempt_number >= MAX_ATTEMPTS:
                job.state = "failed"
                job.failure_code = "attempts_exhausted_or_unresolved_receipt"
                TraceErrorGroup.no_workspace_objects.filter(
                    pk=job.issue.cluster_id, severity_source__in=["default", "llm"]
                ).update(severity_assessment_status="failed")
            else:
                try:
                    snapshot = _snapshot(job.issue)
                except GroupingSnapshotError:
                    job.state, job.failure_code = (
                        "failed",
                        "evidence_unavailable_or_oversized",
                    )
                    TraceErrorGroup.no_workspace_objects.filter(
                        pk=job.issue.cluster_id, severity_source__in=["default", "llm"]
                    ).update(severity_assessment_status="failed")
                else:
                    digest = canonical_snapshot_digest(snapshot)
                    if job.snapshot_digest and job.snapshot_digest != digest:
                        job.state = "superseded"
                    else:
                        token = secrets.token_urlsafe(32)
                        job.snapshot, job.snapshot_digest = snapshot, digest
                        job.state = "running"
                        job.attempt_number += 1
                        job.lease_token_digest = _token_hash(token)
                        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                        claims.append(
                            {
                                "attempt_id": str(job.id),
                                "lease_token": token,
                                "snapshot": snapshot,
                                "snapshot_digest": digest,
                                "candidate_digest": digest,
                                "registry_revision": job.issue_revision,
                                "policy_version": job.policy_version,
                            }
                        )
            job.save()
        if len(claims) >= limit:
            break
    return {"claims": claims}


def lock_severity(
    *, job_id: uuid.UUID, lease_token: str, settlement: bool = False
) -> tuple:
    """Same scope-first lock order as grouping and human edits."""
    reference = (
        TraceGroupingSeverityJob.no_workspace_objects.filter(pk=job_id)
        .values("issue__scope_id")
        .first()
    )
    if reference is None:
        raise GroupingNotFound("severity job not found")
    scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
        pk=reference["issue__scope_id"]
    )
    job = (
        TraceGroupingSeverityJob.no_workspace_objects.select_for_update(of=("self",))
        .select_related("issue__cluster", "issue__scope", "source_attempt__work")
        .get(pk=job_id)
    )
    if not secrets.compare_digest(job.lease_token_digest, _token_hash(lease_token)):
        raise GroupingNotFound("severity job not found")
    if not settlement and (
        job.state != "running"
        or job.lease_expires_at <= timezone.now()
        or not _current(job)
    ):
        raise GroupingConflict("severity lease or issue revision is stale")
    return scope, job


def severity_accounting_authority(
    *, job_id: uuid.UUID, lease_token: str, settlement: bool = False
) -> tuple:
    scope, job = lock_severity(
        job_id=job_id, lease_token=lease_token, settlement=settlement
    )
    return scope, job.source_attempt


def renew_severity(*, job_id: uuid.UUID, lease_token: str, action: str) -> dict:
    if action != "renew":
        raise GroupingControlError("invalid severity action")
    with transaction.atomic():
        _, job = lock_severity(job_id=job_id, lease_token=lease_token)
        job.lease_expires_at = timezone.now() + timedelta(seconds=LEASE_SECONDS)
        job.save(update_fields=["lease_expires_at", "updated_at"])
    return {"status": "running"}


def _validate_result(result: object, snapshot: dict) -> dict:
    if not isinstance(result, dict) or set(result) != {
        "severity",
        "reason",
        "citations",
    }:
        raise GroupingControlError("invalid severity result")
    if not isinstance(result["severity"], str) or result["severity"] not in {
        "critical",
        "high",
        "medium",
        "low",
        "insufficient_evidence",
    }:
        raise GroupingControlError("invalid severity grade")
    if not isinstance(result["reason"], str) or not 1 <= len(result["reason"]) <= 2000:
        raise GroupingControlError("invalid severity reason")
    citations = result["citations"]
    if not isinstance(citations, list) or len(citations) > 30:
        raise GroupingControlError("invalid severity citations")
    allowed = {
        (row["occurrence_id"], ev["ref"]): ev["digest"]
        for row in snapshot["members"]
        for ev in row["evidence"]
    }
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {
            "occurrence_id",
            "ref",
            "digest",
        }:
            raise GroupingControlError("invalid severity citation")
        if (
            not all(isinstance(value, str) for value in citation.values())
            or allowed.get((citation["occurrence_id"], citation["ref"]))
            != citation["digest"]
        ):
            raise GroupingConflict("severity citation is not in assessed evidence")
    if result["severity"] != "insufficient_evidence" and not citations:
        raise GroupingControlError("severity requires evidence")
    return result


def publish_severity(
    *, job_id: uuid.UUID, lease_token: str, receipt_id: uuid.UUID
) -> dict:
    with transaction.atomic():
        scope, job = lock_severity(
            job_id=job_id, lease_token=lease_token, settlement=True
        )
        if (
            job.state in {"completed", "insufficient_evidence"}
            and job.receipt_id == receipt_id
        ):
            return {"status": job.state}
        if (
            not _current(job)
            or job.state != "running"
            or job.lease_expires_at <= timezone.now()
        ):
            raise GroupingConflict("severity result is stale")
        if canonical_snapshot_digest(_snapshot(job.issue)) != job.snapshot_digest:
            raise GroupingConflict("severity source evidence changed")
        call = TraceGroupingCall.no_workspace_objects.filter(
            pk=receipt_id,
            scope=scope,
            attempt=job.source_attempt,
            request_key__startswith=f"severity:{job.id}:",
            status__in=["settled", "unknown"],
        ).first()
        if call is None:
            raise GroupingConflict("severity requires its own durable receipt")
        result = _validate_result(call.result, job.snapshot)
        grade = result["severity"]
        job.result, job.receipt = result, call
        job.state = (
            "insufficient_evidence" if grade == "insufficient_evidence" else "completed"
        )
        job.save(update_fields=["result", "receipt", "state", "updated_at"])
        cluster = TraceErrorGroup.no_workspace_objects.select_for_update().get(
            pk=job.issue.cluster_id
        )
        cluster.severity_assessment_status = job.state
        cluster.severity_reason = result["reason"]
        fields = ["severity_assessment_status", "severity_reason", "updated_at"]
        if grade != "insufficient_evidence":
            cluster.priority = "urgent" if grade == "critical" else grade
            cluster.combined_impact = (
                "HIGH" if grade in {"critical", "high"} else grade.upper()
            )
            cluster.severity_source = "llm"
            fields += ["priority", "combined_impact", "severity_source"]
        cluster.save(update_fields=fields)
        return {"status": job.state}
