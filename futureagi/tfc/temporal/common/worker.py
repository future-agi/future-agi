"""
Temporal worker management.

Provides functions to run workers for specific queues.
Supports dynamic concurrency based on CPU/memory usage via WorkerTuner.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Optional

from temporalio.worker import ResourceBasedSlotConfig, Worker, WorkerTuner

from tfc.temporal import TEMPORAL_HOST, TEMPORAL_NAMESPACE, get_queue_name
from tfc.temporal.common.client import get_client
from tfc.temporal.common.registry import (
    get_activities_for_queue,
    get_workflows_for_queue,
)
from tfc.temporal.common.sentry_interceptor import (
    SentryInterceptor,
    configure_sentry_for_temporal,
)


async def run_worker(
    task_queue: str,
    *,
    max_concurrent_activities: int = 100,
    max_concurrent_workflow_tasks: int = 100,
    graceful_shutdown_timeout: Optional[timedelta] = None,
    target_memory_usage: Optional[float] = None,
    target_cpu_usage: Optional[float] = None,
    skip_otel_init: bool = False,
    workflow_runner: Any | None = None,
) -> None:
    """
    Run a Temporal worker for the specified task queue.

    Args:
        task_queue: Temporal task queue name (e.g., "tasks_l")
        max_concurrent_activities: Max concurrent activity executions
        max_concurrent_workflow_tasks: Max concurrent workflow task executions
        graceful_shutdown_timeout: Timeout for graceful shutdown
        target_memory_usage: Target memory usage (0.0-1.0) for resource-based tuning.
            When set, worker dynamically adjusts concurrency based on memory.
            Example: 0.8 means target 80% memory usage. Default: None (disabled).
        target_cpu_usage: Target CPU usage (0.0-1.0) for resource-based tuning.
            Only used when target_memory_usage is set. Default: 1.0 (no CPU limit).
        skip_otel_init: Skip OTel initialization (caller already did it).
    """
    # Initialize OpenTelemetry FIRST (before any other imports)
    # This ensures all activities, DB queries, Redis, and HTTP calls are traced
    if not skip_otel_init:
        from tfc.telemetry.temporal import init_otel_for_temporal

        init_otel_for_temporal(task_queue)

    from tfc.temporal import init_sentry

    init_sentry(queue_name=task_queue)
    configure_sentry_for_temporal(queue_name=task_queue)

    # Configure structured logging for Temporal workers
    from tfc.logging.temporal import configure_temporal_logging, get_logger

    configure_temporal_logging()

    # Disable litellm's async logging worker completely to prevent event loop
    # crashes. The worker creates asyncio Queue/Task objects that conflict with
    # Temporal's event loop and thread pool model. Since we don't use litellm's
    # callback system (we have our own ClickHouse logging), disabling is safe.
    # Patch at BOTH class and instance level to cover all code paths.
    try:
        from litellm.litellm_core_utils.logging_worker import (
            GLOBAL_LOGGING_WORKER,
            LoggingWorker,
        )

        _noop = lambda *a, **kw: None

        async def _noop_worker_loop(self):
            return

        # Class-level: covers self.start() calls from within methods and
        # any future instances
        LoggingWorker.start = _noop
        LoggingWorker.enqueue = _noop
        LoggingWorker.ensure_initialized_and_enqueue = _noop
        LoggingWorker._worker_loop = _noop_worker_loop

        # Instance-level: belt-and-suspenders for the global singleton
        GLOBAL_LOGGING_WORKER.start = _noop
        GLOBAL_LOGGING_WORKER.enqueue = _noop
        GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue = _noop
    except ImportError:
        pass

    from django.db import close_old_connections

    # Clean up stale DB connections
    close_old_connections()

    client = await get_client()

    workflows = get_workflows_for_queue(task_queue)
    activities = get_activities_for_queue(task_queue)

    log = get_logger(__name__)
    log.info(
        "temporal_worker_starting",
        task_queue=task_queue,
        host=TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
        workflows=[w.__name__ for w in workflows],
        activities=[a.__name__ for a in activities],
        max_concurrent_activities=max_concurrent_activities,
        max_concurrent_workflow_tasks=max_concurrent_workflow_tasks,
    )

    if target_memory_usage is not None:
        log.info(
            "resource_tuning_enabled",
            target_memory_usage=f"{target_memory_usage * 100:.0f}%",
            target_cpu_usage=f"{(target_cpu_usage or 1.0) * 100:.0f}%",
        )
    else:
        log.info("resource_tuning_disabled", mode="fixed_concurrency")

    if not workflows and not activities:
        log.warning("no_workflows_or_activities", task_queue=task_queue)

    # Create dedicated thread pool for activities
    # This pool is used for:
    # 1. Sync activities (via activity_executor parameter)
    # 2. sync_to_async() calls in async activities (via loop.set_default_executor)
    # This prevents thread starvation when multiple activities run concurrently
    thread_pool_size = max_concurrent_activities or 50
    activity_executor = ThreadPoolExecutor(max_workers=thread_pool_size)

    # Set as default executor for the event loop - this makes sync_to_async()
    # use our larger thread pool instead of Django's small default pool
    loop = asyncio.get_running_loop()
    loop.set_default_executor(activity_executor)

    log.info(
        "activity_executor_created",
        max_workers=thread_pool_size,
        note="Also set as default executor for sync_to_async",
    )

    # Base worker configuration
    worker_kwargs = {
        "client": client,
        "task_queue": task_queue,
        "workflows": workflows,
        "activities": activities,
        # Dedicated thread pool for sync activities - prevents thread starvation
        "activity_executor": activity_executor,
        # Heartbeat throttling - prevents too frequent heartbeat calls
        "max_heartbeat_throttle_interval": timedelta(seconds=5),
        "interceptors": [SentryInterceptor()],
    }
    if workflow_runner is not None:
        worker_kwargs["workflow_runner"] = workflow_runner

    if graceful_shutdown_timeout:
        worker_kwargs["graceful_shutdown_timeout"] = graceful_shutdown_timeout

    # Resource-based tuning is disabled by default due to a known bug where
    # workers progressively stop polling queues, causing activities to sit in
    # PENDING for hours. Set TEMPORAL_RESOURCE_TUNING_ENABLED=true to opt in.
    # See: https://github.com/temporalio/sdk-python/issues/1268
    resource_tuning_enabled = os.getenv(
        "TEMPORAL_RESOURCE_TUNING_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    if target_memory_usage is not None and resource_tuning_enabled:
        worker_kwargs["tuner"] = WorkerTuner.create_resource_based(
            target_memory_usage=target_memory_usage,
            target_cpu_usage=target_cpu_usage or 1.0,
            workflow_config=ResourceBasedSlotConfig(
                maximum_slots=max_concurrent_workflow_tasks,
            ),
            activity_config=ResourceBasedSlotConfig(
                maximum_slots=max_concurrent_activities,
            ),
        )
        log.info("worker_tuning_mode", mode="resource_based")
    else:
        if target_memory_usage is not None:
            log.warning(
                "resource_tuning_disabled",
                target_memory=target_memory_usage,
                reason="Set TEMPORAL_RESOURCE_TUNING_ENABLED=true to enable",
            )
        worker_kwargs["max_concurrent_activities"] = max_concurrent_activities
        worker_kwargs["max_concurrent_workflow_tasks"] = max_concurrent_workflow_tasks
        log.info("worker_tuning_mode", mode="fixed_concurrency")

    worker = Worker(**worker_kwargs)
    await worker.run()


def start_worker(task_queue: Optional[str] = None) -> None:
    """
    Start Temporal worker for queue specified by arg or TEMPORAL_TASK_QUEUE env var.

    Called from entrypoint.sh or management command.

    Args:
        task_queue: Queue name (optional, defaults to env var)
    """
    if task_queue is None:
        task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "default")

    # Convert Celery-style queue names to Temporal queue names
    task_queue = get_queue_name(task_queue)

    asyncio.run(run_worker(task_queue))


__all__ = [
    "run_worker",
    "start_worker",
]
