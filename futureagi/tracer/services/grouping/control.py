"""Durable, fenced report-level grouping and feature work control."""

import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from tfc.ee_gating import is_oss
from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingFeatureState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingFeatureJob,
    TraceGroupingOutbox,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
)
from tracer.queries.grouping import (
    GroupingSnapshotError,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)

FEATURE_LEASE_SECONDS = 120
GROUPING_LEASE_SECONDS = 180
MAX_ATTEMPTS = 5
# How long a simulation run's grouping waits before checking again that the run
# has finished; it also keeps waiting works out of the claim window's head.
SIMULATION_SETTLE_SECONDS = 15
# The grouping control client permits 8 MiB payloads. Keep 1 MiB for the
# request envelope while allowing lossless multi-cohort receipts and Registry
# history to remain durable across worker restarts.
MAX_CHECKPOINT_BYTES = 7 * 1024 * 1024


class GroupingControlError(ValueError):
    code = "grouping_invalid"
    http_status = 400


class GroupingNotFound(GroupingControlError):
    code = "grouping_not_found"
    http_status = 404


class GroupingConflict(GroupingControlError):
    code = "grouping_conflict"
    http_status = 409


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _eligible_project(project_id: uuid.UUID, *, simulation: bool = False) -> bool:
    if is_oss() or not getattr(settings, "ERROR_FEED_GROUPING_ENABLED", False):
        return False
    if getattr(settings, "ERROR_FEED_GROUPING_BUDGET_ENFORCED", True):
        try:
            caps = (
                Decimal(str(getattr(settings, name, "0")))
                for name in (
                    "ERROR_FEED_GROUPING_PROJECT_BUDGET_USD",
                    "ERROR_FEED_GROUPING_WORK_BUDGET_USD",
                    "ERROR_FEED_GROUPING_TENANT_BUDGET_USD",
                )
            )
            if not all(cap.is_finite() and cap > 0 for cap in caps):
                return False
        except (InvalidOperation, ValueError):
            return False
    return (
        simulation
        or getattr(settings, "ERROR_FEED_GROUPING_ALL_PROJECTS", False)
        or str(project_id) in getattr(settings, "ERROR_FEED_GROUPING_PROJECT_IDS", ())
    )


def _live_report(report: TraceInvestigationReport) -> bool:
    return (
        not report.deleted
        and not report.project.deleted
        and report.is_current
        and report.execution_status == "completed"
        and report.source == "omega"
        and report.job_id is not None
        and report.job.current_report_id == report.id
        and _eligible_project(
            report.project_id,
            simulation=report.workload_type == "simulation_test_execution",
        )
    )


def require_attempt(
    *,
    attempt_id: uuid.UUID,
    lease_token: str,
    for_update: bool = False,
    allow_expired_for_settlement: bool = False,
) -> TraceGroupingAttempt:
    query = TraceGroupingAttempt.no_workspace_objects.select_related(
        "work__scope", "work__report"
    )
    if for_update:
        query = query.select_for_update(of=("self",))
    attempt = query.filter(pk=attempt_id).first()
    if attempt is None:
        raise GroupingNotFound("grouping attempt was not found")
    if not secrets.compare_digest(attempt.lease_token_digest, _token_hash(lease_token)):
        raise GroupingNotFound("grouping attempt was not found")
    if not allow_expired_for_settlement and not _live_report(attempt.work.report):
        raise GroupingConflict("grouping project or source report is disabled")
    if not allow_expired_for_settlement and (
        attempt.state != GroupingAttemptState.CLAIMED
        or attempt.work.state != GroupingWorkState.RUNNING
        or attempt.fence != attempt.work.scope.lease_fence
        or timezone.now() >= attempt.lease_expires_at
    ):
        raise GroupingConflict("grouping attempt lease is expired or fenced")
    return attempt


def lock_attempt_scope(
    *,
    attempt_id: uuid.UUID,
    lease_token: str,
    allow_expired_for_settlement: bool = False,
) -> tuple[TraceGroupingScope, TraceGroupingAttempt]:
    """Inside a transaction, lock the project fence before its attempt."""
    scope_id = (
        TraceGroupingAttempt.no_workspace_objects.filter(pk=attempt_id)
        .values_list("work__scope_id", flat=True)
        .first()
    )
    if scope_id is None:
        raise GroupingNotFound("grouping attempt was not found")
    scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(pk=scope_id)
    attempt = require_attempt(
        attempt_id=attempt_id,
        lease_token=lease_token,
        for_update=True,
        allow_expired_for_settlement=allow_expired_for_settlement,
    )
    if attempt.work.scope_id != scope.id:
        raise GroupingConflict("attempt scope changed")
    return scope, attempt


