"""Interactive SQL runs separately from long exact-refresh activities."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.workflow import VersioningIntent

from tfc.temporal.drop_in.workflow import TaskRunnerInput, TaskRunnerOutput

SQL_ACTIVITY_QUEUE = "sql_workbench"


@workflow.defn
class SQLExecutionWorkflow:
    @workflow.run
    async def run(self, input: TaskRunnerInput) -> TaskRunnerOutput:
        try:
            result = await workflow.execute_activity(
                input.activity_name,
                {"args": input.args, "kwargs": input.kwargs},
                task_queue=SQL_ACTIVITY_QUEUE,
                versioning_intent=VersioningIntent.DEFAULT,
                start_to_close_timeout=timedelta(seconds=45),
                schedule_to_start_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            # This still runs when execute never obtained an activity slot.
            # No raw Temporal/driver exception text crosses the SQL boundary.
            await workflow.execute_activity(
                "tracer.sql_workbench.fail_execution",
                {"args": input.args, "kwargs": {}},
                task_queue="tasks_s",
                versioning_intent=VersioningIntent.DEFAULT,
                start_to_close_timeout=timedelta(seconds=5),
                schedule_to_start_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise
        return TaskRunnerOutput(input.activity_name, result, "completed")


@workflow.defn
class SQLRetentionWorkflow:
    @workflow.run
    async def run(self) -> None:
        # Retention must not queue behind an hour-long exact refresh either.
        await workflow.execute_activity(
            "tracer.sql_workbench.purge_expired_results",
            {"args": [], "kwargs": {}},
            task_queue="tasks_s",
            versioning_intent=VersioningIntent.DEFAULT,
            start_to_close_timeout=timedelta(seconds=30),
            schedule_to_start_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
