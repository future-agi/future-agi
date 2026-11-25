"""Narrow PostgreSQL outage classification at the control-activity boundary.

Do not import this module from workflows: driver exceptions are inspected here,
then Temporal carries only a stable application-error type across the boundary.
"""

import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Protocol, TypeVar

import psycopg
import structlog
from temporalio.exceptions import ApplicationError

logger = structlog.get_logger(__name__)

# Not every OperationalError/InternalError is an outage (the application's bad
# credentials, statement errors and bad configuration must still terminate the
# task).
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
    # PostgreSQL's 57P03 refusals while it starts, stops or recovers. The
    # 57P03 entry above does not catch them: psycopg attaches no SQLSTATE to
    # a failed connect. PgBouncer relays the same texts to clients still
    # logging in while its pool is cold. "not accepting connections" without
    # "yet" (hot_standby = off) is configuration and stays a failure.
    "the database system is starting up",
    "the database system is shutting down",
    "the database system is in recovery mode",
    "the database system is not yet accepting connections",
)
# PgBouncer sends its own refusals as SQLSTATE 08P01, the code PostgreSQL uses
# for real protocol violations, so only these texts mark an outage or
# maintenance: PostgreSQL unreachable behind the pooler, a crashed server
# connection, a wait for a server that never came, DISABLE, KILL and a pooler
# shutdown. server_login_retry is the pooler's own "try again later" for its
# login to PostgreSQL, whatever cached error newer releases append; the
# application's own credentials are refused by PgBouncer's client
# authentication instead. Those and its other 08P01s (startup parameters,
# limits), and PostgreSQL's own protocol violations, stay non-transient.
_POOLER_OUTAGE = re.compile(
    r"\(server_login_retry\)"
    r"|server conn crashed\?"
    r"|\bquery_wait_timeout\b"
    r"|client_login_timeout \(server down\)"
    r'|\bdatabase "[^"]*" is disabled'
    r"|\bdatabase removed\b"
    r"|\bpooler is shutting down\b"
    r"|\bserver shutting down\b"
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
    if driver.sqlstate in _TRANSIENT_SQLSTATES:
        return True
    if driver.sqlstate not in (None, "08P01") or not isinstance(
        driver, (psycopg.OperationalError, psycopg.InterfaceError)
    ):
        return False
    # A refusal at connect time arrives without a SQLSTATE, inside libpq's
    # "connection failed: ... FATAL: ..." text.
    message = str(driver)
    if _POOLER_OUTAGE.search(message):
        return True
    return driver.sqlstate is None and any(
        text in message.lower() for text in _CONNECTION_MESSAGES
    )


@contextmanager
def database_control_errors(
    *,
    task_id: str,
    control_activity: str,
    claim: bool = False,
    claim_submitted: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Only a submitted claim can have an uncertain commit outcome.

    Never repeat that attempt: a repeat cannot learn which rows the first one
    may have claimed. Park and leave unacknowledged IDs to the existing
    stale-claim reaper, so no entry is evaluated or billed twice. A confirmed
    read-only rejection rolls back, so it can keep the normal bounded activity
    retries.
    Connection establishment failures also retain that budget: no claim could
    commit before submission. Without a submission witness, stay conservative.
    """
    try:
        yield
    except Exception as exc:
        if not is_transient_database_error(exc):
            raise
        driver = _driver_error(exc)
        unknown_claim = (
            claim
            and (claim_submitted is None or claim_submitted())
            and driver is not None
            and driver.sqlstate not in {"08001", "25006"}
        )
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
