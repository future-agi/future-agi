"""Fenced failure transitions; never accept provider bodies or exception text."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import TraceInvestigationGroupingStatus

RETRYABLE_CODES = frozenset(
    {
        "control_timeout",
        "control_network_error",
        "control_server_error",
        "grouping_conflict",
        "gateway_error",
        "worker_cancelled",
        "lease_expired",
    }
)
FAILURE_CODES = RETRYABLE_CODES | frozenset(
    {
        "grouping_paused",
        "snapshot_invalid",
        "context_budget",
        "control_rejected",
        "grouping_worker_error",
        "attempts_exhausted",
        "gateway_unknown_usage",
    }
)


def retry_exhausted(work):
    return work.attempt_number - work.retry_start_attempt >= work.retry_limit


def _fail_work(work, code, *, retryable, now):
    from tracer.services.grouping.control import _live_report

    if work.report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED:
        work.state = GroupingWorkState.COMPLETED
        work.failure_code = ""
        work.save(update_fields=["state", "failure_code", "updated_at"])
        return
    work.failure_code = code
    work.last_failure_at = now
    if not _live_report(work.report):
        work.state = GroupingWorkState.SUPERSEDED
    elif retryable and not retry_exhausted(work):
        work.state = GroupingWorkState.PENDING
        work.not_before = now + timedelta(
            seconds=min(
                300,
                5
                * 2
                ** min(6, max(0, work.attempt_number - work.retry_start_attempt - 1)),
            )
        )
    else:
        work.state = GroupingWorkState.FAILED
    work.save(
        update_fields=[
            "state",
            "failure_code",
            "last_failure_at",
            "not_before",
            "updated_at",
        ]
    )
    if work.state in {GroupingWorkState.PENDING, GroupingWorkState.FAILED}:
        status = (
            TraceInvestigationGroupingStatus.FAILED
            if work.state == GroupingWorkState.FAILED
            else TraceInvestigationGroupingStatus.PENDING
        )
        # Completed/superseded projections must never be overwritten by cleanup.
        type(work.report).no_workspace_objects.filter(
            pk=work.report_id,
            grouping_status__in=[
                TraceInvestigationGroupingStatus.PENDING,
                TraceInvestigationGroupingStatus.FAILED,
            ],
        ).update(grouping_status=status, updated_at=now)


def park_work(work, code="attempts_exhausted"):
    _fail_work(work, code, retryable=False, now=timezone.now())


def _fail_attempt(scope, attempt, code, *, expired=False):
    now = timezone.now()
    # Completion is committed atomically with publication. A late failure ack
    # must be an idempotent no-op, including after a publish response timeout.
    if attempt.state != GroupingAttemptState.CLAIMED:
        return {"state": attempt.state, "failure_code": attempt.failure_code}
    attempt.state = (
        GroupingAttemptState.EXPIRED if expired else GroupingAttemptState.CANCELLED
    )
    attempt.failure_code = code
    attempt.lease_expires_at = now
    attempt.save(
        update_fields=["state", "failure_code", "lease_expires_at", "updated_at"]
    )
    if attempt.fence != scope.lease_fence:
        return {"state": attempt.state, "failure_code": code}
    ids = attempt.claimed_work_ids or [str(attempt.work_id)]
    works = (
        TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
        .select_related("report__job")
        .filter(scope=scope, id__in=ids)
        .order_by("id")
    )
    for work in works:
        if work.state != GroupingWorkState.RUNNING:
            continue
        # Allowance is consumed at claim time for each peer independently.
        _fail_work(work, code, retryable=code in RETRYABLE_CODES, now=now)
    return {"state": attempt.state, "failure_code": code}


def fail_grouping_attempt(*, attempt_id, lease_token, failure_code):
    from tracer.services.grouping.control import (
        GroupingControlError,
        lock_attempt_scope,
    )

    if failure_code not in FAILURE_CODES:
        raise GroupingControlError("unsupported grouping failure code")
    with transaction.atomic():
        # Authenticate the token even for completed/expired attempts; fence
        # checking is performed before changing any associated work.
        scope, attempt = lock_attempt_scope(
            attempt_id=attempt_id,
            lease_token=lease_token,
            allow_expired_for_settlement=True,
        )
        return _fail_attempt(scope, attempt, failure_code)


def reconcile_dead_attempts(*, limit=100):
    """Bounded housekeeping also reaches attempts whose work is terminal."""
    now = timezone.now()
    ids = list(
        TraceGroupingAttempt.no_workspace_objects.filter(
            state=GroupingAttemptState.CLAIMED, lease_expires_at__lt=now
        )
        .order_by("lease_expires_at", "id")
        .values_list("id", "work__scope_id")[:limit]
    )
    for attempt_id, scope_id in ids:
        with transaction.atomic():
            scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
                pk=scope_id
            )
            attempt = TraceGroupingAttempt.no_workspace_objects.select_for_update().get(
                pk=attempt_id
            )
            if (
                attempt.state == GroupingAttemptState.CLAIMED
                and attempt.lease_expires_at < timezone.now()
            ):
                _fail_attempt(scope, attempt, "lease_expired", expired=True)
    # Repair the historical FAILED-work / pending-report mismatch as well.
    failed = list(
        TraceGroupingWork.no_workspace_objects.filter(
            state=GroupingWorkState.FAILED, failure_code=""
        )
        .order_by("id")
        .values_list("id", "scope_id")[:limit]
    )
    for work_id, scope_id in failed:
        with transaction.atomic():
            TraceGroupingScope.no_workspace_objects.select_for_update().get(pk=scope_id)
            work = (
                TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
                .select_related("report__job")
                .get(pk=work_id)
            )
            if work.state == GroupingWorkState.FAILED and not work.failure_code:
                park_work(work)
