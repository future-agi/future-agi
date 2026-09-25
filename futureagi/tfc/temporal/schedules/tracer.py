"""
Tracer Temporal schedules.

These replace the Celery Beat schedules for tracer tasks.
"""

from tfc.temporal.schedules.config import ScheduleConfig

# Tracer schedules (migrated from Celery Beat)
TRACER_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="process-inline-evals",
        activity_name="process_in_line_evals",
        interval_seconds=10,
        queue="tasks_s",
        description="Process pending inline evaluations",
    ),
    # Retired at the eval-task workflow cutover: eval tasks now run as one
    # Temporal workflow per task, started from the views. The cron round-robin
    # is left here (disabled) for reference; do not re-enable alongside the
    # workflows or tasks will be processed twice.
    # ScheduleConfig(
    #     schedule_id="eval-task-cron",
    #     activity_name="eval_task_cron",
    #     interval_seconds=60,
    #     queue="default",
    #     description="Process evaluation tasks",
    # ),
    # Recovery, not processing: the per-task workflow is still the only thing
    # that drains a task, and nothing restarted one that stopped. This reclaims
    # entries stuck ``running`` (the workflow-start reaper cannot reach a task
    # whose workflow is alive) and restarts the workflow of a task nothing is
    # draining. A task draining normally costs one describe and is left alone.
    ScheduleConfig(
        schedule_id="sweep-stranded-eval-tasks",
        activity_name="sweep_stranded_eval_tasks",
        interval_seconds=300,
        queue="tasks_s",
        description="Recover eval tasks whose drain stopped",
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
]
