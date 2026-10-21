"""Bounded metric jobs use the existing single-slot exact-aggregation queue."""

from tfc.temporal import temporal_activity
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
    execute_run(run_id)
