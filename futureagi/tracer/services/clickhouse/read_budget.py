"""Classification helpers for bounded ClickHouse reads."""

import re
import time
from dataclasses import dataclass
from typing import Any

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

# An INSERT refused while merges are behind: back-pressure, like the read
# budget codes, not a fault in the rows.
_WRITE_BACKPRESSURE_ERROR_CODES = {ErrorCodes.TOO_MANY_PARTS}

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


def _clickhouse_error_code(exc: Exception) -> int | None:
    """The server error code of either driver's exception; None otherwise."""
    if isinstance(exc, ClickHouseError):
        return getattr(exc, "code", None)
    if isinstance(exc, ClickHouseConnectDatabaseError):
        return _clickhouse_connect_error_code(exc)
    return None


class ReadDeadlineExceeded(TimeoutError):
    """A request-owned read pipeline exhausted its single wall deadline."""


@dataclass(frozen=True)
class ReadDeadline:
    """One monotonic wall budget shared by every phase of an API read."""

    total_ms: int
    started: float
    # Whether a statement under this deadline also asks the server to stop at
    # it (``server_execution_cap_ms``). Off by default: application reads keep
    # the no-abort policy and use the deadline for admission only.
    enforce_on_server: bool = False

    @classmethod
    def start(cls, total_ms: int, *, enforce_on_server: bool = False) -> "ReadDeadline":
        if total_ms <= 0:
            raise ValueError("read deadline must be positive")
        return cls(
            total_ms=int(total_ms),
            started=time.monotonic(),
            enforce_on_server=enforce_on_server,
        )

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


class WallCappedAnalytics:
    """Ask ClickHouse to stop every statement at what is left of one wall.

    Application reads carry no server deadline by default: ``timeout_ms`` is
    admission arithmetic, and ``application_read_settings`` zeroes
    ``max_execution_time``. A caller that owns a real wall (the exact-refresh
    worker's ``GRAPH_BACKGROUND_WALL_MS``, an inline chart's interactive wall)
    wraps its service in this: each statement is sent with
    ``server_execution_cap_ms`` = the time left on that wall, so a read that
    would outlast the wall is stopped by the server
    (``ReadDeadlineExceeded``) instead of reading to the end and holding its
    slot for a result nobody can use. ``timeout_ms`` and ``settings`` pass
    through unchanged; a caller's own tighter cap is kept. Below
    ``floor_ms`` left, the statement is not sent at all. The cap is sent
    whatever the deadline's ``enforce_on_server`` says: wrapping is the
    opt-in.

    A server profile locked at ``readonly=1`` accepts no query setting, so on
    that lane the cap cannot reach the server (the service drops every
    per-query setting); only the profile's own limits apply there.
    """

    def __init__(self, delegate: Any, deadline: ReadDeadline, *, floor_ms: int) -> None:
        self._delegate = delegate
        self._deadline = deadline
        self._floor_ms = int(floor_ms)

    def execute_ch_query(
        self,
        query: str,
        params: dict | None = None,
        timeout_ms: int | None = None,
        settings: dict | None = None,
        *,
        server_execution_cap_ms: int | None = None,
    ) -> Any:
        cap_ms = self._deadline.remaining_ms(
            server_execution_cap_ms, floor_ms=self._floor_ms
        )
        return self._delegate.execute_ch_query(
            query,
            params or {},
            timeout_ms=timeout_ms,
            settings=settings,
            server_execution_cap_ms=cap_ms,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def is_read_budget_error(exc: Exception) -> bool:
    """Return whether *exc* is a timeout/resource-bounded CH read failure.

    Query construction/programming errors deliberately do not qualify: those
    must surface as failures instead of masquerading as an empty result set.
    """

    if isinstance(exc, (ReadDeadlineExceeded, ClickHouseSocketTimeoutError)):
        return True
    return _clickhouse_error_code(exc) in _READ_BUDGET_ERROR_CODES


def is_clickhouse_overload_error(exc: Exception) -> bool:
    """Return whether ClickHouse refused a statement for capacity, not content.

    The read-budget codes (memory, timeouts, too many queries) plus
    TOO_MANY_PARTS. The same statement can succeed later unchanged, so a
    writer must retry it rather than split it or blame its rows.
    """

    return (
        is_read_budget_error(exc)
        or _clickhouse_error_code(exc) in _WRITE_BACKPRESSURE_ERROR_CODES
    )


def is_clickhouse_query_size_error(exc: Exception) -> bool:
    """Return whether *exc* is ClickHouse's bounded SQL-size rejection.

    ClickHouse reports ``max_query_size`` as syntax error code 62 even when the
    generated statement is otherwise valid. Only that canonical diagnostic is
    safe for an identity-batch caller to retry at a smaller size; arbitrary
    syntax errors remain programming failures and fail closed.
    """

    return _clickhouse_error_code(exc) == ErrorCodes.SYNTAX_ERROR and bool(
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

    # The native driver's socket reader raises a bare EOFError, neither wrapped
    # nor an OSError, when the server closes the connection mid-response. Only
    # the bare exception qualifies: a coded error raised while one is being
    # handled is still judged by its code below.
    if isinstance(
        exc, (ClickHouseNetworkError, ClickHouseSocketTimeoutError, EOFError)
    ):
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

    return (
        is_read_budget_error(exc)
        or is_clickhouse_query_error(exc)
        or _clickhouse_error_code(exc) in _API_READ_UNAVAILABLE_ERROR_CODES
    )
