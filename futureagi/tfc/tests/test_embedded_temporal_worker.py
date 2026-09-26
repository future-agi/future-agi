"""Temporal worker embedded in the API process (tfc/temporal/embedded.py)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import signal
import threading
import time
from types import SimpleNamespace

import pytest
from temporalio.service import RPCError, RPCStatusCode

from tfc.temporal import embedded
from tfc.temporal.embedded import EmbeddedTemporalWorker

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_embedded_env(monkeypatch):
    for name in (
        "FI_EMBEDDED_TEMPORAL_WORKER",
        "FI_EMBEDDED_WORKER_UNHEALTHY_AFTER_SECONDS",
        "REGISTER_TEMPORAL_SCHEDULES",
        "TEMPORAL_EXCLUDED_QUEUES",
        "TEMPORAL_MAX_CONCURRENT_ACTIVITIES",
        "TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS",
        "FI_APP_TEMPORAL_MAX_CONCURRENT_ACTIVITIES",
        "FI_APP_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS",
        "TEMPORAL_MAX_CACHED_WORKFLOWS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(embedded, "_instance", None)
    monkeypatch.setattr(embedded, "_retry_delay", lambda failures: 0.01)
    monkeypatch.setattr(embedded, "_probe_interval", lambda: 0.01)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# -- switch / singleton -------------------------------------------------------


def test_disabled_by_default():
    assert embedded.get_embedded_worker() is None


def test_enabled_returns_process_singleton(monkeypatch):
    monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
    worker = embedded.get_embedded_worker()
    assert isinstance(worker, EmbeddedTemporalWorker)
    assert embedded.get_embedded_worker() is worker


# -- queue plans --------------------------------------------------------------


@pytest.fixture
def fake_registry(monkeypatch):
    from tfc.temporal.common import registry

    monkeypatch.setattr(
        registry,
        "get_all_queues",
        lambda: [
            "tasks_s",
            "default",
            "exact_aggregation",
            "simulation_runner",
            "trace_ingestion",
        ],
    )
    monkeypatch.setattr(registry, "get_all_workflows", lambda: ["AllWorkflows"])
    monkeypatch.setattr(registry, "get_all_activities", lambda: ["all_activities"])
    monkeypatch.setattr(
        registry, "get_workflows_for_queue", lambda queue: [f"{queue}-workflow"]
    )
    monkeypatch.setattr(
        registry, "get_activities_for_queue", lambda queue: [f"{queue}-activity"]
    )


def test_plans_poll_generic_queues_and_a_single_slot_exact_queue(fake_registry):
    plans = embedded._worker_plans()

    assert [plan.queue for plan in plans] == [
        "default",
        "tasks_s",
        "trace_ingestion",
        "exact_aggregation",
    ]
    generic = plans[0]
    assert generic.workflows == ["AllWorkflows"]
    assert generic.activities == ["all_activities"]
    assert (generic.max_activities, generic.max_workflow_tasks) == (8, 8)
    exact = plans[-1]
    assert exact.workflows == ["exact_aggregation-workflow"]
    assert exact.activities == ["exact_aggregation-activity"]
    assert (exact.max_activities, exact.max_workflow_tasks) == (1, 1)


def test_plans_honour_excluded_queues_and_slot_env(fake_registry, monkeypatch):
    monkeypatch.setenv("TEMPORAL_EXCLUDED_QUEUES", "exact_aggregation, tasks_s")
    monkeypatch.setenv("TEMPORAL_MAX_CONCURRENT_ACTIVITIES", "3")

    plans = embedded._worker_plans()

    assert [plan.queue for plan in plans] == [
        "default",
        "simulation_runner",
        "trace_ingestion",
    ]
    assert {plan.max_activities for plan in plans} == {3}


def test_own_slot_settings_win_over_full_install_ones(fake_registry, monkeypatch):
    """A .env carried over from the distributed install sizes dedicated worker
    containers; the embedded worker's own knobs take precedence."""
    monkeypatch.setenv("TEMPORAL_MAX_CONCURRENT_ACTIVITIES", "50")
    monkeypatch.setenv("TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS", "500")
    monkeypatch.setenv("FI_APP_TEMPORAL_MAX_CONCURRENT_ACTIVITIES", "12")
    monkeypatch.setenv("FI_APP_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS", "2")

    plans = embedded._worker_plans()

    generic = [plan for plan in plans if plan.queue != "exact_aggregation"]
    assert {(p.max_activities, p.max_workflow_tasks) for p in generic} == {(12, 2)}
    exact = next(plan for plan in plans if plan.queue == "exact_aggregation")
    assert (exact.max_activities, exact.max_workflow_tasks) == (1, 1)


