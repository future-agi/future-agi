"""Long-running consumer for Cloud Marketplace lifecycle events.

Mirrors UsageConsumerWorkflow: the workflow is a durable loop that owns no I/O,
and an activity does one batch of work and returns. Temporal workflows are
deterministic and sandboxed, so they cannot make network calls, and a blocking
subscribe() would never return anyway.

IMPORTANT: Do NOT use workflow.logger — it causes deadlocks via stdlib logging locks.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from tfc.temporal.marketplace.types import ConsumerState, DrainResult

CONSUMER_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)

# Without continue-as-new the workflow history grows unbounded and the workflow
# eventually dies.
CONTINUE_AS_NEW_THRESHOLD = 500

WAIT_AFTER_EVENTS_SECONDS = 10
WAIT_WHEN_IDLE_SECONDS = 30


@workflow.defn
class GCPMarketplaceConsumerWorkflow:
    def __init__(self) -> None:
        self._state = ConsumerState()
        self._running = True
        self._iteration = 0

    @workflow.run
    async def run(self, state: ConsumerState | None = None) -> None:
        if state is not None:
            self._state = state

        while self._running:
            result: DrainResult = await workflow.execute_activity(
                "drain_gcp_marketplace_events_activity",
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=CONSUMER_RETRY_POLICY,
            )

            had_events = (
                result.had_events
                if hasattr(result, "had_events")
                else result.get("had_events", False)
            )
            events_processed = (
                result.events_processed
                if hasattr(result, "events_processed")
                else result.get("events_processed", 0)
            )

            if had_events:
                self._state.total_events_processed += events_processed

            self._iteration += 1
            if self._iteration >= CONTINUE_AS_NEW_THRESHOLD:
                workflow.continue_as_new(self._state)
                return

            wait_seconds = (
                WAIT_AFTER_EVENTS_SECONDS if had_events else WAIT_WHEN_IDLE_SECONDS
            )
            try:
                await workflow.wait_condition(
                    lambda: not self._running,
                    timeout=timedelta(seconds=wait_seconds),
                )
            except TimeoutError:
                pass

    @workflow.signal
    def stop(self) -> None:
        self._running = False

    @workflow.query
    def get_status(self) -> dict:
        return {
            "running": self._running,
            "iteration": self._iteration,
            "total_events_processed": self._state.total_events_processed,
        }
