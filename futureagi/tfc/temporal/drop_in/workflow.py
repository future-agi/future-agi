"""
Generic task runner workflow for drop-in Celery replacement.

This workflow executes any registered activity by name, mimicking Celery's
behavior of running tasks asynchronously.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import VersioningIntent


@dataclass
class TaskRunnerInput:
    """Input for TaskRunnerWorkflow."""

    activity_name: str
    args: list[Any]
    kwargs: dict[str, Any]
    queue: str = "default"
    time_limit: Optional[int] = None  # Override default timeout
    schedule_to_start_timeout: Optional[int] = None  # Seconds; defaults to 6h


@dataclass
class TaskRunnerOutput:
    """Output from TaskRunnerWorkflow."""

    activity_name: str
    result: Any
    status: str
    error: Optional[str] = None


# Default retry policy (matches common Celery patterns)
DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class TaskRunnerWorkflow:
    """
    Generic workflow that runs any activity by name.

    This is the backbone of the drop-in replacement - it allows starting
    any activity via start_activity() without needing a dedicated workflow.
    """

    @workflow.run
    async def run(self, input: TaskRunnerInput) -> TaskRunnerOutput:
        # NOTE: Do NOT use workflow.logger here - it uses Python's stdlib logging
        # which acquires locks and causes deadlocks in Temporal workflows.
        # Logging should be done in activities instead.

        try:
            # Get timeout from activity metadata or use default
            time_limit = input.time_limit or 3600 * 12  # 12 hours default

            # Don't pin to workflow's build_id; bound stuck-time as safety net.
            result = await workflow.execute_activity(
                input.activity_name,
                {
                    "args": input.args,
                    "kwargs": input.kwargs,
                },
                start_to_close_timeout=timedelta(seconds=time_limit),
                schedule_to_start_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY_POLICY,
                versioning_intent=VersioningIntent.DEFAULT,
            )

            return TaskRunnerOutput(
                activity_name=input.activity_name,
                result=result,
                status="completed",
            )

        except Exception as e:
            # Re-raise to mark workflow as Failed in Temporal UI
            # Error details will be visible in Temporal's UI
            raise


__all__ = [
    "TaskRunnerInput",
    "TaskRunnerOutput",
    "TaskRunnerWorkflow",
]