def test_worker_kwargs_size_each_worker_to_its_own_slots():
    client = object()
    interceptor = object()
    generic = embedded._QueuePlan("tasks_xl", [], [], 8, 8)
    exact = embedded._QueuePlan("exact_aggregation", [], [], 1, 1)

    generic_kwargs = embedded._worker_kwargs(client, generic, interceptor)
    exact_kwargs = embedded._worker_kwargs(client, exact, interceptor)

    # temporalio builds a per-Worker workflow pool sized to the slots when no
    # shared executor is passed.
    assert "workflow_task_executor" not in generic_kwargs
    assert generic_kwargs["max_concurrent_activities"] == 8
    assert generic_kwargs["max_concurrent_workflow_tasks"] == 8
    assert generic_kwargs["max_cached_workflows"] == 100
    assert generic_kwargs["interceptors"] == [interceptor]
    assert exact_kwargs["max_concurrent_activities"] == 1
    assert exact_kwargs["max_concurrent_workflow_tasks"] == 1
    assert exact_kwargs["max_cached_workflows"] == 0


# -- activity thread isolation ------------------------------------------------


def test_activity_executor_calls_run_on_their_queue_pool():
    queue_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="temporal-tasks_s"
    )
    seen = {}

    class _Next:
        async def execute_activity(self, input):
            seen["pool"] = embedded._activity_pool.get()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: threading.current_thread().name
            )

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            embedded._QueueRoutingExecutor(
                max_workers=1, thread_name_prefix="temporal-activity"
            )
        )
        inbound = embedded._activity_interceptor(queue_pool).intercept_activity(_Next())
        in_activity = await inbound.execute_activity(object())
        outside = await loop.run_in_executor(
            None, lambda: threading.current_thread().name
        )
        return in_activity, outside

    try:
        in_activity, outside = asyncio.run(main())
    finally:
        queue_pool.shutdown()

    assert seen["pool"] is queue_pool
    assert in_activity.startswith("temporal-tasks_s")
    assert outside.startswith("temporal-activity")
    assert embedded._activity_pool.get() is None


# -- failure classification and liveness --------------------------------------


@pytest.mark.parametrize(
    ("error", "fatal"),
    [
        (ValueError("max_concurrent_workflow_tasks must be at least 2"), True),
        (TypeError("bad worker option"), True),
        (RPCError("namespace not found", RPCStatusCode.NOT_FOUND, b""), True),
        (RPCError("denied", RPCStatusCode.PERMISSION_DENIED, b""), True),
        (RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""), False),
        (RuntimeError("Failed client connect: connection refused"), False),
        (
            RuntimeError(
                "Worker validation failed\n\nCaused by:\n    0: Namespace nope was "
                'not found: Status { code: NotFound, message: "not found" }'
            ),
            True,
        ),
        (
            RuntimeError(
                "Worker validation failed\n\nCaused by:\n    0: Status { code: "
                'Unavailable, message: "tcp connect error" }'
            ),
            False,
        ),
    ],
)
def test_only_configuration_errors_are_fatal(error, fatal):
    assert embedded._is_fatal(error) is fatal


