"""Metric activities on existing Temporal queues; rollout is checked at execution."""

import structlog

from tfc.temporal import temporal_activity
from tracer.services.derived_metrics import admission
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.services.derived_metrics.service import execute_run

logger = structlog.get_logger(__name__)


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
    # Keep filling freed slots within the tick, not only at its start.
    return admission.dispatch_until(_enqueue_refresh)


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
    from tracer.services.derived_metrics.erasure import sweep
    from tracer.services.derived_metrics.snapshots import (
        enqueue_capture,
        recoverable_captures,
    )

    # Privacy cleanup is mandatory even after the rollout flag is turned off.
    completed = sweep(limit=10)
    if metrics_enabled():
        # Never a completed namespace a (possibly failed) redaction reopened.
        for run_id in recoverable_captures(limit=10):
            enqueue_capture(run_id)
        # Reclaim superseded copies that history GC deleted lightweight: a
        # few partitions per run, outside every refresh pass. A failure keeps
        # the rows masked (unreadable) for the next run.
        from tracer.services.derived_metrics.history_gc import run_scheduled

        try:
            run_scheduled()
        except Exception:
            logger.exception("metric_history_gc_mask_failed")
    return completed
