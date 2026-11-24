"""Opt-in, single-local-shard reader lifetime; not a production provider binding.

The cleanup owner is this supervisor, NOT the request's query subprocess. A
prepared PostgreSQL transaction retains the existing lifecycle lock across
supervisor death, connection loss and uncertain ClickHouse cancellation. Only a
positive terminal receipt permits ROLLBACK PREPARED. Expiry/client timeout/empty
system.processes alone never permits release. Unresolved guards require recovery;
there is deliberately no TTL reaper. Deployments without prepared transactions,
query-log visibility and an independently reachable control lane are ineligible.

The capture callback must enter the existing metrics pin/lock contexts on the
supplied dedicated Django connection. Its outer transaction is ONLY for this
read's capture; it must not contain caller writes. Preparing it transfers its
locks to PostgreSQL. Rolling it back after drain discards only that capture.
Existing publication, retention and erasure algorithms are unchanged.

This is a low-level prerequisite. Only compiler-owned, local SELECTs belong here;
remote/Distributed/parallel-replica queries are not supported. No endpoint or
fast-path flag invokes this module. Recovery must use the SAME physical CH server
and primary PG database. A stopped/never-started submission can be released by
its live supervisor; after supervisor death an absent query is ambiguous and
must remain protected until an independently verified terminal receipt exists.
"""

import multiprocessing
import pickle
import select
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)


class ReaderLifetimeError(RuntimeError):
    """Safe, payload-free errors for the opt-in reader."""


class IncapableReadLane(ReaderLifetimeError):
    pass


class ReadNotSubmitted(ReaderLifetimeError):
    """The dedicated worker positively reports no data-query submission."""


class ReadAcquisitionTimeout(ReaderLifetimeError):
    pass


class ReadDeadlineExceeded(ReaderLifetimeError):
    pass


class ReadCancelled(ReaderLifetimeError):
    pass


class ReadExecutionFailed(ReaderLifetimeError):
    pass


class ReadWorkerUnreaped(ReaderLifetimeError):
    """The client may still submit: cleanup must keep the durable guard."""


class ReadCleanupPending(ReaderLifetimeError):
    """Protection remains in PG. Pass receipt to recover_read; never expire it."""

    def __init__(self, receipt: "ReadReceipt"):
        self.receipt = receipt
        super().__init__(f"metrics read cleanup pending: {receipt.query_id}")


