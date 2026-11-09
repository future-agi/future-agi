"""Narrow PostgreSQL outage classification at the control-activity boundary.

Do not import this module from workflows: driver exceptions are inspected here,
then Temporal carries only a stable application-error type across the boundary.
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Protocol, TypeVar

import psycopg
import structlog
from temporalio.exceptions import ApplicationError

logger = structlog.get_logger(__name__)

# Not every OperationalError/InternalError is an outage (bad credentials,
# statement errors and bad configuration must still terminate the task).
_TRANSIENT_SQLSTATES = {
    "08000",  # connection exception
    "08001",  # unable to establish connection
    "08003",  # connection does not exist
    "08006",  # connection failure
    "08007",  # transaction resolution unknown
    "25006",  # read-only transaction during writer failover
    "57P01",  # admin shutdown
    "57P02",  # crash shutdown
    "57P03",  # cannot connect now
}
# libpq connection failures often have no SQLSTATE. Require a driver exception,
# not merely a matching message on a Django/configuration/provider exception.
_CONNECTION_MESSAGES = (
    "connection refused",
    "connection timeout expired",
    "timeout expired",
    "server closed the connection unexpectedly",
    "connection is closed",
    "connection already closed",
    "connection not open",
    "ssl connection has been closed unexpectedly",
    "could not receive data from server",
    "could not send data to server",
    "consuming input failed",
)


def _driver_error(exc: BaseException | None) -> psycopg.Error | None:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, psycopg.Error):
            return exc
        exc = exc.__cause__
    return None


def is_transient_database_error(exc: BaseException) -> bool:
    driver = _driver_error(exc)
    if driver is None:
        return False
    if driver.sqlstate:
        return driver.sqlstate in _TRANSIENT_SQLSTATES
    return isinstance(
        driver, (psycopg.OperationalError, psycopg.InterfaceError)
    ) and any(text in str(driver).lower() for text in _CONNECTION_MESSAGES)


@contextmanager
def database_control_errors(
    *, task_id: str, control_activity: str, claim: bool = False
) -> Iterator[None]:
    """Only a submitted claim can have an uncertain commit outcome.

    Never repeat that attempt and silently claim a second batch. Park and leave
    unacknowledged IDs to the existing stale-claim reaper. A confirmed read-only
    rejection rolls back, so it can keep the normal bounded activity retries.
    """
    try:
        yield
    except Exception as exc:
        if not is_transient_database_error(exc):
            raise
        driver = _driver_error(exc)
        unknown_claim = claim and driver is not None and driver.sqlstate != "25006"
        error_type = (
            "EvalTaskClaimOutcomeUnknown"
            if unknown_claim
            else "EvalTaskDatabaseUnavailable"
        )
        logger.warning(
            "eval_task_database_unavailable",
            task_id=task_id,
            control_activity=control_activity,
            failure_type=error_type,
        )
        # No raw driver message: it can contain connection details.
        raise ApplicationError(
            "Evaluation task control step is waiting for the database.",
            type=error_type,
            non_retryable=unknown_claim,
        ) from None


class _ControlInput(Protocol):
    task_id: str


_Input = TypeVar("_Input", bound=_ControlInput)
_Result = TypeVar("_Result")


def database_control_activity() -> Callable[
    [Callable[[_Input], Awaitable[_Result]]], Callable[[_Input], Awaitable[_Result]]
]:
    """Normalize only control steps; never wrap provider execution."""

    def decorate(
        fn: Callable[[_Input], Awaitable[_Result]],
    ) -> Callable[[_Input], Awaitable[_Result]]:
        @wraps(fn)
        async def wrapped(input: _Input) -> _Result:
            with database_control_errors(
                task_id=str(input.task_id), control_activity=fn.__name__
            ):
                return await fn(input)

        return wrapped

    return decorate
