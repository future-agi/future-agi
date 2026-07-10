"""ClickHouse span reads for the cluster RCA agent.

Scope (current): read a trace's spans, and read one span — the hot path
(trace summarizer bundle + read(trace) skeleton + read(span)). CH ``spans``
is the source of truth for tracing telemetry; the 656 GB PG table is the
legacy mirror.

Reads are project-scoped, dedup the ReplacingMergeTree via ``LIMIT 1 BY id``,
and filter ``is_deleted = 0`` — mirroring the prod span_list builder.
Returns are column-keyed dicts (the natural DB-row shape); the agent reshapes
them into LLM-facing payloads with aliases.

Cluster-wide span reads (list/search/aggregate across a trace set) still run
on PG and migrate here when needed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.span_reader import CHSpan

logger = structlog.get_logger(__name__)


# Columns cluster_rca needs off a span. Aliased to the agent's vocabulary.
# Includes the trace-level fields denormalized onto every span row
# (trace_name / trace_session_id) so the agent can derive trace context
# without a separate PG Trace read.
_SPAN_COLS = (
    "toString(id) AS span_id",
    "toString(trace_id) AS trace_id",
    "parent_span_id",
    "name",
    "observation_type",
    "operation_name",
    "status",
    "status_message",
    "latency_ms",
    "toString(start_time) AS start_time",
    "toString(end_time) AS end_time",
    "input",
    "output",
    "model",
    "provider",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "tags",
    "trace_name",
    "toString(trace_session_id) AS trace_session_id",
)


def _rows_to_dicts(rows: list, cols: tuple) -> list[dict[str, Any]]:
    keys = [c.split(" AS ")[-1].strip() for c in cols]
    return [dict(zip(keys, row, strict=False)) for row in rows]


# Agent dict keys, derived from _SPAN_COLS so the raw-SQL and CHSpan aliasing
# paths can't drift (test_cluster_rca_spans pins the equality).
_AGENT_SPAN_KEYS = tuple(c.split(" AS ")[-1].strip() for c in _SPAN_COLS)


def _chspan_to_agent_dict(s: CHSpan) -> dict[str, Any]:
    """Map a CHSpan to the agent's column-aliased dict (same keys as
    ``_rows_to_dicts(_SPAN_COLS)``). ``span_id`` is ``CHSpan.id``; times are
    stringified to match the prior ``toString(...)`` shape.
    """
    return {
        "span_id": str(s.id),
        "trace_id": str(s.trace_id),
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "observation_type": s.observation_type,
        "operation_name": s.operation_name,
        "status": s.status,
        "status_message": s.status_message,
        "latency_ms": s.latency_ms,
        "start_time": str(s.start_time) if s.start_time else None,
        "end_time": str(s.end_time) if s.end_time else None,
        "input": s.input,
        "output": s.output,
        "model": s.model,
        "provider": s.provider,
        "prompt_tokens": s.prompt_tokens,
        "completion_tokens": s.completion_tokens,
        "total_tokens": s.total_tokens,
        "cost": s.cost,
        "tags": s.tags,
        "trace_name": s.trace_name,
        "trace_session_id": s.trace_session_id,
    }


def _ids_list(trace_ids: Iterable[str]) -> list[str]:
    return [str(t) for t in trace_ids if t]


# Pooled clickhouse-driver client (the analytics layer's singleton); the agent's
# raw-SQL reads share it instead of opening a fresh HTTP client per query.
_analytics = AnalyticsQueryService()

# Per-query CH timeout for the agent's aggregation reads (ms).
_READ_TIMEOUT_MS = 10000


def _execute_read(query: str, params: dict) -> list:
    # Route through AnalyticsQueryService (pooled clickhouse-driver). It returns
    # column-keyed dicts; re-tuple in driver column order so the positional
    # consumers in this file keep working unchanged. clickhouse-driver binds a
    # list directly for ``IN %(x)s``, so no tuple normalization is needed.
    try:
        result = _analytics.execute_ch_query(query, params, timeout_ms=_READ_TIMEOUT_MS)
        cols = result.columns or []
        return [tuple(row[c] for c in cols) for row in result.data]
    except Exception as e:
        logger.warning("cluster_rca_ch_span_read_failed", error=str(e))
        return []


def spans_for_trace(project_id: str, trace_id: str) -> list[dict[str, Any]]:
    """All spans of one trace, ordered by start_time (full columns).

    Powers the trace summarizer's top-down bundle and read(trace)'s span
    skeleton. Empty list when CH is unavailable.
    """
    if not trace_id:
        return []
    # Delegate to the canonical CHSpanReader (v2, FINAL-deduped, project-scoped)
    # instead of a parallel raw-SQL read; alias CHSpan → the agent dict shape.
    try:
        reader = get_reader()
        try:
            spans = reader.list_by_trace(str(trace_id), project_id=str(project_id))
        finally:
            reader.close()
    except Exception as e:
        logger.warning("cluster_rca_ch_span_read_failed", error=str(e))
        return []
    return [_chspan_to_agent_dict(s) for s in spans]


def read_span(project_id: str, span_id: str) -> dict[str, Any] | None:
    """One span by id, or None if absent / CH unavailable. Delegates to the
    canonical CHSpanReader (v2, FINAL-deduped, project-scoped)."""
    if not span_id:
        return None
    try:
        reader = get_reader()
        try:
            span = reader.get(str(span_id), project_id=str(project_id))
        finally:
            reader.close()
    except Exception as e:
        logger.warning("cluster_rca_ch_span_read_failed", error=str(e))
        return None
    return _chspan_to_agent_dict(span) if span else None


def list_spans_in_traces(
    project_id: str,
    trace_ids: Iterable[str],
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated span list across a trace set. Returns (page, total_distinct)."""
    ids = _ids_list(trace_ids)
    if not ids:
        return [], 0
    cols = ", ".join(_SPAN_COLS)
    base = (
        "FROM spans WHERE project_id = %(pid)s AND is_deleted = 0 "
        "AND toString(trace_id) IN %(tids)s"
    )
    params = {"pid": str(project_id), "tids": ids, "limit": limit, "offset": offset}
    page = _rows_to_dicts(
        _execute_read(
            f"SELECT {cols} {base} ORDER BY trace_id, start_time "
            "LIMIT 1 BY id LIMIT %(limit)s OFFSET %(offset)s",
            params,
        ),
        _SPAN_COLS,
    )
    total_rows = _execute_read(f"SELECT uniqExact(id) {base}", params)
    return page, (int(total_rows[0][0]) if total_rows else 0)