@dataclass(frozen=True)
class ReadBudget:
    # All values are caller-supplied, per call. There are no production defaults.
    acquire_s: float
    execute_s: float
    cancel_s: float
    release_s: float
    transport_s: float
    memory_bytes: int
    result_bytes: int
    threads: int

    def __post_init__(self) -> None:
        for name in ("acquire_s", "execute_s", "cancel_s", "release_s", "transport_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be finite and positive")
            if not isfinite(value) or value < 0.001:
                raise ValueError(f"{name} must be finite and at least one millisecond")
        for name in ("memory_bytes", "result_bytes", "threads"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isfinite(self.total_s):
            raise ValueError("total lifetime must be finite")

    @property
    def total_s(self) -> float:
        return self.acquire_s + self.execute_s + self.cancel_s + self.release_s


@dataclass(frozen=True)
class ReadReceipt:
    # The GID is both a durable recovery inventory and a unique query identity.
    query_id: str

    def __post_init__(self) -> None:
        if str(UUID(self.query_id)) != self.query_id:
            raise ValueError("noncanonical metrics query identity")

    @property
    def gid(self) -> str:
        return f"metrics-reader:{self.query_id}"


class ReadCancellation(Protocol):
    def is_set(self) -> bool: ...


class ReadLane(Protocol):
    def execute(
        self, sql: str, params: dict, receipt: ReadReceipt, budget: ReadBudget
    ) -> list: ...


class ReadControl(Protocol):
    def drain(self, receipt: ReadReceipt, budget: ReadBudget) -> bool: ...


@dataclass(frozen=True, repr=False)
class PinCapture:
    """Spawn-safe capture, using existing production interfaces, not new SQL."""

    database: dict
    project_id: str
    metric_id: str | None = None
    # Compiler-owned, PG-only plan construction under the real capture. Returning
    # plain SQL/params also avoids pickling ORM objects into an uninitialized app.
    query_compiler: Callable | None = None

    def __call__(self, receipt: ReadReceipt, budget: ReadBudget) -> object:
        from datetime import timedelta

        import django
        from django.apps import apps
        from django.db import connections

        # Only the dedicated spawn worker may replace its default connection.
        # Never change a request worker's alias or reuse an ambient transaction.
        if multiprocessing.parent_process() is None:
            raise IncapableReadLane("pin capture requires the dedicated subprocess")
        if not apps.ready:
            django.setup()
        if connections["default"].in_atomic_block:
            raise IncapableReadLane("pin capture cannot use an ambient transaction")
        db = connections["default"].copy(alias="metrics_bounded_reader")
        db.settings_dict = {**db.settings_dict, **self.database}
        connections[db.alias] = db
        try:
            # Existing capture_pin uses default. Isolate the entire spawned
            # process's default connection; no other request runs in this worker.
            connections["default"] = db
            from tracer.services.derived_metrics.deletion_protocol import capture_pin

            def capture() -> object:
                pin = capture_pin(
                    self.project_id,
                    consumer_kind="request",
                    ttl=timedelta(seconds=min(budget.total_s, 24 * 60 * 60)),
                    metric_id=self.metric_id,
                )
                if self.query_compiler is not None:
                    return self.query_compiler(pin.manifest, pin.definition_version)
                return pin.manifest, pin.definition_version

            return prepare_capture(db, receipt, budget, capture)
        finally:
            db.close()


def prepare_capture(
    db: Any,
    receipt: ReadReceipt,
    budget: ReadBudget,
    capture: Callable[[], object],
) -> object:
    """Bound existing pin/lock contexts inside an isolated, crash-stable guard.

    The supervisor also bounds this whole process (including PG connect, PREPARE
    and a stuck socket). A lost PREPARE acknowledgement is treated as possibly
    prepared, not as permission to submit or to assume rollback.
    """
    from django.db import transaction

    if db.in_atomic_block or not db.get_autocommit() or db.vendor != "postgresql":
        raise IncapableReadLane("capture requires a dedicated PostgreSQL connection")
    deadline = time.monotonic() + budget.acquire_s
    # Tag the backend BEFORE any capture statement can be sent. Once a killed
    # client is reaped, cleanup waits for this PG backend too: a lost PREPARE
    # response must not race an empty pg_prepared_xacts lookup and land later.
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('application_name', %s, false)", [receipt.gid]
        )

    def bound(execute, sql, params, many, context):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise ReadAcquisitionTimeout("pin acquisition deadline expired")
        # Use the underlying cursor so the timeout commands do not recurse into
        # Django's execution wrappers. Both settings are transaction-local.
        cursor = context["cursor"].cursor
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true)", [f"{remaining_ms}ms"]
        )
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)", [f"{remaining_ms}ms"]
        )
        return execute(sql, params, many, context)

    with transaction.atomic(using=db.alias):
        with db.execute_wrapper(bound):
            with db.cursor() as cursor:
                cursor.execute("SHOW max_prepared_transactions")
                if int(cursor.fetchone()[0]) <= 0:
                    raise IncapableReadLane("crash-stable PG protection is unavailable")
            snapshot = capture()
            with db.cursor() as cursor:
                # GID is generated internally and strictly validated, not SQL input.
                cursor.execute(f"PREPARE TRANSACTION '{receipt.gid}'")
    # Django's outer commit sees no current transaction after PREPARE. The
    # prepared lifecycle lock remains held in PG even when db.close() follows.
    return snapshot


def _child_entry(output: socket.socket, function: Callable, args: tuple) -> None:
    """Private, memory-only IPC: supervisor death leaves no result files behind."""
    try:
        try:
            result = ("ok", function(*args))
        except ReadNotSubmitted:
            result = ("not_submitted", None)
        except IncapableReadLane:
            result = ("incapable", None)
        except BaseException as exc:
            private_logger.exception("metric_read_child_failed", exc)
            # Driver exceptions can include SQL, data or headers: never transmit.
            result = ("failed", None)
        try:
            output.sendall(pickle.dumps(result))
        except OSError:
            # The supervisor disconnected/crashed. PG still owns the guard.
            return
    finally:
        output.close()


