from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from simulate.temporal.constants import QUEUE_RUNNER, READING_ACTIVITY_TIMEOUT_SECONDS
from simulate.temporal.types.agent_version import (
    AgentVersionFailure,
    AgentVersionInput,
    AgentVersionOutput,
    AgentVersionStep,
)

_QUICK_RETRY = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=1),
)
# A clone or a sandbox provider's outage can outlast a minute; a reading the
# service refused for good returns a failed step instead of raising.
_STEP_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=30),
    maximum_interval=timedelta(minutes=5),
    non_retryable_error_types=[
        "SandboxProviderConfigurationError",
        "SandboxProviderUnavailableError",
    ],
)
_READ_TIMEOUT = timedelta(seconds=READING_ACTIVITY_TIMEOUT_SECONDS)


@workflow.defn
class AgentVersionWorkflow:
    """One new code version of an Agents-page agent: set aside the older ones still
    waiting, store its code, read it, then check and activate it. Each step can
    run again; a retry of the version starts the workflow over and the steps
    already done are skipped."""

    def __init__(self) -> None:
        self.cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, input: AgentVersionInput) -> AgentVersionOutput:
        await self._activity(
            "supersede_agent_versions",
            input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK_RETRY,
        )
        for name, timeout in (
            ("snapshot_agent_version", timedelta(minutes=15)),
            ("read_agent_version", _READ_TIMEOUT),
        ):
            if self.cancel_requested:
                return AgentVersionOutput(
                    version_id=input.version_id, status="cancelled"
                )
            try:
                step: AgentVersionStep = await self._activity(
                    name,
                    input,
                    start_to_close_timeout=timeout,
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=_STEP_RETRY,
                    result_type=AgentVersionStep,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, CancelledError):
                    raise
                workflow.logger.warning(
                    "Agent version %s could not finish %s: %s",
                    input.version_id,
                    name,
                    exc,
                )
                await self._fail(
                    input,
                    "agent_reading_unavailable",
                    "Reading the agent couldn't finish. Try again.",
                    retryable=True,
                )
                return AgentVersionOutput(version_id=input.version_id, status="failed")
            if step.status == "failed":
                await self._fail(
                    input, step.code, step.message, retryable=step.retryable
                )
                return AgentVersionOutput(version_id=input.version_id, status="failed")
            if step.status == "skipped" and step.code == "not_pending":
                return AgentVersionOutput(
                    version_id=input.version_id, status="not_pending"
                )
        if self.cancel_requested:
            return AgentVersionOutput(version_id=input.version_id, status="cancelled")
        status = await self._activity(
            "check_agent_version",
            input,
            start_to_close_timeout=timedelta(minutes=15),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=_STEP_RETRY,
            result_type=str,
        )
        return AgentVersionOutput(version_id=input.version_id, status=str(status))

    async def _activity(self, name: str, input, **options):
        return await workflow.execute_activity(
            name, input, task_queue=QUEUE_RUNNER, **options
        )

    async def _fail(
        self, input: AgentVersionInput, code: str, message: str, *, retryable: bool
    ) -> None:
        await workflow.execute_activity(
            "fail_agent_version",
            AgentVersionFailure(
                version_id=input.version_id,
                code=code,
                message=message,
                retryable=retryable,
            ),
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=1),
            schedule_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(maximum_interval=timedelta(minutes=1)),
        )
