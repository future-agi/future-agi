from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from simulate.temporal.constants import AGENT_FIX_WATCH_WINDOW_SECONDS, QUEUE_RUNNER
from simulate.temporal.types.agent_fix import (
    AgentFixCloseInput,
    AgentFixInput,
    AgentFixLaunch,
    AgentFixOutput,
    AgentFixWatch,
    AgentFixWatchInput,
    Observation,
)

_LAUNCH_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    # Configuration a retry cannot fix.
    non_retryable_error_types=[
        "HostedHarnessError",
        "SandboxProviderConfigurationError",
        "SandboxProviderUnavailableError",
    ],
)
_WATCH_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
)
_WATCH_PAUSE = timedelta(seconds=15)


@workflow.defn
class AgentFixWorkflow:
    """Owns the sandbox of one "Fix my agent" run: starts the engine, watches it, starts it
    again from its checkpoint when it dies, and always deletes the sandbox at the end.
    simulate.services.agent_fix_sandbox decides what each step saw."""

    def __init__(self) -> None:
        self.cancel_requested = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, input: AgentFixInput) -> AgentFixOutput:
        try:
            observation = await self._drive(input)
        finally:
            await self._release(input)
        if observation == Observation.STOPPING:
            await self._close(input, "stopped")
        return AgentFixOutput(fix_run_id=input.fix_run_id, observation=observation)

    async def _drive(self, input: AgentFixInput) -> Observation:
        """Keep an engine running until the run ends or the user stops it."""
        while not self.cancel_requested:
            try:
                launch = await workflow.execute_activity(
                    "launch_agent_fix_sandbox",
                    input,
                    task_queue=QUEUE_RUNNER,
                    # Creating the sandbox alone can take five minutes.
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=_LAUNCH_RETRY,
                    result_type=AgentFixLaunch,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, CancelledError):
                    raise
                workflow.logger.error(
                    "Agent fix %s could not start its sandbox: %s",
                    input.fix_run_id,
                    exc,
                )
                await self._close(input, "failed", "fix_sandbox_failed", _cause(exc))
                return Observation.ENDED
            if launch.observation != Observation.RUNNING:
                return launch.observation
            observation = await self._watch(
                AgentFixWatchInput(
                    fix_run_id=input.fix_run_id, command_id=launch.command_id
                )
            )
            if observation != Observation.ENGINE_GONE:
                return observation
            # The engine died while the run still runs. The next launch starts it again from
            # its checkpoint, or ends the run once its sandboxes are used up.
        return Observation.STOPPING

    async def _watch(self, input: AgentFixWatchInput) -> Observation:
        """Watch the engine until something changes."""
        while not self.cancel_requested:
            try:
                watch = await workflow.execute_activity(
                    "watch_agent_fix_engine",
                    input,
                    task_queue=QUEUE_RUNNER,
                    start_to_close_timeout=timedelta(
                        seconds=AGENT_FIX_WATCH_WINDOW_SECONDS + 5 * 60
                    ),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=_WATCH_RETRY,
                    result_type=AgentFixWatch,
                )
            except ActivityError as exc:
                if isinstance(exc.cause, CancelledError):
                    raise
                # Only an outage reaches here (the sandbox API, the network, the database,
                # or a lost worker): the service ends the run itself on any other error.
                # An outage must not end a live engine. Each look reads the run's deadline
                # and the engine's last report before the sandbox, so a dead engine or a
                # late run still ends once the database answers.
                workflow.logger.warning(
                    "Agent fix %s watch unavailable: %s", input.fix_run_id, exc
                )
                await workflow.sleep(_WATCH_PAUSE)
                continue
            if watch.observation != Observation.RUNNING:
                return watch.observation
        return Observation.STOPPING

    async def _release(self, input: AgentFixInput) -> None:
        try:
            await workflow.execute_activity(
                "release_agent_fix_sandbox",
                input,
                task_queue=QUEUE_RUNNER,
                # A provider delete waits up to two minutes.
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(
                    maximum_attempts=5,
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                ),
            )
        except ActivityError as exc:
            # Fail open: the sandbox's lease, renewed only while it is watched, deletes it.
            workflow.logger.error(
                "Agent fix %s could not delete its sandbox: %s", input.fix_run_id, exc
            )

    async def _close(
        self, input: AgentFixInput, state: str, code: str = "", message: str = ""
    ) -> None:
        await workflow.execute_activity(
            "close_agent_fix_run",
            AgentFixCloseInput(
                fix_run_id=input.fix_run_id, state=state, code=code, message=message
            ),
            task_queue=QUEUE_RUNNER,
            start_to_close_timeout=timedelta(minutes=1),
            # Closing is idempotent; keep trying through a database outage.
            schedule_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(maximum_interval=timedelta(minutes=1)),
        )


def _cause(exc: ActivityError) -> str:
    cause = exc.cause
    return str(getattr(cause, "message", "") or cause or exc)[:500]
