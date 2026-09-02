"""Classification helpers for bounded ClickHouse reads."""

import re
import time
from dataclasses import dataclass

from clickhouse_connect.driver.exceptions import (
    DatabaseError as ClickHouseConnectDatabaseError,
)
from clickhouse_connect.driver.exceptions import (
    OperationalError as ClickHouseConnectOperationalError,
)
from clickhouse_driver.errors import Error as ClickHouseError
from clickhouse_driver.errors import ErrorCodes
from clickhouse_driver.errors import NetworkError as ClickHouseNetworkError
from clickhouse_driver.errors import SocketTimeoutError as ClickHouseSocketTimeoutError

_READ_BUDGET_ERROR_CODES = {
    ErrorCodes.CANNOT_ALLOCATE_MEMORY,
    ErrorCodes.LIMIT_EXCEEDED,
    ErrorCodes.MEMORY_LIMIT_EXCEEDED,
    ErrorCodes.QUERY_WAS_CANCELLED,
    ErrorCodes.RECEIVED_ERROR_TOO_MANY_REQUESTS,
    ErrorCodes.SET_SIZE_LIMIT_EXCEEDED,
    ErrorCodes.SOCKET_TIMEOUT,
    ErrorCodes.TIMEOUT_EXCEEDED,
    ErrorCodes.TOO_MANY_BYTES,
    ErrorCodes.TOO_MANY_ROWS,
    ErrorCodes.TOO_MANY_ROWS_OR_BYTES,
    ErrorCodes.TOO_MANY_SIMULTANEOUS_QUERIES,
}

_TRANSIENT_CLICKHOUSE_ERROR_CODES = {
    ErrorCodes.ALL_CONNECTION_TRIES_FAILED,
    ErrorCodes.CANNOT_READ_FROM_SOCKET,
    ErrorCodes.CANNOT_WRITE_TO_SOCKET,
    ErrorCodes.NETWORK_ERROR,
    ErrorCodes.NO_ACTIVE_REPLICAS,
    ErrorCodes.NO_AVAILABLE_REPLICA,
    ErrorCodes.NO_FREE_CONNECTION,
    ErrorCodes.SHARD_HAS_NO_CONNECTIONS,
}

# Code 386 (NO_COMMON_TYPE) has appeared on customer-facing browse/value APIs
# when heterogeneous production values reach a ClickHouse comparison.  It is
# not a timeout and must not be treated as one inside selectors, but at the HTTP
# read boundary it is an unavailable telemetry response: retryable/sanitized
# 503, never a client-validation 400 or a leaked server diagnostic.
_API_READ_UNAVAILABLE_ERROR_CODES = {ErrorCodes.NO_COMMON_TYPE}

_CLICKHOUSE_CONNECT_CODE_RE = re.compile(
    r"\A(?:Received ClickHouse exception,\s*code:|Code:)\s*(\d+)\b",
    flags=re.IGNORECASE,
)
_CLICKHOUSE_CONNECT_TRANSPORT_RE = re.compile(
    r"\A(?:"
    r"Network Error:|"
    r"Failed to read response data from server\b|"
    r"Error .+ executing HTTP request attempt \d+\b"
    r")",
    flags=re.IGNORECASE,
)
_CLICKHOUSE_CONNECT_TRANSIENT_HTTP_RE = re.compile(
    r"\AHTTP driver received HTTP status (?:408|429|502|503|504)\b",
    flags=re.IGNORECASE,
)
_CLICKHOUSE_MAX_QUERY_SIZE_RE = re.compile(
    r"\bMax query size exceeded\b",
    flags=re.IGNORECASE,
)


def _clickhouse_connect_error_code(exc: Exception) -> int | None:
    match = _CLICKHOUSE_CONNECT_CODE_RE.match(str(exc))
    return int(match.group(1)) if match else None


class ReadDeadlineExceeded(TimeoutError):
    """A request-owned read pipeline exhausted its single wall deadline."""


