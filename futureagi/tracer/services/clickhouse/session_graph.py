"""Direct-ClickHouse session graph dispatch with one request-owned read budget."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from time import monotonic
from typing import Any

from django.conf import settings

from tracer.services.clickhouse.bounded_graph_reads import (
    GRAPH_CANDIDATE_LIMIT,
    GRAPH_DECORATION_CANDIDATE_DEADLINE_MS,
    BoundedGraphReadError,
    GraphCandidateSample,
    read_graph_candidates,
)
from tracer.services.clickhouse.graph_dispatch import (
    degraded_graph_response,
    fetch_annotation_graph_ch,
    fetch_eval_graph_ch,
    format_system_metric_graph,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.session_time_series import (
    SessionRollupTimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.query_service import QueryExecutor, QueryResult
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.exact_aggregation_cache import (
    read_or_schedule_exact_snapshot,
)

SESSION_GRAPH_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_GRAPH_QUERY_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
SESSION_GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS = (
    settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
)
SESSION_GRAPH_RESULT_BYTES = settings.DASHBOARD_ROLLUP_MAX_RESULT_BYTES
SESSION_GRAPH_HYDRATION_BATCH_SIZE = 512
SESSION_GRAPH_MAX_HYDRATION_QUERIES = (
    GRAPH_CANDIDATE_LIMIT + SESSION_GRAPH_HYDRATION_BATCH_SIZE - 1
) // SESSION_GRAPH_HYDRATION_BATCH_SIZE
SESSION_SYSTEM_METRICS = frozenset(
    {
        "latency",
        "tokens",
        "cost",
        "traffic",
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "error_rate",
        "session_count",
        "avg_duration",
        "avg_traces_per_session",
        "total_cost",
    }
)
_SESSION_IDENTITY_ONLY_METRICS = frozenset(
    {"traffic", "session_count", "avg_traces_per_session"}
)
_SESSION_ROLLUP_METRICS = SESSION_SYSTEM_METRICS - {"avg_traces_per_session"}
_SESSION_ROLLUP_RESULT_COLUMNS = frozenset(
    {
        "time_bucket",
        "avg_latency",
        "total_tokens",
        "avg_cost",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_rate",
        "session_count",
        "avg_duration",
        "avg_traces_per_session",
        "total_cost_sum",
    }
)

_SESSION_GRAPH_READ_CAPS = {
    "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "max_result_rows": 10_001,
    "max_result_bytes": SESSION_GRAPH_RESULT_BYTES,
}


def _bounded_read_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Merge one graph phase's settings without permitting it to exceed caps."""

    bounded = dict(_SESSION_GRAPH_READ_CAPS)
    for key, value in (settings or {}).items():
        if key == "max_rows_to_read":
            # Large but valid historical windows must be allowed to finish;
            # bytes, memory, result size, query count and the shared wall clock
            # remain authoritative bounds.
            continue
        if key in _SESSION_GRAPH_READ_CAPS:
            bounded[key] = min(int(value), int(_SESSION_GRAPH_READ_CAPS[key]))
        else:
            bounded[key] = value
    bounded.update(
        {
            "read_overflow_mode": "throw",
            "result_overflow_mode": "throw",
            "timeout_overflow_mode": "throw",
        }
    )
    return bounded


def _has_only_positive_window_filters(filters: list[dict[str, Any]]) -> bool:
    """Return whether the session rollup can answer the whole filter shape."""

    return all(
        (item.get("column_id") or item.get("columnId")) in {"created_at", "start_time"}
        and not BaseQueryBuilder.is_datetime_complement_filter(item)
        for item in filters
    )


def _require_rollup_result_shape(rows: list[Any], columns: list[str]) -> None:
    """Reject schema drift instead of publishing a successful zero graph."""

    missing = _SESSION_ROLLUP_RESULT_COLUMNS.difference(columns)
    if missing:
        raise BoundedGraphReadError("query_failed")
    bucket_index = columns.index("time_bucket")
    for row in rows:
        bucket = (
            row.get("time_bucket")
            if isinstance(row, dict)
            else row[bucket_index]
            if bucket_index < len(row)
            else None
        )
        if bucket is None:
            raise BoundedGraphReadError("query_failed")


