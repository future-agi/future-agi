"""``webhooks-dispatcher-<n>``: one durable loop per dispatcher (DESIGN.md section 13.1).

The workflow owns no I/O. It runs one heartbeating activity, ``dispatch_loop``, which loops for 10 minutes and
returns; the workflow then continues as new, so its history stays small. Activity retry is unlimited with a 1 s to
30 s backoff, so a crashed worker's loop resumes on another one.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

LOOP_SECONDS = 600
HEARTBEAT_TIMEOUT = timedelta(seconds=30)
LOOP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_attempts=0,
)


def dispatcher_workflow_id(n: int) -> str:
    return f"webhooks-dispatcher-{n}"


@workflow.defn(name="WebhooksDispatcherWorkflow")
class WebhooksDispatcherWorkflow:
    @workflow.run
    async def run(self, loop: int) -> None:
        await workflow.execute_activity(
            "webhooks_dispatch_loop",
            args=[loop, LOOP_SECONDS],
            start_to_close_timeout=timedelta(seconds=LOOP_SECONDS + 120),
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=LOOP_RETRY,
        )
        workflow.continue_as_new(loop)


HEALTH_TIMEOUT = timedelta(seconds=55)
RETENTION_TIMEOUT = timedelta(hours=2)
OPS_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=3)


@workflow.defn(name="WebhooksHealthWorkflow")
class WebhooksHealthWorkflow:
    """``webhooks-health`` (every 60 s): first every dispatcher loop that is not running is started (conflict policy
    "use existing", so a running loop is left alone), then the health pass. The loop check comes first so that a
    health pass that fails or runs long never keeps a stopped loop down (section 13.1)."""

    @workflow.run
    async def run(self) -> dict:
        ensured = await workflow.execute_activity(
            "webhooks_ensure_dispatchers",
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=OPS_RETRY,
        )
        out = await workflow.execute_activity(
            "webhooks_health_pass",
            start_to_close_timeout=HEALTH_TIMEOUT,
            retry_policy=OPS_RETRY,
        )
        out["dispatchers_ensured"] = ensured
        return out


@workflow.defn(name="WebhooksRetentionWorkflow")
class WebhooksRetentionWorkflow:
    """``webhooks-retention`` (daily, ``30 3 * * *``): the chunked purges of section 13.4."""

    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(
            "webhooks_retention_pass",
            start_to_close_timeout=RETENTION_TIMEOUT,
            heartbeat_timeout=None,
            retry_policy=OPS_RETRY,
        )


__all__ = [
    "WebhooksDispatcherWorkflow",
    "WebhooksHealthWorkflow",
    "WebhooksRetentionWorkflow",
    "dispatcher_workflow_id",
]