def search_spans_in_traces(
    project_id: str,
    trace_ids: Iterable[str],
    query_text: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Case-insensitive substring search over span input/output/name/
    status_message within a trace set."""
    ids = _ids_list(trace_ids)
    if not ids or not query_text:
        return []
    cols = ", ".join(_SPAN_COLS)
    text_pred = (
        "(positionCaseInsensitive(input, %(q)s) > 0 "
        "OR positionCaseInsensitive(output, %(q)s) > 0 "
        "OR positionCaseInsensitive(name, %(q)s) > 0 "
        "OR positionCaseInsensitive(ifNull(status_message, ''), %(q)s) > 0)"
    )
    query = f"""
        SELECT {cols}
        FROM spans
        WHERE project_id = %(pid)s AND is_deleted = 0
          AND toString(trace_id) IN %(tids)s
          AND {text_pred}
        ORDER BY trace_id, start_time
        LIMIT 1 BY id
        LIMIT %(limit)s
    """
    params = {"pid": str(project_id), "tids": ids, "q": query_text, "limit": limit}
    return _rows_to_dicts(_execute_read(query, params), _SPAN_COLS)


# group_by token → (CH column, extra predicate) for span_count aggregation.
_SPAN_AGG_FIELDS: dict[str, tuple[str, str | None]] = {
    "span_tool_name": ("name", "observation_type = 'tool'"),
    "span_status": ("status", None),
    "span_type": ("observation_type", None),
}


def aggregate_span_field(
    project_id: str,
    trace_ids: Iterable[str],
    group_by: str,
) -> tuple[list[dict[str, Any]], int] | None:
    """span_count grouped by a whitelisted field (deduped via LIMIT 1 BY id).
    Returns (buckets, total) or None if group_by isn't whitelisted."""
    if group_by not in _SPAN_AGG_FIELDS:
        return None
    ids = _ids_list(trace_ids)
    if not ids:
        return [], 0
    field, extra = _SPAN_AGG_FIELDS[group_by]
    extra_pred = f"AND {extra}" if extra else ""
    query = f"""
        SELECT {field} AS k, count() AS c
        FROM (
            SELECT id, {field}
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s
              {extra_pred}
            LIMIT 1 BY id
        )
        GROUP BY k ORDER BY c DESC
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    buckets = [{"key": r[0] or "(none)", "count": r[1]} for r in rows]
    return buckets, sum(b["count"] for b in buckets)


def trace_roots(project_id: str, trace_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per-trace representative (root) I/O + context, keyed by trace_id.

    The trace's I/O is its earliest span's I/O (argMin on start_time). Used
    for list(traces) snippets, read(session) member previews, etc. — a single
    batched query instead of N per-trace fetches.
    """
    ids = _ids_list(trace_ids)
    if not ids:
        return {}
    query = """
        SELECT
            trace_id,
            argMin(input, start_time)  AS input,
            argMin(output, start_time) AS output,
            any(trace_name)            AS trace_name,
            toString(any(trace_session_id)) AS trace_session_id,
            max(status = 'ERROR')      AS has_error,
            min(start_time)            AS first_start
        FROM (
            SELECT trace_id, input, output, trace_name, trace_session_id,
                   status, start_time, id
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s
            LIMIT 1 BY id
        )
        GROUP BY trace_id
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[str(r[0])] = {
            "input": r[1],
            "output": r[2],
            "trace_name": r[3],
            "trace_session_id": r[4] or None,
            "has_error": bool(r[5]),
            "first_start": str(r[6]) if r[6] else None,
        }
    return out


# group_by token → CH trace-level column for trace_count aggregation.
_TRACE_AGG_FIELDS: dict[str, str] = {
    "version": "toString(project_version_id)",
    "session_id": "toString(trace_session_id)",
}


def aggregate_trace_field(
    project_id: str, trace_ids: Iterable[str], group_by: str
) -> tuple[list[dict[str, Any]], int] | None:
    """trace_count grouped by a CH trace-level field (version / session_id).
    Counts distinct traces per value. Returns (buckets, total) or None if
    group_by isn't a CH trace field (caller falls back to PG/relational)."""
    field = _TRACE_AGG_FIELDS.get(group_by)
    if field is None:
        return None
    ids = _ids_list(trace_ids)
    if not ids:
        return [], 0
    query = f"""
        SELECT k, uniqExact(trace_id) AS c
        FROM (
            SELECT toString(trace_id) AS trace_id, {field} AS k, id
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s
            LIMIT 1 BY id
        )
        GROUP BY k ORDER BY c DESC
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    buckets = [{"key": r[0] or "(none)", "count": r[1]} for r in rows]
    return buckets, len(ids)


def timeline_trace_counts(
    project_id: str, trace_ids: Iterable[str], bucket: str = "hour"
) -> list[dict[str, Any]]:
    """Distinct-trace count per time bucket (trace's earliest span time)."""
    bucket_fn = {
        "minute": "toStartOfMinute",
        "hour": "toStartOfHour",
        "day": "toStartOfDay",
    }.get(bucket)
    ids = _ids_list(trace_ids)
    if not bucket_fn or not ids:
        return []
    query = f"""
        SELECT {bucket_fn}(first_start) AS b, count() AS c
        FROM (
            SELECT trace_id, min(start_time) AS first_start
            FROM (
                SELECT trace_id, start_time, id FROM spans
                WHERE project_id = %(pid)s AND is_deleted = 0
                  AND toString(trace_id) IN %(tids)s
                LIMIT 1 BY id
            )
            GROUP BY trace_id
        )
        GROUP BY b ORDER BY b
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    return [{"bucket_start": str(r[0]) if r[0] else None, "count": r[1]} for r in rows]


def search_trace_ids(
    project_id: str, trace_ids: Iterable[str], query_text: str, *, limit: int = 50
) -> list[str]:
    """Distinct trace_ids whose any span matches the text (input/output/name)."""
    ids = _ids_list(trace_ids)
    if not ids or not query_text:
        return []
    query = """
        SELECT DISTINCT toString(trace_id)
        FROM spans
        WHERE project_id = %(pid)s AND is_deleted = 0
          AND toString(trace_id) IN %(tids)s
          AND (positionCaseInsensitive(input, %(q)s) > 0
            OR positionCaseInsensitive(output, %(q)s) > 0
            OR positionCaseInsensitive(name, %(q)s) > 0)
        LIMIT %(limit)s
    """
    rows = _execute_read(
        query,
        {"pid": str(project_id), "tids": ids, "q": query_text, "limit": limit},
    )
    return [r[0] for r in rows]


def distinct_sessions(project_id: str, trace_ids: Iterable[str]) -> list[str]:
    """Distinct trace_session_id values across a trace set (non-empty)."""
    ids = _ids_list(trace_ids)
    if not ids:
        return []
    query = """
        SELECT DISTINCT toString(trace_session_id)
        FROM spans
        WHERE project_id = %(pid)s AND is_deleted = 0
          AND toString(trace_id) IN %(tids)s
          AND trace_session_id IS NOT NULL
          AND toString(trace_session_id) != ''
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    return [r[0] for r in rows if r[0]]


def traces_in_session(project_id: str, session_id: str) -> dict[str, dict[str, Any]]:
    """Per-trace root info for all traces in one session (chronological via
    first_start). Powers read(session). Returns {trace_id: root-info}."""
    if not session_id:
        return {}
    query = """
        SELECT
            trace_id,
            argMin(input, start_time)  AS input,
            argMin(output, start_time) AS output,
            any(trace_name)            AS trace_name,
            max(status = 'ERROR')      AS has_error,
            min(start_time)            AS first_start
        FROM (
            SELECT trace_id, input, output, trace_name, status, start_time, id
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_session_id) = %(sid)s
            LIMIT 1 BY id
        )
        GROUP BY trace_id
        ORDER BY first_start
    """
    rows = _execute_read(query, {"pid": str(project_id), "sid": str(session_id)})
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[str(r[0])] = {
            "input": r[1],
            "output": r[2],
            "trace_name": r[3],
            "has_error": bool(r[4]),
            "first_start": str(r[5]) if r[5] else None,
        }
    return out


def error_messages_in_traces(
    project_id: str,
    trace_ids: Iterable[str],
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Distinct ERROR-status span status_messages with counts. Returns
    (page_items, total_distinct)."""
    ids = _ids_list(trace_ids)
    if not ids:
        return [], 0
    query = """
        SELECT status_message AS k, count() AS c
        FROM (
            SELECT id, status_message
            FROM spans
            WHERE project_id = %(pid)s AND is_deleted = 0
              AND toString(trace_id) IN %(tids)s
              AND status = 'ERROR'
              AND notEmpty(ifNull(status_message, ''))
            LIMIT 1 BY id
        )
        GROUP BY k ORDER BY c DESC
    """
    rows = _execute_read(query, {"pid": str(project_id), "tids": ids})
    total = len(rows)
    page = rows[offset : offset + limit]
    return [{"key": r[0], "count": r[1]} for r in page], total
