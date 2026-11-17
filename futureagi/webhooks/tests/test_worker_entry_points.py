"""F1 (P0, WH-3's e2e on the real stack): every product entry point of the Temporal worker runs the webhooks
dispatcher, on a REAL Temporal server (the CLI dev server ``WorkflowEnvironment.start_local`` runs).

``webhooks_dispatch_loop`` (and the health and retention passes) are sync ``def`` activities. temporalio refuses to
build a ``Worker`` that registers one without an ``activity_executor``, and before the fix only
``tfc.temporal.common.worker.run_worker`` passed one. So ``manage.py start_temporal_worker --task-queue webhooks``
(the hosted dedicated deployment), ``--all-queues`` (the distributed compose ``worker``: it then stopped for every
queue) and the embedded worker (the standalone install: ``/health/`` answered 503) all refused it at boot.
``test_worker_boot.py`` drove ``handle()`` with a fake ``Worker``, so it never saw that.

Each test here boots one entry point for real: the command's own ``handle()`` (in this process, on the main thread,
stopped by SIGTERM through its own handler, as a pod is) or ``EmbeddedTemporalWorker.start()``. The registry, the
``Worker`` construction, the boot hook that starts ``webhooks-dispatcher-<n>``, the workflow and the
``dispatch_loop`` activity are the production ones. The test proves that the loop runs and sends a published event
to a receiver on 127.0.0.1, and, for the two all-queue entry points, that every generic queue still runs a workflow
with an async and a sync activity (a canary workflow added to the registry's lists).

Substitutions, and why: ``build_dispatcher`` is the test receiver's dispatcher (the address rules never admit
loopback; ``webhooks.tests.receiver``, as in WT-25); the command's ``os._exit(0)`` (it would end pytest), its OTel
init and its logging setup (process-global, outside F1) are stubbed; schedule registration is off (no FA schedule
starts on the dev server).
"""

from __future__ import annotations

import asyncio
import os
import runpy
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import close_old_connections, transaction
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment

import tfc.logging.temporal
import tfc.telemetry.temporal
import tfc.temporal.common.registry as registry
from tfc import asgi_startup
from tfc.management.commands import start_temporal_worker as cmd
from tfc.temporal import embedded
from webhooks import config
from webhooks.models import Delivery
from webhooks.publish import publish
from webhooks.temporal import QUEUE
from webhooks.temporal import activities as dispatch_activities
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Receiver, real_dispatcher

pytestmark = pytest.mark.django_db(transaction=True)

WAIT_S = 90


@activity.defn(name="webhooks_f1_canary_async")
async def canary_async(queue: str) -> str:
    return f"async:{queue}"


@activity.defn(name="webhooks_f1_canary_sync")
def canary_sync(queue: str) -> str:
    return f"sync:{queue}:{threading.current_thread().name}"


@workflow.defn(name="WebhooksF1CanaryWorkflow")
class CanaryWorkflow:
    """Runs one async and one sync activity on the queue it was started on."""

    @workflow.run
    async def run(self, queue: str) -> list[str]:
        out = []
        for name in ("webhooks_f1_canary_async", "webhooks_f1_canary_sync"):
            out.append(
                await workflow.execute_activity(
                    name, queue, start_to_close_timeout=timedelta(seconds=30)
                )
            )
        return out


@pytest.fixture
def dev_server():
    """A Temporal CLI dev server for this test, owned by a thread of its own (its loop stays up while the worker
    under test runs its own loops). Yields ``host:port`` on 127.0.0.1."""
    box: dict = {}
    ready = threading.Event()

    def main():
        async def run():
            box["loop"], box["stop"] = asyncio.get_running_loop(), asyncio.Event()
            env = await WorkflowEnvironment.start_local()
            try:
                box["target"] = env.client.service_client.config.target_host
                ready.set()
                await box["stop"].wait()
            finally:
                await env.shutdown()

        try:
            asyncio.run(run())
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            box["error"] = exc
            ready.set()

    thread = threading.Thread(target=main, name="temporal-dev-server", daemon=True)
    thread.start()
    assert ready.wait(180), "the Temporal dev server did not start"
    if "error" in box:
        raise box["error"]
    yield box["target"]
    box["loop"].call_soon_threadsafe(box["stop"].set)
    thread.join(60)


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


