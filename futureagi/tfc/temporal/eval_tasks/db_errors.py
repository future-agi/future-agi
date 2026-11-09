"""Narrow PostgreSQL outage classification at the control-activity boundary.

Do not import this module from workflows: driver exceptions are inspected here,
then Temporal carries only a stable application-error type across the boundary.
"""

from functools import wraps

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


def is_transient_database_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, psycopg.Error):
            if exc.sqlstate:
                return exc.sqlstate in _TRANSIENT_SQLSTATES
            if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
                return any(text in str(exc).lower() for text in _CONNECTION_MESSAGES)
        exc = exc.__cause__
    return False


def database_control_activity(*, claim: bool = False):
    """Normalize only control steps; never wrap provider execution.

    A claim may have committed without returning its IDs. Do not let Temporal
    repeat that attempt and silently claim a second batch. The workflow parks,
    re-proves the row set, and leaves any unacknowledged claims to the existing
    stale-claim reaper. No provider call is dispatched for those unknown IDs.
    """

    def decorate(fn):
        @wraps(fn)
        async def wrapped(input):
            try:
                return await fn(input)
            except Exception as exc:
                if not is_transient_database_error(exc):
                    raise
                error_type = (
                    "EvalTaskClaimOutcomeUnknown" if claim else "EvalTaskDatabaseUnavailable"
                )
                logger.warning(
                    "eval_task_database_unavailable",
                    task_id=str(input.task_id),
                    control_activity=fn.__name__,
                    failure_type=error_type,
                )
                # No raw driver message: it can contain connection details.
                raise ApplicationError(
                    "Evaluation task control step is waiting for the database.",
                    type=error_type,
                    non_retryable=claim,
                ) from None

        return wrapped

    return decorate