def claim_feature_jobs(*, worker_id: str, limit: int) -> dict:
    if not worker_id or not 1 <= limit <= 10:
        raise GroupingControlError("invalid feature claim request")
    if is_oss() or not getattr(settings, "ERROR_FEED_GROUPING_ENABLED", False):
        return {"claims": []}
    now = timezone.now()
    claims = []
    with transaction.atomic():
        jobs = list(
            TraceGroupingFeatureJob.no_workspace_objects.select_for_update(
                skip_locked=True, of=("self",)
            )
            .select_related("report__job", "report__project")
            .filter(
                Q(state=GroupingFeatureState.PENDING, not_before__lte=now)
                | Q(state=GroupingFeatureState.RUNNING, lease_expires_at__lt=now)
            )
            .order_by("not_before", "id")[:limit]
        )
        for job in jobs:
            if not _live_report(job.report):
                job.state = GroupingFeatureState.SUPERSEDED
                job.save(update_fields=["state", "updated_at"])
                continue
            if job.attempt_number >= MAX_ATTEMPTS:
                job.state = GroupingFeatureState.FAILED
                job.failure_code = "attempts_exhausted"
                job.save(update_fields=["state", "failure_code", "updated_at"])
                continue
            try:
                snapshot = export_grouping_snapshot(report=job.report)
            except GroupingSnapshotError:
                job.state = GroupingFeatureState.FAILED
                job.failure_code = "snapshot_invalid"
                job.save(update_fields=["state", "failure_code", "updated_at"])
                continue
            token = secrets.token_urlsafe(32)
            job.state = GroupingFeatureState.RUNNING
            job.worker_id = worker_id
            job.lease_token_digest = _token_hash(token)
            job.lease_expires_at = now + timedelta(seconds=FEATURE_LEASE_SECONDS)
            job.attempt_number += 1
            job.save(
                update_fields=[
                    "state",
                    "worker_id",
                    "lease_token_digest",
                    "lease_expires_at",
                    "attempt_number",
                    "updated_at",
                ]
            )
            claims.append(
                {
                    "feature_job_id": str(job.id),
                    "feature_attempt_id": str(job.id),
                    "attempt_number": job.attempt_number,
                    "lease_token": token,
                    "lease_expires_at": job.lease_expires_at,
                    "report_id": str(job.report_id),
                    "organization_id": str(job.report.organization_id),
                    "workspace_id": (
                        str(job.report.workspace_id)
                        if job.report.workspace_id
                        else None
                    ),
                    "project_id": str(job.report.project_id),
                    "policy_version": job.policy_version,
                    "snapshot": snapshot,
                }
            )
    return {"claims": claims}


def require_feature_job(
    *, feature_job_id: uuid.UUID, lease_token: str
) -> TraceGroupingFeatureJob:
    job = (
        TraceGroupingFeatureJob.no_workspace_objects.select_for_update(of=("self",))
        .select_related("report__job")
        .filter(pk=feature_job_id)
        .first()
    )
    if job is None or not secrets.compare_digest(
        job.lease_token_digest, _token_hash(lease_token)
    ):
        raise GroupingNotFound("feature attempt was not found")
    if (
        job.state != GroupingFeatureState.RUNNING
        or job.lease_expires_at is None
        or timezone.now() >= job.lease_expires_at
        or not _live_report(job.report)
    ):
        raise GroupingConflict("feature attempt is stale or expired")
    return job


def renew_feature_job(*, feature_job_id: uuid.UUID, lease_token: str) -> dict:
    with transaction.atomic():
        job = require_feature_job(
            feature_job_id=feature_job_id, lease_token=lease_token
        )
        job.lease_expires_at = timezone.now() + timedelta(seconds=FEATURE_LEASE_SECONDS)
        job.save(update_fields=["lease_expires_at", "updated_at"])
        return {"state": job.state, "lease_expires_at": job.lease_expires_at}