def _fetch_rollup_system_metric_graph(
    *,
    analytics: QueryExecutor,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    started: float,
) -> dict[str, Any]:
    """Execute one retained-rollup statement inside the interactive deadline."""

    builder = SessionRollupTimeSeriesQueryBuilder(
        project_id=project_id,
        filters=filters,
        interval=interval,
    )
    query, params = builder.build()
    if (
        builder.start_date is not None
        and builder.end_date is not None
        and builder.start_date >= builder.end_date
    ):
        rows: list[Any] = []
        columns: list[str] = []
        query_count = 0
    else:
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=SESSION_GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
            settings={"max_threads": 4},
        )
        rows = list(result.data or [])
        columns = list(result.columns or [])
        _require_rollup_result_shape(rows, columns)
        query_count = 1
    response = format_system_metric_graph(
        builder.format_result(rows, columns),
        metric_id,
    )
    response.update(
        {
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_count": query_count,
            "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
            "query_rows_returned": len(rows),
            "query_provenance": "materialized_rollup",
            "query_exact": False,
        }
    )
    return response


class _DeadlineBoundAnalytics:
    """Clamp every nested graph query to one monotonic request deadline."""

    def __init__(self, delegate: QueryExecutor, deadline: ReadDeadline) -> None:
        self._delegate = delegate
        self._deadline = deadline
        self.supports_per_query_read_settings = bool(
            getattr(delegate, "supports_per_query_read_settings", True)
        )

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = SESSION_GRAPH_QUERY_TIMEOUT_MS,
        settings: dict[str, Any] | None = None,
    ) -> QueryResult:
        requested_timeout_ms = min(
            int(timeout_ms),
            SESSION_GRAPH_QUERY_TIMEOUT_MS,
        )
        return self._delegate.execute_ch_query(
            query,
            params or {},
            timeout_ms=self._deadline.remaining_ms(requested_timeout_ms),
            settings=_bounded_read_settings(settings),
        )


def _canonical_session_ids(
    *,
    analytics: QueryExecutor,
    session_ids: tuple[str, ...],
) -> tuple[dict[str, str], int, int]:
    """Resolve a finite set of old/new session IDs without a global remap join."""

    if not session_ids:
        return {}, 0, 0
    # Resolve the complete consolidation group for every requested old or new
    # ID. This is the finite equivalent of survivor_map_subquery: every old and
    # its shared new ID map to the lexicographically-smallest old ID.
    limit = len(session_ids) * 8 + 1
    query = """
    WITH relevant_new_ids AS (
        SELECT DISTINCT new_id
        FROM trace_session_id_remap FINAL
        WHERE toString(old_id) IN %(session_ids)s
           OR toString(new_id) IN %(session_ids)s
    )
    SELECT toString(old_id) AS old_id, toString(new_id) AS new_id
    FROM trace_session_id_remap FINAL
    WHERE new_id IN (SELECT new_id FROM relevant_new_ids)
    ORDER BY new_id, old_id
    LIMIT %(remap_limit)s
    """
    result = analytics.execute_ch_query(
        query,
        {"session_ids": session_ids, "remap_limit": limit},
        timeout_ms=SESSION_GRAPH_QUERY_TIMEOUT_MS,
        settings={"max_result_rows": limit},
    )
    rows = list(result.data or [])
    if len(rows) >= limit:
        raise BoundedGraphReadError("sample_limit")
    groups: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        old_id = str(row.get("old_id") or "")
        new_id = str(row.get("new_id") or "")
        if old_id and new_id:
            groups[new_id].add(old_id)
    resolved = {session_id: session_id for session_id in session_ids}
    for new_id, old_ids in groups.items():
        survivor = min(old_ids)
        resolved[new_id] = survivor
        resolved.update(dict.fromkeys(old_ids, survivor))
    return resolved, len(rows), _result_payload_bytes(rows)