def test_health_allows_a_startup_window_then_fails(monkeypatch):
    worker = EmbeddedTemporalWorker()
    assert worker.health()["healthy"] is True

    monkeypatch.setenv("FI_EMBEDDED_WORKER_UNHEALTHY_AFTER_SECONDS", "0")
    assert worker.health()["healthy"] is False

    worker._set_state(connected=True, workers_running=True)
    state = worker.health()
    assert state["healthy"] is True
    assert state["down_for_seconds"] == 0


def test_health_fails_immediately_on_fatal_error():
    worker = EmbeddedTemporalWorker()
    worker._set_state(connected=True, workers_running=True)

    worker._record_failure(ValueError("bad config"), fatal=True)

    state = worker.health()
    assert state["healthy"] is False
    assert state["fatal"] is True
    assert state["workers_running"] is False
    assert state["consecutive_failures"] == 1
    assert state["last_error"] == "ValueError: bad config"


def test_running_again_resets_failure_count():
    worker = EmbeddedTemporalWorker()
    worker._record_failure(RuntimeError("down"))
    worker._record_failure(RuntimeError("down"))
    assert worker.health()["consecutive_failures"] == 2

    worker._set_state(connected=True, workers_running=True)

    assert worker.health()["consecutive_failures"] == 0


# -- supervision --------------------------------------------------------------


def test_supervise_retries_until_temporal_is_reachable(monkeypatch):
    attempts = []

    async def fake_run_workers(self):
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise RuntimeError("Failed client connect: connection refused")
        self._set_state(connected=True, workers_running=True)
        self._shutdown.set()

    monkeypatch.setattr(EmbeddedTemporalWorker, "_run_workers", fake_run_workers)
    worker = EmbeddedTemporalWorker()

    asyncio.run(worker._supervise())

    assert len(attempts) == 3
    state = worker.health()
    assert state["fatal"] is False
    assert state["consecutive_failures"] == 0


def test_supervise_stops_retrying_on_fatal_error(monkeypatch):
    attempts = []

    async def fake_run_workers(self):
        attempts.append(1)
        raise ValueError("invalid worker configuration")

    monkeypatch.setattr(EmbeddedTemporalWorker, "_run_workers", fake_run_workers)
    worker = EmbeddedTemporalWorker()

    asyncio.run(worker._supervise())

    assert attempts == [1]
    assert worker.health()["fatal"] is True
    assert worker.health()["healthy"] is False


def test_supervise_does_nothing_when_stop_was_requested_first(monkeypatch):
    async def fail_run_workers(self):
        raise AssertionError("must not start workers")

    monkeypatch.setattr(EmbeddedTemporalWorker, "_run_workers", fail_run_workers)
    worker = EmbeddedTemporalWorker()
    worker.request_stop()

    asyncio.run(worker._supervise())


class _FakeWorker:
    """Mimics temporalio.worker.Worker's run()/shutdown() contract, including
    its trap: when run() fails before polling starts (namespace validation
    while the server is unreachable), shutdown() never returns."""

    fail = {}  # queue -> error run() raises right after starting
    fail_on_stop = {}  # queue -> error run() raises once asked to stop
    instances = []
    events = []

    def __init__(self, **kwargs):
        self.task_queue = kwargs["task_queue"]
        self.kwargs = kwargs
        self.is_running = False
        self._stop = asyncio.Event()
        self._done = asyncio.Event()
        self.generation = sum(
            1 for other in self.instances if other.task_queue == self.task_queue
        )
        self.instances.append(self)
        self.events.append(("create", self.task_queue, self.generation))

    async def run(self):
        error = self.fail.pop(self.task_queue, None)
        if error is not None:
            await asyncio.sleep(0.02)
            self.events.append(("failed", self.task_queue, self.generation))
            raise error
        self.is_running = True
        await self._stop.wait()
        self.is_running = False
        error = self.fail_on_stop.pop(self.task_queue, None)
        if error is not None:
            self.events.append(("failed", self.task_queue, self.generation))
            raise error
        self.events.append(("drained", self.task_queue, self.generation))
        self._done.set()

    async def shutdown(self):
        self._stop.set()
        await self._done.wait()


