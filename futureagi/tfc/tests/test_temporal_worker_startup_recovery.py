"""A reachable Temporal frontend with unavailable persistence must not strand workers."""

import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from tfc.management.commands import start_temporal_worker as command


@pytest.fixture
def worker_command(monkeypatch):
    """Run the real management-command lifecycle without process exit or providers."""
    handlers = {}
    monkeypatch.setattr(
        command.signal, "signal", lambda sig, fn: handlers.update({sig: fn})
    )
    monkeypatch.setattr(command.os, "_exit", lambda code: None)
    monkeypatch.setattr(command, "disable_litellm_logging_worker", lambda: None)
    monkeypatch.setattr("tfc.telemetry.temporal.init_otel_for_temporal", lambda _: None)
    monkeypatch.setattr(
        "tfc.telemetry.temporal.get_interceptors_for_client", lambda: []
    )
    monkeypatch.setattr("tfc.logging.temporal.configure_temporal_logging", lambda: None)
    monkeypatch.setattr(
        "tfc.temporal.get_workflows_and_activities_for_queue", lambda _: ([], [])
    )

    class BoundedRunner(asyncio.Runner):
        def run(self, coro, **kwargs):
            async def bounded():
                async with asyncio.timeout(2):
                    return await coro

            return super().run(bounded(), **kwargs)

    monkeypatch.setattr(command.asyncio, "Runner", BoundedRunner)
    wait_for = asyncio.wait_for

    async def fast_retry(awaitable, timeout):
        return await wait_for(awaitable, timeout=min(timeout, 0.01))

    monkeypatch.setattr(command.asyncio, "wait_for", fast_retry)
    namespace = SimpleNamespace(calls=0, ready=False, stop=False, fatal=False, starts=0)

    async def describe(request, **kwargs):
        namespace.calls += 1
        assert request.namespace == "default"
        assert kwargs["retry"] is False
        if namespace.stop:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        if namespace.calls < 3:
            raise RPCError(
                "synthetic persistence outage", RPCStatusCode.UNAVAILABLE, b""
            )
        namespace.ready = True
        return Mock()

    client = SimpleNamespace(
        namespace="default",
        workflow_service=SimpleNamespace(describe_namespace=describe),
    )
    monkeypatch.setattr(
        "temporalio.client.Client.connect", AsyncMock(return_value=client)
    )

    class Worker:
        def __init__(self, **kwargs):
            self.stopped = asyncio.Event()

        async def run(self):
            namespace.starts += 1
            if not namespace.ready:
                raise RuntimeError(
                    "Worker validation failed: synthetic persistence outage"
                )
            if namespace.fatal:
                raise ValueError("synthetic deterministic worker error")
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            await self.stopped.wait()

        async def shutdown(self):
            self.stopped.set()

        async def __aenter__(self):
            # The old command leaves a failed worker task unobserved and waits
            # forever for shutdown. BoundedRunner turns that into a test failure.
            await self.run()
            return self

        async def __aexit__(self, *args):
            await self.shutdown()

    monkeypatch.setattr("temporalio.worker.Worker", Worker)

    def run():
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

    return namespace, run


def test_worker_waits_for_persistence_and_starts_after_restoration(worker_command):
    namespace, run = worker_command
    run()
    assert namespace.calls == 3
    assert namespace.starts == 1


def test_shutdown_during_startup_outage_does_not_start_workers(worker_command):
    namespace, run = worker_command
    namespace.stop = True
    run()
    assert namespace.calls == 1
    assert namespace.starts == 0


def test_fatal_worker_failure_reaches_supervisor_instead_of_silent_idle(worker_command):
    namespace, run = worker_command
    namespace.fatal = True
    with pytest.raises(ExceptionGroup) as caught:
        run()
    assert any(isinstance(exc, ValueError) for exc in caught.value.exceptions)