def _result_payload_bytes(rows: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _hydrate_session_trace_candidates(
    *,
    analytics: QueryExecutor,
    project_id: str,
    filters: list[dict[str, Any]],
    rows: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], int, int, int]:
    """Hydrate metrics for only the finite, proven canonical trace roots.

    Graph candidate discovery intentionally classifies trace identities without
    presentation columns. Session reducers need the canonical roots' metrics,
    so replay those exact ``(project, trace, root, start_time)`` identities in
    bounded batches. Missing, duplicated, or session-drifted rows fail closed;
    a classifier result is never silently replaced with a different root.
    """

    if not rows:
        return rows, 0, 0, 0
    if len(rows) > GRAPH_CANDIDATE_LIMIT:
        raise BoundedGraphReadError("sample_limit")

    builder = TraceListQueryBuilderV2(
        project_id=project_id,
        filters=filters,
        bounded_identity_only=True,
    )
    try:
        expected_by_identity = {
            builder.bounded_filter_page_hydration_identity(row): row for row in rows
        }
    except (KeyError, TypeError, ValueError):
        raise BoundedGraphReadError("query_failed") from None
    if len(expected_by_identity) != len(rows):
        raise BoundedGraphReadError("query_failed")

    hydrated_by_identity: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
    query_count = 0
    rows_returned = 0
    result_payload_bytes = 0
    ordered_identities = tuple(expected_by_identity)
    for offset in range(0, len(rows), SESSION_GRAPH_HYDRATION_BATCH_SIZE):
        batch_identities = ordered_identities[
            offset : offset + SESSION_GRAPH_HYDRATION_BATCH_SIZE
        ]
        batch_rows = [expected_by_identity[identity] for identity in batch_identities]
        try:
            query, params = builder.build_filter_page_hydration_query(batch_rows)
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=SESSION_GRAPH_QUERY_TIMEOUT_MS,
                settings={"max_result_rows": len(batch_rows)},
            )
        except BoundedGraphReadError:
            raise
        except Exception as exc:
            if is_read_budget_error(exc) or is_clickhouse_query_error(exc):
                error_code = (
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                )
                raise BoundedGraphReadError(error_code, retryable=True) from None
            raise

        hydrated_rows = list(result.data or [])
        batch_identity_set = set(batch_identities)
        for hydrated_row in hydrated_rows:
            try:
                identity = builder.bounded_filter_page_hydration_identity(hydrated_row)
            except (KeyError, TypeError, ValueError):
                raise BoundedGraphReadError("query_failed") from None
            if identity not in batch_identity_set or identity in hydrated_by_identity:
                raise BoundedGraphReadError("query_failed")
            hydrated_by_identity[identity] = hydrated_row
        if not batch_identity_set.issubset(hydrated_by_identity):
            raise BoundedGraphReadError("query_failed")
        query_count += 1
        if query_count > SESSION_GRAPH_MAX_HYDRATION_QUERIES:
            raise BoundedGraphReadError("sample_limit")
        rows_returned += len(hydrated_rows)
        result_payload_bytes += _result_payload_bytes(hydrated_rows)
        if result_payload_bytes > SESSION_GRAPH_RESULT_BYTES:
            raise BoundedGraphReadError("sample_limit")

    if set(hydrated_by_identity) != set(expected_by_identity):
        raise BoundedGraphReadError("query_failed")
    for identity, candidate_row in expected_by_identity.items():
        candidate_session_id = str(candidate_row.get("trace_session_id") or "")
        hydrated_session_id = str(
            hydrated_by_identity[identity].get("trace_session_id") or ""
        )
        if not candidate_session_id or hydrated_session_id != candidate_session_id:
            raise BoundedGraphReadError("query_failed")

    return (
        tuple(hydrated_by_identity[identity] for identity in ordered_identities),
        query_count,
        rows_returned,
        result_payload_bytes,
    )


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _session_metric_value(metric_id: str, state: dict[str, Any]) -> float:
    if metric_id == "latency":
        return state["latency_sum"] / max(state["latency_count"], 1)
    if metric_id in {"tokens", "total_tokens"}:
        return state["total_tokens"]
    if metric_id in {"prompt_tokens", "input_tokens"}:
        return state["prompt_tokens"]
    if metric_id in {"completion_tokens", "output_tokens"}:
        return state["completion_tokens"]
    if metric_id == "cost":
        return state["cost_sum"]
    if metric_id == "total_cost":
        return state["cost_sum"]
    if metric_id == "traffic" or metric_id == "session_count":
        return 1.0
    if metric_id == "error_rate":
        return 100.0 if state["has_error"] else 0.0
    if metric_id == "avg_duration":
        return state["duration_seconds"]
    if metric_id == "avg_traces_per_session":
        return float(state["trace_count"])
    raise ValueError("Unsupported session system metric")


