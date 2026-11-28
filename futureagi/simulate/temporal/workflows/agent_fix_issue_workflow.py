from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from simulate.temporal.constants import AGENT_FIX_ISSUE_TICK_SECONDS, QUEUE_RUNNER
from simulate.temporal.types.agent_fix import AgentFixIssueInput, AgentFixIssueLook

_LOOK_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
)
_OUTAGE_PAUSE = timedelta(seconds=30)


@workflow.defn
class AgentFixIssueWorkflow:
    """Waits for a Run Epoch from an issue to finish its baseline run, then lets
    simulate.services.agent_fix_issue launch the epoch from it. Each look is one
    activity; the job ends itself when it takes too long to start its epoch, whatever
    holds it up."""

    @workflow.run
    async def run(self, input: AgentFixIssueInput) -> None:
        while True:
            try:
                look = await workflow.execute_activity(
                    "advance_agent_fix_issue",
                    input,
                    task_queue=QUEUE_RUNNER,
                    # A launch reads the run's spans and starts the epoch's workflow.
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_LOOK_RETRY,
                    result_type=AgentFixIssueLook,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, CancelledError):
                    raise
                # An outage: the job's own patience still ends it once the database answers.
                workflow.logger.warning(
                    "Agent fix issue job %s unavailable: %s", input.job_id, exc
                )
                await workflow.sleep(_OUTAGE_PAUSE)
                continue
            if look.done:
                return
            await workflow.sleep(timedelta(seconds=AGENT_FIX_ISSUE_TICK_SECONDS))