@pytest.fixture
def sending(organization, workspace, monkeypatch, receiver):
    """Sending on (not hosted, keys set), one endpoint on the receiver, one event published, the dispatcher built
    around the receiver, and the canary in the registry's lists. Returns the endpoint."""
    use_test_keys(monkeypatch)
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    for name in (
        "FI_WEBHOOKS_ENABLED",
        "WEBHOOKS_DISPATCHER_LOOPS",
        "TEMPORAL_EXCLUDED_QUEUES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REGISTER_TEMPORAL_SCHEDULES", "false")
    monkeypatch.setenv("TEMPORAL_RELOAD_DISPATCHER_ON_START", "false")
    ep = make_endpoint(organization, workspace, url=receiver.url("/ok"))
    with transaction.atomic():
        publish(
            type="prompt.label.changed",
            organization_id=organization.id,
            workspace_id=workspace.id,
            data=label_event_data(),
            source_key="f1-1",
            source_hash="h",
            producer="test",
            occurred_at=T0,
        )

    def build(heartbeat=lambda: None):
        d = real_dispatcher(receiver)
        d.heartbeat = heartbeat
        return d

    monkeypatch.setattr(dispatch_activities, "build_dispatcher", build)
    workflows, activities = registry.get_all_workflows, registry.get_all_activities
    monkeypatch.setattr(
        registry, "get_all_workflows", lambda: [*workflows(), CanaryWorkflow]
    )
    monkeypatch.setattr(
        registry,
        "get_all_activities",
        lambda: [*activities(), canary_async, canary_sync],
    )
    # The embedded worker's dedicated exact-aggregation queue registers only its own workflows and activities
    # (``embedded._worker_plans``): the canary joins them there too, so that queue's sync path is proven as well.
    for_queue_w, for_queue_a = (
        registry.get_workflows_for_queue,
        registry.get_activities_for_queue,
    )
    exact = embedded.EXACT_AGGREGATION_QUEUE
    monkeypatch.setattr(
        registry,
        "get_workflows_for_queue",
        lambda q: [*for_queue_w(q), CanaryWorkflow] if q == exact else for_queue_w(q),
    )
    monkeypatch.setattr(
        registry,
        "get_activities_for_queue",
        lambda q: (
            [*for_queue_a(q), canary_async, canary_sync]
            if q == exact
            else for_queue_a(q)
        ),
    )
    return ep


def _succeeded(ep) -> int:
    try:
        return Delivery.objects.filter(endpoint=ep, status="succeeded").count()
    finally:
        close_old_connections()