@dataclass(frozen=True)
class ReadDeadline:
    """One monotonic wall budget shared by every phase of an API read."""

    total_ms: int
    started: float

    @classmethod
    def start(cls, total_ms: int) -> "ReadDeadline":
        if total_ms <= 0:
            raise ValueError("read deadline must be positive")
        return cls(total_ms=int(total_ms), started=time.monotonic())

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000

    def remaining_ms(self, cap_ms: int | None = None, *, floor_ms: int = 25) -> int:
        remaining = int(self.total_ms - self.elapsed_ms())
        if remaining < floor_ms:
            raise ReadDeadlineExceeded("read deadline exceeded")
        if cap_ms is None:
            return remaining
        if cap_ms <= 0:
            raise ValueError("read timeout cap must be positive")
        return min(int(cap_ms), remaining)


def is_read_budget_error(exc: Exception) -> bool:
    """Return whether *exc* is a timeout/resource-bounded CH read failure.

    Query construction/programming errors deliberately do not qualify: those
    must surface as failures instead of masquerading as an empty result set.
    """

    if isinstance(exc, (ReadDeadlineExceeded, ClickHouseSocketTimeoutError)):
        return True
    if isinstance(exc, ClickHouseError):
        return getattr(exc, "code", None) in _READ_BUDGET_ERROR_CODES
    if isinstance(exc, ClickHouseConnectDatabaseError):
        return _clickhouse_connect_error_code(exc) in _READ_BUDGET_ERROR_CODES
    return False


def is_clickhouse_query_size_error(exc: Exception) -> bool:
    """Return whether *exc* is ClickHouse's bounded SQL-size rejection.

    ClickHouse reports ``max_query_size`` as syntax error code 62 even when the
    generated statement is otherwise valid. Only that canonical diagnostic is
    safe for an identity-batch caller to retry at a smaller size; arbitrary
    syntax errors remain programming failures and fail closed.
    """

    if isinstance(exc, ClickHouseError):
        code = getattr(exc, "code", None)
    elif isinstance(exc, ClickHouseConnectDatabaseError):
        code = _clickhouse_connect_error_code(exc)
    else:
        return False
    return code == ErrorCodes.SYNTAX_ERROR and bool(
        _CLICKHOUSE_MAX_QUERY_SIZE_RE.search(str(exc))
    )


def is_clickhouse_query_error(exc: Exception) -> bool:
    """Return whether *exc* is a narrow, degradable ClickHouse failure.

    This classifier deliberately fails closed.  A driver exception alone does
    not prove that a read may safely degrade: ClickHouse uses the same driver
    exception hierarchy for unknown columns/tables, syntax errors, and type
    mismatches.  Only explicit network/availability codes and known transport
    failures qualify here; read-budget failures are classified separately by
    :func:`is_read_budget_error`.
    """

    if isinstance(exc, (ClickHouseNetworkError, ClickHouseSocketTimeoutError)):
        return True
    if isinstance(exc, ClickHouseError):
        return getattr(exc, "code", None) in _TRANSIENT_CLICKHOUSE_ERROR_CODES
    if not isinstance(exc, ClickHouseConnectDatabaseError):
        return False

    code = _clickhouse_connect_error_code(exc)
    if code is not None:
        return code in _TRANSIENT_CLICKHOUSE_ERROR_CODES

    message = str(exc)
    # clickhouse-connect raises bare DatabaseError for canonical non-200 HTTP
    # responses in addition to OperationalError.  Parsed ClickHouse server
    # codes were already handled above, so a compiler defect cannot qualify by
    # appending transient-looking HTTP text to its private server response.
    if _CLICKHOUSE_CONNECT_TRANSIENT_HTTP_RE.match(message):
        return True
    return isinstance(exc, ClickHouseConnectOperationalError) and bool(
        _CLICKHOUSE_CONNECT_TRANSPORT_RE.match(message)
    )


def is_clickhouse_api_read_unavailable_error(exc: Exception) -> bool:
    """Return whether a public CH read should answer with sanitized HTTP 503.

    Selector-owned budget and transport classification stays deliberately
    narrow.  This API-boundary classifier additionally covers the production
    heterogeneous-type failure above, while still rejecting syntax, unknown
    identifiers/tables, arbitrary runtime errors, and untyped message text.
    """

    if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
        return True
    if isinstance(exc, ClickHouseError):
        return getattr(exc, "code", None) in _API_READ_UNAVAILABLE_ERROR_CODES
    if isinstance(exc, ClickHouseConnectDatabaseError):
        return _clickhouse_connect_error_code(exc) in _API_READ_UNAVAILABLE_ERROR_CODES
    return False
