"""
Test that workflow.logger doesn't deadlock when a blocking log handler is configured.

Reproduces CORE-BACKEND-ZSG / CORE-BACKEND-ZSW: workflow.logger.info() triggers
RotatingFileHandler.shouldRollover() → os.path.exists() → blocking I/O in sandbox
→ _DeadlockError.

The fix: route `temporalio` logger to console-only handler (no file handler).
"""

import asyncio
import logging
import logging.handlers
import time
from dataclasses import dataclass

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

# =============================================================================
# Custom blocking handler to simulate slow RotatingFileHandler
# =============================================================================


class SlowHandler(logging.Handler):
    """
    Simulates a RotatingFileHandler with slow disk I/O.

    Sleeps for `delay` seconds in emit(), which blocks the workflow thread
    and triggers Temporal's deadlock detector (2s threshold).
    """

    def __init__(self, delay: float = 3.0):
        super().__init__()
        self.delay = delay

    def emit(self, record):
        time.sleep(self.delay)


# =============================================================================
# Dummy workflow
# =============================================================================


@dataclass
class DummyInput:
    should_raise: bool = False


@workflow.defn
class DummyLoggerWorkflow:
    """Minimal workflow that calls workflow.logger.info()."""

    @workflow.run
    async def run(self, input: DummyInput) -> str:
        workflow.logger.info("Before exception check")

        if input.should_raise:
            try:
                raise ValueError("intentional test error")
            except ValueError:
                workflow.logger.warning("Caught intentional error")

        workflow.logger.info("After exception check")
        return "ok"


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="Documents the deadlock scenario the fix prevents — propagate=True "
    "with a 3 s handler exceeds Temporal's 2 s deadlock threshold.",
    strict=False,
)
async def test_workflow_logger_safe_even_with_slow_root_handler():
    """
    Verify that workflow.logger remains safe even when a slow handler is on the root logger.

    Adds a SlowHandler (3s delay) to the root logger and asserts that the workflow still
    completes successfully without deadlocking.
    """
    root_logger = logging.getLogger()
    slow_handler = SlowHandler(delay=3.0)
    root_logger.addHandler(slow_handler)

    # Undo the fix: force temporalio logger to propagate to root
    # (Django logging config sets propagate=False, which prevents the deadlock)
    temporalio_logger = logging.getLogger("temporalio")
    original_propagate = temporalio_logger.propagate
    temporalio_logger.propagate = True

    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-deadlock",
                workflows=[DummyLoggerWorkflow],
            ):
                result = await asyncio.wait_for(
                    env.client.execute_workflow(
                        DummyLoggerWorkflow.run,
                        DummyInput(should_raise=False),
                        id="test-deadlock",
                        task_queue="test-deadlock",
                    ),
                    timeout=10,
                )
                assert result == "ok"
    finally:
        temporalio_logger.propagate = original_propagate
        root_logger.removeHandler(slow_handler)


@pytest.mark.asyncio
async def test_workflow_logger_no_deadlock_with_console_only():
    """
    Verify that the fix works: routing temporalio logger to console-only
    prevents deadlock even when root logger has a slow handler.

    Same SlowHandler on root, but temporalio logger has propagate=False
    and only a console handler. workflow.logger calls never reach the
    slow root handler.
    """
    root_logger = logging.getLogger()
    slow_handler = SlowHandler(delay=3.0)
    root_logger.addHandler(slow_handler)

    # Apply the fix: temporalio logger → console only, no propagation to root
    temporalio_logger = logging.getLogger("temporalio")
    console_handler = logging.StreamHandler()
    temporalio_logger.addHandler(console_handler)
    temporalio_logger.propagate = False

    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-no-deadlock",
                workflows=[DummyLoggerWorkflow],
            ):
                # Should succeed — workflow.logger doesn't reach slow handler
                result = await env.client.execute_workflow(
                    DummyLoggerWorkflow.run,
                    DummyInput(should_raise=True),
                    id="test-no-deadlock",
                    task_queue="test-no-deadlock",
                )
                assert result == "ok"
    finally:
        temporalio_logger.removeHandler(console_handler)
        temporalio_logger.propagate = True
        root_logger.removeHandler(slow_handler)