def _scope_for_report(report: TraceInvestigationReport) -> TraceGroupingScope:
    scope, _ = TraceGroupingScope.no_workspace_objects.get_or_create(
        project_id=report.project_id,
        defaults={
            "organization_id": report.organization_id,
            "workspace_id": report.workspace_id,
        },
    )
    if (
        scope.organization_id != report.organization_id
        or scope.workspace_id != report.workspace_id
        or scope.project.deleted
    ):
        raise GroupingConflict("grouping scope does not match the project")
    return scope


def mark_feature_ready(
    *, job: TraceGroupingFeatureJob, feature_digest: str, serving_release: str
) -> dict:
    """Call inside the feature-completion transaction, after verified CH writes."""
    scope = _scope_for_report(job.report)
    scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(pk=scope.pk)
    scope.pending_revision = F("pending_revision") + 1
    scope.save(update_fields=["pending_revision", "updated_at"])
    scope.refresh_from_db(fields=["pending_revision"])
    work, _ = TraceGroupingWork.no_workspace_objects.get_or_create(
        report=job.report,
        scope=scope,
        defaults={
            "feature_job": job,
            "input_revision": scope.pending_revision,
            "not_before": timezone.now()
            + timedelta(
                seconds=int(
                    getattr(settings, "ERROR_FEED_GROUPING_DEBOUNCE_SECONDS", 5)
                )
            ),
        },
    )
    TraceGroupingOutbox.no_workspace_objects.get_or_create(
        event_kind="error-feed.grouping-ready.v1",
        source_id=job.id,
        revision=scope.pending_revision,
        defaults={"scope": scope},
    )
    job.state = GroupingFeatureState.READY
    job.feature_digest = feature_digest
    job.serving_release = serving_release
    job.save(update_fields=["state", "feature_digest", "serving_release", "updated_at"])
    return {
        "state": job.state,
        "scope_revision": scope.pending_revision,
        "work_id": str(work.id),
    }


def _simulation_run_settling(report) -> bool:
    """A simulation run is a closed batch: group it once every call is read.

    Grouping a report the moment it lands shows discovery one call at a time,
    so a failure shared across calls never meets its peers and is deferred for
    good. Waiting for the run lets one cohort hold all of it.
    """
    return (
        TraceInvestigationJob.no_workspace_objects.filter(
            test_execution_id=report.test_execution_id,
            workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION,
            state__in=[
                TraceInvestigationJobState.WAITING,
                TraceInvestigationJobState.RUNNING,
            ],
        ).exists()
        or TraceGroupingFeatureJob.no_workspace_objects.filter(
            report__test_execution_id=report.test_execution_id,
            report__is_current=True,
            state__in=[GroupingFeatureState.PENDING, GroupingFeatureState.RUNNING],
        ).exists()
    )


