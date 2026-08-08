"""Finite, latest-state ClickHouse reads for tracing filter-value pickers.

These selectors never use ``FINAL`` or ``timeout_overflow_mode=break``. Every
physical span is collapsed with ``argMax(_version)`` before liveness, root-span,
or value predicates are applied. A server budget failure therefore becomes an
explicit degraded API response instead of a falsely exact empty picker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NotRequired, TypedDict

from tracer.services.clickhouse.query_builders.voice_call_list import (
    VOICE_CALL_STATUS_FILTER_EXPRESSION,
    VOICE_COST_CENTS_FILTER_EXPRESSION,
)
from tracer.services.clickhouse.query_service import QueryExecutor
from tracer.services.clickhouse.v2.id_remap_sql import (
    NIL_UUID,
    remap_left_join,
    resolved_id_expr,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    rewrite_v1_sql_to_v2,
)

FILTER_VALUE_READ_TIMEOUT_MS = 4_000
FILTER_VALUE_MAX_ROWS_TO_READ = 2_000_000
FILTER_VALUE_MAX_BYTES_TO_READ = 512 * 1024 * 1024
FILTER_VALUE_MAX_MEMORY_USAGE = 256 * 1024 * 1024

FILTER_VALUE_READ_SETTINGS: dict[str, Any] = {
    "max_threads": 2,
    "max_rows_to_read": FILTER_VALUE_MAX_ROWS_TO_READ,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": FILTER_VALUE_MAX_BYTES_TO_READ,
    "max_memory_usage": FILTER_VALUE_MAX_MEMORY_USAGE,
    "max_result_bytes": 8 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

_SYSTEM_VALUE_SOURCE_COLUMNS = {
    "trace_id": "trace_id",
    "span_id": "id",
    "project": "project_id",
    "model": "model",
    "status": "status",
    "provider": "provider",
    "observation_type": "observation_type",
    "span_kind": "observation_type",
    "service_name": "service_name",
    "name": "name",
    "span_name": "name",
    "session": "trace_session_id",
    "user": "end_user_id",
    "user_id": "end_user_id",
    "tag": "tags",
    "prompt_name": "prompt_version_id",
    "prompt_version": "prompt_version_id",
    "prompt_label": "prompt_label_id",
}
_VOICE_SYSTEM_VALUE_EXPRESSIONS = {
    "call_status": rewrite_v1_sql_to_v2(VOICE_CALL_STATUS_FILTER_EXPRESSION),
    "cost_cents": rewrite_v1_sql_to_v2(VOICE_COST_CENTS_FILTER_EXPRESSION),
}
SYSTEM_FILTER_VALUE_METRICS = frozenset(
    {*_SYSTEM_VALUE_SOURCE_COLUMNS, *_VOICE_SYSTEM_VALUE_EXPRESSIONS}
)


class FilterValueMetadata(TypedDict):
    query_complete: bool
    query_status: Literal["complete", "sampled", "degraded"]
    query_window_start: str
    query_window_end: str
    query_error_code: NotRequired[str]


@dataclass(frozen=True)
class FilterValueRead:
    values: tuple[str, ...]
    query_complete: bool
    query_error_code: str | None
    query_window_start: datetime
    query_window_end: datetime
    has_more: bool = False

    @property
    def query_status(self) -> Literal["complete", "sampled", "degraded"]:
        if self.query_complete:
            return "complete"
        if self.query_error_code == "sample_limit" and self.values:
            return "sampled"
        return "degraded"

    def metadata(self) -> FilterValueMetadata:
        payload: FilterValueMetadata = {
            "query_complete": self.query_complete,
            "query_status": self.query_status,
            "query_window_start": self.query_window_start.isoformat(),
            "query_window_end": self.query_window_end.isoformat(),
        }
        if self.query_error_code is not None:
            payload["query_error_code"] = self.query_error_code
        return payload


def _window(*, lookback_days: int, now: datetime | None) -> tuple[datetime, datetime]:
    if not 1 <= int(lookback_days) <= 365:
        raise ValueError("filter-value lookback must be between 1 and 365 days")
    end = now or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    return end - timedelta(days=int(lookback_days)), end


def _latest_span_value_cte(source_column: str) -> str:
    return f"""
        latest_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(tuple({source_column}), _version).1 AS raw_value
            FROM spans
            PREWHERE project_id IN %(project_ids)s
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
            GROUP BY project_id, trace_id, id, start_time
        )
    """


def _latest_voice_value_cte() -> str:
    """Latest root fields needed by normalized voice response expressions."""

    return """
        latest_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(observation_type, _version) AS latest_observation_type,
                argMax(tuple(provider), _version).1 AS provider,
                argMax(tuple(cost), _version).1 AS cost,
                argMax(attrs_string, _version) AS attrs_string,
                argMax(attrs_number, _version) AS attrs_number,
                argMax(tuple(attributes_extra), _version).1 AS attributes_extra
            FROM spans
            PREWHERE project_id IN %(project_ids)s
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
            GROUP BY project_id, trace_id, id, start_time
        )
    """


def _system_value_expression(metric_name: str) -> tuple[str, str]:
    """Return the code-owned value expression and any remap join."""

    if metric_name == "session":
        join = remap_left_join(
            "latest_spans.raw_value",
            "trace_session_id_remap",
            "filter_value_session_remap",
        )
        value = resolved_id_expr("latest_spans.raw_value", "filter_value_session_remap")
        return value, join
    if metric_name == "tag":
        return (
            "arrayJoin(JSONExtract(latest_spans.raw_value, 'Array(String)'))",
            "",
        )
    if metric_name == "prompt_name":
        return "dictGet('prompt_dict', 'prompt_name', latest_spans.raw_value)", ""
    if metric_name == "prompt_version":
        return (
            "dictGet('prompt_dict', 'template_version', latest_spans.raw_value)",
            "",
        )
    if metric_name == "prompt_label":
        return "dictGet('prompt_label_dict', 'name', latest_spans.raw_value)", ""
    return "latest_spans.raw_value", ""


def read_span_system_filter_values(
    analytics: QueryExecutor,
    *,
    project_ids: list[str] | tuple[str, ...],
    metric_name: str,
    search: str = "",
    limit: int = 500,
    lookback_days: int = 7,
    now: datetime | None = None,
) -> FilterValueRead:
    """Return exact latest-state values within one finite partition window.

    ``query_complete=False/sample_limit`` means the exact distinct vocabulary
    exceeded the public picker cap; a timeout/resource exception is deliberately
    allowed to reach the API boundary for sanitized degraded handling.
    """

    if not 1 <= int(limit) <= 500:
        raise ValueError("filter-value limit must be between 1 and 500")
    voice_expression = _VOICE_SYSTEM_VALUE_EXPRESSIONS.get(metric_name)
    if voice_expression is None:
        try:
            source_column = _SYSTEM_VALUE_SOURCE_COLUMNS[metric_name]
        except KeyError as exc:
            raise ValueError("unsupported system filter-value metric") from exc
        latest_value_cte = _latest_span_value_cte(source_column)
    else:
        latest_value_cte = _latest_voice_value_cte()
    window_start, window_end = _window(lookback_days=lookback_days, now=now)
    project_scope = tuple(dict.fromkeys(str(value) for value in project_ids if value))
    if not project_scope:
        return FilterValueRead((), True, None, window_start, window_end)

    if voice_expression is None:
        value_expression, join = _system_value_expression(metric_name)
    else:
        value_expression, join = voice_expression, ""
    if voice_expression is not None:
        root_clause = (
            "AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '') "
            "AND latest_observation_type = 'conversation'"
        )
    elif metric_name == "name":
        root_clause = (
            "AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')"
        )
    else:
        root_clause = ""
    search_clause = (
        "AND positionCaseInsensitiveUTF8(toString(raw_picker_value), "
        "%(filter_value_search)s) > 0"
        if search
        else ""
    )
    query = f"""
        WITH {latest_value_cte}
        SELECT DISTINCT toString(raw_picker_value) AS val
        FROM (
            SELECT {value_expression} AS raw_picker_value
            FROM latest_spans
            {join}
            WHERE latest_is_deleted = 0
              {root_clause}
        )
        WHERE raw_picker_value IS NOT NULL
          AND toString(raw_picker_value) NOT IN (
              '', '00000000-0000-0000-0000-000000000000'
          )
          {search_clause}
        ORDER BY val
        LIMIT %(result_limit)s
    """
    params: dict[str, Any] = {
        "project_ids": project_scope,
        "window_start": window_start,
        "window_end": window_end,
        "result_limit": int(limit) + 1,
    }
    if search:
        params["filter_value_search"] = search
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=FILTER_VALUE_READ_TIMEOUT_MS,
        settings={
            **FILTER_VALUE_READ_SETTINGS,
            "max_result_rows": int(limit) + 1,
        },
    )
    rows = tuple(str(row["val"]) for row in (result.data or []))
    truncated = len(rows) > int(limit)
    return FilterValueRead(
        rows[: int(limit)],
        not truncated,
        "sample_limit" if truncated else None,
        window_start,
        window_end,
        has_more=truncated,
    )


def read_session_message_filter_values(
    analytics: QueryExecutor,
    *,
    project_id: str,
    message_position: str,
    search: str = "",
    page: int = 0,
    page_size: int = 50,
    lookback_days: int = 30,
    now: datetime | None = None,
) -> FilterValueRead:
    """Return a finite page of first/last messages from latest live root spans."""

    if message_position not in {"first", "last"}:
        raise ValueError("message_position must be first or last")
    if page < 0 or not 1 <= int(page_size) <= 500:
        raise ValueError("invalid session filter-value page")
    window_start, window_end = _window(lookback_days=lookback_days, now=now)
    session_join = remap_left_join(
        "latest_roots.latest_trace_session_id",
        "trace_session_id_remap",
        "message_session_remap",
    )
    resolved_session = resolved_id_expr(
        "latest_roots.latest_trace_session_id", "message_session_remap"
    )
    aggregate = "argMin" if message_position == "first" else "argMax"
    search_clause = (
        "AND positionCaseInsensitiveUTF8(val, %(filter_value_search)s) > 0"
        if search
        else ""
    )
    query = f"""
        WITH latest_roots AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(tuple(trace_session_id), _version).1
                    AS latest_trace_session_id,
                argMax(tuple(input), _version).1 AS latest_input
            FROM spans
            PREWHERE project_id = toUUID(%(project_id)s)
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
            GROUP BY project_id, trace_id, id, start_time
        ),
        session_messages AS (
            SELECT
                {resolved_session} AS resolved_trace_session_id,
                {aggregate}(latest_input, start_time) AS val
            FROM latest_roots
            {session_join}
            WHERE latest_is_deleted = 0
              AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')
              AND latest_trace_session_id IS NOT NULL
              AND latest_trace_session_id != toUUID('{NIL_UUID}')
            GROUP BY resolved_trace_session_id
        )
        SELECT DISTINCT toString(val) AS val
        FROM session_messages
        WHERE val IS NOT NULL
          AND toString(val) != ''
          {search_clause}
        ORDER BY val
        LIMIT %(result_limit)s OFFSET %(result_offset)s
    """
    params: dict[str, Any] = {
        "project_id": str(project_id),
        "window_start": window_start,
        "window_end": window_end,
        "result_limit": int(page_size) + 1,
        "result_offset": int(page) * int(page_size),
    }
    if search:
        params["filter_value_search"] = search
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=FILTER_VALUE_READ_TIMEOUT_MS,
        settings={
            **FILTER_VALUE_READ_SETTINGS,
            "max_result_rows": int(page_size) + 1,
        },
    )
    rows = tuple(str(row["val"]) for row in (result.data or []))
    has_more = len(rows) > int(page_size)
    # This is a genuine numbered page, not a cardinality sample. A has-more
    # sentinel does not make the returned page inexact.
    return FilterValueRead(
        rows[: int(page_size)],
        True,
        None,
        window_start,
        window_end,
        has_more=has_more,
    )


__all__ = [
    "FILTER_VALUE_MAX_BYTES_TO_READ",
    "FILTER_VALUE_MAX_MEMORY_USAGE",
    "FILTER_VALUE_MAX_ROWS_TO_READ",
    "FILTER_VALUE_READ_SETTINGS",
    "FILTER_VALUE_READ_TIMEOUT_MS",
    "SYSTEM_FILTER_VALUE_METRICS",
    "FilterValueRead",
    "read_session_message_filter_values",
    "read_span_system_filter_values",
]