def _aggregate_session_candidates(
    *,
    sample: GraphCandidateSample,
    rows: tuple[dict[str, Any], ...],
    session_id_map: dict[str, str],
    interval: str,
    metric_id: str,
    started: float,
    extra_query_count: int,
    extra_rows_returned: int,
    extra_result_payload_bytes: int,
) -> dict[str, Any]:
    def response_metadata() -> dict[str, Any]:
        metadata = sample.metadata()
        metadata.update(
            {
                "query_count": sample.query_count + extra_query_count,
                "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
                "query_rows_returned": sample.rows_returned + extra_rows_returned,
                "query_result_bytes": (
                    sample.result_payload_bytes + extra_result_payload_bytes
                ),
            }
        )
        return metadata

    if sample.window_start >= sample.window_end:
        return {
            "metric_name": metric_id,
            "data": [],
            **response_metadata(),
        }
    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw_session_id = str(row.get("trace_session_id") or "")
        trace_id = str(row.get("trace_id") or "")
        start_time = row.get("start_time")
        if not raw_session_id or not trace_id or not isinstance(start_time, datetime):
            raise BoundedGraphReadError("query_failed")
        session_id = session_id_map.get(raw_session_id, raw_session_id)
        end_time = row.get("end_time")
        if not isinstance(end_time, datetime) or end_time < start_time:
            end_time = start_time
        state = sessions.setdefault(
            session_id,
            {
                "start_time": start_time,
                "end_time": end_time,
                "trace_ids": set(),
                "latency_sum": 0.0,
                "latency_count": 0,
                "cost_sum": 0.0,
                "total_tokens": 0.0,
                "prompt_tokens": 0.0,
                "completion_tokens": 0.0,
                "has_error": False,
            },
        )
        state["start_time"] = min(state["start_time"], start_time)
        state["end_time"] = max(state["end_time"], end_time)
        state["trace_ids"].add(trace_id)
        if row.get("latency_ms") is not None:
            state["latency_sum"] += _number(row.get("latency_ms"))
            state["latency_count"] += 1
        state["cost_sum"] += _number(row.get("cost"))
        state["total_tokens"] += _number(row.get("total_tokens"))
        state["prompt_tokens"] += _number(row.get("prompt_tokens"))
        state["completion_tokens"] += _number(row.get("completion_tokens"))
        state["has_error"] = state["has_error"] or str(
            row.get("status") or ""
        ).upper() in {"ERROR", "ERRORED", "FAILED"}

    buckets: dict[datetime, list[float]] = defaultdict(list)
    traffic: dict[datetime, int] = defaultdict(int)
    for state in sessions.values():
        state["trace_count"] = len(state["trace_ids"])
        state["duration_seconds"] = max(
            (state["end_time"] - state["start_time"]).total_seconds(), 0.0
        )
        bucket = BaseQueryBuilder._normalize_timestamp(state["start_time"], interval)
        buckets[bucket].append(_session_metric_value(metric_id, state))
        traffic[bucket] += 1

    points = []
    for index, timestamp in enumerate(
        BaseQueryBuilder._generate_timestamp_range(
            sample.window_start,
            sample.window_end,
            interval,
        )
    ):
        if index >= 10_000:
            raise BoundedGraphReadError("sample_limit")
        values = buckets.get(timestamp, [])
        if metric_id in {
            "tokens",
            "total_tokens",
            "prompt_tokens",
            "input_tokens",
            "completion_tokens",
            "output_tokens",
            "total_cost",
            "traffic",
            "session_count",
        }:
            value = sum(values)
        else:
            value = sum(values) / max(len(values), 1)
        points.append(
            {
                "timestamp": timestamp.isoformat(),
                "value": round(value, 9),
                "primary_traffic": traffic.get(timestamp, 0),
            }
        )

    return {
        "metric_name": metric_id,
        "data": points,
        **response_metadata(),
    }