def claim_grouping_work(*, worker_id: str, limit: int) -> dict:
    if not worker_id or not 1 <= limit <= 10:
        raise GroupingControlError("invalid grouping claim request")
    if is_oss() or not getattr(settings, "ERROR_FEED_GROUPING_ENABLED", False):
        return {"claims": []}
    now = timezone.now()
    claimed = []
    with transaction.atomic():
        works = list(
            TraceGroupingWork.no_workspace_objects.select_related(
                "scope", "report__job", "feature_job"
            )
            .filter(
                state__in=[GroupingWorkState.PENDING, GroupingWorkState.RUNNING],
                not_before__lte=now,
            )
            .order_by("not_before", "id")[: max(100, limit * 20)]
        )
        for work in works:
            if len(claimed) >= limit:
                break
            scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
                pk=work.scope_id
            )
            fresh_work = (
                TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
                .filter(pk=work.pk)
                .first()
            )
            if (
                fresh_work is None
                or fresh_work.state
                not in {GroupingWorkState.PENDING, GroupingWorkState.RUNNING}
                or fresh_work.not_before > now
            ):
                continue
            work = fresh_work
            if (
                not _live_report(work.report)
                or work.feature_job.state != GroupingFeatureState.READY
            ):
                work.state = GroupingWorkState.SUPERSEDED
                work.save(update_fields=["state", "updated_at"])
                continue
            if (
                work.report.workload_type
                == InvestigationWorkload.SIMULATION_TEST_EXECUTION
            ):
                if _simulation_run_settling(work.report):
                    work.not_before = now + timedelta(seconds=SIMULATION_SETTLE_SECONDS)
                    work.save(update_fields=["not_before", "updated_at"])
                    continue
                # The run has settled, so all of its works are due: this claim
                # takes them as one cohort, not whichever came due first.
                TraceGroupingWork.no_workspace_objects.filter(
                    scope=scope, state=GroupingWorkState.PENDING, not_before__gt=now
                ).update(not_before=now)
            if TraceGroupingAttempt.no_workspace_objects.filter(
                work__scope=scope,
                state=GroupingAttemptState.CLAIMED,
                lease_expires_at__gte=now,
            ).exists():
                continue
            if work.attempt_number >= MAX_ATTEMPTS:
                work.state = GroupingWorkState.FAILED
                work.save(update_fields=["state", "updated_at"])
                continue
            pending_snapshots = []
            peer_works = []
            pending_count = 0
            previous = work.attempts.order_by("-attempt_number").first()
            try:
                if previous and previous.claimed_work_ids:
                    current_peers = list(
                        TraceGroupingWork.no_workspace_objects.select_for_update(
                            of=("self",)
                        )
                        .select_related("report__job", "report__project", "feature_job")
                        .filter(
                            id__in=previous.claimed_work_ids,
                            scope=scope,
                            state__in=[
                                GroupingWorkState.PENDING,
                                GroupingWorkState.RUNNING,
                            ],
                            not_before__lte=now,
                        )
                    )
                    work_by_id = {str(item.id): item for item in current_peers}
                    peers = [
                        work_by_id[key]
                        for key in previous.claimed_work_ids
                        if key in work_by_id
                    ]
                    if not peers or peers[0].id != work.id:
                        continue
                else:
                    current_peers = list(
                        TraceGroupingWork.no_workspace_objects.select_for_update(
                            of=("self",)
                        )
                        .select_related("report__job", "report__project", "feature_job")
                        .filter(
                            scope=scope,
                            state__in=[
                                GroupingWorkState.PENDING,
                                GroupingWorkState.RUNNING,
                            ],
                            not_before__lte=now,
                        )
                        .order_by("not_before", "id")[:20]
                    )
                    peer_by_id = {item.id: item for item in current_peers}
                    current_work = peer_by_id.get(work.id)
                    if current_work is None:
                        continue
                    peers = [current_work] + [
                        item for item in current_peers if item.id != current_work.id
                    ]
                for peer in peers:
                    if len(peer_works) >= 20:
                        break
                    if (
                        not _live_report(peer.report)
                        or peer.feature_job.state != GroupingFeatureState.READY
                    ):
                        continue
                    candidate = export_grouping_snapshot(report=peer.report)
                    count = len(candidate["occurrences"])
                    if not count or pending_count + count > 100:
                        if peer.id == work.id:
                            raise GroupingSnapshotError(
                                "primary cohort exceeds 100 findings"
                            )
                        continue
                    pending_snapshots.append(candidate)
                    peer_works.append(peer)
                    pending_count += count
            except GroupingSnapshotError:
                work.state = GroupingWorkState.FAILED
                work.save(update_fields=["state", "updated_at"])
                continue
            snapshot = pending_snapshots[0]
            cohort_digest = canonical_snapshot_digest(
                {"pending_snapshots": pending_snapshots}
            )
            reusable_checkpoint = bool(
                previous
                and previous.snapshot_digest == cohort_digest
                and previous.registry_revision == scope.registry_revision
                and [str(item.id) for item in peer_works] == previous.claimed_work_ids
            )
            if previous and previous.state == GroupingAttemptState.CLAIMED:
                previous.state = GroupingAttemptState.EXPIRED
                previous.save(update_fields=["state", "updated_at"])
            token = secrets.token_urlsafe(32)
            scope.lease_fence = F("lease_fence") + 1
            scope.save(update_fields=["lease_fence", "updated_at"])
            scope.refresh_from_db(fields=["lease_fence"])
            work.attempt_number += 1
            for peer in peer_works:
                peer.state = GroupingWorkState.RUNNING
                if peer.id == work.id:
                    peer.attempt_number = work.attempt_number
                    peer.save(update_fields=["attempt_number", "state", "updated_at"])
                else:
                    peer.save(update_fields=["state", "updated_at"])
            attempt = TraceGroupingAttempt.no_workspace_objects.create(
                work=work,
                attempt_number=work.attempt_number,
                worker_id=worker_id,
                lease_token_digest=_token_hash(token),
                lease_expires_at=now + timedelta(seconds=GROUPING_LEASE_SECONDS),
                fence=scope.lease_fence,
                snapshot_digest=cohort_digest,
                registry_revision=scope.registry_revision,
                claimed_work_ids=[str(item.id) for item in peer_works],
                checkpoint=previous.checkpoint if reusable_checkpoint else {},
                checkpoint_revision=(
                    previous.checkpoint_revision if reusable_checkpoint else 0
                ),
            )
            claimed.append((attempt, token, snapshot, pending_snapshots))
    # Candidate retrieval and ClickHouse I/O occur after releasing PG locks.
    from tracer.services.grouping.context import build_claim_context

    claims = []
    for attempt, token, snapshot, pending_snapshots in claimed:
        context = build_claim_context(
            attempt=attempt, pending_snapshots=pending_snapshots
        )
        attempt.refresh_from_db(
            fields=["checkpoint", "checkpoint_revision", "candidate_digest"]
        )
        claims.append(
            {
                "attempt_id": str(attempt.id),
                "lease_token": token,
                "lease_expires_at": attempt.lease_expires_at,
                "report_id": str(attempt.work.report_id),
                "organization_id": str(attempt.work.scope.organization_id),
                "workspace_id": (
                    str(attempt.work.scope.workspace_id)
                    if attempt.work.scope.workspace_id
                    else None
                ),
                "project_id": str(attempt.work.scope.project_id),
                "scope_revision": attempt.work.input_revision,
                "registry_revision": attempt.registry_revision,
                "candidate_digest": attempt.candidate_digest,
                "policy_version": attempt.work.scope.policy_version,
                "snapshot": snapshot,
                "snapshot_digest": attempt.snapshot_digest,
                "pending_snapshots": pending_snapshots,
                "pending_ids": [
                    item["occurrence_id"]
                    for snap in pending_snapshots
                    for item in snap["occurrences"]
                ],
                "checkpoint": attempt.checkpoint,
                "checkpoint_revision": attempt.checkpoint_revision,
                "receipt_ids": [
                    str(item)
                    for item in TraceGroupingCall.no_workspace_objects.filter(
                        scope_id=attempt.work.scope_id,
                        attempt__snapshot_digest=attempt.snapshot_digest,
                        attempt__registry_revision=attempt.registry_revision,
                        attempt__candidate_digest=attempt.candidate_digest,
                        status__in=["settled", "unknown"],
                        result__isnull=False,
                    ).values_list("id", flat=True)[:100]
                ],
                **context,
            }
        )
    return {"claims": claims}


