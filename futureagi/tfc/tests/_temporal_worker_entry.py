"""The real ``start_temporal_worker`` command as a child-process entry point.

Run as ``python -c ENTRY <fatal|clean>``: the process status is what
bin/temporal-worker, Kubernetes or an on-failure restart policy sees, with
the real os._exit. Temporal and the providers are stubbed; ``fatal`` makes
the worker raise, ``clean`` sends the process SIGTERM for a graceful stop.
"""

ENTRY = """
import asyncio, os, signal, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import django

django.setup()

import temporalio.client
import temporalio.worker

import tfc.logging.temporal
import tfc.telemetry.temporal
import tfc.temporal
from tfc.management.commands import start_temporal_worker as command

MODE = sys.argv[1]
command.disable_litellm_logging_worker = lambda: None
tfc.telemetry.temporal.init_otel_for_temporal = lambda _: None
tfc.telemetry.temporal.get_interceptors_for_client = lambda: []
tfc.logging.temporal.configure_temporal_logging = lambda: None
tfc.temporal.get_workflows_and_activities_for_queue = lambda _: ([], [])


async def describe(request, **kwargs):
    return Mock()


temporalio.client.Client.connect = AsyncMock(
    return_value=SimpleNamespace(
        namespace="default",
        workflow_service=SimpleNamespace(describe_namespace=describe),
    )
)


class Worker:
    def __init__(self, **kwargs):
        self.stopped = asyncio.Event()

    async def run(self):
        if MODE == "fatal":
            raise ValueError("synthetic fatal worker poller error")
        os.kill(os.getpid(), signal.SIGTERM)
        await self.stopped.wait()

    async def shutdown(self):
        self.stopped.set()


temporalio.worker.Worker = Worker
command.Command().handle(
    task_queue="tasks_s",
    all_queues=False,
    temporal_host="localhost:7233",
    namespace="default",
    graceful_timeout=1,
    max_concurrent_activities=1,
    max_concurrent_workflow_tasks=2,
    target_memory_usage=None,
    target_cpu_usage=None,
    reload_dispatcher=False,
)
os.write(1, b"handle() returned without exiting\\n")
"""
