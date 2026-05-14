"""
Drop-in replacement decorator for Celery tasks.

Usage:
    # Before (Celery)
    @celery_app.task(bind=True, time_limit=3600, queue="tasks_l")
    def my_task(self, arg1, arg2):
        ...

    # After (Temporal)
    @temporal_activity(time_limit=3600, queue="tasks_l")
    def my_task(arg1, arg2):
        ...
"""

import functools
from typing import Any, Callable, Optional

from django.db import close_old_connections

# Global registry of activities
_ACTIVITY_REGISTRY: dict[str, dict[str, Any]] = {}

# Flag to track if temporalio activity decorator has been applied
_ACTIVITY_WRAPPERS: dict[str, Callable] = {}


class AsyncResult:
    """
    Celery-compatible AsyncResult class for Temporal workflows.
    Mimics Celery's AsyncResult so code using task.id continues to work.
    """

    def __init__(self, workflow_id: str):
        self._id = workflow_id

    @property
    def id(self) -> str:
        return self._id

    def __str__(self) -> str:
        return self._id

    def __repr__(self) -> str:
        return f"AsyncResult(id={self._id!r})"


def temporal_activity(
    time_limit: int = 3600,
    queue: str = "default",
    max_retries: Optional[int] = None,
    retry_delay: Optional[int] = None,
    rate_limit: Optional[str] = None,
    name: Optional[str] = None,
):
    """
    Drop-in replacement decorator for @celery_app.task.

    Args:
        time_limit: Maximum execution time in seconds (maps to start_to_close_timeout)
        queue: Task queue name (maps to Temporal task queue)
        max_retries: Maximum retry attempts. None (default) means use the
            workflow-level DEFAULT_RETRY_POLICY. Set explicitly (e.g. 0) to
            override per-activity.
        retry_delay: Delay between retries in seconds. None (default) means
            use the workflow-level default. Only consulted when
            max_retries is set explicitly.
        rate_limit: Rate limit string (e.g., "100/s") - stored for reference
        name: Activity name (defaults to function name)

    Example:
        @temporal_activity(time_limit=3600, queue="tasks_l")
        def process_data(data_id: str):
            # Your existing sync code
            ...
    """

    def decorator(func: Callable) -> Callable:
        activity_name = name or func.__name__

        # Store metadata for later use
        _ACTIVITY_REGISTRY[activity_name] = {
            "func": func,
            "time_limit": time_limit,
            "queue": queue,
            "max_retries": max_retries,
            "retry_delay": retry_delay,
            "rate_limit": rate_limit,
        }

        # Log registration for debugging (using structlog - safe for async)
        from tfc.logging.temporal import get_logger

        get_logger(__name__).debug(
            "temporal_activity_registered", activity_name=activity_name, queue=queue
        )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            """Sync wrapper that handles DB connections."""
            close_old_connections()
            try:
                return func(*args, **kwargs)
            finally:
                close_old_connections()

        # Create a placeholder wrapper that will be converted to a real activity
        # when the worker starts (lazy initialization to avoid protobuf conflicts)
        @functools.wraps(func)
        def activity_placeholder(*args, **kwargs):
            """Placeholder that runs sync when called directly."""
            return sync_wrapper(*args, **kwargs)

        # Store references
        activity_placeholder._original_func = func
        activity_placeholder._sync_wrapper = sync_wrapper
        activity_placeholder._activity_name = activity_name
        activity_placeholder._metadata = _ACTIVITY_REGISTRY[activity_name]
        activity_placeholder._is_temporal_activity = True

        # Add helper methods to mimic Celery interface
        def apply_async(args=None, kwargs=None, queue=None, **options):
            """
            Mimic Celery's apply_async. Starts a Temporal workflow to run this activity.
            Returns an AsyncResult object with .id attribute for Celery compatibility.
            """
            from tfc.logging.temporal import get_logger

            log = get_logger(__name__)

            try:
                from tfc.temporal.drop_in.runner import start_activity

                target_queue = queue or _ACTIVITY_REGISTRY[activity_name]["queue"]
                log.info(
                    "apply_async_called",
                    activity_name=activity_name,
                    args_count=len(args) if args else 0,
                    queue=target_queue,
                )

                workflow_id = start_activity(
                    activity_name,
                    args=args or (),
                    kwargs=kwargs or {},
                    queue=target_queue,
                )
                log.info(
                    "apply_async_completed",
                    activity_name=activity_name,
                    workflow_id=workflow_id,
                )
                # Wrap in AsyncResult for Celery compatibility (task.id works)
                return AsyncResult(workflow_id)
            except Exception as e:
                log.exception(
                    "apply_async_failed", activity_name=activity_name, error=str(e)
                )
                raise

        def delay(*args, **kwargs):
            """Mimic Celery's delay. Shortcut for apply_async."""
            return apply_async(args=args, kwargs=kwargs)

        # Attach methods to the wrapper
        activity_placeholder.apply_async = apply_async
        activity_placeholder.delay = delay
        activity_placeholder.name = activity_name

        # Also allow direct sync call (for testing or direct execution)
        activity_placeholder.run_sync = sync_wrapper

        return activity_placeholder

    return decorator


