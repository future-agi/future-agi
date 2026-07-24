"""
Django management command to start a Temporal worker.

For local development, use --all-queues to run a single worker process
that polls all queues (creates multiple workers internally).
"""

import asyncio
import os
import signal
import threading
from typing import TYPE_CHECKING, List

from django.core.management.base import BaseCommand, CommandError

if TYPE_CHECKING:
    from temporalio.worker import Worker


class Command(BaseCommand):
    help = "Start a Temporal worker for processing workflows and activities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--task-queue",
            type=str,
            default=os.getenv("TEMPORAL_TASK_QUEUE", "default"),
            help="Task queue to poll (default: from TEMPORAL_TASK_QUEUE env var or 'default')",
        )
        parser.add_argument(
            "--all-queues",
            action="store_true",
            default=os.getenv("TEMPORAL_ALL_QUEUES", "").lower()
            in ("true", "1", "yes"),
            help="Poll ALL queues with a single worker process (for local development)",
        )
        parser.add_argument(
            "--temporal-host",
            type=str,
            default=os.getenv("TEMPORAL_HOST", "localhost:7233"),
            help="Temporal server host:port (default: localhost:7233)",
        )
        parser.add_argument(
            "--namespace",
            type=str,
            default=os.getenv("TEMPORAL_NAMESPACE", "default"),
            help="Temporal namespace (default: default)",
        )
        parser.add_argument(
            "--graceful-timeout",
            type=int,
            default=int(os.getenv("TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT") or "300"),
            help="Graceful shutdown timeout in seconds (default: 300 or TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT env var)",
        )
        parser.add_argument(
            "--max-concurrent-activities",
            type=int,
            default=int(os.getenv("TEMPORAL_MAX_CONCURRENT_ACTIVITIES") or "100"),
            help="Maximum concurrent activity task executions (default: 100 or TEMPORAL_MAX_CONCURRENT_ACTIVITIES env var)",
        )
        parser.add_argument(
            "--max-concurrent-workflow-tasks",
            type=int,
            default=int(os.getenv("TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS") or "100"),
            help="Maximum concurrent workflow task executions (default: 100 or TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS env var)",
        )
        parser.add_argument(
            "--target-memory-usage",
            type=float,
            default=(
                float(os.getenv("TEMPORAL_TARGET_MEMORY_USAGE"))
                if os.getenv("TEMPORAL_TARGET_MEMORY_USAGE")
                else None
            ),
            help="Target memory usage (0.0-1.0) for resource-based tuning. "
            "When set, worker dynamically adjusts concurrency based on memory. "
            "Example: 0.8 means target 80%% memory usage. (default: disabled)",
        )
        parser.add_argument(
            "--target-cpu-usage",
            type=float,
            default=float(os.getenv("TEMPORAL_TARGET_CPU_USAGE") or "1.0"),
            help="Target CPU usage (0.0-1.0) for resource-based tuning. "
            "Only used when --target-memory-usage is set. (default: 1.0)",
        )
        parser.add_argument(
            "--reload-dispatcher",
            action="store_true",
            default=os.getenv("TEMPORAL_RELOAD_DISPATCHER_ON_START", "true").lower()
            in ("true", "1", "yes"),
            help="Send reload signal to CallDispatcherWorkflow on startup to pick up new code. "
            "Useful for development. (default: true, set TEMPORAL_RELOAD_DISPATCHER_ON_START=false to disable)",
        )

    def handle(self, *args, **options):
        # Initialize OpenTelemetry FIRST (before other imports)
        # This ensures all activities, DB queries, Redis, and HTTP calls are traced
        from tfc.telemetry.temporal import init_otel_for_temporal

        task_queue_for_name = options.get("task_queue", "worker")
        init_otel_for_temporal(task_queue_for_name)

        # Lazy imports to avoid loading temporalio at Django startup
        from django.db import close_old_connections
        from temporalio.client import Client
        from temporalio.worker import UnsandboxedWorkflowRunner, Worker

        from tfc.logging.temporal import configure_temporal_logging, get_logger
        from tfc.temporal import (
            TASK_QUEUES,
            get_workflows_and_activities_for_queue,
        )
        from tfc.temporal.common.registry import (
            get_all_activities,
            get_all_queues,
            get_all_workflows,
        )

        # Configure structured logging for Temporal workers
        configure_temporal_logging()

        # Disable litellm's async logging worker to prevent event loop crashes.
        # The worker creates asyncio Queue/Task objects on temporary event loops
        # (from asyncio.run() in thread pool threads), which get destroyed when
        # those loops close — causing "Task was destroyed but it is pending".
        # Since we don't use litellm's callback system, disabling is safe.
        # Patch at BOTH class and instance level to cover all code paths.
        try:
            from litellm.litellm_core_utils.logging_worker import (
                GLOBAL_LOGGING_WORKER,
                LoggingWorker,
            )

            _noop = lambda *a, **kw: None

            async def _noop_worker_loop(self):
                return

            # Class-level
            LoggingWorker.start = _noop
            LoggingWorker.enqueue = _noop
            LoggingWorker.ensure_initialized_and_enqueue = _noop
            LoggingWorker._worker_loop = _noop_worker_loop

            # Instance-level for the global singleton
            GLOBAL_LOGGING_WORKER.start = _noop
            GLOBAL_LOGGING_WORKER.enqueue = _noop
            GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue = _noop
        except ImportError:
            pass

        task_queue = options["task_queue"]
        all_queues_mode = options["all_queues"]
        temporal_host = options["temporal_host"]
        namespace = options["namespace"]
        graceful_timeout = options["graceful_timeout"]
        max_concurrent_activities = options["max_concurrent_activities"]
        max_concurrent_workflow_tasks = options["max_concurrent_workflow_tasks"]
        target_memory_usage = options["target_memory_usage"]
        target_cpu_usage = options["target_cpu_usage"]
        reload_dispatcher = options["reload_dispatcher"]

        # Clean up stale DB connections
        close_old_connections()

        log = get_logger(__name__)

        # Determine which queues to poll
        if all_queues_mode:
            queues_to_poll = get_all_queues()
            # Get all unique workflows and activities across all queues
            workflows = get_all_workflows()
            activities = get_all_activities()
            log.info(
                "all_queues_mode",
                queue_count=len(queues_to_poll),
                queues=queues_to_poll,
            )
        else:
            # Map Celery-style queue names to Temporal queue names
            if task_queue in TASK_QUEUES:
                task_queue = TASK_QUEUES[task_queue]
            # Dedicated path may still target backfill alone; require slot=1 there.
            if task_queue == "backfill" and max_concurrent_activities != 1:
                raise CommandError(
                    "backfill queue requires --max-concurrent-activities 1 "
                    "(or use start_vapi_backfill_worker / start_backfill_worker)"
                )
            queues_to_poll = [task_queue]
            workflows, activities = get_workflows_and_activities_for_queue(task_queue)

        log.info(
            "temporal_worker_initializing",
            queues=queues_to_poll,
            host=temporal_host,
            namespace=namespace,
            graceful_timeout=graceful_timeout,
            max_activities=max_concurrent_activities,
            max_workflows=max_concurrent_workflow_tasks,
        )
        resource_tuning_enabled = os.getenv(
            "TEMPORAL_RESOURCE_TUNING_ENABLED", "false"
        ).lower() in ("true", "1", "yes")

        if target_memory_usage and resource_tuning_enabled:
            log.info(
                "worker_tuning_mode",
                mode="resource_based",
                target_memory=f"{target_memory_usage * 100:.0f}%",
                target_cpu=f"{target_cpu_usage * 100:.0f}%",
            )
        elif target_memory_usage:
            log.warning(
                "resource_tuning_disabled",
                target_memory=f"{target_memory_usage * 100:.0f}%",
                reason="Set TEMPORAL_RESOURCE_TUNING_ENABLED=true to enable",
            )
            log.info("worker_tuning_mode", mode="fixed_concurrency")
        else:
            log.info("worker_tuning_mode", mode="fixed_concurrency")

        log.info(
            "worker_configuration",
            workflows=[w.__name__ for w in workflows],
            activities=[a.__name__ for a in activities],
        )

        # Run worker with graceful shutdown handling
        runner = asyncio.Runner()
        shutdown_event = asyncio.Event()

        def signal_handler(signum, frame):
            log.info("signal_received", signal=signum, action="graceful_shutdown")
            runner.get_loop().call_soon_threadsafe(shutdown_event.set)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        async def run_worker():
            """Run the Temporal worker(s) with graceful shutdown support."""
            from datetime import timedelta

            from temporalio.worker import ResourceBasedSlotConfig, WorkerTuner

            # Get OpenTelemetry tracing interceptors (handles logging internally)
            from tfc.telemetry.temporal import get_interceptors_for_client

            interceptors = get_interceptors_for_client()

            client = await Client.connect(
                temporal_host,
                namespace=namespace,
                interceptors=interceptors,
            )

            # Send reload signal to dispatcher if enabled
            if reload_dispatcher:
                try:
                    from simulate.temporal.constants import (
                        DISPATCHER_WORKFLOW_ID,
                        PHONE_NUMBER_DISPATCHER_WORKFLOW_ID,
                    )

                    call_dispatcher_workflow_handle = client.get_workflow_handle(
                        DISPATCHER_WORKFLOW_ID
                    )
                    phone_number_dispatcher_workflow_handle = (
                        client.get_workflow_handle(PHONE_NUMBER_DISPATCHER_WORKFLOW_ID)
                    )

                    call_dispatcher_result, phone_number_dispatcher_result = (
                        await asyncio.gather(
                            call_dispatcher_workflow_handle.signal("reload"),
                            phone_number_dispatcher_workflow_handle.signal("reload"),
                            return_exceptions=True,
                        )
                    )

                    if isinstance(call_dispatcher_result, Exception):
                        log.warning(
                            f"Failed to reload the call dispatcher singleton workflow: {str(call_dispatcher_result)}"
                        )
                    if isinstance(phone_number_dispatcher_result, Exception):
                        log.warning(
                            f"Failed to reload the call phone number dispatcher singleton workflow: {str(phone_number_dispatcher_result)}"
                        )

                    log.info(
                        "dispatcher_reload_signal_sent",
                        workflow_id=DISPATCHER_WORKFLOW_ID,
                    )
                except Exception as e:
                    # Don't fail worker startup if dispatcher doesn't exist yet
                    log.warning(
                        "dispatcher_reload_signal_failed",
                        error=str(e),
                        hint="Dispatcher may not be running yet, will pick up new code on next start",
                    )

            def create_worker_kwargs(queue_name: str) -> dict:
                """Create worker configuration for a specific queue."""
                # One Worker per queue with that queue's own registration.
                # backfill is forced to a single activity slot so all-queues can
                # poll it without defeating the process-wide Vapi rate limiter.
                q_workflows, q_activities = get_workflows_and_activities_for_queue(
                    queue_name
                )
                activity_slots = (
                    1 if queue_name == "backfill" else max_concurrent_activities
                )

                kwargs = {
                    "client": client,
                    "task_queue": queue_name,
                    "workflows": q_workflows,
                    "activities": q_activities,
                    "workflow_runner": UnsandboxedWorkflowRunner(),
                    "graceful_shutdown_timeout": timedelta(seconds=graceful_timeout),
                    "max_heartbeat_throttle_interval": timedelta(seconds=5),
                }

                # Resource-based tuning is disabled by default due to a known
                # bug where workers stop polling queues.
                # See: https://github.com/temporalio/sdk-python/issues/1268
                if target_memory_usage and resource_tuning_enabled:
                    kwargs["tuner"] = WorkerTuner.create_resource_based(
                        target_memory_usage=target_memory_usage,
                        target_cpu_usage=target_cpu_usage or 1.0,
                        workflow_config=ResourceBasedSlotConfig(
                            maximum_slots=max_concurrent_workflow_tasks,
                        ),
                        activity_config=ResourceBasedSlotConfig(
                            maximum_slots=activity_slots,
                        ),
                    )
                else:
                    kwargs["max_concurrent_activities"] = activity_slots
                    kwargs["max_concurrent_workflow_tasks"] = (
                        max_concurrent_workflow_tasks
                    )

                return kwargs

            # Create workers for each queue
            workers: List[Worker] = []
            for queue_name in queues_to_poll:
                worker_kwargs = create_worker_kwargs(queue_name)
                workers.append(Worker(**worker_kwargs))
                log.info("worker_created", queue=queue_name)

            log.info(
                "starting_workers",
                worker_count=len(workers),
                queues=queues_to_poll,
            )

            # Run all workers concurrently until shutdown signal
            async def run_single_worker(worker: Worker, queue_name: str):
                async with worker:
                    await shutdown_event.wait()
                log.info("worker_shutdown", queue=queue_name)

            # Start all workers concurrently
            worker_tasks = [
                asyncio.create_task(run_single_worker(w, q))
                for w, q in zip(workers, queues_to_poll)
            ]

            # Wait for shutdown signal, then wait for all workers to finish
            await shutdown_event.wait()
            log.info("shutdown_signal_received")

            # Wait for all worker tasks to complete
            await asyncio.gather(*worker_tasks, return_exceptions=True)

            log.info("all_workers_shutdown_complete")

        try:
            runner.run(run_worker())
        except KeyboardInterrupt:
            log.info("worker_interrupted", reason="keyboard")
        except Exception as e:
            log.exception("worker_error", error=str(e))
            raise
        finally:
            runner.close()

            # Log active threads during shutdown
            active_threads = [t.name for t in threading.enumerate() if t.is_alive()]
            log.info("shutdown_threads", active_threads=active_threads)

            # Force exit to avoid hanging on any remaining threads
            os._exit(0)
