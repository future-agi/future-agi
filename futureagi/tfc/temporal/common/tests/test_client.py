import asyncio
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tfc.temporal.common import client as temporal_client


class LoopBoundClient:
    """Fail immediately if a test uses a client from the wrong event loop."""

    def __init__(self):
        self.creation_loop = asyncio.get_running_loop()
        self.namespace = "default"
        self.operation_loops = []
        self.workflow_ids = []

    async def start_workflow(self, *_args, **_kwargs):
        operation_loop = asyncio.get_running_loop()
        assert operation_loop is self.creation_loop
        assert operation_loop.is_running()
        self.operation_loops.append(operation_loop)
        self.workflow_ids.append(_kwargs["id"])
        return self

    async def ping(self):
        operation_loop = asyncio.get_running_loop()
        assert operation_loop is self.creation_loop
        assert operation_loop.is_running()
        self.operation_loops.append(operation_loop)


@pytest.fixture(autouse=True)
def reset_temporal_client():
    temporal_client.reset_client()
    yield
    temporal_client.reset_client()


@pytest.fixture
def no_temporal_interceptors(monkeypatch):
    monkeypatch.setattr(
        "tfc.telemetry.temporal.get_interceptors_for_client",
        lambda: [],
    )


async def _get_client_and_ping():
    client = await temporal_client.get_client()
    await client.ping()
    return client


@pytest.mark.unit
def test_after_fork_reset_replaces_inherited_state_and_locked_mutexes(monkeypatch):
    inherited_bridge_lock = threading.Lock()
    inherited_client_lock = threading.Lock()
    inherited_bridge_lock.acquire()
    inherited_client_lock.acquire()

    monkeypatch.setattr(
        temporal_client, "_sync_bridge_state_lock", inherited_bridge_lock
    )
    monkeypatch.setattr(temporal_client, "_sync_bridge_loop", object())
    monkeypatch.setattr(temporal_client, "_sync_bridge_thread", object())
    monkeypatch.setattr(temporal_client, "_sync_bridge_ready", object())
    monkeypatch.setattr(temporal_client, "_sync_bridge_pid", -1)
    monkeypatch.setattr(
        temporal_client, "_client_generation_lock", inherited_client_lock
    )
    monkeypatch.setattr(temporal_client, "_client_generation", 41)
    monkeypatch.setattr(temporal_client, "_has_current_client", True)

    try:
        temporal_client._reset_temporal_state_after_fork()

        assert temporal_client._sync_bridge_loop is None
        assert temporal_client._sync_bridge_thread is None
        assert temporal_client._sync_bridge_ready is None
        assert temporal_client._sync_bridge_pid == temporal_client.os.getpid()
        assert temporal_client._sync_bridge_state_lock is not inherited_bridge_lock
        assert temporal_client._client_generation_lock is not inherited_client_lock
        assert temporal_client._client_generation == 42
        assert temporal_client._has_current_client is False
        assert temporal_client._sync_bridge_state_lock.acquire(blocking=False)
        temporal_client._sync_bridge_state_lock.release()
        assert temporal_client._client_generation_lock.acquire(blocking=False)
        temporal_client._client_generation_lock.release()
    finally:
        inherited_bridge_lock.release()
        inherited_client_lock.release()


