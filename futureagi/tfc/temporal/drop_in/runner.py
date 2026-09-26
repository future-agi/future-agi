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
from typing import Any

from tfc.logging.temporal import get_logger

logger = get_logger(__name__)


def start_activity(
    activity_name: str,
    args: tuple = (),
    kwargs: dict | None = None,
    queue: str = "default",
    task_id: str | None = None,
    id_conflict_policy: Any | None = None,
    start_delay: Any | None = None,
    dispatch_timeout_seconds: float | None = None,
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
        dispatch_timeout_seconds: Optional client-side deadline for starting
            the workflow. It does not change the activity execution timeout.

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
        from tfc.temporal.common.client import _run_async_in_sync_context

        async def dispatch() -> str:
            coroutine = _start_activity_async(
                activity_name,
                args,
                kwargs,
                temporal_queue,
                task_id,
                id_conflict_policy,
                start_delay,
            )
            if dispatch_timeout_seconds is not None:
                return await asyncio.wait_for(
                    coroutine,
                    timeout=max(float(dispatch_timeout_seconds), 0.001),
                )
            return await coroutine

        result = _run_async_in_sync_context(dispatch)
        logger.info(
            "start_activity_completed",
            activity_name=activity_name,
            workflow_id=result,
            context="sync_bridge",
        )
        return result
    except Exception as e:
        logger.exception(
            "start_activity_failed", activity_name=activity_name, error=str(e)
        )
        raise


async def _start_activity_async(
    activity_name: str,
    args: tuple,
    kwargs: dict,
    queue: str,
    task_id: str,
    id_conflict_policy: Any | None = None,
    start_delay: Any | None = None,
) -> str:
    """Async implementation of start_activity."""
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
        # Only pass these when explicitly provided so every existing caller
        # resolves to the exact same start_workflow call (zero blast radius).
        _extra_start_kwargs: dict = {}
        if id_conflict_policy is not None:
            _extra_start_kwargs["id_conflict_policy"] = id_conflict_policy
        if start_delay is not None:
            _extra_start_kwargs["start_delay"] = start_delay
        await client.start_workflow(
            TaskRunnerWorkflow.run,
            TaskRunnerInput(
                activity_name=activity_name,
                args=safe_args,
                kwargs=safe_kwargs,
                queue=queue,
                time_limit=activity_metadata.get("time_limit"),
                max_retries=activity_metadata.get("max_retries"),
                retry_delay=activity_metadata.get("retry_delay"),
                schedule_to_start_timeout=activity_metadata.get(
                    "schedule_to_start_timeout"
                ),
            ),
            id=workflow_id,
            task_queue=queue,
            # Existing activities retain the historical 24h/13h defaults.
            # Long-queued activities can opt into validated metadata overrides.
            execution_timeout=timedelta(
                seconds=activity_metadata.get("workflow_execution_timeout")
                or 24 * 60 * 60
            ),
            run_timeout=timedelta(
                seconds=activity_metadata.get("workflow_run_timeout") or 13 * 60 * 60
            ),
            **_extra_start_kwargs,
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
    args: tuple = (),
    kwargs: dict | None = None,
    queue: str = "default",
    task_id: str | None = None,
) -> str:
    """
    Synchronous version of start_activity.
    Uses the persistent synchronous Temporal bridge loop.
    """
    return start_activity(
        activity_name,
        args=args,
        kwargs=kwargs,
        queue=queue,
        task_id=task_id,
    )


async def start_activity_async(
    activity_name: str,
    args: tuple = (),
    kwargs: dict | None = None,
    queue: str = "default",
    task_id: str | None = None,
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