def _fetch_system_metric_graph(
    *,
    analytics: QueryExecutor,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    started: float,
    deadline_ms: int = SESSION_GRAPH_WALL_DEADLINE_MS,
) -> dict[str, Any]:
    """Aggregate latest trace rows from an exact or fully executed sample."""

    if any(
        (item.get("column_id") or item.get("columnId"))
        in {
            "duration",
            "total_cost",
            "total_tokens",
            "traces_count",
            "total_traces_count",
        }
        for item in filters
    ):
        # These are post-session-aggregation HAVING predicates. Applying them
        # to trace rows would change membership, so fail closed until there is
        # a bounded session-level selector for those shapes.
        raise BoundedGraphReadError("unsupported_filter_shape")
    session_filters = _session_scoped_filters(filters)
    sample: GraphCandidateSample | None = None
    try:
        sample = read_graph_candidates(
            analytics=analytics,
            project_id=project_id,
            filters=session_filters,
            observe_type="trace",
            deadline_ms=min(
                int(deadline_ms),
                GRAPH_DECORATION_CANDIDATE_DEADLINE_MS,
            ),
        )
        # A session graph folds every selected trace into per-session aggregates.
        # A distributed sample changes membership and values, so it is renderable
        # only when all declared strata executed and the response retains the
        # selector's explicit sampled metadata. Failed/partial reads still fail
        # closed and never reach the reducer.
        if not sample.query_complete and not (
            sample.query_status == "sampled"
            and sample.query_error_code == "sample_limit"
            and sample.sampling_strategy
            and sample.sampling_strata > 0
            and sample.sampling_strata_completed == sample.sampling_strata
        ):
            raise BoundedGraphReadError(sample.query_error_code or "sample_limit")
        rows = tuple(sample.rows)
        hydration_query_count = 0
        hydration_rows_returned = 0
        hydration_result_payload_bytes = 0
        if metric_id not in _SESSION_IDENTITY_ONLY_METRICS:
            (
                rows,
                hydration_query_count,
                hydration_rows_returned,
                hydration_result_payload_bytes,
            ) = _hydrate_session_trace_candidates(
                analytics=analytics,
                project_id=project_id,
                filters=session_filters,
                rows=rows,
            )
        raw_session_ids = tuple(
            dict.fromkeys(
                str(row.get("trace_session_id"))
                for row in rows
                if row.get("trace_session_id")
            )
        )
        if rows and len(raw_session_ids) == 0:
            raise BoundedGraphReadError("query_failed")
        session_id_map, remap_rows_returned, remap_result_payload_bytes = (
            _canonical_session_ids(
                analytics=analytics,
                session_ids=raw_session_ids,
            )
        )
        return _aggregate_session_candidates(
            sample=sample,
            rows=rows,
            session_id_map=session_id_map,
            interval=interval,
            metric_id=metric_id,
            started=started,
            extra_query_count=(hydration_query_count + (1 if raw_session_ids else 0)),
            extra_rows_returned=hydration_rows_returned + remap_rows_returned,
            extra_result_payload_bytes=(
                hydration_result_payload_bytes + remap_result_payload_bytes
            ),
        )
    except BoundedGraphReadError as exc:
        return degraded_graph_response(
            metric_id,
            exc,
            sample=sample,
            provenance="bounded_candidates",
        )
    except Exception as exc:
        if not is_read_budget_error(exc):
            raise
        return degraded_graph_response(
            metric_id,
            exc,
            sample=sample,
            provenance="bounded_candidates",
        )


