"""
Temporal client utilities.

Provides singleton client management and generic workflow helpers.
Domain-specific workflow starters should be in their respective feature's client.py
(e.g., tfc/temporal/experiments/client.py)
"""

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

T = TypeVar("T")

_sync_bridge_state_lock = threading.Lock()
_sync_bridge_loop: asyncio.AbstractEventLoop | None = None
_sync_bridge_thread: threading.Thread | None = None
_sync_bridge_ready: threading.Event | None = None
_sync_bridge_pid = os.getpid()


def _run_sync_bridge_loop(ready: threading.Event) -> None:
    """Own the process-wide event loop used by all synchronous callers."""
    global _sync_bridge_loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    with _sync_bridge_state_lock:
        _sync_bridge_loop = loop

    # Set readiness from inside the running loop.  Callers can safely submit
    # work as soon as the event is visible.
    loop.call_soon(ready.set)
    loop.run_forever()


def _get_sync_bridge_loop() -> asyncio.AbstractEventLoop:
    """Start (once) and return the daemon loop for synchronous bridges."""
    global _sync_bridge_loop, _sync_bridge_ready, _sync_bridge_thread

    if os.getpid() != _sync_bridge_pid:
        # ``register_at_fork`` handles normal forks.  Keep this PID guard for
        # runtimes that fork before registration or do not expose the hook.
        _reset_temporal_state_after_fork()

    with _sync_bridge_state_lock:
        thread = _sync_bridge_thread
        if thread is None or not thread.is_alive():
            # Normal prefork deployment must make its first Temporal connection
            # in the final child process.  The PID/at-fork reset below protects
            # our Python references and locks; it cannot reset Temporal SDK's
            # module-global Runtime.default() after that runtime was used.
            ready = threading.Event()
            thread = threading.Thread(
                target=_run_sync_bridge_loop,
                args=(ready,),
                name="temporal-sync-bridge",
                daemon=True,
            )
            _sync_bridge_loop = None
            _sync_bridge_ready = ready
            _sync_bridge_thread = thread
            thread.start()
        else:
            ready = _sync_bridge_ready

    if ready is None or not ready.wait(timeout=5):
        raise RuntimeError("Timed out starting the Temporal sync bridge loop")

    with _sync_bridge_state_lock:
        loop = _sync_bridge_loop
        thread = _sync_bridge_thread
    if loop is None or thread is None or not thread.is_alive() or not loop.is_running():
        raise RuntimeError("Temporal sync bridge loop is not running")
    return loop


