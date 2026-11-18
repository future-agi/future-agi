import asyncio
import sys
import time
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async

from tfc.management.commands.start_temporal_worker import (
    _activity_thread_kwargs,
    _generic_all_queues,
    _workflow_cache_kwargs,
    disable_litellm_logging_worker,
)
from tfc.temporal.common.activity_thread import OwnThreadActivityInterceptor

_LOGGING_WORKER = "litellm.litellm_core_utils.logging_worker"
_PATCHED = ("start", "enqueue", "ensure_initialized_and_enqueue")


@pytest.mark.unit
def test_generic_all_queue_worker_excludes_dedicated_queues():
    registered = [
        "default",
        "tasks_xl",
        "exact_aggregation",
        "trace_ingestion",
    ]

    assert _generic_all_queues(registered) == [
        "default",
        "tasks_xl",
        "trace_ingestion",
    ]


@pytest.mark.unit
def test_generic_all_queue_worker_preserves_other_queue_order():
    registered = ["agent_compass", "tasks_s", "tasks_l"]

    assert _generic_all_queues(registered) == registered


@pytest.mark.unit
def test_runner_activities_get_their_own_thread():
    interceptors = _activity_thread_kwargs("simulation_runner")["interceptors"]

    assert [type(one) for one in interceptors] == [OwnThreadActivityInterceptor]


@pytest.mark.unit
def test_other_queues_keep_their_shared_thread():
    assert _activity_thread_kwargs("tasks_s") == {}


@pytest.mark.unit
def test_a_slow_runner_activity_does_not_hold_up_the_others():
    class _Next:
        async def execute_activity(self, _input):
            await sync_to_async(time.sleep, thread_sensitive=True)(0.2)

    inbound = OwnThreadActivityInterceptor().intercept_activity(_Next())

    async def run():
        started = time.perf_counter()
        await asyncio.gather(*(inbound.execute_activity(None) for _ in range(4)))
        return time.perf_counter() - started

    # On one shared thread these four take 0.8 s.
    assert asyncio.run(run()) < 0.6


@pytest.mark.unit
def test_exact_worker_disables_sticky_workflow_cache_for_single_slot_queue():
    assert _workflow_cache_kwargs("exact_aggregation") == {"max_cached_workflows": 0}


@pytest.mark.unit
def test_other_workers_keep_temporal_default_workflow_cache():
    assert _workflow_cache_kwargs("default") == {}


@pytest.mark.unit
def test_always_on_exact_worker_disables_startup_database_mutations():
    compose_path = (
        Path(__file__).resolve().parents[3] / "docker-compose.distributed.yml"
    )
    compose = compose_path.read_text(encoding="utf-8")
    start = compose.index("\n  worker-exact-aggregation:")
    end = compose.index("\n  worker-trace-ingestion:", start)
    exact_worker = compose[start:end]

    assert 'NO_STARTUP_DB_MUTATIONS: "true"' in exact_worker
    assert 'CH25_DROP_LEGACY_CDC_CHAIN: "false"' in exact_worker
    assert 'CH25_TRACE_DUAL_WRITE: "false"' in exact_worker
    assert 'CH_DUAL_WRITE: "false"' in exact_worker


@pytest.fixture
def logging_worker(monkeypatch):
    """litellm's logging worker module; its class and singleton are restored."""
    module = pytest.importorskip(_LOGGING_WORKER)
    for name in (*_PATCHED, "_worker_loop"):
        monkeypatch.setattr(
            module.LoggingWorker, name, module.LoggingWorker.__dict__[name]
        )
    monkeypatch.setattr(module, "GLOBAL_LOGGING_WORKER", module.LoggingWorker())
    return module


async def _log_through(worker):
    """What litellm does per call; returns the queue and task it left behind."""
    pending = asyncio.sleep(0)
    worker.ensure_initialized_and_enqueue(pending)
    worker.start()
    worker.enqueue(pending)
    pending.close()
    return worker._queue, worker._worker_task


@pytest.mark.unit
def test_litellm_logging_worker_never_queues_or_starts_a_task(logging_worker):
    """Its Queue/Task would be bound to asyncio.run() loops that then close."""
    cls = logging_worker.LoggingWorker
    worker = cls()
    originals = {name: cls.__dict__[name] for name in (*_PATCHED, "_worker_loop")}

    disable_litellm_logging_worker()

    # Every entry point is replaced, on the class and on the singleton.
    assert all(cls.__dict__[name] is not fn for name, fn in originals.items())
    singleton = logging_worker.GLOBAL_LOGGING_WORKER
    assert set(_PATCHED) <= set(vars(singleton))
    assert asyncio.run(_log_through(worker)) == (None, None)

    async def worker_loop_finishes():
        worker._queue, worker._sem = asyncio.Queue(), asyncio.Semaphore(1)
        loop_task = asyncio.create_task(worker._worker_loop())
        done, _ = await asyncio.wait({loop_task}, timeout=0.5)
        loop_task.cancel()
        return loop_task in done

    # The real loop waits on its queue forever.
    assert asyncio.run(worker_loop_finishes())
    # The singleton stays silent even if the class methods come back.
    for name, method in originals.items():
        setattr(cls, name, method)
    assert asyncio.run(_log_through(singleton)) == (None, None)


@pytest.mark.unit
def test_disabling_is_a_no_op_without_litellm_logging_worker(
    logging_worker, monkeypatch
):
    before = dict(vars(logging_worker.LoggingWorker))
    monkeypatch.setitem(sys.modules, _LOGGING_WORKER, None)

    disable_litellm_logging_worker()

    assert dict(vars(logging_worker.LoggingWorker)) == before
    assert not set(_PATCHED) & set(vars(logging_worker.GLOBAL_LOGGING_WORKER))