async def _probe(
    target: str, ep, receiver, canary_queues, abort: threading.Event
) -> dict:
    """What the running worker did: the dispatcher loop's state, the delivery, and the canary on each queue."""
    client = await Client.connect(target)
    seen: dict = {"queues": {}}
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline and not abort.is_set():
        if receiver.arrivals("/ok") and await asyncio.to_thread(_succeeded, ep) == 1:
            break
        await asyncio.sleep(0.5)
    seen["arrivals"] = len(receiver.arrivals("/ok"))
    seen["succeeded"] = await asyncio.to_thread(_succeeded, ep)
    if seen["arrivals"]:
        seen["loop"] = (
            await client.get_workflow_handle("webhooks-dispatcher-1").describe()
        ).status
    for queue in canary_queues:
        if abort.is_set():
            break
        seen["queues"][queue] = await client.execute_workflow(
            CanaryWorkflow.run,
            queue,
            id=f"f1-canary-{queue}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
    return seen


def _run_worker_command(target, probe, **options) -> dict:
    """Run the real ``start_temporal_worker`` on this (the main) thread until ``probe`` (another thread) is done,
    then SIGTERM this process: the command's own handler drains its workers, as a pod's SIGTERM does."""
    out: dict = {}
    done, lock = threading.Event(), threading.Lock()

    def side():
        try:
            out["seen"] = asyncio.run(probe(done))
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            out["error"] = exc
        finally:
            with lock:
                if not done.is_set():
                    os.kill(os.getpid(), signal.SIGTERM)

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    def too_early(_sig, _frame):
        # The probe ended before the command installed its own handler: fail here instead of letting the default
        # disposition end pytest.
        raise RuntimeError(
            f"the probe ended before the worker started: {out.get('error')!r}"
        )

    signal.signal(signal.SIGTERM, too_early)
    thread = threading.Thread(target=side, name="f1-probe", daemon=True)
    thread.start()
    try:
        call_command(
            "start_temporal_worker",
            temporal_host=target,
            reload_dispatcher=False,
            graceful_timeout=2,
            max_concurrent_activities=8,
            **options,
        )
    finally:
        with lock:
            done.set()
        thread.join(WAIT_S + 60)
        for s, h in saved.items():
            signal.signal(s, h)
    if "error" in out:
        raise out["error"]
    return out["seen"]


@pytest.fixture
def command(monkeypatch):
    monkeypatch.setattr(cmd.os, "_exit", lambda _code: None)
    monkeypatch.setattr(
        tfc.telemetry.temporal, "init_otel_for_temporal", lambda *_a: None
    )
    monkeypatch.setattr(
        tfc.logging.temporal, "configure_temporal_logging", lambda: None
    )
    return _run_worker_command


def _assert_sent(seen, receiver) -> None:
    assert seen["arrivals"] == 1, seen
    assert seen["succeeded"] == 1, seen
    assert seen["loop"] == WorkflowExecutionStatus.RUNNING
    assert receiver.requests[0]["headers"]["webhook-id"]


def _assert_canary(seen, queues) -> None:
    assert sorted(seen["queues"]) == sorted(queues)
    for queue, (a, s) in seen["queues"].items():
        assert a == f"async:{queue}"
        # The sync activity ran on that queue's own thread pool (its Worker's activity_executor).
        assert s.startswith(f"sync:{queue}:temporal-{queue}_"), s


def test_the_webhooks_queue_worker_command_runs_the_dispatch_loop(
    dev_server, sending, receiver, command
):
    """``manage.py start_temporal_worker --task-queue webhooks`` (hosted's dedicated deployment)."""
    seen = command(
        dev_server,
        lambda abort: _probe(dev_server, sending, receiver, [], abort),
        task_queue=QUEUE,
    )
    _assert_sent(seen, receiver)


def test_the_all_queues_worker_runs_the_dispatch_loop_and_every_generic_queue(
    dev_server, sending, receiver, command
):
    """``manage.py start_temporal_worker --all-queues`` (the distributed compose ``worker`` service): the webhooks
    loop runs, and the worker still serves every other queue it polls."""
    queues = cmd._generic_all_queues(registry.get_all_queues())
    assert QUEUE in queues and len(queues) > 1
    seen = command(
        dev_server,
        lambda abort: _probe(dev_server, sending, receiver, queues, abort),
        all_queues=True,
    )
    _assert_sent(seen, receiver)
    _assert_canary(seen, queues)


@pytest.fixture
def asgi_router(monkeypatch):
    """tfc/asgi.py's router with the Django app stubbed (as ``tfc/tests/test_asgi_embedded_worker.py`` runs it)."""
    calls: list[str] = []

    async def django_app(scope, receive, send):
        calls.append(scope["path"])

    monkeypatch.setattr("tfc.telemetry.init_telemetry", lambda *, component: None)
    monkeypatch.setattr("django.core.asgi.get_asgi_application", lambda: django_app)
    monkeypatch.setattr(asgi_startup, "warm_http_urlconf", lambda: 0)
    module = runpy.run_path(str(Path(embedded.__file__).parents[1] / "asgi.py"))
    namespace = module["http_router"].__globals__
    namespace["_get_mcp_starlette_app"] = lambda: None
    return namespace, calls


async def _health(namespace) -> list[dict]:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await namespace["http_router"](
        {"type": "http", "path": "/health/", "method": "GET"}, receive, send
    )
    return sent


def test_the_embedded_worker_runs_the_dispatch_loop_and_health_stays_up(
    dev_server, sending, receiver, monkeypatch, asgi_router
):
    """``FI_EMBEDDED_TEMPORAL_WORKER=true`` (the standalone install): the API process's worker starts, the loop
    sends, every generic queue runs the canary, and ``/health/`` is answered by the app, not with 503."""
    monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
    monkeypatch.setenv("TEMPORAL_HOST", dev_server)
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "default")
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT", "2")
    monkeypatch.setenv("FI_EMBEDDED_WORKER_PROBE_SECONDS", "1")
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    worker = embedded.EmbeddedTemporalWorker()
    namespace, django_calls = asgi_router
    namespace["get_embedded_worker"] = lambda: worker
    worker.start()
    try:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline and not worker.health()["workers_running"]:
            if worker.health()["fatal"]:
                break
            time.sleep(0.5)
        health = worker.health()
        assert health["fatal"] is False, health
        assert health["workers_running"] is True, health
        assert QUEUE in health["queues"], health
        # Every embedded queue, the dedicated exact-aggregation queue included.
        canary_queues = list(health["queues"])
        assert embedded.EXACT_AGGREGATION_QUEUE in canary_queues, health
        assert len(canary_queues) > 2
        seen = asyncio.run(
            _probe(dev_server, sending, receiver, canary_queues, threading.Event())
        )
        sent = asyncio.run(_health(namespace))
        assert worker.health()["healthy"] is True
    finally:
        worker.stop()
        for s, h in saved.items():
            signal.signal(s, h)
    _assert_sent(seen, receiver)
    _assert_canary(seen, canary_queues)
    assert django_calls == ["/health/"]
    assert not [m for m in sent if m.get("status") == 503]