def _run_stage(
    function: Callable,
    args: tuple,
    *,
    deadline: float,
    reap_deadline: float,
    payload_limit: int,
    cancel: ReadCancellation | None = None,
) -> tuple[str, object]:
    """Wall-bound the process AND partial IPC; never mistake kill for drain.

    Socketpair IPC is byte-capped, nonblocking and memory-only. Neither a child
    blocked sending a large result nor a partial pickle can extend the deadline.
    """
    if time.monotonic() >= deadline:
        return "timeout", None
    context = multiprocessing.get_context("spawn")
    incoming, outgoing = socket.socketpair()
    process = context.Process(target=_child_entry, args=(outgoing, function, args))
    outcome = None
    payload = bytearray()
    eof = False
    try:
        process.start()
        outgoing.close()
        incoming.setblocking(False)
        while process.is_alive() or not eof:
            if cancel is not None and cancel.is_set():
                outcome = "cancelled"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "timeout"
                break
            if eof:
                process.join(min(0.02, remaining))
            elif select.select([incoming], [], [], min(0.02, remaining))[0]:
                chunk = incoming.recv(min(65536, payload_limit + 1 - len(payload)))
                eof = not chunk
                payload.extend(chunk)
                if len(payload) > payload_limit:
                    outcome = "failed"
                    break
    finally:
        incoming.close()
        outgoing.close()
        if process.pid is not None:
            try:
                if process.is_alive():
                    process.kill()
                # Reaping consumes the ORIGINAL lifetime, not a reset timeout.
                process.join(max(0, reap_deadline - time.monotonic()))
                if process.is_alive():
                    raise ReadWorkerUnreaped("client process could not be reaped")
            except BaseException as exc:
                private_logger.exception("metric_read_worker_reap_failed", exc)
                raise ReadWorkerUnreaped("client process drain is unproved") from None
    if outcome:
        return outcome, None
    if process.exitcode != 0 or not payload:
        return "failed", None
    # The child sends a pickled (state, value) pair.
    result: tuple[str, object] = pickle.loads(payload)  # noqa: S301 - private child-owned IPC
    return result


@dataclass(frozen=True, repr=False)
class PreparedGuardStore:
    """Connect only to the authoritative PG database that performed capture."""

    connect_kwargs: dict

    def release(self, receipt: ReadReceipt, budget: ReadBudget) -> None:
        import psycopg
        from psycopg import sql

        options = dict(self.connect_kwargs)
        options["connect_timeout"] = max(1, int(budget.transport_s))
        with psycopg.connect(**options, autocommit=True) as connection:
            connection.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                [f"{max(1, int(budget.release_s * 1000))}ms"],
            )
            deadline = time.monotonic() + budget.release_s
            while connection.execute(
                "SELECT 1 FROM pg_stat_activity WHERE application_name = %s "
                "AND datname = current_database() AND pid <> pg_backend_pid()",
                [receipt.gid],
            ).fetchone():
                if time.monotonic() >= deadline:
                    raise ReadCleanupPending(receipt)
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            present = connection.execute(
                "SELECT 1 FROM pg_prepared_xacts WHERE gid = %s AND database = current_database()",
                [receipt.gid],
            ).fetchone()
            if present:
                connection.execute(
                    sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(receipt.gid))
                )


def recover_read(
    receipt: ReadReceipt,
    *,
    control: ReadControl,
    guards: PreparedGuardStore,
    budget: ReadBudget,
) -> None:
    """Retry one bounded cleanup; no proof means the durable guard stays put."""
    start = time.monotonic()
    end = start + budget.cancel_s + budget.release_s
    try:
        state, acknowledged = _run_stage(
            control.drain,
            (receipt, budget),
            deadline=start + budget.cancel_s,
            reap_deadline=end,
            payload_limit=budget.result_bytes,
        )
        if state != "ok" or acknowledged is not True:
            raise ReadCleanupPending(receipt)
        state, _ = _run_stage(
            guards.release,
            (receipt, budget),
            deadline=end,
            reap_deadline=end,
            payload_limit=budget.result_bytes,
        )
        if state != "ok":
            raise ReadCleanupPending(receipt)
    except ReadWorkerUnreaped:
        raise ReadCleanupPending(receipt) from None


