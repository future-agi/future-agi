"""
Tracer Temporal schedules.

These replace the Celery Beat schedules for tracer tasks.
"""

from typing import List

from tfc.temporal.schedules.config import ScheduleConfig

# Tracer schedules (migrated from Celery Beat)
TRACER_SCHEDULES: List[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="process-inline-evals",
        activity_name="process_in_line_evals",
        interval_seconds=10,
        queue="tasks_s",
        description="Process pending inline evaluations",
    ),
    ScheduleConfig(
        schedule_id="eval-task-cron",
        activity_name="eval_task_cron",
        interval_seconds=60,
        queue="default",
        description="Process evaluation tasks",
    ),
    ScheduleConfig(
        schedule_id="check-alerts",
        activity_name="check_alerts",
        interval_seconds=60,
        queue="tasks_l",
        description="Check and process alert monitors",
    ),
    ScheduleConfig(
        schedule_id="run-evals-on-spans",
        activity_name="run_evals_on_spans",
        interval_seconds=10,
        queue="tasks_s",
        description="Run evaluations on observation spans",
    ),
    ScheduleConfig(
        schedule_id="process-external-evals",
        activity_name="process_external_evals",
        interval_seconds=30,
        queue="default",
        description="Process external evaluation configs",
    ),
    ScheduleConfig(
        schedule_id="fetch-observability-logs",
        activity_name="fetch_observability_logs",
        interval_seconds=600,
        queue="tasks_s",
        description="Fetch logs from observability providers (VAPI, Retell, etc.)",
    ),
    # Deep analysis beat DISABLED — replaced by event-driven trace scanner (TH-3817)
    # Scanner triggers from OTLP ingestion via scan_traces_task.
    # Deep analysis kept for on-demand use (Layer 3) but no longer auto-runs.
    # ScheduleConfig(
    #     schedule_id="check-trace-errors",
    #     activity_name="check_and_process_trace_errors",
    #     interval_seconds=240,
    #     queue="agent_compass",
    #     description="Check and process trace error analysis",
    # ),
]
