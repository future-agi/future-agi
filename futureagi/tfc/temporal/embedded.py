"""Run the Temporal worker inside the API process.

Enabled with ``FI_EMBEDDED_TEMPORAL_WORKER=true`` (the standalone install's ``app``
container). ``tfc.asgi`` starts the worker from the ASGI lifespan ``startup``
event and drains it on ``shutdown``, so the API and the worker share one
interpreter: Django, the URL graph and the activity modules are imported once
instead of once per container. The distributed install runs separate worker
containers and leaves this off.

Isolation from the HTTP event loop:

* The worker runs on its **own thread with its own asyncio loop**. A blocking
  call inside an ``async def`` activity stalls other activities, not HTTP
  requests.
* Every queue gets its own ``Worker``, with its own workflow-task pool
  (temporalio sizes it to the queue's workflow-task slots) and its own
  **activity thread pool sized to its activity slots**. The loop's default
  executor sends ``run_in_executor(None, ...)`` (which drop-in activities use
  via ``sync_to_async(thread_sensitive=False)``) to the pool of the queue the
  activity came from, so long ``tasks_xl`` work cannot starve
  ``trace_ingestion`` or ``tasks_s``.
* ``exact_aggregation`` gets a dedicated single-slot ``Worker``: the same
  admission boundary as the distributed install's ``worker-exact-aggregation``.
* Each activity runs inside an ``asgiref.sync.ThreadSensitiveContext``. Without
  it, every async-ORM call made by an activity is funnelled into asgiref's
  process-global ``SyncToAsync.single_thread_executor``, which Channels
  websocket consumers in the same process also use.

Failure handling, matching a separate worker container:

* The Workers run in one ``asyncio.TaskGroup``. A fatal error in one cancels
  and drains the others before anything is rebuilt, so no Worker outlives its
  siblings and two sets of pollers never run at once.
* Connection errors are retried with backoff (the Temporal server starts next
  to us). Configuration errors are fatal and are not retried.
* After every (re)connect the eval-task search attributes and the schedules are
  registered again. Both are idempotent, and the co-located server can come up
  after us or restart.
* :meth:`EmbeddedTemporalWorker.health` feeds ``/health/`` (see ``tfc.asgi``),
  which answers 503 once the worker is fatal or has been down for
  ``FI_EMBEDDED_WORKER_UNHEALTHY_AFTER_SECONDS``.
* SIGTERM starts the Temporal drain right away, concurrently with the server's
  HTTP drain, instead of after it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import os
import signal
import threading
import time
from datetime import timedelta
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")

EXACT_AGGREGATION_QUEUE = "exact_aggregation"

# Activity thread pool of the queue whose activity runs in the current task.
# temporalio runs each activity in its own task, so setting it in the activity
# interceptor scopes it to that one activity.
_activity_pool: contextvars.ContextVar[concurrent.futures.Executor | None] = (
    contextvars.ContextVar("fi_embedded_activity_pool", default=None)
)


def enabled() -> bool:
    return os.getenv("FI_EMBEDDED_TEMPORAL_WORKER", "").strip().lower() in _TRUE


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


# Activity and workflow-task slots per generic queue. FI_APP_* wins: a .env
# carried over from the distributed install sets TEMPORAL_MAX_CONCURRENT_* to size one
# dedicated worker container per queue (50-200 slots), and applied to every
# queue of this one process that would open more Postgres connections than the
# standalone install's database allows. docker-compose.yml maps FI_APP_* onto
# TEMPORAL_MAX_CONCURRENT_* for the same reason.
DEFAULT_QUEUE_SLOTS = 8


def _queue_slots(name: str) -> int:
    own = f"FI_APP_{name}"
    if os.getenv(own, "").strip():
        return _env_int(own, DEFAULT_QUEUE_SLOTS)
    return _env_int(name, DEFAULT_QUEUE_SLOTS)


def _probe_interval() -> float:
    return max(1, _env_int("FI_EMBEDDED_WORKER_PROBE_SECONDS", 15))


def _retry_delay(consecutive_failures: int) -> float:
    # The count restarts at 0 once the Workers have run, so a crash after a
    # healthy period is retried after 1 s.
    return min(2.0 ** (consecutive_failures - 1), 30.0)


def _schedule_registration_enabled() -> bool:
    # Same switch the entrypoint honours; only an explicit false turns it off.
    value = os.getenv("REGISTER_TEMPORAL_SCHEDULES", "").strip().lower()
    return value not in _FALSE


class _QueuePlan(NamedTuple):
    queue: str
    workflows: list
    activities: list
    max_activities: int
    max_workflow_tasks: int


def _worker_plans() -> list[_QueuePlan]:
    """One plan per polled queue: the generic queues with every workflow and
    activity (like ``start_temporal_worker --all-queues``), then the dedicated
    exact-aggregation queue with only its own registrations and one slot."""
    from tfc.management.commands.start_temporal_worker import _generic_all_queues
    from tfc.temporal.common.registry import (
        get_activities_for_queue,
        get_all_activities,
        get_all_queues,
        get_all_workflows,
        get_workflows_for_queue,
    )

    excluded_env = os.getenv("TEMPORAL_EXCLUDED_QUEUES", "simulation_runner")
    excluded = {queue.strip() for queue in excluded_env.split(",") if queue.strip()}
    max_activities = _queue_slots("TEMPORAL_MAX_CONCURRENT_ACTIVITIES")
    max_workflow_tasks = _queue_slots("TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS")
    workflows = get_all_workflows()
    activities = get_all_activities()
    plans = [
        _QueuePlan(queue, workflows, activities, max_activities, max_workflow_tasks)
        for queue in sorted(_generic_all_queues(get_all_queues()))
        if queue not in excluded
    ]
    if EXACT_AGGREGATION_QUEUE not in excluded:
        plans.append(
            _QueuePlan(
                EXACT_AGGREGATION_QUEUE,
                get_workflows_for_queue(EXACT_AGGREGATION_QUEUE),
                get_activities_for_queue(EXACT_AGGREGATION_QUEUE),
                max_activities=1,
                max_workflow_tasks=1,
            )
        )
    return plans


class _QueueRoutingExecutor(concurrent.futures.ThreadPoolExecutor):
    """Default executor of the worker loop.

    ``run_in_executor(None, ...)`` made inside an activity runs on that
    activity's queue pool; anything else (SDK internals, registration) runs on
    this pool.
    """

    def submit(self, fn, /, *args, **kwargs):
        pool = _activity_pool.get()
        if pool is not None:
            return pool.submit(fn, *args, **kwargs)
        return super().submit(fn, *args, **kwargs)


def _activity_interceptor(pool: concurrent.futures.Executor):
    from asgiref.sync import ThreadSensitiveContext, sync_to_async
    from django.db import close_old_connections
    from temporalio.worker import (
        ActivityInboundInterceptor,
        ExecuteActivityInput,
        Interceptor,
    )

    class _Inbound(ActivityInboundInterceptor):
        async def execute_activity(self, input: ExecuteActivityInput):
            token = _activity_pool.set(pool)
            try:
                async with ThreadSensitiveContext():
                    try:
                        return await super().execute_activity(input)
                    finally:
                        # The context's private thread dies with the context;
                        # close the DB connection it opened instead of leaving
                        # it to GC.
                        await sync_to_async(
                            close_old_connections, thread_sensitive=True
                        )()
            finally:
                _activity_pool.reset(token)

    class _Interceptor(Interceptor):
        def intercept_activity(self, next):
            return _Inbound(next)

    return _Interceptor()


def _worker_kwargs(client, plan: _QueuePlan, interceptor) -> dict:
    from temporalio.worker import PollerBehaviorSimpleMaximum, UnsandboxedWorkflowRunner

    from tfc.management.commands.start_temporal_worker import _workflow_cache_kwargs

    # More pollers than slots cannot take more work; the exact queue has one.
    polls = _env_int("TEMPORAL_MAX_CONCURRENT_TASK_POLLS", 2)
    workflow_polls = max(1, min(polls, plan.max_workflow_tasks))
    activity_polls = max(1, min(polls, plan.max_activities))
    kwargs = {
        "client": client,
        "task_queue": plan.queue,
        "workflows": plan.workflows,
        "activities": plan.activities,
        "workflow_runner": UnsandboxedWorkflowRunner(),
        # No workflow_task_executor: each Worker builds its own pool sized to
        # its workflow-task slots. A smaller shared pool makes activations
        # queue, and queueing counts toward the SDK's 2 s deadlock timeout.
        "interceptors": [interceptor],
        "graceful_shutdown_timeout": timedelta(
            seconds=_env_int("TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT", 30)
        ),
        "max_heartbeat_throttle_interval": timedelta(seconds=5),
        "max_concurrent_activities": plan.max_activities,
        "max_concurrent_workflow_tasks": plan.max_workflow_tasks,
        "max_cached_workflows": _env_int("TEMPORAL_MAX_CACHED_WORKFLOWS", 100),
        "workflow_task_poller_behavior": PollerBehaviorSimpleMaximum(
            maximum=workflow_polls
        ),
        "activity_task_poller_behavior": PollerBehaviorSimpleMaximum(
            maximum=activity_polls
        ),
    }
    kwargs.update(_workflow_cache_kwargs(plan.queue))
    return kwargs


async def _reload_dispatchers(client) -> None:
    """Parity with start_temporal_worker --reload-dispatcher (default on): tell
    the singleton dispatcher workflows to pick up new code after a deploy."""
    if os.getenv("TEMPORAL_RELOAD_DISPATCHER_ON_START", "true").lower() not in _TRUE:
        return
    try:
        from simulate.temporal.constants import (
            DISPATCHER_WORKFLOW_ID,
            PHONE_NUMBER_DISPATCHER_WORKFLOW_ID,
        )
    except ImportError:
        return
    results = await asyncio.gather(
        client.get_workflow_handle(DISPATCHER_WORKFLOW_ID).signal("reload"),
        client.get_workflow_handle(PHONE_NUMBER_DISPATCHER_WORKFLOW_ID).signal(
            "reload"
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            # Normal on a fresh install: the dispatchers are not running yet.
            logger.info("embedded_dispatcher_reload_skipped", error=str(result))


async def _drain(worker, run: asyncio.Task) -> None:
    """Gracefully stop a running Worker and wait for its ``run()`` task.

    Waits on ``run()`` rather than ``worker.shutdown()``: shutdown() never
    returns when run() failed before it started polling (namespace validation
    while the server was unreachable), which would hang the drain.
    """
    shutdown = asyncio.create_task(worker.shutdown())
    try:
        await asyncio.wait({run})
    finally:
        shutdown.cancel()
    if not run.cancelled():
        run.exception()  # retrieved; the caller re-raises it when it should


def _leaf_errors(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaf_errors(inner)]
    return [error]


def _is_fatal(error: BaseException) -> bool:
    """Errors a retry cannot fix: Worker() configuration and Temporal
    rejecting the namespace or credentials. Everything else, connection
    errors included, is retried."""
    from temporalio.service import RPCError, RPCStatusCode

    if isinstance(error, (ValueError, TypeError)):
        return True
    if isinstance(error, RPCError):
        return error.status in (
            RPCStatusCode.INVALID_ARGUMENT,
            RPCStatusCode.NOT_FOUND,
            RPCStatusCode.PERMISSION_DENIED,
            RPCStatusCode.UNAUTHENTICATED,
        )
    # Worker.run() reports the namespace check as a RuntimeError carrying the
    # gRPC status, e.g. "Worker validation failed ... Status { code: NotFound".
    message = str(error)
    return message.startswith("Worker validation failed") and any(
        f"code: {code}" in message
        for code in (
            "InvalidArgument",
            "NotFound",
            "PermissionDenied",
            "Unauthenticated",
        )
    )


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:300]


class EmbeddedTemporalWorker:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None
        self._stop_requested = False
        # Liveness, written by the worker thread and read by /health/. Down
        # since creation until the Workers first run.
        self._connected = False
        self._workers_running = False
        self._down_since: float | None = time.monotonic()
        self._fatal = False
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._queues: list[str] = []

    # -- lifecycle (called from the ASGI loop thread) ------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        # Import every workflow/activity module now, on the caller's thread and
        # before the server accepts traffic, rather than concurrently with the
        # first requests from the worker thread.
        from tfc.temporal.common.registry import get_all_activities, get_all_workflows

        try:
            get_all_workflows()
            get_all_activities()
        except Exception as exc:
            # Keep serving HTTP; /health/ reports the broken worker.
            logger.exception("embedded_temporal_worker_import_failed")
            self._record_failure(exc, fatal=True)
            return
        self.install_signal_drain()
        self._thread = threading.Thread(
            target=self._thread_main, name="temporal-worker", daemon=True
        )
        self._thread.start()

    def request_stop(self) -> None:
        """Non-blocking: stop polling and start draining in-flight work."""
        self._stop_requested = True
        loop, shutdown = self._loop, self._shutdown
        if loop is None or shutdown is None:
            return  # _supervise() sees _stop_requested when it starts
        try:
            loop.call_soon_threadsafe(shutdown.set)
        except RuntimeError:
            pass  # loop already closed: the worker has stopped

    def stop(self, timeout: float | None = None) -> None:
        """Blocking: request a graceful drain and wait for the worker thread."""
        if self._thread is None:
            return
        self.request_stop()
        grace = _env_int("TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT", 30)
        self._thread.join(timeout if timeout is not None else grace + 10)
        if self._thread.is_alive():
            logger.warning("embedded_temporal_worker_stop_timeout")

    def install_signal_drain(self) -> None:
        """Start the Temporal drain on SIGTERM/SIGINT, before the server's own
        handler starts the HTTP drain, so the two overlap. The server only runs
        lifespan shutdown after HTTP has drained; waiting until then would
        leave the Temporal drain less of the kill timeout."""
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(signum)
            # Chain only onto a Python handler, i.e. a server that drains. With
            # SIG_DFL/SIG_IGN or a C-level handler, leave the signal alone.
            if not callable(previous) or getattr(previous, "_fi_embedded_drain", False):
                continue

            def _handler(sig, frame, previous=previous):
                self.request_stop()
                previous(sig, frame)

            _handler._fi_embedded_drain = True
            signal.signal(signum, _handler)

    # -- liveness ------------------------------------------------------------

    def health(self) -> dict:
        """Liveness for /health/: unhealthy once fatal, or once the Workers
        have not been running (or Temporal not reachable) for
        FI_EMBEDDED_WORKER_UNHEALTHY_AFTER_SECONDS, counted from startup."""
        down_since = self._down_since
        down_for = 0.0 if down_since is None else time.monotonic() - down_since
        limit = _env_int("FI_EMBEDDED_WORKER_UNHEALTHY_AFTER_SECONDS", 300)
        return {
            "healthy": not self._fatal and (down_since is None or down_for < limit),
            "fatal": self._fatal,
            "connected": self._connected,
            "workers_running": self._workers_running,
            "queues": list(self._queues),
            "down_for_seconds": int(down_for),
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }

    def _set_state(
        self, *, connected: bool | None = None, workers_running: bool | None = None
    ) -> None:
        if connected is not None:
            self._connected = connected
        if workers_running is not None:
            self._workers_running = workers_running
        if self._connected and self._workers_running:
            self._down_since = None
            self._consecutive_failures = 0
        elif self._down_since is None:
            self._down_since = time.monotonic()

    def _record_failure(self, error: BaseException, *, fatal: bool = False) -> None:
        self._consecutive_failures += 1
        self._last_error = _describe(error)
        self._fatal = self._fatal or fatal
        self._set_state(connected=False, workers_running=False)

    # -- worker thread -------------------------------------------------------

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_default_executor(
            _QueueRoutingExecutor(
                max_workers=_env_int("FI_EMBEDDED_ACTIVITY_THREADS", 4),
                thread_name_prefix="temporal-activity",
            )
        )
        self._loop = loop
        try:
            loop.run_until_complete(self._supervise())
        except Exception as exc:
            logger.exception("embedded_temporal_worker_crashed")
            self._record_failure(exc, fatal=True)
        finally:
            self._set_state(connected=False, workers_running=False)
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    async def _supervise(self) -> None:
        self._shutdown = asyncio.Event()
        if self._stop_requested:
            self._shutdown.set()
        while not self._shutdown.is_set():
            try:
                await self._run_workers()
            except Exception as exc:
                errors = _leaf_errors(exc)
                if self._shutdown.is_set():
                    logger.warning(
                        "embedded_temporal_worker_stopped_with_error",
                        error=_describe(errors[0]),
                    )
                    return
                fatal = any(_is_fatal(error) for error in errors)
                self._record_failure(errors[0], fatal=fatal)
                if fatal:
                    logger.error(
                        "embedded_temporal_worker_fatal",
                        error=self._last_error,
                        exc_info=errors[0],
                    )
                    return
                delay = _retry_delay(self._consecutive_failures)
                logger.warning(
                    "embedded_temporal_worker_retry",
                    error=self._last_error,
                    retry_in_seconds=delay,
                )
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def _run_workers(self) -> None:
        from temporalio.client import Client
        from temporalio.worker import Worker

        from tfc.management.commands.start_temporal_worker import (
            disable_litellm_logging_worker,
        )
        from tfc.telemetry.temporal import get_interceptors_for_client

        # No instrument_for_temporal(): tfc.asgi already applied
        # instrument_for_django(), a superset, and a second pass double-wraps.
        disable_litellm_logging_worker()

        client = await Client.connect(
            os.getenv("TEMPORAL_HOST", "localhost:7233"),
            namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            interceptors=get_interceptors_for_client(),
        )
        self._set_state(connected=True)
        registered = await self._register_temporal_state(client)
        await _reload_dispatchers(client)
        if self._shutdown.is_set():
            return

        plans = _worker_plans()
        pools: list[concurrent.futures.ThreadPoolExecutor] = []
        try:
            workers = []
            for plan in plans:
                pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=plan.max_activities,
                    thread_name_prefix=f"temporal-{plan.queue}",
                )
                pools.append(pool)
                workers.append(
                    Worker(**_worker_kwargs(client, plan, _activity_interceptor(pool)))
                )
            self._queues = [plan.queue for plan in plans]
            logger.info(
                "embedded_temporal_worker_started",
                activity_slots={plan.queue: plan.max_activities for plan in plans},
            )
            async with asyncio.TaskGroup() as group:
                for worker in workers:
                    group.create_task(self._run_worker(worker))
                group.create_task(self._monitor(client, workers, registered))
        finally:
            # The TaskGroup has shut every Worker down, so nothing submits to
            # the pools any more.
            self._queues = []
            self._set_state(workers_running=False)
            for pool in pools:
                pool.shutdown(wait=False, cancel_futures=True)
            logger.info("embedded_temporal_worker_stopped")

    async def _run_worker(self, worker) -> None:
        """Run one Worker until shutdown is requested. Its fatal error
        propagates, so the TaskGroup cancels (and so drains) the others."""
        run = asyncio.create_task(worker.run())
        stop = asyncio.create_task(self._shutdown.wait())
        try:
            await asyncio.wait({run, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            if not run.done():
                # Shutdown requested, or a sibling failed: drain either way.
                await _drain(worker, run)
        await run

    async def _monitor(self, client, workers, registered: bool) -> None:
        """Keep the liveness state current, and re-register the Temporal state
        whenever the server becomes reachable again (it may have restarted)."""
        interval = _probe_interval()
        while not self._shutdown.is_set():
            try:
                reachable = await client.service_client.check_health(
                    timeout=timedelta(seconds=5)
                )
            except Exception as exc:
                reachable = False
                self._last_error = _describe(exc)
            if reachable and (not registered or not self._connected):
                registered = await self._register_temporal_state(client)
            self._set_state(
                connected=bool(reachable),
                workers_running=all(worker.is_running for worker in workers),
            )
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def _register_temporal_state(self, client) -> bool:
        """Idempotently create what the Temporal server must hold for this app:
        the eval-task search attributes (upserting an unregistered one wedges
        the workflow task) and the recurring schedules. Returns False when
        something failed; the monitor tries again."""
        ok = True
        try:
            from tfc.temporal.eval_tasks.registration import (
                register_search_attributes,
            )

            await register_search_attributes(client, client.namespace)
        except Exception as exc:
            ok = False
            logger.warning(
                "embedded_search_attribute_registration_failed", error=_describe(exc)
            )
        if _schedule_registration_enabled():
            try:
                from tfc.temporal.schedules import ALL_SCHEDULES, a_register_schedules

                await a_register_schedules(client, ALL_SCHEDULES)
            except Exception as exc:
                ok = False
                logger.warning(
                    "embedded_schedule_registration_failed", error=_describe(exc)
                )
        return ok


_instance: EmbeddedTemporalWorker | None = None


def get_embedded_worker() -> EmbeddedTemporalWorker | None:
    """Process-wide singleton, or None when embedded mode is off."""
    global _instance
    if not enabled():
        return None
    if _instance is None:
        _instance = EmbeddedTemporalWorker()
    return _instance