def _run_async_in_sync_context(
    async_coro_fn: Callable[[], Coroutine[Any, Any, T]],
) -> T:
    """
    Run an async coroutine on the process-wide synchronous bridge loop.

    Temporal clients are event-loop-bound.  Routing every synchronous bridge
    through one persistent loop lets all Django request threads safely reuse
    that loop's client instead of creating a client on a short-lived
    ``asyncio.run`` loop.  ``run_coroutine_threadsafe`` schedules with the
    caller's context, preserving OpenTelemetry contextvars.

    Args:
        async_coro_fn: A zero-argument function that returns a coroutine

    Returns:
        The result of the coroutine
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    bridge_loop = _get_sync_bridge_loop()
    if running_loop is bridge_loop:
        raise RuntimeError(
            "A synchronous Temporal helper cannot run on the Temporal sync bridge "
            "loop; use its async counterpart"
        )

    coroutine = async_coro_fn()
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, bridge_loop)
    except BaseException:
        coroutine.close()
        raise

    try:
        return future.result()
    except BaseException:
        # Propagate caller cancellation/interruption to work queued on the
        # bridge rather than leaving an orphaned Temporal operation behind.
        future.cancel()
        raise


# =============================================================================
# Per-event-loop Client
# =============================================================================

_LOOP_CLIENT_STATE_ATTRIBUTE = "_futureagi_temporal_client_state"


@dataclass
class _LoopClientState:
    generation: int
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: Client | None = None


_client_generation = 0
_client_generation_lock = threading.Lock()
_has_current_client = False


def _reset_temporal_state_after_fork() -> None:
    """Discard inherited Python loop/client state and possibly locked mutexes.

    This is defense-in-depth for a normal lazy-connect prefork deployment.  It
    does not make fork-after-Temporal-use supported because the SDK's
    process-global ``Runtime.default()`` cannot be reset here.
    """
    global _client_generation, _client_generation_lock, _has_current_client
    global _sync_bridge_loop, _sync_bridge_pid, _sync_bridge_ready
    global _sync_bridge_state_lock, _sync_bridge_thread

    # Only the forking thread survives in the child.  Never acquire inherited
    # locks here: their owner may have been another thread in the parent.
    _sync_bridge_state_lock = threading.Lock()
    _sync_bridge_loop = None
    _sync_bridge_thread = None
    _sync_bridge_ready = None
    _sync_bridge_pid = os.getpid()

    _client_generation_lock = threading.Lock()
    _client_generation += 1
    _has_current_client = False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_temporal_state_after_fork)


def _get_loop_client_state(
    loop: asyncio.AbstractEventLoop,
) -> _LoopClientState:
    """Return state owned exclusively by ``loop`` for the reset generation."""
    with _client_generation_lock:
        generation = _client_generation

    state = getattr(loop, _LOOP_CLIENT_STATE_ATTRIBUTE, None)
    if not isinstance(state, _LoopClientState) or state.generation != generation:
        state = _LoopClientState(generation=generation)
        setattr(loop, _LOOP_CLIENT_STATE_ATTRIBUTE, state)
    return state


def reset_client() -> None:
    """
    Reset the cached Temporal client.

    Call this after init_telemetry() if you need to recreate the client
    with a properly configured TracingInterceptor.

    Existing in-flight callers may finish on their current client.  The next
    request on each event loop creates a client for the new generation.
    """
    global _client_generation, _has_current_client
    with _client_generation_lock:
        had_client = _has_current_client
        _client_generation += 1
        _has_current_client = False
    if had_client:
        import structlog

        logger = structlog.get_logger(__name__)
        logger.info("temporal_client_reset")


async def get_client() -> Client:
    """
    Get a connected Temporal client owned by the current event loop.

    Concurrent tasks on the same loop share one initialization.  Different
    event loops never share a Temporal client or an asyncio synchronization
    primitive.  Synchronous helpers all use the persistent bridge loop above.
    OpenTelemetry tracing is automatically enabled via TracingInterceptor.
    """
    global _has_current_client

    loop = asyncio.get_running_loop()
    while True:
        state = _get_loop_client_state(loop)
        async with state.lock:
            # reset_client() can invalidate a state while this task waits for
            # its loop-local lock.  Move to the replacement generation before
            # reading or initializing a client.
            if _get_loop_client_state(loop) is not state:
                continue
            if state.client is not None:
                return state.client

            import structlog

            logger = structlog.get_logger(__name__)

            # Get OpenTelemetry tracing interceptors (handles logging internally)
            from tfc.telemetry.temporal import get_interceptors_for_client
            from tfc.temporal import TEMPORAL_HOST, TEMPORAL_NAMESPACE

            interceptors = get_interceptors_for_client()

            connected_client = await Client.connect(
                TEMPORAL_HOST,
                namespace=TEMPORAL_NAMESPACE,
                interceptors=interceptors,
            )
            logger.info(
                "temporal_client_connected",
                host=TEMPORAL_HOST,
                namespace=TEMPORAL_NAMESPACE,
                interceptor_count=len(interceptors),
            )

            # A reset during Client.connect detaches this generation.  The
            # initiating caller may finish, but stale state must not repopulate
            # the loop cache for subsequent requests.
            with _client_generation_lock:
                if state.generation == _client_generation:
                    state.client = connected_client
                    _has_current_client = True
            return connected_client


def get_client_sync() -> Client:
    """
    Get a connected Temporal client synchronously.

    Handles running in both sync and async contexts (e.g., Django views, ASGI).
    Propagates OpenTelemetry context to maintain trace connectivity.
    """
    return _run_async_in_sync_context(get_client)


# =============================================================================
# Generic Workflow Helpers
# =============================================================================


async def start_workflow_async(
    workflow_class,
    workflow_input: Any,
    workflow_id: str,
    task_queue: str,
    *,
    cancel_existing: bool = True,
    id_reuse_policy: WorkflowIDReusePolicy = WorkflowIDReusePolicy.TERMINATE_IF_RUNNING,
    id_conflict_policy: WorkflowIDConflictPolicy = (
        WorkflowIDConflictPolicy.UNSPECIFIED
    ),
) -> WorkflowHandle:
    """
    Start a workflow asynchronously with common options.

    Args:
        workflow_class: The workflow class (e.g., RunExperimentWorkflow)
        workflow_input: Input dataclass for the workflow
        workflow_id: Unique workflow identifier
        task_queue: Temporal task queue name
        cancel_existing: If True, cancel existing running workflow first
        id_reuse_policy: Policy for reusing workflow IDs
        id_conflict_policy: Policy when the workflow ID is already running

    Returns:
        WorkflowHandle for the started workflow
    """
    client = await get_client()

    if cancel_existing:
        try:
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()

            if description.status == WorkflowExecutionStatus.RUNNING:
                await handle.cancel()
                await asyncio.sleep(0.5)  # Allow cancellation to propagate
        except Exception:
            pass  # Workflow doesn't exist or other error

    return await client.start_workflow(
        workflow_class.run,
        workflow_input,
        id=workflow_id,
        task_queue=task_queue,
        id_reuse_policy=id_reuse_policy,
        id_conflict_policy=id_conflict_policy,
    )


def start_workflow_sync(
    workflow_class,
    workflow_input: Any,
    workflow_id: str,
    task_queue: str,
    **kwargs,
) -> WorkflowHandle:
    """
    Start a workflow synchronously.

    Convenience wrapper for Django views and other sync contexts.
    Propagates OpenTelemetry context to maintain trace connectivity.
    """
    return _run_async_in_sync_context(
        lambda: start_workflow_async(
            workflow_class, workflow_input, workflow_id, task_queue, **kwargs
        )
    )


async def get_workflow_status_async(
    workflow_id: str,
    timeout_seconds: float | None = None,
) -> dict | None:
    """
    Get the status of a workflow.

    Args:
        workflow_id: The workflow ID

    Returns:
        Dict with workflow status info, or None if not found
    """
    try:

        async def _describe():
            client = await get_client()
            handle = client.get_workflow_handle(workflow_id)
            return await handle.describe()

        if timeout_seconds is None:
            description = await _describe()
        else:
            description = await asyncio.wait_for(
                _describe(),
                timeout=max(0.001, float(timeout_seconds)),
            )

        return {
            "workflow_id": workflow_id,
            "run_id": description.run_id,
            "status": str(description.status),
            "status_name": description.status.name,
            "start_time": (
                description.start_time.isoformat() if description.start_time else None
            ),
            "close_time": (
                description.close_time.isoformat() if description.close_time else None
            ),
        }
    except Exception:
        return None


def get_workflow_status_sync(
    workflow_id: str,
    timeout_seconds: float | None = None,
) -> dict | None:
    """Get workflow status synchronously with OTel context propagation."""
    return _run_async_in_sync_context(
        lambda: get_workflow_status_async(workflow_id, timeout_seconds)
    )


async def cancel_workflow_async(workflow_id: str) -> bool:
    """
    Cancel a running workflow.

    Args:
        workflow_id: The workflow ID

    Returns:
        True if cancellation was requested, False if workflow not found
    """
    client = await get_client()

    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        return True
    except Exception:
        return False


def cancel_workflow_sync(workflow_id: str) -> bool:
    """Cancel a workflow synchronously with OTel context propagation."""
    return _run_async_in_sync_context(lambda: cancel_workflow_async(workflow_id))


async def signal_workflow_async(workflow_id: str, signal: str, *args) -> bool:
    """Send a signal to a running workflow.

    Returns True if delivered, False if the workflow isn't found / already
    closed (best-effort: callers fall back to the durable DB state).
    """
    client = await get_client()
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(signal, *args)
        return True
    except Exception:
        return False


def signal_workflow_sync(workflow_id: str, signal: str, *args) -> bool:
    """Signal a workflow synchronously with OTel context propagation."""
    return _run_async_in_sync_context(
        lambda: signal_workflow_async(workflow_id, signal, *args)
    )


async def get_workflow_result_async(workflow_id: str, timeout: float = 3600) -> Any:
    """
    Wait for a workflow to complete and return its result.

    Args:
        workflow_id: The workflow ID
        timeout: Maximum time to wait in seconds (default 1 hour)

    Returns:
        The workflow result

    Raises:
        Exception if workflow fails or times out
    """
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id)

    # Wait for result with timeout
    return await asyncio.wait_for(handle.result(), timeout=timeout)


def get_workflow_result_sync(workflow_id: str, timeout: float = 3600) -> Any:
    """
    Wait for a workflow to complete and return its result (sync version).

    Args:
        workflow_id: The workflow ID
        timeout: Maximum time to wait in seconds (default 1 hour)

    Returns:
        The workflow result

    Raises:
        Exception if workflow fails or times out
    """
    return _run_async_in_sync_context(
        lambda: get_workflow_result_async(workflow_id, timeout)
    )


__all__ = [
    "get_client",
    "get_client_sync",
    "start_workflow_async",
    "start_workflow_sync",
    "get_workflow_status_async",
    "get_workflow_status_sync",
    "cancel_workflow_async",
    "cancel_workflow_sync",
    "get_workflow_result_async",
    "get_workflow_result_sync",
]
