"""
Temporal client utilities.

Provides singleton client management and generic workflow helpers.
Domain-specific workflow starters should be in their respective feature's client.py
(e.g., tfc/temporal/experiments/client.py)
"""

import asyncio
import concurrent.futures
from collections.abc import Callable
from typing import Any, TypeVar

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy

T = TypeVar("T")


def _run_async_in_sync_context(async_coro_fn: Callable[[], Any]) -> Any:
    """
    Run an async coroutine from a sync context, handling event loop detection.

    If already in an async context (running event loop), runs in a thread pool
    with OTel context propagation. Otherwise, uses asyncio.run() directly.

    Args:
        async_coro_fn: A zero-argument function that returns a coroutine

    Returns:
        The result of the coroutine
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # In async context - run in thread pool with OTel context propagation
        def run_in_new_loop():
            return asyncio.run(async_coro_fn())

        try:
            from tfc.telemetry import wrap_for_thread

            wrapped_run = wrap_for_thread(run_in_new_loop)
        except ImportError:
            wrapped_run = run_in_new_loop

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(wrapped_run)
            return future.result()
    else:
        return asyncio.run(async_coro_fn())


# =============================================================================
# Singleton Client
# =============================================================================

_client: Client | None = None
_client_lock = asyncio.Lock()


def reset_client() -> None:
    """
    Reset the cached Temporal client.

    Call this after init_telemetry() if you need to recreate the client
    with a properly configured TracingInterceptor.

    WARNING: This will close any existing client connections.
    """
    global _client
    if _client is not None:
        import structlog

        logger = structlog.get_logger(__name__)
        logger.info("temporal_client_reset")
        _client = None


async def get_client() -> Client:
    """
    Get a connected Temporal client (singleton).

    Thread-safe and handles concurrent initialization.
    OpenTelemetry tracing is automatically enabled via TracingInterceptor.
    """
    global _client

    if _client is not None:
        return _client

    async with _client_lock:
        # Double-check after acquiring lock
        if _client is not None:
            return _client

        import structlog

        logger = structlog.get_logger(__name__)

        # Get OpenTelemetry tracing interceptors (handles logging internally)
        from tfc.telemetry.temporal import get_interceptors_for_client
        from tfc.temporal import TEMPORAL_HOST, TEMPORAL_NAMESPACE

        interceptors = get_interceptors_for_client()

        _client = await Client.connect(
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
        return _client


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


async def get_workflow_status_async(workflow_id: str) -> dict | None:
    """
    Get the status of a workflow.

    Args:
        workflow_id: The workflow ID

    Returns:
        Dict with workflow status info, or None if not found
    """
    client = await get_client()

    try:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()

        return {
            "workflow_id": workflow_id,
            "run_id": description.run_id,
            "status": str(description.status),
            "start_time": (
                description.start_time.isoformat() if description.start_time else None
            ),
            "close_time": (
                description.close_time.isoformat() if description.close_time else None
            ),
        }
    except Exception:
        return None


def get_workflow_status_sync(workflow_id: str) -> dict | None:
    """Get workflow status synchronously with OTel context propagation."""
    return _run_async_in_sync_context(lambda: get_workflow_status_async(workflow_id))


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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run, get_workflow_result_async(workflow_id, timeout)
            )
            return future.result(timeout=timeout + 10)  # Extra buffer for setup
    else:
        return asyncio.run(get_workflow_result_async(workflow_id, timeout))


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