def update_grouping_attempt(
    *, attempt_id: uuid.UUID, lease_token: str, action: str
) -> dict:
    if action not in {"renew", "cancel"}:
        raise GroupingControlError("unsupported grouping attempt action")
    with transaction.atomic():
        _, attempt = lock_attempt_scope(attempt_id=attempt_id, lease_token=lease_token)
        if action == "renew":
            attempt.lease_expires_at = timezone.now() + timedelta(
                seconds=GROUPING_LEASE_SECONDS
            )
            attempt.save(update_fields=["lease_expires_at", "updated_at"])
        else:
            attempt.state = GroupingAttemptState.CANCELLED
            attempt.save(update_fields=["state", "updated_at"])
            TraceGroupingWork.no_workspace_objects.filter(
                id__in=attempt.claimed_work_ids,
                state=GroupingWorkState.RUNNING,
            ).update(
                state=GroupingWorkState.PENDING,
                not_before=timezone.now() + timedelta(seconds=5),
            )
        return {"state": attempt.state, "lease_expires_at": attempt.lease_expires_at}


def checkpoint_attempt(
    *, attempt_id: uuid.UUID, lease_token: str, expected_revision: int, checkpoint: dict
) -> dict:
    import json

    if (
        not isinstance(checkpoint, dict)
        or len(
            json.dumps(
                checkpoint, allow_nan=False, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        > MAX_CHECKPOINT_BYTES
    ):
        raise GroupingControlError("checkpoint is not a bounded object")
    with transaction.atomic():
        _, attempt = lock_attempt_scope(attempt_id=attempt_id, lease_token=lease_token)
        if attempt.checkpoint_revision != expected_revision:
            raise GroupingConflict("checkpoint revision changed")
        attempt.checkpoint = checkpoint
        attempt.checkpoint_revision += 1
        attempt.save(update_fields=["checkpoint", "checkpoint_revision", "updated_at"])
        return {"checkpoint_revision": attempt.checkpoint_revision}