@pytest.fixture
def fake_temporal(monkeypatch):
    import temporalio.client
    import temporalio.worker

    from tfc.management.commands import start_temporal_worker
    from tfc.telemetry import temporal as telemetry_temporal

    _FakeWorker.fail = {}
    _FakeWorker.fail_on_stop = {}
    _FakeWorker.instances = []
    _FakeWorker.events = []
    pools = []

    class _ServiceClient:
        async def check_health(self, timeout=None):
            return True

    client = SimpleNamespace(namespace="default", service_client=_ServiceClient())

    async def connect(*args, **kwargs):
        return client

    real_interceptor = embedded._activity_interceptor

    def recording_interceptor(pool):
        pools.append(pool)
        return real_interceptor(pool)

    async def register(self, client):
        return True

    monkeypatch.setattr(temporalio.client.Client, "connect", connect)
    monkeypatch.setattr(temporalio.worker, "Worker", _FakeWorker)
    monkeypatch.setattr(telemetry_temporal, "get_interceptors_for_client", lambda: [])
    monkeypatch.setattr(
        start_temporal_worker, "disable_litellm_logging_worker", lambda: None
    )
    monkeypatch.setattr(embedded, "_activity_interceptor", recording_interceptor)
    monkeypatch.setattr(
        embedded,
        "_worker_plans",
        lambda: [
            embedded._QueuePlan("default", [], [], 2, 2),
            embedded._QueuePlan("tasks_xl", [], [], 2, 2),
            embedded._QueuePlan("exact_aggregation", [], [], 1, 1),
        ],
    )
    monkeypatch.setattr(EmbeddedTemporalWorker, "_register_temporal_state", register)
    monkeypatch.setenv("TEMPORAL_RELOAD_DISPATCHER_ON_START", "false")
    return SimpleNamespace(pools=pools, worker_cls=_FakeWorker)


def _events(fake_temporal, kind, generation=None):
    return {
        queue
        for event_kind, queue, gen in fake_temporal.worker_cls.events
        if event_kind == kind and generation in (None, gen)
    }


def test_fatal_worker_error_drains_every_sibling_before_stopping(fake_temporal):
    fake_temporal.worker_cls.fail["tasks_xl"] = ValueError("bad queue config")
    worker = EmbeddedTemporalWorker()

    asyncio.run(asyncio.wait_for(worker._supervise(), timeout=10))

    assert _events(fake_temporal, "failed") == {"tasks_xl"}
    assert _events(fake_temporal, "drained") == {"default", "exact_aggregation"}
    # Never rebuilt after a fatal error, and no pool left serving.
    assert len(fake_temporal.worker_cls.instances) == 3
    assert all(pool._shutdown for pool in fake_temporal.pools)
    assert worker.health()["fatal"] is True
    assert worker.health()["last_error"] == "ValueError: bad queue config"


def test_transient_worker_error_rebuilds_only_after_all_workers_drained(
    fake_temporal,
):
    # The real Worker's shutdown() would hang on this one; it must not matter.
    fake_temporal.worker_cls.fail["default"] = RuntimeError(
        "Worker validation failed: tcp connect error"
    )
    worker = EmbeddedTemporalWorker()

    async def main():
        task = asyncio.create_task(worker._supervise())
        while (
            len(fake_temporal.worker_cls.instances) < 6
            or not worker.health()["workers_running"]
        ):
            await asyncio.sleep(0.01)
        worker._shutdown.set()
        await task

    asyncio.run(asyncio.wait_for(main(), timeout=10))

    events = fake_temporal.worker_cls.events
    first_rebuild = events.index(("create", "default", 1))
    assert {queue for kind, queue, _ in events[:first_rebuild] if kind != "create"} == {
        "default",
        "tasks_xl",
        "exact_aggregation",
    }
    assert _events(fake_temporal, "drained", generation=1) == {
        "default",
        "tasks_xl",
        "exact_aggregation",
    }
    assert worker.health()["fatal"] is False
    assert all(pool._shutdown for pool in fake_temporal.pools)


