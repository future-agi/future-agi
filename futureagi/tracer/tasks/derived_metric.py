"""Metric activities on existing Temporal queues; rollout is checked at execution."""

from tfc.temporal import temporal_activity
from tracer.services.derived_metrics import admission
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.services.derived_metrics.service import execute_run


@temporal_activity(
    name="tracer.compute_derived_metric",
    queue="exact_aggregation",
    time_limit=600,
    schedule_to_start_timeout=300,
    workflow_run_timeout=960,
    workflow_execution_timeout=1200,
    max_retries=0,
)
def compute_derived_metric(run_id: str) -> None:
    if metrics_enabled():
        execute_run(run_id)


@temporal_activity(
    name="tracer.dispatch_derived_metrics",
    queue="tasks_s",
    time_limit=50,
    max_retries=0,
)
def dispatch_derived_metrics() -> int:
    return admission.dispatch(_enqueue_refresh)


def _enqueue_refresh(payload: dict) -> None:
    refresh_derived_metric_project.apply_async(
        args=[payload],
        task_id=f"metric-refresh-{payload['project_id']}-{payload['epoch']}",
        dispatch_timeout_seconds=10,
    )


@temporal_activity(
    name="tracer.refresh_derived_metric_project",
    queue="tasks_l",
    time_limit=600,
    schedule_to_start_timeout=300,
    workflow_run_timeout=960,
    workflow_execution_timeout=1200,
    max_retries=0,
)
def refresh_derived_metric_project(payload: dict) -> bool:
    # PG owns retry/backoff, fencing and continuation. Temporal retries must not
    # turn a queued lease token into two independently executing writers.
    return admission.execute_reservation(payload)


@temporal_activity(
    name="tracer.capture_derived_metric_snapshot",
    queue="exact_aggregation",
    time_limit=600,
    schedule_to_start_timeout=300,
    workflow_run_timeout=960,
    workflow_execution_timeout=1200,
    max_retries=0,
)
def capture_derived_metric_snapshot(run_id: str) -> None:
    from tracer.services.derived_metrics.snapshots import execute_snapshot

    if metrics_enabled():
        execute_snapshot(run_id)


@temporal_activity(
    name="tracer.cleanup_derived_metric_snapshots",
    queue="tasks_l",
    time_limit=600,
    max_retries=0,
)
def cleanup_derived_metric_snapshots() -> int:
    from django.db.models import Q
    from django.utils import timezone
    from tracer.models.derived_metric import DerivedMetricRun
    from tracer.services.derived_metrics.erasure import sweep
    from tracer.services.derived_metrics.snapshots import enqueue_capture

    # Privacy cleanup is mandatory even after the rollout flag is turned off.
    completed = sweep(limit=10)
    if metrics_enabled():
        pending = (
            DerivedMetricRun.objects.filter(
                Q(status="queued")
                | Q(status="running", lease_expires_at__lte=timezone.now()),
                kind="snapshot",
                deleted_at__isnull=True,
            )
            .order_by("created_at")
            .values_list("id", flat=True)[:10]
        )
        for run_id in pending:
            enqueue_capture(run_id)
    return completed
