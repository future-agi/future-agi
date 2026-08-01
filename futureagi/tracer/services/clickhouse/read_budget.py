"""Classification helpers for bounded ClickHouse reads."""

import re
from collections.abc import Iterable
from datetime import datetime

from clickhouse_connect.driver.exceptions import (
    DatabaseError as ClickHouseConnectDatabaseError,
)
from clickhouse_driver.errors import Error as ClickHouseError
from clickhouse_driver.errors import ErrorCodes

FUTURE_TAIL_PROBE_TIMEOUT_MS = 100
FUTURE_TAIL_PROBE_SETTINGS = {
    "max_execution_time": FUTURE_TAIL_PROBE_TIMEOUT_MS / 1000,
    "timeout_overflow_mode": "throw",
    "max_threads": 1,
    "max_memory_usage": 64 * 1024 * 1024,
    "max_bytes_to_read": 64 * 1024 * 1024,
    "max_rows_to_read": 1_000_000,
    "read_overflow_mode": "throw",
    "max_result_rows": 1,
    "result_overflow_mode": "throw",
}

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

# ``clickhouse-driver`` exposes the server code on ``exc.code``.  The HTTP
# driver does not: its ``DatabaseError`` stores the code only in the canonical
# prefix produced by ``HttpClient._error_handler``.  Match that exact prefix
# and exception family rather than looking for a loose ``Code: N`` substring;
# arbitrary application/validation errors must never be mistaken for a safe
# resource-budget fallback.
_CLICKHOUSE_CONNECT_CODE_RE = re.compile(
    r"\AReceived ClickHouse exception,\s*code:\s*(\d+)\b",
    flags=re.IGNORECASE,
)


def build_future_tail_probe(
    *,
    start: datetime,
    end: datetime,
    root_only: bool,
    project_id: str | None = None,
    project_ids: Iterable[str] | None = None,
) -> tuple[str, dict]:
    """Build a bounded physical-row existence probe for a skipped future tail.

    The query intentionally does not use ``FINAL`` or ``is_deleted = 0``:
    fallback completion is safe only when no physical row at all exists in the
    half-open interval. Stale/tombstoned rows therefore conservatively keep the
    caller incomplete.
    """

    normalized_project_ids = tuple(str(value) for value in (project_ids or ()) if value)
    if normalized_project_ids:
        project_scope = "project_id IN %(future_tail_project_ids)s"
        params = {"future_tail_project_ids": normalized_project_ids}
    elif project_id:
        project_scope = "project_id = %(future_tail_project_id)s"
        params = {"future_tail_project_id": str(project_id)}
    else:
        raise ValueError("A project scope is required for a future-tail probe")

    root_predicate = (
        "WHERE parent_span_id IS NULL OR parent_span_id = ''" if root_only else ""
    )
    query = f"""
    SELECT 1 AS future_tail_row
    FROM spans
    PREWHERE {project_scope}
      AND start_time >= %(future_tail_start)s
      AND start_time < %(future_tail_end)s
    {root_predicate}
    LIMIT 1
    """
    params.update(
        {
            "future_tail_start": start,
            "future_tail_end": end,
        }
    )
    return query, params


def is_read_budget_error(exc: Exception) -> bool:
    """Return whether *exc* is a timeout/resource-bounded CH read failure.

    Query construction/programming errors deliberately do not qualify: those
    must surface as failures instead of masquerading as an empty result set.
    """

    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ClickHouseError):
        return getattr(exc, "code", None) in _READ_BUDGET_ERROR_CODES
    if isinstance(exc, ClickHouseConnectDatabaseError):
        match = _CLICKHOUSE_CONNECT_CODE_RE.match(str(exc))
        return bool(match and int(match.group(1)) in _READ_BUDGET_ERROR_CODES)
    return False