def test_shutdown_does_not_hang_on_a_worker_failing_while_stopping(fake_temporal):
    fake_temporal.worker_cls.fail_on_stop["tasks_xl"] = RuntimeError(
        "Worker validation failed"
    )
    worker = EmbeddedTemporalWorker()

    async def main():
        task = asyncio.create_task(worker._supervise())
        while not worker.health()["workers_running"]:
            await asyncio.sleep(0.01)
        worker._shutdown.set()
        await task

    asyncio.run(asyncio.wait_for(main(), timeout=10))

    assert _events(fake_temporal, "drained") == {"default", "exact_aggregation"}
    assert len(fake_temporal.worker_cls.instances) == 3
    assert worker.health()["fatal"] is False


def test_monitor_registers_temporal_state_again_after_an_outage(monkeypatch):
    worker = EmbeddedTemporalWorker()
    worker._set_state(connected=True)
    probes = iter([True, False, False, True, True])
    registrations = []

    class _ServiceClient:
        async def check_health(self, timeout=None):
            try:
                return next(probes)
            except StopIteration:
                worker._shutdown.set()
                return True

    async def register(client):
        registrations.append(worker.health()["connected"])
        return True

    monkeypatch.setattr(worker, "_register_temporal_state", register)
    client = SimpleNamespace(service_client=_ServiceClient())

    async def main():
        worker._shutdown = asyncio.Event()
        await worker._monitor(client, [SimpleNamespace(is_running=True)], True)

    asyncio.run(main())

    # Once, when the server came back; not on every healthy probe.
    assert registrations == [False]
    assert worker.health()["healthy"] is True


def test_monitor_retries_a_failed_registration(monkeypatch):
    worker = EmbeddedTemporalWorker()
    worker._set_state(connected=True)
    results = [False, True]
    registrations = []

    class _ServiceClient:
        async def check_health(self, timeout=None):
            if len(registrations) == 2:
                worker._shutdown.set()
            return True

    async def register(client):
        registrations.append(1)
        return results.pop(0)

    monkeypatch.setattr(worker, "_register_temporal_state", register)

    async def main():
        worker._shutdown = asyncio.Event()
        await worker._monitor(
            SimpleNamespace(service_client=_ServiceClient()),
            [SimpleNamespace(is_running=True)],
            False,
        )

    asyncio.run(main())

    assert registrations == [1, 1]


# -- Temporal state registration ----------------------------------------------


@pytest.fixture
def registration_calls(monkeypatch):
    from tfc.temporal import schedules
    from tfc.temporal.eval_tasks import registration

    calls = []

    async def register_search_attributes(client, namespace):
        calls.append(("search_attributes", namespace))
        return True

    async def a_register_schedules(client, configs, cleanup_orphans=False):
        calls.append(("schedules", len(configs), cleanup_orphans))

    monkeypatch.setattr(
        registration, "register_search_attributes", register_search_attributes
    )
    monkeypatch.setattr(schedules, "a_register_schedules", a_register_schedules)
    return calls


def test_registration_creates_search_attributes_and_every_schedule(
    registration_calls,
):
    from tfc.temporal.schedules import ALL_SCHEDULES

    worker = EmbeddedTemporalWorker()
    client = SimpleNamespace(namespace="default")

    assert asyncio.run(worker._register_temporal_state(client)) is True
    assert registration_calls == [
        ("search_attributes", "default"),
        ("schedules", len(ALL_SCHEDULES), False),
    ]