def _session_scoped_filters(
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Require graph candidates to belong to a non-null trace session."""

    return [
        *filters,
        {
            "column_id": "trace_session_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "is_not_null",
                "filter_value": None,
            },
        },
    ]


def fetch_session_graph_ch(
    *,
    analytics: QueryExecutor,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    wall_deadline_ms: int = SESSION_GRAPH_WALL_DEADLINE_MS,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch rollups, bounded average-trace samples, or exact snapshots."""

    if wall_deadline_ms <= 0:
        raise ValueError("session graph wall deadline must be positive")
    metric_type = str(req_data_config.get("type") or "")
    metric_id = str(req_data_config.get("id") or "session_count")
    filters = list(filters or [])

    if metric_type == "SYSTEM_METRIC":
        if metric_id not in SESSION_SYSTEM_METRICS:
            raise ValueError("Unsupported session system metric")
        if metric_id in _SESSION_ROLLUP_METRICS and _has_only_positive_window_filters(
            filters
        ):
            if not bool(getattr(analytics, "supports_per_query_read_settings", True)):
                return degraded_graph_response(
                    metric_id,
                    BoundedGraphReadError("query_failed", retryable=True),
                    provenance="server_read_policy_unavailable",
                )
            started = monotonic()
            deadline = ReadDeadline.start(
                min(
                    int(wall_deadline_ms),
                    SESSION_GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
                )
            )
            return _fetch_rollup_system_metric_graph(
                analytics=_DeadlineBoundAnalytics(analytics, deadline),
                project_id=str(project_id),
                filters=filters,
                interval=interval,
                metric_id=metric_id,
                started=started,
            )
        if metric_id == "avg_traces_per_session" and _has_only_positive_window_filters(
            filters
        ):
            started = monotonic()
            deadline_ms = min(int(wall_deadline_ms), SESSION_GRAPH_WALL_DEADLINE_MS)
            response = _fetch_system_metric_graph(
                analytics=_DeadlineBoundAnalytics(
                    analytics,
                    ReadDeadline.start(deadline_ms),
                ),
                project_id=str(project_id),
                filters=filters,
                interval=interval,
                metric_id=metric_id,
                started=started,
                deadline_ms=deadline_ms,
            )
            response.update(
                {
                    "query_exact": False,
                    "query_provenance": "bounded_candidates",
                }
            )
            return response
        identity = {
            "project_id": str(project_id),
            "filters": filters,
            "interval": interval,
            "metric_id": metric_id,
        }
        if organization_id is not None:
            identity["organization_id"] = str(organization_id)
        if workspace_id is not None:
            identity["workspace_id"] = str(workspace_id)
        return read_or_schedule_exact_snapshot(
            "observe-session-system-graph",
            identity,
            refresh=bool(refresh),
            pending_payload={
                "metric_name": metric_id,
                "data": [],
                "query_complete": False,
                "query_status": "pending",
                "query_sampled": False,
                "query_refreshing": True,
            },
        )

    # Eval and annotation graphs, like filtered system graphs, are exact
    # aggregation snapshots. Their request path only schedules/polls work and
    # performs no ClickHouse read.
    bounded_analytics = _DeadlineBoundAnalytics(
        analytics,
        ReadDeadline.start(min(int(wall_deadline_ms), SESSION_GRAPH_WALL_DEADLINE_MS)),
    )
    session_filters = _session_scoped_filters(filters)
    if metric_type == "EVAL":
        return fetch_eval_graph_ch(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=session_filters,
            interval=interval,
            req_data_config=req_data_config,
            observe_type="trace",
            refresh=refresh,
            aggregation_context="session",
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
    if metric_type == "ANNOTATION":
        return fetch_annotation_graph_ch(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=session_filters,
            interval=interval,
            req_data_config=req_data_config,
            observe_type="trace",
            refresh=refresh,
            aggregation_context="session",
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
    raise ValueError("Unsupported session graph metric type")


__all__ = [
    "SESSION_GRAPH_QUERY_TIMEOUT_MS",
    "SESSION_GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS",
    "SESSION_GRAPH_WALL_DEADLINE_MS",
    "SESSION_SYSTEM_METRICS",
    "fetch_session_graph_ch",
]