def bounded_read(
    sql: str,
    params: dict,
    *,
    capture: Callable,
    lane: ReadLane,
    control: ReadControl,
    guards: PreparedGuardStore,
    budget: ReadBudget,
    cancel: ReadCancellation | None = None,
) -> object:
    """Capture -> execute -> acknowledged drain -> release, under original cuts.

    Independent review/provider binding must qualify the local-only compiler and
    physical shard/PG routing before this can be used by an application endpoint.
    Do not catch CleanupPending and expire the guard to make native fallback run.
    """
    receipt = ReadReceipt(str(uuid4()))
    start = time.monotonic()
    acquisition_end = start + budget.acquire_s
    execution_end = acquisition_end + budget.execute_s
    cancellation_end = execution_end + budget.cancel_s
    release_end = cancellation_end + budget.release_s
    submitted = False
    failure = None
    value = None
    try:
        state, _snapshot = _run_stage(
            capture,
            (receipt, budget),
            deadline=acquisition_end,
            reap_deadline=release_end,
            payload_limit=budget.result_bytes,
            cancel=cancel,
        )
        if state == "incapable":
            raise IncapableReadLane("pin lane lacks crash-stable protection")
        if state == "cancelled":
            raise ReadCancelled("read cancelled before submission")
        if state != "ok":
            raise ReadAcquisitionTimeout(
                "pin acquisition failed or exceeded its budget"
            )
        if isinstance(capture, PinCapture) and capture.query_compiler is not None:
            # The real pin's plan was built inside the bounded PG capture stage.
            # Supplied SQL cannot override that immutable generation selection.
            sql, params = cast("tuple[str, dict]", _snapshot)
        if cancel is not None and cancel.is_set():
            raise ReadCancelled("read cancelled before submission")
        # Set BEFORE spawn: any failure after this point can be an uncertain send.
        submitted = True
        state, value = _run_stage(
            lane.execute,
            (sql, params, receipt, budget),
            deadline=min(execution_end, time.monotonic() + budget.execute_s),
            reap_deadline=release_end,
            payload_limit=budget.result_bytes,
            cancel=cancel,
        )
        if state == "not_submitted":
            submitted = False
            raise IncapableReadLane("local read lane refused before data submission")
        if state == "cancelled":
            raise ReadCancelled("read cancelled")
        if state == "timeout":
            raise ReadDeadlineExceeded("read execution deadline expired")
        if state != "ok":
            raise ReadExecutionFailed("read transport or execution failed")
    except BaseException as error:  # noqa: BLE001 - log after drain/release, then propagate the original interruption
        failure = error
    # The sole cleanup owner ignores request cancellation during drain/release.
    # Its own crash leaves the prepared lock for recover_read, never a TTL.
    try:
        if isinstance(failure, ReadWorkerUnreaped):
            raise ReadCleanupPending(receipt) from failure
        if submitted:
            state, acknowledged = _run_stage(
                control.drain,
                (receipt, budget),
                deadline=min(cancellation_end, time.monotonic() + budget.cancel_s),
                reap_deadline=release_end,
                payload_limit=budget.result_bytes,
            )
            if state != "ok" or acknowledged is not True:
                raise ReadCleanupPending(receipt) from failure
        state, _ = _run_stage(
            guards.release,
            (receipt, budget),
            deadline=min(release_end, time.monotonic() + budget.release_s),
            reap_deadline=release_end,
            payload_limit=budget.result_bytes,
        )
        if state != "ok":
            raise ReadCleanupPending(receipt) from failure
    except ReadWorkerUnreaped:
        raise ReadCleanupPending(receipt) from None
    finally:
        if failure is not None:
            private_logger.exception("metric_read_supervisor_failed", failure)
    if failure:
        raise failure
    return value