def test_registration_skips_schedules_when_disabled(registration_calls, monkeypatch):
    monkeypatch.setenv("REGISTER_TEMPORAL_SCHEDULES", "false")
    worker = EmbeddedTemporalWorker()

    asyncio.run(worker._register_temporal_state(SimpleNamespace(namespace="ns")))

    assert registration_calls == [("search_attributes", "ns")]


def test_registration_failure_is_reported_not_raised(monkeypatch):
    from tfc.temporal import schedules
    from tfc.temporal.eval_tasks import registration

    async def unavailable(*args, **kwargs):
        raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

    monkeypatch.setattr(registration, "register_search_attributes", unavailable)
    monkeypatch.setattr(schedules, "a_register_schedules", unavailable)
    worker = EmbeddedTemporalWorker()

    assert (
        asyncio.run(worker._register_temporal_state(SimpleNamespace(namespace="ns")))
        is False
    )


# -- lifecycle ----------------------------------------------------------------


@pytest.fixture
def idle_worker(monkeypatch):
    from tfc.temporal.common import registry

    monkeypatch.setattr(registry, "get_all_workflows", lambda: [])
    monkeypatch.setattr(registry, "get_all_activities", lambda: [])
    monkeypatch.setattr(
        EmbeddedTemporalWorker, "install_signal_drain", lambda self: None
    )

    async def fake_run_workers(self):
        self._set_state(connected=True, workers_running=True)
        await self._shutdown.wait()
        self._set_state(workers_running=False)

    monkeypatch.setattr(EmbeddedTemporalWorker, "_run_workers", fake_run_workers)
    worker = EmbeddedTemporalWorker()
    yield worker
    worker.stop(timeout=5)


def test_start_and_stop_are_idempotent(idle_worker):
    idle_worker.start()
    thread = idle_worker._thread
    idle_worker.start()
    assert idle_worker._thread is thread
    assert _wait_for(lambda: idle_worker.health()["workers_running"])

    idle_worker.stop(timeout=5)
    idle_worker.stop(timeout=5)

    assert not thread.is_alive()


def test_request_stop_drains_without_blocking(idle_worker):
    idle_worker.start()
    assert _wait_for(lambda: idle_worker.health()["workers_running"])

    idle_worker.request_stop()

    assert _wait_for(lambda: not idle_worker._thread.is_alive())


def test_stop_before_start_is_a_no_op():
    EmbeddedTemporalWorker().stop(timeout=1)


def test_import_failure_keeps_http_up_and_reports_fatal(monkeypatch):
    from tfc.temporal.common import registry

    def broken():
        raise ImportError("activity module failed to import")

    monkeypatch.setattr(registry, "get_all_workflows", broken)
    worker = EmbeddedTemporalWorker()

    worker.start()

    assert worker._thread is None
    assert worker.health()["fatal"] is True


# -- SIGTERM drain ------------------------------------------------------------


@pytest.fixture
def restore_signals():
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_sigterm_starts_the_drain_and_keeps_the_server_handler(restore_signals):
    server_calls = []
    signal.signal(signal.SIGTERM, lambda sig, frame: server_calls.append(sig))
    worker = EmbeddedTemporalWorker()

    worker.install_signal_drain()
    worker.install_signal_drain()  # chained once, not twice
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    assert worker._stop_requested is True
    assert server_calls == [signal.SIGTERM]


def test_signal_drain_leaves_default_disposition_alone(restore_signals):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    EmbeddedTemporalWorker().install_signal_drain()

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


# -- logging ------------------------------------------------------------------


def test_embedded_mode_adds_temporal_ids_to_structlog(monkeypatch):
    from tfc.logging import config

    assert config._merge_temporal_context not in config.get_processors()

    monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
    processors = config.get_processors()

    assert processors[1] is config._merge_temporal_context
    # Outside a workflow or activity the event passes through untouched.
    event = {"event": "request_finished"}
    assert config._merge_temporal_context(None, "info", dict(event)) == event
