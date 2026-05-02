"""Workflows for billing operations.

``MonthlyClosingWorkflow`` runs once per month, started by the
``monthly-closing`` Temporal schedule (cron ``0 0 1 * *``). It locks the
closing period to ``workflow.now()`` — the deterministic, replay-safe
clock fixed at workflow start — and passes that period explicitly to
``monthly_closing_activity``. Wall-clock derivation inside the activity
would otherwise depend on the worker pod's local clock at execution
time, which is fragile across worker clock skew and across activity
retries that span midnight UTC of the 1st.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from tfc.temporal.billing.types import (
        MonthlyClosingInput,
        MonthlyClosingOutput,
    )


CLOSING_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)


def _previous_period(fire) -> str:
    if fire.month == 1:
        return f"{fire.year - 1}-12"
    return f"{fire.year}-{fire.month - 1:02d}"


@workflow.defn
class MonthlyClosingWorkflow:
    @workflow.run
    async def run(self) -> MonthlyClosingOutput:
        period = _previous_period(workflow.now())
        return await workflow.execute_activity(
            "monthly_closing_activity",
            MonthlyClosingInput(period=period),
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=CLOSING_RETRY_POLICY,
        )


__all__ = ["MonthlyClosingWorkflow"]
