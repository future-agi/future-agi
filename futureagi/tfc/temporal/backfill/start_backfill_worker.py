"""Dedicated, single-replica Temporal worker for the Vapi backfill queue."""

import asyncio
import os

from temporalio.worker import UnsandboxedWorkflowRunner

from tfc.temporal.common.worker import run_worker


def main() -> None:
    concurrency = int(os.getenv("BACKFILL_MAX_CONCURRENT_ACTIVITIES", "1"))
    if concurrency != 1:
        raise ValueError(
            "BACKFILL_MAX_CONCURRENT_ACTIVITIES must be 1 so the process-wide Vapi rate limiter is global"
        )
    asyncio.run(
        run_worker(
            "backfill",
            max_concurrent_activities=1,
            max_concurrent_workflow_tasks=20,
            # This temporary backfill keeps workflow and activity code in one module.
            # The workflow itself uses only deterministic Temporal APIs, but sandbox
            # validation would import Django/structlog through the activity side.
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
    )


if __name__ == "__main__":
    main()