@pytest.mark.unit
def test_concurrent_dashboard_start_activity_dispatches_share_bridge_client(
    monkeypatch,
    no_temporal_interceptors,
):
    connect_started = threading.Event()
    release_connect = threading.Event()
    callers_ready = threading.Barrier(3)
    connected_clients = []
    state_lock = threading.Lock()

    async def connect(*_args, **_kwargs):
        client = LoopBoundClient()
        with state_lock:
            connected_clients.append(client)
        connect_started.set()
        assert await asyncio.to_thread(release_connect.wait, 5)
        return client

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    def start_workflow_from_request_thread(widget_id):
        from tfc.temporal.drop_in.runner import start_activity

        callers_ready.wait(timeout=5)
        return start_activity(
            "refresh_dashboard_exact_widget",
            task_id=widget_id,
            dispatch_timeout_seconds=2,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(start_workflow_from_request_thread, "widget-one")
        second = executor.submit(start_workflow_from_request_thread, "widget-two")
        callers_ready.wait(timeout=5)
        assert connect_started.wait(timeout=5)
        # Keep Client.connect suspended while both request threads submit work
        # to the persistent bridge loop.
        time.sleep(0.05)
        release_connect.set()
        first_workflow_id = first.result(timeout=2)
        second_workflow_id = second.result(timeout=2)

    assert {first_workflow_id, second_workflow_id} == {
        "task-widget-one",
        "task-widget-two",
    }
    assert len(connected_clients) == 1
    connected = connected_clients[0]
    assert connected.creation_loop is temporal_client._get_sync_bridge_loop()
    assert connected.creation_loop.is_running()
    assert connected.operation_loops == [
        connected.creation_loop,
        connected.creation_loop,
    ]
    assert set(connected.workflow_ids) == {
        "task-widget-one",
        "task-widget-two",
    }


@pytest.mark.unit
def test_timed_out_dashboard_dispatch_releases_bridge_lock_for_waiter(
    monkeypatch,
    no_temporal_interceptors,
):
    connect_started = threading.Event()
    waiter_registered = threading.Event()
    connect_loops = []
    connected_clients = []
    call_count = 0
    state_calls = 0
    state_lock = threading.Lock()

    original_get_state = temporal_client._get_loop_client_state

    def get_state(loop):
        nonlocal state_calls
        state = original_get_state(loop)
        with state_lock:
            state_calls += 1
            if state_calls == 3:
                waiter_registered.set()
        return state

    async def connect(*_args, **_kwargs):
        nonlocal call_count
        with state_lock:
            call_count += 1
            attempt = call_count
            connect_loops.append(asyncio.get_running_loop())
        if attempt == 1:
            connect_started.set()
            await asyncio.Future()
        client = LoopBoundClient()
        connected_clients.append(client)
        return client

    monkeypatch.setattr(temporal_client, "_get_loop_client_state", get_state)
    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    from tfc.temporal.drop_in.runner import start_activity

    with ThreadPoolExecutor(max_workers=2) as executor:
        timed_out = executor.submit(
            start_activity,
            "refresh_dashboard_exact_widget",
            task_id="timed-out-widget",
            dispatch_timeout_seconds=1,
        )
        assert connect_started.wait(timeout=5)
        waiting = executor.submit(
            start_activity,
            "refresh_dashboard_exact_widget",
            task_id="waiting-widget",
            dispatch_timeout_seconds=2,
        )
        assert waiter_registered.wait(timeout=5)
        assert not timed_out.done()

        with pytest.raises(TimeoutError):
            timed_out.result(timeout=2)
        assert waiting.result(timeout=2) == "task-waiting-widget"

    assert call_count == 2
    assert connect_loops == [
        temporal_client._get_sync_bridge_loop(),
        temporal_client._get_sync_bridge_loop(),
    ]
    assert len(connected_clients) == 1
    assert connected_clients[0].workflow_ids == ["task-waiting-widget"]
    assert connected_clients[0].operation_loops == [connected_clients[0].creation_loop]


@pytest.mark.unit
def test_sync_bridge_preserves_contextvars():
    trace_context = contextvars.ContextVar("trace_context", default=None)
    token = trace_context.set("request-span")

    async def read_context():
        return trace_context.get()

    try:
        assert (
            temporal_client._run_async_in_sync_context(read_context) == "request-span"
        )
    finally:
        trace_context.reset(token)


@pytest.mark.unit
def test_async_worker_loop_gets_distinct_loop_bound_client(
    monkeypatch,
    no_temporal_interceptors,
):
    connected_clients = []

    async def connect(*_args, **_kwargs):
        client = LoopBoundClient()
        connected_clients.append(client)
        return client

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    sync_client = temporal_client._run_async_in_sync_context(_get_client_and_ping)

    async def use_worker_client_twice():
        first = await _get_client_and_ping()
        second = await _get_client_and_ping()
        assert first is second
        return first

    worker_client = asyncio.run(use_worker_client_twice())

    assert sync_client is not worker_client
    assert sync_client.creation_loop is not worker_client.creation_loop
    assert sync_client.operation_loops == [sync_client.creation_loop]
    assert worker_client.operation_loops == [
        worker_client.creation_loop,
        worker_client.creation_loop,
    ]
    assert connected_clients == [sync_client, worker_client]


@pytest.mark.unit
def test_sequential_asyncio_run_loops_get_distinct_clients(
    monkeypatch,
    no_temporal_interceptors,
):
    connected_clients = []

    async def connect(*_args, **_kwargs):
        client = LoopBoundClient()
        connected_clients.append(client)
        return client

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    first = asyncio.run(_get_client_and_ping())
    second = asyncio.run(_get_client_and_ping())

    assert first is not second
    assert first.creation_loop is not second.creation_loop
    assert connected_clients == [first, second]
    assert first.operation_loops == [first.creation_loop]
    assert second.operation_loops == [second.creation_loop]


@pytest.mark.unit
def test_failed_bridge_initialization_can_be_retried(
    monkeypatch,
    no_temporal_interceptors,
):
    call_count = 0

    async def connect(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("temporal unavailable")
        return LoopBoundClient()

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    with pytest.raises(RuntimeError, match="temporal unavailable"):
        temporal_client._run_async_in_sync_context(_get_client_and_ping)

    connected = temporal_client._run_async_in_sync_context(_get_client_and_ping)
    assert connected.operation_loops == [connected.creation_loop]
    assert call_count == 2


@pytest.mark.unit
def test_cancelled_initialization_releases_loop_lock_for_retry(
    monkeypatch,
    no_temporal_interceptors,
):
    call_count = 0
    connect_started = None

    async def connect(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert connect_started is not None
            connect_started.set()
            await asyncio.Future()
        return LoopBoundClient()

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    async def cancel_then_retry():
        nonlocal connect_started
        connect_started = asyncio.Event()
        initialization = asyncio.create_task(temporal_client.get_client())
        await asyncio.wait_for(connect_started.wait(), timeout=2)
        initialization.cancel()
        with pytest.raises(asyncio.CancelledError):
            await initialization
        return await asyncio.wait_for(_get_client_and_ping(), timeout=2)

    connected = asyncio.run(cancel_then_retry())
    assert connected.operation_loops == [connected.creation_loop]
    assert call_count == 2


@pytest.mark.unit
def test_cancelled_waiter_does_not_cancel_loop_local_client_owner(
    monkeypatch,
    no_temporal_interceptors,
):
    call_count = 0
    connect_started = None
    release_connect = None
    waiter_registered = threading.Event()
    state_calls = 0

    original_get_state = temporal_client._get_loop_client_state

    def get_state(loop):
        nonlocal state_calls
        state = original_get_state(loop)
        state_calls += 1
        if state_calls == 3:
            waiter_registered.set()
        return state

    async def connect(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        client = LoopBoundClient()
        assert connect_started is not None
        assert release_connect is not None
        connect_started.set()
        await release_connect.wait()
        return client

    monkeypatch.setattr(temporal_client, "_get_loop_client_state", get_state)
    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    async def cancel_waiter_then_release_owner():
        nonlocal connect_started, release_connect
        connect_started = asyncio.Event()
        release_connect = asyncio.Event()
        owner = asyncio.create_task(temporal_client.get_client())
        await asyncio.wait_for(connect_started.wait(), timeout=2)
        waiter = asyncio.create_task(temporal_client.get_client())
        assert await asyncio.to_thread(waiter_registered.wait, 5)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not owner.done()

        release_connect.set()
        connected = await asyncio.wait_for(owner, timeout=2)
        assert await temporal_client.get_client() is connected
        await connected.ping()
        return connected

    connected = asyncio.run(cancel_waiter_then_release_owner())
    assert connected.operation_loops == [connected.creation_loop]
    assert call_count == 1


@pytest.mark.unit
def test_reset_during_bridge_initialization_does_not_publish_stale_client(
    monkeypatch,
    no_temporal_interceptors,
):
    connect_started = threading.Event()
    release_connect = threading.Event()
    connected_clients = []

    async def connect(*_args, **_kwargs):
        client = LoopBoundClient()
        connected_clients.append(client)
        if len(connected_clients) == 1:
            connect_started.set()
            assert await asyncio.to_thread(release_connect.wait, 5)
        return client

    monkeypatch.setattr(temporal_client.Client, "connect", staticmethod(connect))

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_request = executor.submit(
            temporal_client._run_async_in_sync_context,
            _get_client_and_ping,
        )
        assert connect_started.wait(timeout=5)
        temporal_client.reset_client()
        release_connect.set()
        stale_client = stale_request.result(timeout=2)

    current_client = temporal_client._run_async_in_sync_context(_get_client_and_ping)

    assert stale_client is connected_clients[0]
    assert current_client is connected_clients[1]
    assert stale_client is not current_client
    assert stale_client.creation_loop is current_client.creation_loop
    assert stale_client.operation_loops == [stale_client.creation_loop]
    assert current_client.operation_loops == [current_client.creation_loop]
