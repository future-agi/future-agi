"""
Model Hub Temporal schedules.

These replace the Celery Beat schedules for model_hub tasks.
"""

from typing import List

from tfc.temporal.schedules.config import ScheduleConfig

# Model Hub schedules (migrated from Celery Beat)
# Note: execute_run_prompt removed - workflows are now triggered directly from API
MODEL_HUB_SCHEDULES: List[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="eval-evaluation",
        activity_name="execute_evaluation",
        interval_seconds=10,
        queue="default",
        description="Execute pending evaluations",
    ),
    ScheduleConfig(
        schedule_id="error-localizer",
        activity_name="error_localizer_task",
        interval_seconds=30,
        queue="default",
        description="Process error localization tasks",
    ),
    ScheduleConfig(
        schedule_id="process-pending-row-tasks",
        activity_name="process_pending_row_tasks",
        interval_seconds=10,
        queue="default",
        description="Process pending experiment row tasks",
    ),
    ScheduleConfig(
        schedule_id="optimization-runner",
        activity_name="optimization_runner",
        interval_seconds=10,
        queue="tasks_l",
        description="Run optimization tasks",
    ),
    ScheduleConfig(
        schedule_id="delete-unused-compare-folder",
        activity_name="delete_unused_compare_folder",
        interval_seconds=36000,  # 10 hours
        queue="default",
        description="Clean up unused compare folders",
    ),
    ScheduleConfig(
        schedule_id="recover-stuck-run-prompts",
        activity_name="recover_stuck_run_prompts",
        interval_seconds=300,  # 5 minutes
        queue="default",
        description="Recover run prompts stuck in RUNNING status",
    ),
    ScheduleConfig(
        schedule_id="annotation-automation-rules",
        activity_name="evaluate_due_automation_rules",
        interval_seconds=3600,  # Due checker for hourly/daily/weekly/monthly rules
        queue="default",
        description="Evaluate due annotation automation rules",
    ),
]