def _create_real_activity(activity_name: str, sync_wrapper: Callable) -> Callable:
    """
    Create the real temporalio activity wrapper.
    Called when worker starts to register activities.
    """
    from temporalio import activity

    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.common.heartbeat import Heartbeater

    @activity.defn(name=activity_name)
    async def activity_wrapper(input_data: dict) -> Any:
        """Async activity wrapper with automatic heartbeating."""
        args = input_data.get("args", ())
        kwargs = input_data.get("kwargs", {})

        activity.logger.info(
            f"Activity '{activity_name}' starting: args_count={len(args)}, kwargs_keys={list(kwargs.keys())}"
        )

        try:
            # Use Heartbeater to automatically send periodic heartbeats
            # This prevents timeout for long-running sync activities
            async with Heartbeater(
                details=(activity_name, "processing")
            ) as heartbeater:
                # Use otel_sync_to_async to propagate OTel context to the sync thread.
                # This uses attach/detach (not copy_context) to avoid the
                # "Token was created in a different Context" error.
                # LLM spans will now appear as children of the activity span.
                result = await otel_sync_to_async(sync_wrapper, thread_sensitive=False)(
                    *args, **kwargs
                )
                heartbeater.details = (activity_name, "completed")

            activity.logger.info(f"Activity '{activity_name}' completed successfully")
            return {"result": result, "status": "completed"}

        except Exception as e:
            activity.logger.error(f"Activity '{activity_name}' FAILED: {str(e)}")
            raise

    return activity_wrapper


def _make_db_safe_wrapper(func: Callable) -> Callable:
    """Create a wrapper that handles DB connections properly."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapper


def get_temporal_activities() -> list[Callable]:
    """
    Get all registered activities as real temporalio activity functions.
    Call this from the worker to get activities for registration.

    This lazily creates the real @activity.defn wrappers to avoid
    protobuf conflicts at import time.
    """
    from tfc.logging.temporal import get_logger

    log = get_logger(__name__)

    activities = []
    for activity_name, info in _ACTIVITY_REGISTRY.items():
        if activity_name not in _ACTIVITY_WRAPPERS:
            original_func = info["func"]
            # Create wrapper that handles DB connections
            safe_wrapper = _make_db_safe_wrapper(original_func)
            _ACTIVITY_WRAPPERS[activity_name] = _create_real_activity(
                activity_name, safe_wrapper
            )
            log.debug("temporal_activity_wrapper_created", activity_name=activity_name)
        activities.append(_ACTIVITY_WRAPPERS[activity_name])

    log.info(
        "get_temporal_activities_complete",
        activity_count=len(activities),
        activities=list(_ACTIVITY_WRAPPERS.keys()),
    )
    return activities


def get_registered_activities() -> list[Callable]:
    """Get all registered activity functions for worker registration."""
    return [
        info["func"]._activity_wrapper
        for info in _ACTIVITY_REGISTRY.values()
        if hasattr(info["func"], "_activity_wrapper")
    ]


def get_activity_by_name(name: str) -> Optional[dict]:
    """Get activity metadata by name."""
    return _ACTIVITY_REGISTRY.get(name)


def get_all_activity_functions() -> list[Callable]:
    """
    Get all activity wrapper functions for worker registration.
    Call this after all modules with @temporal_activity are imported.
    """
    activities = []
    for activity_name, info in _ACTIVITY_REGISTRY.items():
        # The activity_wrapper is stored during decoration
        # We need to find it - it should be the decorated function
        func = info["func"]
        # Look for the activity wrapper in the module
        import sys

        for module in sys.modules.values():
            if module and hasattr(module, func.__name__):
                attr = getattr(module, func.__name__)
                if (
                    hasattr(attr, "_activity_name")
                    and attr._activity_name == activity_name
                ):
                    activities.append(attr)
                    break
    return activities


__all__ = [
    "temporal_activity",
    "get_temporal_activities",
    "get_registered_activities",
    "get_activity_by_name",
    "get_all_activity_functions",
    "_ACTIVITY_REGISTRY",
]
