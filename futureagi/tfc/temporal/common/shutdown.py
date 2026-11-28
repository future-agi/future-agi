"""
Temporal worker shutdown monitoring utilities.

Provides:
- ShutdownMonitor: Monitors for worker shutdown signals
- WorkerShuttingDownError: Retryable exception for shutdown scenarios
"""

import asyncio
import contextvars
import threading
import typing

from temporalio import activity


class WorkerShuttingDownError(Exception):
    """Exception raised when a worker shutdown was issued.

    This should always be retried - Temporal will pick up the activity
    on another worker.
    """

    def __init__(
        self,
        activity_id: str,
        activity_type: str,
        task_queue: str,
        attempt: int,
        workflow_id: str | None,
        workflow_type: str | None,
    ):
        self.activity_id = activity_id
        self.activity_type = activity_type
        self.attempt = attempt
        self.task_queue = task_queue
        self.workflow_id = workflow_id
        self.workflow_type = workflow_type

        super().__init__(
            f"Activity <{activity_type}: {activity_id}> "
            f"from workflow <{workflow_type}: {workflow_id}> "
            f"on attempt {attempt} from queue '{task_queue}' "
            "is running on a worker that is shutting down"
        )

    @classmethod
    def from_activity_context(cls) -> typing.Self:
        """Initialize this exception from within an activity context."""
        info = activity.info()
        return cls(
            info.activity_id,
            info.activity_type,
            info.task_queue,
            info.attempt,
            info.workflow_id,
            info.workflow_type,
        )


class ShutdownMonitor:
    """Monitor for Temporal worker graceful shutdown.

    Handling shutdown is cooperative: Activities must actively check for
    shutdown by calling `is_worker_shutdown` or `raise_if_is_worker_shutdown`.

    Usage (async):
        async with ShutdownMonitor() as monitor:
            for item in items:
                monitor.raise_if_is_worker_shutdown()
                process(item)

    Usage (sync):
        with ShutdownMonitor() as monitor:
            for item in items:
                monitor.raise_if_is_worker_shutdown()
                process(item)

    All Temporal activities should consider `WorkerShuttingDownError` as a
    retryable exception.
    """

    def __init__(self):
        self._monitor_shutdown_task: asyncio.Task[None] | None = None
        self._monitor_shutdown_thread: threading.Thread | None = None
        self._is_shutdown_event = asyncio.Event()
        self._is_shutdown_event_sync = threading.Event()
        self._stop_event_sync = threading.Event()

    def __str__(self) -> str:
        """Return a string representation of this ShutdownMonitor."""
        if not self._monitor_shutdown_task and not self._monitor_shutdown_thread:
            return "<ShutdownMonitor: Not started>"

        if self.is_worker_shutdown():
            return "<ShutdownMonitor: Worker shutting down>"
        else:
            return "<ShutdownMonitor: Worker running>"

    def start(self):
        """Start an asyncio.Task to monitor for worker shutdown."""

        async def monitor() -> None:
            activity.logger.debug("Starting async shutdown monitoring task")

            try:
                await activity.wait_for_worker_shutdown()
            except RuntimeError:
                # Not running in an activity context
                return

            activity.logger.info("Worker shutdown detected (async)")
            self._is_shutdown_event.set()

        self._monitor_shutdown_task = asyncio.create_task(monitor())

    def start_sync(self):
        """Start a threading.Thread to monitor for worker shutdown.

        Copies context to preserve the activity context for the monitoring thread.
        """
        context = contextvars.copy_context()

        def monitor() -> None:
            activity.logger.debug("Starting sync shutdown monitoring thread")

            while not self._stop_event_sync.is_set():
                try:
                    activity.wait_for_worker_shutdown_sync(timeout=0.1)
                except RuntimeError:
                    # Not running in an activity context
                    return
                except Exception:
                    activity.logger.exception(
                        "Unknown error in shutdown monitor thread"
                    )
                    raise

                if activity.is_worker_shutdown():
                    activity.logger.info("Worker shutdown detected (sync)")
                    self._is_shutdown_event_sync.set()
                    break

        self._monitor_shutdown_thread = threading.Thread(
            target=context.run, args=(monitor,), daemon=True
        )
        self._monitor_shutdown_thread.start()

    def stop(self):
        """Cancel pending monitoring asyncio.Task."""
        if self._monitor_shutdown_task and not self._monitor_shutdown_task.done():
            self._monitor_shutdown_task.cancel()
            self._monitor_shutdown_task = None

    def stop_sync(self):
        """Cancel pending monitoring threading.Thread."""
        if self._monitor_shutdown_thread:
            self._stop_event_sync.set()
            self._monitor_shutdown_thread.join(timeout=1.0)
            self._monitor_shutdown_thread = None

    async def __aenter__(self) -> typing.Self:
        """Async context manager that manages monitoring task."""
        self.start()
        return self

    async def __aexit__(self, *args, **kwargs):
        """Stop pending monitoring tasks on context exit."""
        self.stop()

    def __enter__(self) -> typing.Self:
        """Context manager that manages monitoring thread."""
        self.start_sync()
        return self

    def __exit__(self, *args, **kwargs):
        """Stop pending monitoring threads on context exit."""
        self.stop_sync()

    async def wait_for_worker_shutdown(self) -> None:
        """Asynchronously wait for worker shutdown event."""
        await self._is_shutdown_event.wait()

    def wait_for_worker_shutdown_sync(self, timeout: float | None = None) -> bool:
        """Synchronously wait for worker shutdown event."""
        return self._is_shutdown_event_sync.wait(timeout)

    def is_worker_shutdown(self) -> bool:
        """Check if worker is shutting down."""
        return self._is_shutdown_event.is_set() or self._is_shutdown_event_sync.is_set()

    def raise_if_is_worker_shutdown(self):
        """Raise WorkerShuttingDownError if worker is shutting down.

        Call this in loops to allow graceful shutdown and task migration
        to another worker.
        """
        if self.is_worker_shutdown():
            activity.logger.debug("Worker is shutting down, raising exception")
            raise WorkerShuttingDownError.from_activity_context()


__all__ = [
    "ShutdownMonitor",
    "WorkerShuttingDownError",
]
