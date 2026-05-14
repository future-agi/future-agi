"""
Helper functions to start Temporal activities (replacing Celery's apply_async/delay).

Usage:
    # Before (Celery)
    my_task.apply_async(args=(arg1, arg2), queue="tasks_l")

    # After (Temporal)
    start_activity("my_task", args=(arg1, arg2), queue="tasks_l")

    # Or using the decorated function directly:
    my_task.apply_async(args=(arg1, arg2))  # Works the same!
"""

import asyncio
import uuid
from datetime import timedelta
from typing import Any, Optional, Tuple

from tfc.logging.temporal import get_logger

logger = get_logger(__name__)


def start_activity(
    activity_name: str,
    args: Tuple = (),
    kwargs: Optional[dict] = None,
    queue: str = "default",
    task_id: Optional[str] = None,
) -> str:
    """
    Start a Temporal activity (drop-in replacement for Celery's apply_async).

    This starts a TaskRunnerWorkflow that executes the specified activity.

    Args:
        activity_name: Name of the activity to run
        args: Positional arguments to pass to the activity
        kwargs: Keyword arguments to pass to the activity
        queue: Task queue to use
        task_id: Optional workflow ID (auto-generated if not provided)

    Returns:
        The workflow ID

    Example:
        # Start an activity
        workflow_id = start_activity(
            "process_evaluation_single_task",
            args=({"eval_id": "123", "type": "single"},),
            queue="tasks_l"
        )
    """
    kwargs = kwargs or {}
    task_id = task_id or f"{activity_name}-{uuid.uuid4().hex[:8]}"

    # Map common Celery queue names to Temporal queue names
    queue_mapping = {
        "tasks_s": "tasks_s",
        "tasks_l": "tasks_l",
        "tasks_xl": "tasks_xl",
        "default": "default",
        "trace_ingestion": "trace_ingestion",
        "agent_compass": "agent_compass",
    }
    temporal_queue = queue_mapping.get(queue, queue)

    logger.info(
        "start_activity_called",
        activity_name=activity_name,
        queue=queue,
        temporal_queue=temporal_queue,
        task_id=task_id,
    )

    try:
        # Check if there's a running event loop
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context - need to run synchronously in a new thread
            # to avoid blocking the event loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _start_activity_async(
                        activity_name, args, kwargs, temporal_queue, task_id
                    ),
                )
                result = future.result(timeout=30)  # 30 second timeout
                logger.info(
                    "start_activity_completed",
                    activity_name=activity_name,
                    workflow_id=result,
                    context="async",
                )
                return result
        except RuntimeError:
            # No running loop, create one - this is the normal case for sync Django views
            result = asyncio.run(
                _start_activity_async(
                    activity_name, args, kwargs, temporal_queue, task_id
                )
            )
            logger.info(
                "start_activity_completed",
                activity_name=activity_name,
                workflow_id=result,
                context="sync",
            )
            return result
    except Exception as e:
        logger.exception(
            "start_activity_failed", activity_name=activity_name, error=str(e)
        )
        raise


async def _start_activity_async(
    activity_name: str,
    args: Tuple,
    kwargs: dict,
    queue: str,
    task_id: str,
) -> str:
    """Async implementation of start_activity."""
    from temporalio.client import Client

    from tfc.temporal.common.client import get_client
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.drop_in.workflow import TaskRunnerInput, TaskRunnerWorkflow

    activity_metadata = _ACTIVITY_REGISTRY.get(activity_name, {})

    logger.info(
        "start_activity_async_called",
        activity_name=activity_name,
        queue=queue,
        task_id=task_id,
    )
    logger.debug(
        "activity_args",
        activity_name=activity_name,
        args_count=len(args),
        kwargs_keys=list(kwargs.keys()),
    )

    try:
        logger.info("getting_temporal_client")
        client = await get_client()
        logger.info("temporal_client_connected", namespace=client.namespace)

        workflow_id = f"task-{task_id}"

        # Pre-check: ensure all args/kwargs are JSON-serializable before
        # sending to Temporal.  Converts any stray Django models, UUIDs,
        # Decimals, datetimes, etc. to strings via json round-trip.
        import json as _json

        def _make_serializable(obj):
            """Round-trip through JSON to strip non-serializable types."""
            try:
                return _json.loads(_json.dumps(obj, default=str))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "temporal_arg_not_serializable",
                    activity_name=activity_name,
                    error=str(exc),
                    obj_type=type(obj).__name__,
                )
                return str(obj)

        safe_args = [_make_serializable(a) for a in args]
        safe_kwargs = _make_serializable(kwargs)

        logger.info(
            "starting_workflow",
            workflow_id=workflow_id,
            queue=queue,
            activity_name=activity_name,
        )
        handle = await client.start_workflow(
            TaskRunnerWorkflow.run,
            TaskRunnerInput(
                activity_name=activity_name,
                args=safe_args,
                kwargs=safe_kwargs,
                queue=queue,
                max_retries=activity_metadata.get("max_retries"),
                retry_delay=activity_metadata.get("retry_delay"),
            ),
            id=workflow_id,
            task_queue=queue,
            # Prevent stuck workflows - auto-timeout after 24 hours
            execution_timeout=timedelta(hours=24),
            # Prevent single run from running forever - 13 hours max
            run_timeout=timedelta(hours=13),
        )

        logger.info(
            "workflow_started",
            workflow_id=workflow_id,
            activity_name=activity_name,
        )
        return workflow_id

    except Exception as e:
        logger.exception(
            "start_activity_async_failed",
            activity_name=activity_name,
            error=str(e),
        )
        raise


def start_activity_sync(
    activity_name: str,
    args: Tuple = (),
    kwargs: Optional[dict] = None,
    queue: str = "default",
    task_id: Optional[str] = None,
) -> str:
    """
    Synchronous version of start_activity.
    Always creates a new event loop.
    """
    kwargs = kwargs or {}
    task_id = task_id or f"{activity_name}-{uuid.uuid4().hex[:8]}"

    queue_mapping = {
        "tasks_s": "tasks_s",
        "tasks_l": "tasks_l",
        "tasks_xl": "tasks_xl",
        "default": "default",
        "trace_ingestion": "trace_ingestion",
        "agent_compass": "agent_compass",
    }
    temporal_queue = queue_mapping.get(queue, queue)

    return asyncio.run(
        _start_activity_async(activity_name, args, kwargs, temporal_queue, task_id)
    )


async def start_activity_async(
    activity_name: str,
    args: Tuple = (),
    kwargs: Optional[dict] = None,
    queue: str = "default",
    task_id: Optional[str] = None,
) -> str:
    """
    Async version of start_activity.
    Use this when you're already in an async context.
    """
    kwargs = kwargs or {}
    task_id = task_id or f"{activity_name}-{uuid.uuid4().hex[:8]}"

    queue_mapping = {
        "tasks_s": "tasks_s",
        "tasks_l": "tasks_l",
        "tasks_xl": "tasks_xl",
        "default": "default",
        "trace_ingestion": "trace_ingestion",
        "agent_compass": "agent_compass",
    }
    temporal_queue = queue_mapping.get(queue, queue)

    return await _start_activity_async(
        activity_name, args, kwargs, temporal_queue, task_id
    )


__all__ = [
    "start_activity",
    "start_activity_sync",
    "start_activity_async",
]
