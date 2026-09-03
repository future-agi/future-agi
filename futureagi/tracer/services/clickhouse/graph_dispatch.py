"""Bounded ClickHouse dispatch for Observe trace/span graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import UUID

from django.conf import settings

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.services.annotation_label_source import (
    AnnotationLabelScoresProjectPG,
    AnnotationScoreReadUnavailable,
)
from tracer.services.clickhouse.bounded_graph_reads import (
    GRAPH_CANDIDATE_LIMIT,
    GRAPH_MAX_POINTS,
    BoundedGraphReadError,
    GraphCandidateSample,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.exact_graph_reads import (
    ExactGraphReadError,
    _annotation_label_ids_for_filters,
    read_exact_all_system_metrics,
    read_exact_annotation_graph,
    read_exact_eval_graph,
    read_exact_user_system_graph,
)
from tracer.services.clickhouse.query_builders import (
    TimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.exact_aggregation_cache import (
    read_or_schedule_exact_snapshot,
)

GRAPH_WALL_DEADLINE_MS = settings.GRAPH_BACKGROUND_WALL_MS
GRAPH_QUERY_TIMEOUT_MS = settings.GRAPH_BACKGROUND_WALL_MS
GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
GRAPH_DECORATION_TIMEOUT_MS = settings.GRAPH_BACKGROUND_WALL_MS
GRAPH_EVENT_LIMIT = settings.GRAPH_EVENT_LIMIT
GRAPH_RESULT_BYTES = settings.DASHBOARD_ROLLUP_MAX_RESULT_BYTES
# Part of the cache identity, not a database schema version. Incrementing this
# prevents a rolling deploy from serving a 30-day cached payload produced by
# the retired hierarchy-as-path projection.
AGENT_GRAPH_PAYLOAD_VERSION = 5
# A short-window selector may prove as many as 4,096 trace matches. Decoration
# fans each trace set into child-span reads, so keep the same finite 40-trace
# envelope used by the long-window sampler before any decoration query runs.
GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT = settings.GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT
# Candidate discovery is already capped at forty trace IDs and 4,097 returned
# physical identities. Keep it in one bounded statement to avoid rescanning the
# same project/window once per five traces.
GRAPH_TRACE_ENTITY_BATCH_SIZE = GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT
# A 1,024-identity replay remains well below the 4,096 global sentinel while
# removing the extra metric statement observed for the twelve-trace graph
# sample. Additive sufficient statistics preserve exact cross-batch merges.
GRAPH_SPAN_METRIC_BATCH_SIZE = settings.GRAPH_SPAN_METRIC_BATCH_SIZE
_TRACE_ROLLUP_RESULT_COLUMNS = frozenset(
    {
        "time_bucket",
        "avg_latency",
        "total_tokens",
        "avg_cost",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_rate",
    }
)
# Optional raw trace-ID pruning is worthwhile only for genuinely selective
# positive scalar witnesses. EXPLAIN ESTIMATE is metadata-only and bounded so
# a missing/old ClickHouse capability simply retains the ordinary one-pass
# graph query. The thresholds are intentionally conservative: the production
# Colly benchmark completed below four seconds at 1.6M estimated rows / 259
# marks, while a 106M-row / 14.6K-mark string witness was slower than one-pass.
_GRAPH_SEED_ESTIMATE_WALL_MS = 2_500
_GRAPH_SEED_ESTIMATE_QUERY_MS = 1_500
_GRAPH_SEED_ESTIMATE_MAX_CANDIDATES = 10
_GRAPH_SEED_MAX_ESTIMATED_ROWS = 10_000_000
_GRAPH_SEED_MAX_ESTIMATED_MARKS = 4_096
_GRAPH_SEED_SCALAR_FILTER_TYPES = frozenset({"boolean", "number", "string", "text"})
_GRAPH_BASE_READ_SETTINGS = {
    # The retained hourly rollup is already row-reduced. Four workers keep the
    # interactive scan parallel without leaving concurrency unbounded on the
    # largest Coletia/Whatfix projects.
    "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
GRAPH_READ_SETTINGS = {
    **_GRAPH_BASE_READ_SETTINGS,
    "max_result_rows": GRAPH_MAX_POINTS + 1,
    "max_result_bytes": GRAPH_RESULT_BYTES,
}
GRAPH_EVENT_READ_SETTINGS = {
    **_GRAPH_BASE_READ_SETTINGS,
    "max_result_rows": GRAPH_EVENT_LIMIT + 1,
}
GRAPH_ENTITY_READ_SETTINGS = {
    **_GRAPH_BASE_READ_SETTINGS,
    "max_result_rows": GRAPH_CANDIDATE_LIMIT + 1,
}

SpanIdentity = tuple[str, str, int]
SpanEntityIdentity = tuple[str, str]


@dataclass(frozen=True)
class _GraphRawTraceCandidate:
    predicate: str
    params: dict[str, Any]
    rank: int
    filter_index: int


def _validated_project_id(project_id: Any) -> str:
    try:
        if project_id in (None, ""):
            raise ValueError
        return str(UUID(str(project_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("A valid project_id is required") from exc


def _active_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in filters
        if (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        or BaseQueryBuilder.is_datetime_complement_filter(item)
    ]


def _raw_trace_seed_candidates(
    filters: list[dict[str, Any]],
) -> list[_GraphRawTraceCandidate]:
    """Compile generic exhaustive scalar witnesses for optional trace pruning."""

    # Lazy imports avoid pulling the v1/v2 filter cycle into module startup.
    from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
        UnsupportedFilterShapeError,
        compile_span_filter_plans,
    )
    from tracer.services.clickhouse.v2.query_builders.filters import (
        rewrite_v1_sql_to_v2,
    )

    candidates: list[_GraphRawTraceCandidate] = []
    for filter_index, item in enumerate(filters or []):
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if not isinstance(config, dict):
            continue
        col_type = str(config.get("col_type") or config.get("colType") or "").upper()
        filter_type = str(
            config.get("filter_type") or config.get("filterType") or ""
        ).lower()
        if (
            col_type != "SPAN_ATTRIBUTE"
            or filter_type not in _GRAPH_SEED_SCALAR_FILTER_TYPES
        ):
            continue
        try:
            plans = compile_span_filter_plans([item])
        except (UnsupportedFilterShapeError, ValueError):
            continue
        if len(plans) != 1:
            continue
        plan = plans[0]
        predicate = rewrite_v1_sql_to_v2(
            str(plan.raw_graph_value_witness_predicate or "")
        ).strip()
        if not predicate or plan.exclude_group_matches:
            continue

        namespaced_params: dict[str, Any] = {}
        for old_name in sorted(plan.params, key=len, reverse=True):
            new_name = f"graph_seed_{filter_index}_{old_name}"
            predicate = predicate.replace(
                f"%({old_name})s",
                f"%({new_name})s",
            )
            namespaced_params[new_name] = plan.params[old_name]
        candidates.append(
            _GraphRawTraceCandidate(
                predicate=predicate,
                params=namespaced_params,
                rank=(
                    int(plan.raw_witness_rank)
                    if plan.raw_witness_rank is not None
                    else 10_000
                ),
                filter_index=filter_index,
            )
        )
    return sorted(candidates, key=lambda item: (item.rank, item.filter_index))


def _select_raw_trace_seed_candidate(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    timeout_ms: int,
) -> tuple[_GraphRawTraceCandidate | None, int]:
    """Use bounded ClickHouse estimates to reject dense witness subqueries."""

    candidates = _raw_trace_seed_candidates(filters)
    total_budget_ms = min(
        _GRAPH_SEED_ESTIMATE_WALL_MS,
        max(0, int(timeout_ms) - 25),
    )
    if not candidates or total_budget_ms < 100:
        return None, 0

    estimate_started = monotonic()
    probe_count = 0
    for candidate in candidates[:_GRAPH_SEED_ESTIMATE_MAX_CANDIDATES]:
        elapsed_ms = int((monotonic() - estimate_started) * 1000)
        remaining_ms = total_budget_ms - elapsed_ms
        if remaining_ms < 100:
            break
        estimate_params = {
            "graph_seed_project_id": project_id,
            "graph_seed_start_date": start_date - timedelta(days=1),
            "graph_seed_end_date": end_date + timedelta(days=1),
            **candidate.params,
        }
        estimate_query = f"""
        EXPLAIN ESTIMATE
        SELECT trace_id
        FROM spans
        PREWHERE project_id = toUUID(%(graph_seed_project_id)s)
          AND start_time >= %(graph_seed_start_date)s
          AND start_time < %(graph_seed_end_date)s
        WHERE is_deleted = 0
          AND ({candidate.predicate})
        GROUP BY trace_id
        """
        probe_count += 1
        try:
            result = analytics.execute_ch_query(
                estimate_query,
                estimate_params,
                timeout_ms=min(_GRAPH_SEED_ESTIMATE_QUERY_MS, remaining_ms),
                settings={
                    **GRAPH_READ_SETTINGS,
                    "max_threads": 1,
                    "max_result_rows": 32,
                    "max_result_bytes": 64 * 1024,
                },
            )
        except Exception as exc:
            if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                raise
            continue

        estimate_rows = list(result.data or [])
        if not estimate_rows or any(not isinstance(row, dict) for row in estimate_rows):
            continue
        try:
            rows = sum(max(0, int(row["rows"])) for row in estimate_rows)
            marks = sum(max(0, int(row["marks"])) for row in estimate_rows)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            rows <= _GRAPH_SEED_MAX_ESTIMATED_ROWS
            and marks <= _GRAPH_SEED_MAX_ESTIMATED_MARKS
        ):
            return candidate, probe_count
    return None, probe_count


def degraded_graph_response(
    metric_id: str,
    exc: Exception,
    *,
    sample: GraphCandidateSample | None = None,
    provenance: str | None = None,
) -> dict[str, Any]:
    """Return a stable graph payload without leaking database diagnostics."""

    explicit_code = getattr(exc, "error_code", None)
    if explicit_code in {"deadline_exceeded", "read_budget_exceeded"}:
        error_code = "read_budget_exceeded"
    elif explicit_code == "sample_limit":
        error_code = "sample_limit"
    elif is_read_budget_error(exc):
        error_code = "read_budget_exceeded"
    else:
        error_code = "query_failed"
    response: dict[str, Any] = {
        "metric_name": str(metric_id or ""),
        "data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "query_exact": False,
        "query_error_code": error_code,
    }
    if sample is not None:
        # Preserve honest progress from the bounded selector.  In particular,
        # a deadline after N of M temporal strata must not collapse into an
        # indistinguishable false-empty response.  The points remain empty
        # until all strata finish, but the caller can show useful progress and
        # retry/narrow the same window.
        response.update(sample.metadata())
        response.update(
            {
                "data": [],
                "query_complete": False,
                "query_status": "degraded",
                "query_sampled": False,
                "query_exact": False,
                "query_error_code": error_code,
            }
        )
    if provenance:
        response["query_provenance"] = provenance
    return response


def _bounded_interactive_read_settings(
    read_settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Retain finite graph caps while removing source-row ceilings."""

    caps = {
        "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
        "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
        "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
        "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
        "max_result_rows": GRAPH_MAX_POINTS + 1,
        "max_result_bytes": GRAPH_RESULT_BYTES,
    }
    bounded = dict(caps)
    for key, value in (read_settings or {}).items():
        if key == "max_rows_to_read":
            continue
        if key in caps:
            bounded[key] = min(int(value), int(caps[key]))
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


class _DeadlineBoundGraphAnalytics:
    """Clamp discovery and decoration to one request-owned wall deadline."""

    def __init__(
        self,
        delegate: Any,
        deadline: ReadDeadline,
    ) -> None:
        self._delegate = delegate
        self._deadline = deadline
        self.supports_per_query_read_settings = bool(
            getattr(delegate, "supports_per_query_read_settings", True)
        )

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
        settings: dict[str, Any] | None = None,
    ) -> Any:
        requested_timeout_ms = min(int(timeout_ms), GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS)
        return self._delegate.execute_ch_query(
            query,
            params or {},
            timeout_ms=self._deadline.remaining_ms(requested_timeout_ms),
            settings=_bounded_interactive_read_settings(settings),
        )


def _remaining_timeout_ms(
    started: float,
    cap_ms: int,
    *,
    wall_deadline_ms: int = GRAPH_WALL_DEADLINE_MS,
) -> int:
    elapsed_ms = (monotonic() - started) * 1000
    remaining_ms = int(wall_deadline_ms - elapsed_ms)
    if remaining_ms < 25:
        raise BoundedGraphReadError("deadline_exceeded")
    return min(cap_ms, remaining_ms)


def _result_metric_value(point: dict[str, Any], fields: tuple[str, ...]) -> float | int:
    for field in fields:
        if point.get(field) is not None:
            return point[field]
    return 0


_SYSTEM_METRIC_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "latency": ("latency", ("value", "latency")),
    "traffic": ("traffic", ("traffic", "value")),
    "tokens": ("tokens", ("value", "tokens")),
    "total_tokens": ("total_tokens", ("value", "tokens")),
    "prompt_tokens": ("prompt_tokens", ("value", "prompt_tokens")),
    "input_tokens": ("input_tokens", ("value", "prompt_tokens")),
    "completion_tokens": (
        "completion_tokens",
        ("value", "completion_tokens"),
    ),
    "output_tokens": ("output_tokens", ("value", "completion_tokens")),
    "cost": ("cost", ("value", "cost")),
    "error_rate": ("error_rate", ("value", "error_rate")),
}


def format_system_metric_graph(
    ch_data: dict[str, list[dict[str, Any]]], metric_id: str
) -> dict[str, Any]:
    normalized = str(metric_id or "latency").strip().lower()
    metric_key, value_fields = _SYSTEM_METRIC_FIELDS.get(
        normalized,
        (normalized if normalized in ch_data else "latency", ("value", normalized)),
    )
    metric_points = ch_data.get(metric_key, [])
    traffic_points = ch_data.get("traffic", [])
    traffic_by_timestamp = {
        point.get("timestamp"): _result_metric_value(point, ("traffic", "value"))
        for point in traffic_points
    }
    return {
        "metric_name": metric_id,
        "data": [
            {
                "timestamp": point.get("timestamp"),
                "value": _result_metric_value(point, value_fields),
                "primary_traffic": traffic_by_timestamp.get(point.get("timestamp"), 0),
            }
            for point in metric_points
        ],
    }


def _complete_metadata(
    *, started: float, query_count: int, rows_returned: int
) -> dict[str, Any]:
    return {
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_count": query_count,
        "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
        "query_rows_returned": rows_returned,
    }


def _require_rollup_result_shape(
    rows: list[Any],
    columns: list[str],
    *,
    expected_columns: frozenset[str],
) -> None:
    """Reject schema drift instead of formatting it into a successful zero graph."""

    missing = expected_columns.difference(columns)
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


def _ensure_point_budget(
    *, start_date: datetime, end_date: datetime, interval: str
) -> None:
    """Reject a zero-filled series that would exceed the graph contract."""

    for index, _ in enumerate(
        BaseQueryBuilder._generate_timestamp_range(start_date, end_date, interval)
    ):
        if index >= GRAPH_MAX_POINTS:
            raise BoundedGraphReadError("sample_limit")


def _candidate_trace_ids(sample: GraphCandidateSample) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(row.get("trace_id")) for row in sample.rows if row.get("trace_id")
        )
    )


def _trace_decoration_row_key(row: dict[str, Any]) -> tuple[datetime, str]:
    start_time = row.get("start_time")
    if not isinstance(start_time, datetime):
        normalized_start = datetime.min
    elif start_time.tzinfo is None:
        normalized_start = start_time
    else:
        normalized_start = start_time.astimezone(UTC).replace(tzinfo=None)
    return normalized_start, str(row.get("trace_id") or "")


def _bounded_trace_decoration_sample(
    sample: GraphCandidateSample,
) -> GraphCandidateSample:
    """Cap trace fan-out to the globally newest finite identity sample."""

    ordered_rows = sorted(
        (row for row in sample.rows if row.get("trace_id")),
        key=_trace_decoration_row_key,
        reverse=True,
    )
    unique_rows: list[dict[str, Any]] = []
    seen_trace_ids: set[str] = set()
    for row in ordered_rows:
        trace_id = str(row["trace_id"])
        if trace_id in seen_trace_ids:
            continue
        seen_trace_ids.add(trace_id)
        unique_rows.append(row)

    if len(unique_rows) <= GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT:
        return sample

    sampling_strata = sample.sampling_strata if sample.sampling_strata > 0 else 1
    sampling_strata_completed = (
        sample.sampling_strata_completed
        if sample.sampling_strata_completed > 0
        else sampling_strata
    )
    return replace(
        sample,
        rows=tuple(unique_rows[:GRAPH_TRACE_DECORATION_CANDIDATE_LIMIT]),
        query_complete=False,
        query_status="sampled",
        query_error_code="sample_limit",
        total_rows_lower_bound=max(sample.total_rows_lower_bound, len(unique_rows)),
        sampling_strategy=(sample.sampling_strategy or "newest_trace_candidates"),
        sampling_strata=sampling_strata,
        sampling_strata_completed=sampling_strata_completed,
    )


def _require_renderable_sample(sample: GraphCandidateSample) -> None:
    """Accept only exact rows or an intentional bounded sample marker.

    A ``sample_limit`` candidate set contains proven matches selected by the
    bounded temporal graph sampler. It is safe to return its diagnostics and
    graph points when metadata proves every declared sampling stratum ran.
    An empty fully-executed sample is also intentional; it does not claim global
    absence. Partial rows from an unclassified or failed query remain a typed
    degraded error.
    """

    if sample.query_complete:
        return
    if (
        sample.query_status == "sampled"
        and sample.sampling_strategy
        and sample.sampling_strata > 0
        and sample.sampling_strata_completed == sample.sampling_strata
    ):
        return
    raise BoundedGraphReadError(sample.query_error_code or "sample_limit")


def enforce_exact_graph_data_contract(
    response: dict[str, Any],
) -> dict[str, Any]:
    """Publish points only for exact or explicitly sampled graph reads.

    Bounded candidate rows are useful internally for proving coverage and for
    exact finite decoration queries. Once any phase reports incomplete
    coverage, aggregating those rows produces a sample. ``query_status`` must
    say ``sampled`` before those values can enter ``data``; every other
    incomplete/degraded response remains empty.
    """

    if response.get("query_status") == "sampled":
        planned = response.get("query_sampling_strata")
        completed = response.get("query_sampling_strata_completed")
        if (
            response.get("query_sampling_strategy")
            and isinstance(planned, int)
            and not isinstance(planned, bool)
            and planned > 0
            and completed == planned
        ):
            return response
        return {
            **response,
            "data": [],
            "query_complete": False,
            "query_status": "degraded",
            "query_sampled": False,
            "query_error_code": response.get("query_error_code") or "sample_limit",
        }
    if (
        response.get("query_complete") is False
        or response.get("query_status") == "degraded"
    ):
        return {**response, "data": []}
    return response


def graph_payload_is_publishable(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    allow_sampled: bool,
) -> bool:
    """Accept only complete or explicitly pending graph series.

    ``allow_sampled`` remains in the callable contract while old application
    processes and browser tabs drain, but it is intentionally ignored. Public
    graph endpoints no longer publish sampled points.
    """

    del allow_sampled

    series = payload if isinstance(payload, list) else [payload]
    for item in series:
        if not isinstance(item, dict):
            return False
        status = item.get("query_status")
        complete = item.get("query_complete")
        if status == "sampled":
            return False
        if status == "pending":
            if (
                complete is not False
                or item.get("query_sampled") is not False
                or item.get("query_refreshing") is not True
                or bool(item.get("data"))
            ):
                return False
            continue
        if (
            status != "complete"
            or complete is not True
            or item.get("query_sampled") is not False
            or item.get("error")
        ):
            return False
    return True


def _read_or_refresh_exact_graph(
    *,
    namespace: str,
    identity: dict[str, Any],
    refresh: bool,
    pending_payload: Any,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    schedule_on_miss: bool = True,
) -> Any:
    """Return immediately while a deduplicated exact refresh runs out of band."""

    if organization_id is not None:
        identity["organization_id"] = str(organization_id)
    if workspace_id is not None:
        identity["workspace_id"] = str(workspace_id)
    return read_or_schedule_exact_snapshot(
        namespace,
        identity,
        refresh=refresh,
        pending_payload=pending_payload,
        schedule_on_miss=schedule_on_miss,
    )


def _pending_graph_payload(metric_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "metric_name": str(metric_id or ""),
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
        **extra,
    }


def _span_identity(row: dict[str, Any]) -> SpanIdentity | None:
    trace_id = str(row.get("trace_id") or "")
    span_id = str(row.get("id") or "")
    start_time = row.get("start_time")
    if not trace_id or not span_id or not isinstance(start_time, datetime):
        return None
    utc_start = (
        start_time.replace(tzinfo=UTC)
        if start_time.tzinfo is None
        else start_time.astimezone(UTC)
    )
    delta = utc_start - datetime(1970, 1, 1, tzinfo=UTC)
    start_us = (
        delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    return trace_id, span_id, start_us


def _span_entities(rows: tuple[dict[str, Any], ...]) -> tuple[SpanEntityIdentity, ...]:
    """Return trace-scoped score/logger keys at the external-table boundary."""

    return tuple(
        dict.fromkeys(
            (str(row.get("trace_id")), str(row.get("id")))
            for row in rows
            if row.get("trace_id") and row.get("id")
        )
    )


def _span_identity_dates(identities: tuple[SpanIdentity, ...]) -> tuple[date, ...]:
    """Return exact partition dates for immutable physical span identities."""

    return tuple(
        sorted(
            {
                datetime.fromtimestamp(identity[2] // 1_000_000, tz=UTC).date()
                for identity in identities
            }
        )
    )


def _external_entity_scope(
    entities: tuple[SpanEntityIdentity, ...],
    *,
    entities_param: str,
) -> tuple[str, dict[str, Any]]:
    """Scope logger/score rows without trusting bare span IDs.

    ``tracer_eval_logger`` has no project column and migration 0075 guarantees
    both IDs for span/trace targets. Score rows can be legacy-null, but a bare
    OTel span ID cannot prove trace ownership. Both paths therefore fail closed
    unless the external row carries the trace-scoped pair.
    """

    if not entities:
        return "0 = 1", {}
    predicate = (
        "(NOT isNull(trace_id) AND "
        f"(toString(trace_id), observation_span_id) IN %({entities_param})s)"
    )
    return predicate, {entities_param: entities}


def _finite_trace_span_candidates(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    project_id: str,
    started: float,
    timeout_cap_ms: int,
) -> tuple[tuple[SpanIdentity, ...], bool, int, int]:
    """Seed a finite identity superset for the sampled traces.

    Scope predicates intentionally belong only to this physical seed.  Every
    caller must replay the returned identities against all physical versions before a
    row can contribute to a graph.
    """

    bounded_sample = _bounded_trace_decoration_sample(sample)
    trace_candidates_truncated = bounded_sample is not sample
    trace_ids = _candidate_trace_ids(bounded_sample)
    if not trace_ids:
        return (), False, 0, 0
    query = """
    SELECT trace_id, id, start_time
    FROM spans
    PREWHERE project_id = toUUID(%(graph_project_id)s)
      AND trace_id IN %(graph_trace_ids)s
      AND start_time >= %(graph_start_date)s
      AND start_time < %(graph_end_date)s
    GROUP BY trace_id, id, start_time
    ORDER BY start_time DESC, id DESC, trace_id DESC
    LIMIT %(graph_entity_limit)s
    """
    identities: list[SpanIdentity] = []
    rows_returned = 0
    query_count = 0
    truncated = trace_candidates_truncated
    for batch_offset in range(0, len(trace_ids), GRAPH_TRACE_ENTITY_BATCH_SIZE):
        trace_batch = trace_ids[
            batch_offset : batch_offset + GRAPH_TRACE_ENTITY_BATCH_SIZE
        ]
        result = analytics.execute_ch_query(
            query,
            {
                "graph_project_id": project_id,
                "graph_trace_ids": trace_batch,
                "graph_start_date": sample.window_start,
                "graph_end_date": sample.window_end,
                "graph_entity_limit": GRAPH_CANDIDATE_LIMIT + 1,
            },
            timeout_ms=_remaining_timeout_ms(started, timeout_cap_ms),
            settings=GRAPH_ENTITY_READ_SETTINGS,
        )
        batch_rows = list(result.data or [])
        query_count += 1
        rows_returned += len(batch_rows)
        truncated = truncated or len(batch_rows) > GRAPH_CANDIDATE_LIMIT
        identities.extend(
            identity
            for row in batch_rows[: GRAPH_CANDIDATE_LIMIT + 1]
            if (identity := _span_identity(row)) is not None
        )

    ordered_identities = sorted(
        dict.fromkeys(identities),
        key=lambda identity: (identity[2], identity[1], identity[0]),
        reverse=True,
    )
    truncated = truncated or len(ordered_identities) > GRAPH_CANDIDATE_LIMIT
    return (
        tuple(ordered_identities[:GRAPH_CANDIDATE_LIMIT]),
        truncated,
        query_count,
        rows_returned,
    )


def _trace_system_metric_query(
    *,
    sample: GraphCandidateSample,
    span_identities: tuple[SpanIdentity, ...],
    interval: str,
    project_id: str,
) -> tuple[str, dict[str, Any]]:
    """Aggregate every live span belonging to a finite candidate trace set."""

    trace_ids = tuple(dict.fromkeys(identity[0] for identity in span_identities))
    if not trace_ids or not span_identities:
        return "", {}
    bucket_fn = BaseQueryBuilder.time_bucket_expr(interval)
    query = f"""
    SELECT
        {bucket_fn}(latest_start_time) AS time_bucket,
        sum(toFloat64(latest_latency_ms)) AS graph_latency_sum,
        count(latest_latency_ms) AS graph_latency_count,
        sum(latest_total_tokens) AS total_tokens,
        sum(latest_cost) AS graph_cost_sum,
        count(latest_cost) AS graph_cost_count,
        count() AS traffic_count,
        sum(latest_prompt_tokens) AS prompt_tokens,
        sum(latest_completion_tokens) AS completion_tokens,
        countIf(upper(latest_status) IN ('ERROR', 'ERRORED', 'FAILED'))
            AS graph_error_count
    FROM (
        SELECT
            id AS grouped_id,
            trace_id AS grouped_trace_id,
            start_time AS latest_start_time,
            argMax(latency_ms, _version) AS latest_latency_ms,
            argMax(cost, _version) AS latest_cost,
            argMax(total_tokens, _version) AS latest_total_tokens,
            argMax(prompt_tokens, _version) AS latest_prompt_tokens,
            argMax(completion_tokens, _version) AS latest_completion_tokens,
            argMax(status, _version) AS latest_status,
            argMax(is_deleted, _version) AS latest_is_deleted
        FROM spans
        PREWHERE project_id = toUUID(%(graph_project_id)s)
          AND toDate(start_time) IN %(graph_span_dates)s
          AND trace_id IN %(graph_trace_ids)s
          AND id IN %(graph_span_ids)s
        WHERE (
            trace_id,
            id,
            toUnixTimestamp64Micro(start_time)
        ) IN %(graph_span_identities)s
        GROUP BY trace_id, id, start_time
    )
    WHERE latest_is_deleted = 0
      AND latest_start_time >= %(graph_start_date)s
      AND latest_start_time < %(graph_end_date)s
    GROUP BY time_bucket
    ORDER BY time_bucket
    LIMIT %(graph_point_limit)s
    """
    return query, {
        "graph_project_id": project_id,
        "graph_span_dates": _span_identity_dates(span_identities),
        "graph_trace_ids": trace_ids,
        "graph_span_ids": tuple(
            dict.fromkeys(identity[1] for identity in span_identities)
        ),
        "graph_span_identities": span_identities,
        "graph_start_date": sample.window_start,
        "graph_end_date": sample.window_end,
        "graph_point_limit": GRAPH_MAX_POINTS + 1,
    }


def _fetch_trace_system_metrics(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    project_id: str,
    interval: str,
    started: float,
    timeout_ms: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Aggregate all metrics for a finite set of trace candidates once."""

    sample = _bounded_trace_decoration_sample(sample)
    _ensure_point_budget(
        start_date=sample.window_start,
        end_date=sample.window_end,
        interval=interval,
    )
    (
        span_identities,
        span_ids_truncated,
        identity_query_count,
        identity_rows_returned,
    ) = _finite_trace_span_candidates(
        analytics=analytics,
        sample=sample,
        project_id=project_id,
        started=started,
        timeout_cap_ms=timeout_ms,
    )
    bucket_totals: dict[datetime, dict[str, float]] = {}
    metric_query_count = 0
    metric_rows_returned = 0
    for batch_offset in range(0, len(span_identities), GRAPH_SPAN_METRIC_BATCH_SIZE):
        identity_batch = span_identities[
            batch_offset : batch_offset + GRAPH_SPAN_METRIC_BATCH_SIZE
        ]
        query, params = _trace_system_metric_query(
            sample=sample,
            span_identities=identity_batch,
            interval=interval,
            project_id=project_id,
        )
        if not query:
            continue
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=min(timeout_ms, _remaining_timeout_ms(started, timeout_ms)),
            settings=GRAPH_READ_SETTINGS,
        )
        partial_rows = list(result.data or [])
        partial_columns = list(result.columns or [])
        metric_query_count += 1
        metric_rows_returned += len(partial_rows)
        column_indexes = {name: index for index, name in enumerate(partial_columns)}

        def value(
            row: Any,
            name: str,
            default: Any = 0,
            indexes: dict[str, int] = column_indexes,
        ) -> Any:
            if isinstance(row, dict):
                return row.get(name, default)
            index = indexes.get(name)
            return row[index] if index is not None and index < len(row) else default

        for row in partial_rows:
            time_bucket = value(row, "time_bucket", None)
            if not isinstance(time_bucket, (date, datetime)):
                raise AssertionError("trace metric query returned an invalid bucket")
            # ClickHouse returns ``Date`` for calendar buckets such as
            # ``toStartOfMonth`` while hour buckets arrive as ``DateTime``.
            # Normalize both valid wire types before merging metric batches so
            # the exact trace reducer and zero-fill formatter share one key
            # type.  Do not coerce strings/nulls: those still indicate schema
            # drift and must fail before publication.
            time_bucket = BaseQueryBuilder._normalize_timestamp(time_bucket, interval)
            traffic = float(value(row, "traffic_count", 0) or 0)
            totals = bucket_totals.setdefault(
                time_bucket,
                {
                    "latency_sum": 0.0,
                    "latency_count": 0.0,
                    "total_tokens": 0.0,
                    "cost_sum": 0.0,
                    "cost_count": 0.0,
                    "traffic": 0.0,
                    "prompt_tokens": 0.0,
                    "completion_tokens": 0.0,
                    "error_count": 0.0,
                },
            )
            # Real queries return additive sufficient statistics. The fallback
            # names keep deterministic unit fakes and older shadow evidence
            # readable while exercising the same exact weighted merge.
            totals["latency_sum"] += float(
                value(
                    row,
                    "graph_latency_sum",
                    float(value(row, "avg_latency", 0) or 0) * traffic,
                )
                or 0
            )
            totals["latency_count"] += float(
                value(row, "graph_latency_count", traffic) or 0
            )
            totals["total_tokens"] += float(value(row, "total_tokens", 0) or 0)
            totals["cost_sum"] += float(
                value(
                    row,
                    "graph_cost_sum",
                    float(value(row, "avg_cost", 0) or 0) * traffic,
                )
                or 0
            )
            totals["cost_count"] += float(value(row, "graph_cost_count", traffic) or 0)
            totals["traffic"] += traffic
            totals["prompt_tokens"] += float(value(row, "prompt_tokens", 0) or 0)
            totals["completion_tokens"] += float(
                value(row, "completion_tokens", 0) or 0
            )
            totals["error_count"] += float(
                value(
                    row,
                    "graph_error_count",
                    float(value(row, "error_rate", 0) or 0) * traffic / 100.0,
                )
                or 0
            )

    rows: list[dict[str, Any]] = []
    for time_bucket, totals in sorted(bucket_totals.items()):
        traffic = totals["traffic"]
        rows.append(
            {
                "time_bucket": time_bucket,
                "avg_latency": (
                    totals["latency_sum"] / totals["latency_count"]
                    if totals["latency_count"]
                    else 0.0
                ),
                "total_tokens": totals["total_tokens"],
                "avg_cost": (
                    totals["cost_sum"] / totals["cost_count"]
                    if totals["cost_count"]
                    else 0.0
                ),
                "traffic_count": int(traffic),
                "prompt_tokens": totals["prompt_tokens"],
                "completion_tokens": totals["completion_tokens"],
                "error_rate": (
                    totals["error_count"] * 100.0 / traffic if traffic else 0.0
                ),
            }
        )
    columns = [
        "time_bucket",
        "avg_latency",
        "total_tokens",
        "avg_cost",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_rate",
    ]
    if len(rows) > GRAPH_MAX_POINTS:
        raise BoundedGraphReadError("sample_limit")

    formatter = TimeSeriesQueryBuilder(
        project_id=project_id,
        filters=[],
        interval=interval,
    )
    formatter.start_date = sample.window_start
    formatter.end_date = sample.window_end
    metrics = formatter.format_result(rows, columns)
    metadata = _decoration_metadata(
        sample=sample,
        truncated=span_ids_truncated,
        started=started,
        query_count=sample.query_count + identity_query_count + metric_query_count,
        rows_returned=(
            sample.rows_returned + identity_rows_returned + metric_rows_returned
        ),
    )
    if metadata.get("query_status") == "degraded":
        metrics = {key: [] for key in metrics}
    return metrics, metadata


def _fetch_trace_system_metric_graph(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    project_id: str,
    interval: str,
    metric_id: str,
    started: float,
    timeout_ms: int,
) -> dict[str, Any]:
    """Preserve trace-graph semantics by aggregating all child spans."""

    metrics, metadata = _fetch_trace_system_metrics(
        analytics=analytics,
        sample=sample,
        project_id=project_id,
        interval=interval,
        started=started,
        timeout_ms=timeout_ms,
    )
    response = format_system_metric_graph(metrics, metric_id)
    response.update(metadata)
    return enforce_exact_graph_data_contract(response)


def _fetch_rollup_system_metric_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    observe_type: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Serve the main-compatible date-only system graph in one request.

    The hourly rollup is the established fast path for an empty filter set or
    a positive date window. Relational, attribute, complement-datetime, eval,
    and annotation filters are handled by the bounded candidate dispatcher.
    """

    started = monotonic()
    # Bind the normalized window explicitly and pass no filters to the query
    # builder. This is a physical-source invariant: even if the general
    # builder learns another filter shape later, this interactive route can
    # only emit the ``spans_hourly_rollup`` query and can never fall back to a
    # raw ``spans`` scan.
    start_date, end_date = BaseQueryBuilder.parse_time_range(filters, strict=True)
    if (
        start_date is not None
        and end_date is not None
        and end_date - start_date
        > timedelta(days=settings.DASHBOARD_WEEKLY_AGGREGATION_AFTER_DAYS)
    ):
        interval = "week"
    builder = TimeSeriesQueryBuilder(
        project_id=project_id,
        filters=[],
        interval=interval,
        observe_type=observe_type,
        start_date=start_date,
        end_date=end_date,
    )
    query, params = builder.build()
    interactive_deadline_ms = max(
        1,
        min(int(timeout_ms), GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS),
    )
    if start_date is not None and end_date is not None and start_date >= end_date:
        rows: list[Any] = []
        columns: list[str] = []
        query_count = 0
    else:
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_remaining_timeout_ms(
                started,
                interactive_deadline_ms,
                wall_deadline_ms=interactive_deadline_ms,
            ),
            settings=GRAPH_READ_SETTINGS,
        )
        rows = list(result.data or [])
        columns = list(result.columns or [])
        _require_rollup_result_shape(
            rows,
            columns,
            expected_columns=_TRACE_ROLLUP_RESULT_COLUMNS,
        )
        query_count = 1
    response = format_system_metric_graph(
        builder.format_result(rows, columns),
        metric_id,
    )
    response.update(
        _complete_metadata(
            started=started,
            query_count=query_count,
            rows_returned=len(rows),
        )
    )
    response.update(
        {
            "query_provenance": "materialized_rollup",
            "query_exact": False,
        }
    )
    return enforce_exact_graph_data_contract(response)


def _fetch_direct_raw_system_metric_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    observe_type: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Run one complete append-only filtered graph statement."""

    started = monotonic()
    start_date, end_date = BaseQueryBuilder.parse_time_range(filters, strict=True)
    if start_date is None or end_date is None:
        raise ValueError("filtered graph requires a bounded time range")
    if end_date - start_date > timedelta(
        days=settings.DASHBOARD_WEEKLY_AGGREGATION_AFTER_DAYS
    ):
        interval = "week"
    _ensure_point_budget(
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )
    seed_candidate: _GraphRawTraceCandidate | None = None
    seed_probe_count = 0
    if (
        start_date < end_date
        and observe_type == "trace"
        and settings.DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER
    ):
        seed_candidate, seed_probe_count = _select_raw_trace_seed_candidate(
            analytics=analytics,
            project_id=project_id,
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            timeout_ms=timeout_ms,
        )
    builder = TimeSeriesQueryBuilder(
        project_id=project_id,
        filters=filters,
        interval=interval,
        exact_snapshot=True,
        resolve_span_versions=False,
        raw_replica_shard_cluster=settings.DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER,
        raw_replica_shard_count=settings.DASHBOARD_TRACE_REPLICA_SHARD_COUNT,
        observe_type=observe_type,
        start_date=start_date,
        end_date=end_date,
        annotation_label_ids=_annotation_label_ids_for_filters(project_id, filters),
        raw_trace_candidate_predicate=(
            seed_candidate.predicate if seed_candidate is not None else ""
        ),
        raw_trace_candidate_params=(
            seed_candidate.params if seed_candidate is not None else None
        ),
    )
    empty_window = start_date >= end_date
    if empty_window:
        rows: list[Any] = []
        columns: list[str] = []
        query_count = 0
    else:
        query, params = builder.build()
        elapsed_ms = int((monotonic() - started) * 1000)
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=max(1, int(timeout_ms) - elapsed_ms),
            settings=GRAPH_READ_SETTINGS,
        )
        rows = list(result.data or [])
        columns = list(result.columns or [])
        _require_rollup_result_shape(
            rows,
            columns,
            expected_columns=_TRACE_ROLLUP_RESULT_COLUMNS,
        )
        query_count = seed_probe_count + 1
    response = format_system_metric_graph(
        builder.format_result(rows, columns),
        metric_id,
    )
    response.update(
        _complete_metadata(
            started=started,
            query_count=query_count,
            rows_returned=len(rows),
        )
    )
    response.update(
        {
            # The full bounded window is read without sampling, but physical
            # ReplacingMergeTree versions are intentionally not collapsed on
            # this latency-critical path.
            "query_provenance": (
                "exact_snapshot" if empty_window else "bounded_candidates"
            ),
            "query_exact": empty_window,
        }
    )
    return enforce_exact_graph_data_contract(response)


def fetch_system_metric_graph_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    observe_type: str = "trace",
    timeout_ms: int = GRAPH_QUERY_TIMEOUT_MS,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read an unfiltered rollup or an exact synchronous filtered graph."""

    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    normalized_observe_type = str(observe_type or "trace").strip().lower()
    if normalized_observe_type not in {"trace", "span"}:
        raise ValueError("observe_type must be trace or span")
    if not _active_filters(filters):
        if not bool(getattr(analytics, "supports_per_query_read_settings", True)):
            return degraded_graph_response(
                str(metric_id or ""),
                BoundedGraphReadError("query_failed", retryable=True),
                provenance="server_read_policy_unavailable",
            )
        return _fetch_rollup_system_metric_graph(
            analytics=analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            metric_id=str(metric_id or ""),
            observe_type=normalized_observe_type,
            timeout_ms=timeout_ms,
        )
    bounded_time_range = BaseQueryBuilder.analyze_bounded_datetime_filters(
        filters,
        strict=True,
    )
    if bounded_time_range.empty:
        return _fetch_direct_raw_system_metric_graph(
            analytics=analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            metric_id=str(metric_id or ""),
            observe_type=normalized_observe_type,
            timeout_ms=timeout_ms,
        )
    # Observe charts are interactive. Compile all filters into one raw physical
    # spans scan and fold trace membership in ClickHouse, instead of running the
    # serial candidate/classifier/replay reader. A cache-only probe prevents a
    # running heavy refresh from being duplicated by every browser poll. True
    # cold misses still try the direct path first; only a proven read-budget
    # failure is handed to the existing deduplicated background worker.
    identity = {
        "project_id": project_id,
        "filters": filters,
        "interval": interval,
        "metric_id": str(metric_id or ""),
        "observe_type": normalized_observe_type,
    }
    pending_payload = _pending_graph_payload(str(metric_id or ""))
    cached = _read_or_refresh_exact_graph(
        namespace="observe-system-graph",
        identity=dict(identity),
        refresh=False,
        pending_payload=pending_payload,
        organization_id=organization_id,
        workspace_id=workspace_id,
        schedule_on_miss=False,
    )
    if (
        isinstance(cached, dict)
        and cached.get("query_status") == "complete"
        and graph_payload_is_publishable(cached, allow_sampled=False)
    ):
        if refresh and organization_id:
            return _read_or_refresh_exact_graph(
                namespace="observe-system-graph",
                identity=dict(identity),
                refresh=True,
                pending_payload=pending_payload,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        return cached
    if isinstance(cached, dict) and cached.get("query_refreshing") is True:
        return cached
    if refresh and organization_id:
        return _read_or_refresh_exact_graph(
            namespace="observe-system-graph",
            identity=dict(identity),
            refresh=True,
            pending_payload=pending_payload,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )

    interactive_deadline_ms = min(
        int(timeout_ms),
        GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
    )
    if interactive_deadline_ms <= 0:
        raise ValueError("graph timeout must be positive")
    bounded_analytics = _DeadlineBoundGraphAnalytics(
        analytics,
        ReadDeadline.start(interactive_deadline_ms),
    )
    try:
        response = _fetch_direct_raw_system_metric_graph(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            metric_id=str(metric_id or ""),
            observe_type=normalized_observe_type,
            timeout_ms=interactive_deadline_ms,
        )
        return response
    except ExactGraphReadError as exc:
        degraded = degraded_graph_response(
            str(metric_id or ""), exc, provenance="bounded_candidates"
        )
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        degraded = degraded_graph_response(
            str(metric_id or ""), exc, provenance="bounded_candidates"
        )
    if organization_id:
        try:
            return _read_or_refresh_exact_graph(
                namespace="observe-system-graph",
                identity=dict(identity),
                refresh=True,
                pending_payload=pending_payload,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        except Exception:
            # The direct failure is already sanitized. Cache/worker transport
            # availability must not turn it into a raw API exception.
            return degraded
    return degraded


def fetch_agent_graph_ch(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read or schedule one exact Agent Graph snapshot.

    Agent topology is an aggregation, not a list.  A cold request therefore
    returns an explicit non-renderable pending envelope; a manual refresh keeps
    the last fully exact snapshot visible until its atomic replacement lands.
    """

    project_id = _validated_project_id(project_id)
    normalized_filters = list(filters or [])
    return _read_or_refresh_exact_graph(
        namespace="observe-agent-graph",
        identity={
            "project_id": project_id,
            "filters": normalized_filters,
            "payload_version": AGENT_GRAPH_PAYLOAD_VERSION,
        },
        refresh=bool(refresh),
        pending_payload={
            "nodes": [],
            "edges": [],
            "path_edges": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
            "query_refreshing": True,
        },
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def fetch_all_system_metrics_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    timeout_ms: int = GRAPH_QUERY_TIMEOUT_MS,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read the complete exact project-chart metric bundle synchronously."""

    del refresh, organization_id, workspace_id
    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    interactive_deadline_ms = min(
        int(timeout_ms),
        GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
    )
    if interactive_deadline_ms <= 0:
        raise ValueError("graph timeout must be positive")
    bounded_analytics = _DeadlineBoundGraphAnalytics(
        analytics,
        ReadDeadline.start(interactive_deadline_ms),
    )
    try:
        response = read_exact_all_system_metrics(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
        )
        response.update(
            {
                "query_provenance": "exact_snapshot",
                "query_exact": True,
            }
        )
        return response
    except ExactGraphReadError as exc:
        degraded = degraded_graph_response(
            "",
            exc,
            provenance="exact_snapshot",
        )
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        degraded = degraded_graph_response(
            "",
            exc,
            provenance="exact_snapshot",
        )
    return {
        **{
            "latency": [],
            "tokens": [],
            "cost": [],
            "traffic": [],
        },
        **{key: value for key, value in degraded.items() if key.startswith("query_")},
    }


def fetch_user_system_metric_graph_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    timeout_ms: int = GRAPH_QUERY_TIMEOUT_MS,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Read one complete exact user-grain graph snapshot synchronously."""

    del refresh, organization_id, workspace_id
    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    interactive_deadline_ms = min(
        int(timeout_ms),
        GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
    )
    if interactive_deadline_ms <= 0:
        raise ValueError("graph timeout must be positive")
    bounded_analytics = _DeadlineBoundGraphAnalytics(
        analytics,
        ReadDeadline.start(interactive_deadline_ms),
    )
    normalized_metric_id = str(metric_id or "")
    try:
        response = read_exact_user_system_graph(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            metric_id=normalized_metric_id,
        )
        response.update(
            {
                "query_provenance": "exact_snapshot",
                "query_exact": True,
            }
        )
        return enforce_exact_graph_data_contract(response)
    except ExactGraphReadError as exc:
        return degraded_graph_response(
            normalized_metric_id,
            exc,
            provenance="exact_snapshot",
        )
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        return degraded_graph_response(
            normalized_metric_id,
            exc,
            provenance="exact_snapshot",
        )


def normalize_eval_graph_output_type(req_data_config: dict[str, Any]) -> str:
    raw = req_data_config.get("eval_output_type")
    if raw in (None, ""):
        raw = req_data_config.get("output_type", "SCORE")
    normalized = str(raw).strip().lower().replace("-", "_").replace("/", "_")
    normalized = "_".join(normalized.split())
    if normalized in {"bool", "pass_fail", "passfail"}:
        return "PASS_FAIL"
    if normalized in {"str_list", "choice", "choices"}:
        return "CHOICES"
    return "SCORE"


def _selected_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "failed", "fail", "no", "0"}
    return True


def _eval_entity_scope(
    sample: GraphCandidateSample, observe_type: str
) -> tuple[str, dict[str, Any]]:
    if observe_type == "span":
        span_entities = _span_entities(sample.rows)
        return _external_entity_scope(
            span_entities,
            entities_param="graph_span_entities",
        )

    trace_ids = tuple(
        str(row.get("trace_id")) for row in sample.rows if row.get("trace_id")
    )
    return "toString(trace_id) IN %(graph_trace_ids)s", {"graph_trace_ids": trace_ids}


def _finite_eval_rows(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    observe_type: str,
    eval_config_id: str,
    started: float,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    table, _ = eval_logger_source()
    version = "_version" if table.endswith("_v2") else "_peerdb_version"
    if table.endswith("_v2"):
        deleted_projection = "is_deleted AS graph_is_deleted"
        live_predicate = "graph_is_deleted = 0"
    else:
        deleted_projection = (
            "_peerdb_is_deleted AS graph_is_deleted, deleted AS graph_soft_deleted"
        )
        live_predicate = (
            "graph_is_deleted = 0 AND "
            "(graph_soft_deleted = 0 OR graph_soft_deleted IS NULL)"
        )
    # Neither eval-logger schema has a tracer project column. Project and
    # window ownership are therefore proven by the finite CH25 span/trace
    # identities in ``sample`` before this config-scoped logger read runs.
    entity_predicate, entity_params = _eval_entity_scope(sample, observe_type)
    if not entity_params or not any(entity_params.values()):
        return [], False, 0, 0
    query = f"""
    SELECT created_at, output_bool, output_float, output_str, output_str_list, error
    FROM (
        SELECT
            created_at,
            trace_id,
            observation_span_id,
            output_bool,
            output_float,
            output_str,
            output_str_list,
            error,
            {deleted_projection}
        FROM {table}
        PREWHERE custom_eval_config_id = toUUID(%(graph_eval_config_id)s)
          AND created_at >= %(graph_start_date)s - INTERVAL 7 DAY
          AND created_at < %(graph_end_date)s + INTERVAL 7 DAY
        WHERE {entity_predicate}
        ORDER BY {version} DESC
        LIMIT 1 BY id
    )
    WHERE {live_predicate}
      AND created_at >= %(graph_start_date)s
      AND created_at < %(graph_end_date)s
      AND {entity_predicate}
    ORDER BY created_at
    LIMIT %(graph_event_limit)s
    """
    params = {
        **entity_params,
        "graph_eval_config_id": str(eval_config_id),
        "graph_start_date": sample.window_start,
        "graph_end_date": sample.window_end,
        "graph_event_limit": GRAPH_EVENT_LIMIT + 1,
    }
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=_remaining_timeout_ms(started, GRAPH_DECORATION_TIMEOUT_MS),
        settings=GRAPH_EVENT_READ_SETTINGS,
    )
    rows = list(result.data or [])
    truncated = len(rows) > GRAPH_EVENT_LIMIT
    return rows[:GRAPH_EVENT_LIMIT], truncated, 1, len(rows)


def _json_choices(row: dict[str, Any]) -> list[str]:
    raw = row.get("output_str_list")
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        if isinstance(parsed, list):
            return [str(value) for value in parsed]
    fallback = row.get("output_str")
    return [str(fallback)] if fallback not in (None, "", "ERROR") else []


def _zero_filled_points(
    *,
    sample: GraphCandidateSample,
    interval: str,
    values: dict[datetime, tuple[float, int]],
) -> list[dict[str, Any]]:
    if sample.window_start >= sample.window_end:
        return []
    timestamps = list(
        BaseQueryBuilder._generate_timestamp_range(
            sample.window_start, sample.window_end, interval
        )
    )
    if len(timestamps) > GRAPH_MAX_POINTS:
        raise BoundedGraphReadError("sample_limit")
    return [
        {
            "timestamp": timestamp.isoformat(),
            "value": round(values.get(timestamp, (0.0, 0))[0], 9),
            "primary_traffic": values.get(timestamp, (0.0, 0))[1],
        }
        for timestamp in timestamps
    ]


def _eval_bucket_values(
    rows: list[dict[str, Any]],
    *,
    interval: str,
    output_type: str,
    selected: Any = None,
) -> dict[datetime, tuple[float, int]]:
    """Reduce one finite eval-event set into one chart series."""

    states: dict[datetime, dict[str, Any]] = {}
    selected_choice = str(selected) if selected not in (None, "") else None
    for row in rows:
        if row.get("error") or str(row.get("output_str") or "") == "ERROR":
            continue
        created_at = row.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        bucket = BaseQueryBuilder._normalize_timestamp(created_at, interval)
        state = states.setdefault(bucket, {"sum": 0.0, "count": 0})
        if output_type == "PASS_FAIL" and row.get("output_bool") is not None:
            matched = bool(row.get("output_bool")) == _selected_bool(selected)
            state["sum"] += 100.0 if matched else 0.0
            state["count"] += 1
        elif output_type == "CHOICES":
            row_choices = _json_choices(row)
            if row_choices:
                state["sum"] += 100.0 if selected_choice in row_choices else 0.0
                state["count"] += 1
        elif row.get("output_float") is not None:
            state["sum"] += float(row["output_float"]) * 100.0
            state["count"] += 1
    return {
        bucket: (state["sum"] / max(state["count"], 1), state["count"])
        for bucket, state in states.items()
    }


def _decoration_metadata(
    *,
    sample: GraphCandidateSample,
    truncated: bool,
    started: float,
    query_count: int,
    rows_returned: int,
) -> dict[str, Any]:
    """Describe exact decoration reads without presenting a sample as complete."""

    complete = sample.query_complete and not truncated
    sampled = sample.query_status == "sampled"
    metadata = sample.metadata()
    metadata.update(
        {
            "query_complete": complete,
            "query_status": (
                "sampled" if sampled else "complete" if complete else "degraded"
            ),
            "query_count": query_count,
            "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
            "query_rows_returned": rows_returned,
        }
    )
    if complete:
        metadata.pop("query_error_code", None)
    elif sampled:
        metadata["query_error_code"] = sample.query_error_code or "sample_limit"
    else:
        metadata["query_error_code"] = "sample_limit"
    return metadata


def _finite_eval_graph(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    observe_type: str,
    interval: str,
    req_data_config: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    if str(observe_type or "").strip().lower() == "trace":
        sample = _bounded_trace_decoration_sample(sample)
    metric_id = str(req_data_config.get("id") or "")
    output_type = normalize_eval_graph_output_type(req_data_config)
    selected = req_data_config.get("value")
    choices = list(req_data_config.get("choices") or [])
    selected_choice = str(selected) if selected not in (None, "") else None
    if output_type == "CHOICES" and selected_choice is None and choices:
        selected_choice = str(choices[0])

    rows, truncated, event_query_count, event_rows_returned = _finite_eval_rows(
        analytics=analytics,
        sample=sample,
        observe_type=observe_type,
        eval_config_id=metric_id,
        started=started,
    )
    values = _eval_bucket_values(
        rows,
        interval=interval,
        output_type=output_type,
        selected=selected_choice if output_type == "CHOICES" else selected,
    )
    metadata = _decoration_metadata(
        sample=sample,
        truncated=truncated,
        started=started,
        query_count=sample.query_count + event_query_count,
        rows_returned=sample.rows_returned + event_rows_returned,
    )
    return enforce_exact_graph_data_contract(
        {
            "metric_name": metric_id,
            "data": _zero_filled_points(
                sample=sample,
                interval=interval,
                values=values,
            ),
            **metadata,
        }
    )


def fetch_eval_graph_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    observe_type: str = "trace",
    timeout_ms: int = GRAPH_QUERY_TIMEOUT_MS,
    refresh: bool = False,
    aggregation_context: str = "trace",
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    normalized_observe_type = str(observe_type or "trace").strip().lower()
    normalized_aggregation_context = str(aggregation_context or "trace").strip().lower()
    if normalized_aggregation_context not in {"trace", "session", "user"}:
        raise ValueError("unsupported eval graph aggregation context")
    del refresh, organization_id, workspace_id
    interactive_deadline_ms = min(
        int(timeout_ms),
        GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
    )
    if interactive_deadline_ms <= 0:
        raise ValueError("graph timeout must be positive")
    bounded_analytics = _DeadlineBoundGraphAnalytics(
        analytics,
        ReadDeadline.start(interactive_deadline_ms),
    )
    try:
        response = read_exact_eval_graph(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            req_data_config=req_data_config,
            observe_type=normalized_observe_type,
            aggregation_context=normalized_aggregation_context,
        )
    except ExactGraphReadError as exc:
        return degraded_graph_response(
            str(req_data_config.get("id") or ""),
            exc,
            provenance="exact_snapshot",
        )
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        return degraded_graph_response(
            str(req_data_config.get("id") or ""),
            exc,
            provenance="exact_snapshot",
        )
    if not isinstance(response, dict):
        raise ExactGraphReadError("eval graph returned an invalid payload")
    response.update({"query_provenance": "exact_snapshot", "query_exact": True})
    return enforce_exact_graph_data_contract(response)


def fetch_eval_chart_series_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    eval_name: str,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a cached complete exact eval-chart series bundle."""

    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    identity = {
        "project_id": project_id,
        "filters": filters,
        "interval": interval,
        "req_data_config": req_data_config,
        "eval_name": eval_name,
    }
    return _read_or_refresh_exact_graph(
        namespace="observe-eval-chart-series",
        identity=identity,
        refresh=bool(refresh),
        pending_payload=[
            _pending_graph_payload(
                str(req_data_config.get("id") or ""),
                id=str(req_data_config.get("id") or ""),
                name=str(eval_name or ""),
            )
        ],
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def annotation_output_type(
    label: AnnotationsLabels, requested: str | None = None
) -> str:
    if requested:
        return str(requested)
    if label.type in (
        AnnotationTypeChoices.NUMERIC.value,
        AnnotationTypeChoices.STAR.value,
    ):
        return "float"
    if label.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
        return "bool"
    if label.type == AnnotationTypeChoices.CATEGORICAL.value:
        return "str_list"
    return "text"


def _finite_trace_span_ids(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    project_id: str,
    started: float,
) -> tuple[tuple[SpanIdentity, ...], bool, int, int]:
    """Resolve finite trace-span candidates against their global latest state."""

    (
        candidate_span_identities,
        truncated,
        seed_query_count,
        seed_rows_returned,
    ) = _finite_trace_span_candidates(
        analytics=analytics,
        sample=sample,
        project_id=project_id,
        started=started,
        timeout_cap_ms=GRAPH_DECORATION_TIMEOUT_MS,
    )
    bounded_sample = _bounded_trace_decoration_sample(sample)
    trace_ids = _candidate_trace_ids(bounded_sample)
    if not trace_ids or not candidate_span_identities:
        return (), truncated, seed_query_count, seed_rows_returned
    query = """
    SELECT grouped_trace_id AS trace_id, grouped_id AS id, latest_start_time AS start_time
    FROM (
        SELECT
            id AS grouped_id,
            trace_id AS grouped_trace_id,
            start_time AS latest_start_time,
            argMax(is_deleted, _version) AS latest_is_deleted
        FROM spans
        PREWHERE project_id = toUUID(%(graph_project_id)s)
          AND toDate(start_time) IN %(graph_span_dates)s
          AND trace_id IN %(graph_trace_ids)s
          AND id IN %(graph_span_ids)s
        WHERE (
            trace_id,
            id,
            toUnixTimestamp64Micro(start_time)
        ) IN %(graph_span_identities)s
        GROUP BY trace_id, id, start_time
    )
    WHERE latest_is_deleted = 0
      AND latest_start_time >= %(graph_start_date)s
      AND latest_start_time < %(graph_end_date)s
    ORDER BY latest_start_time DESC, grouped_id DESC, grouped_trace_id DESC
    LIMIT %(graph_entity_limit)s
    """
    result = analytics.execute_ch_query(
        query,
        {
            "graph_project_id": project_id,
            "graph_span_dates": _span_identity_dates(candidate_span_identities),
            "graph_trace_ids": trace_ids,
            "graph_span_ids": tuple(
                dict.fromkeys(identity[1] for identity in candidate_span_identities)
            ),
            "graph_span_identities": candidate_span_identities,
            "graph_start_date": sample.window_start,
            "graph_end_date": sample.window_end,
            "graph_entity_limit": GRAPH_CANDIDATE_LIMIT + 1,
        },
        timeout_ms=_remaining_timeout_ms(started, GRAPH_DECORATION_TIMEOUT_MS),
        settings=GRAPH_ENTITY_READ_SETTINGS,
    )
    rows = list(result.data or [])
    span_identities = tuple(
        dict.fromkeys(
            identity for row in rows if (identity := _span_identity(row)) is not None
        )
    )
    return (
        span_identities,
        truncated,
        seed_query_count + 1,
        seed_rows_returned + len(rows),
    )


def _annotation_entity_scope(
    sample: GraphCandidateSample,
    observe_type: str,
    trace_span_identities: tuple[SpanIdentity, ...],
) -> tuple[str, dict[str, Any]]:
    if observe_type == "span":
        span_entities = _span_entities(sample.rows)
        return _external_entity_scope(
            span_entities,
            entities_param="graph_span_entities",
        )

    trace_ids = tuple(
        str(row.get("trace_id")) for row in sample.rows if row.get("trace_id")
    )
    predicates: list[str] = []
    params: dict[str, Any] = {}
    if trace_ids:
        predicates.append("toString(trace_id) IN %(graph_trace_ids)s")
        params["graph_trace_ids"] = trace_ids
    if trace_span_identities:
        span_entities = tuple(
            dict.fromkeys(
                (identity[0], identity[1]) for identity in trace_span_identities
            )
        )
        span_predicate, span_params = _external_entity_scope(
            span_entities,
            entities_param="graph_span_entities",
        )
        predicates.append(span_predicate)
        params.update(span_params)
    return "(" + " OR ".join(predicates) + ")", params


def _finite_annotation_rows(
    *,
    analytics: Any,
    sample: GraphCandidateSample,
    project_id: str,
    observe_type: str,
    trace_span_identities: tuple[SpanIdentity, ...],
    label_id: str,
    started: float,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    _entity_predicate, entity_params = _annotation_entity_scope(
        sample, observe_type, trace_span_identities
    )
    if not entity_params or not any(entity_params.values()):
        return [], False, 0, 0
    # Score is authoritative in PostgreSQL.  The legacy CDC score table is not
    # co-located with direct-write CH25 spans, so a V2 query must never try to
    # join the two clusters.  CH25 has already supplied a finite, project-
    # scoped candidate set; replay only those exact identities against the
    # denormalized Score.tracer_project_id index.
    _remaining_timeout_ms(started, GRAPH_DECORATION_TIMEOUT_MS)
    try:
        rows = AnnotationLabelScoresProjectPG().annotation_rows_for_candidates(
            project_id=project_id,
            label_id=str(label_id),
            start_date=sample.window_start,
            end_date=sample.window_end,
            trace_ids=tuple(entity_params.get("graph_trace_ids") or ()),
            span_entities=tuple(entity_params.get("graph_span_entities") or ()),
            limit=GRAPH_EVENT_LIMIT + 1,
        )
    except AnnotationScoreReadUnavailable:
        raise BoundedGraphReadError("read_budget_exceeded") from None
    except Exception as exc:
        # Database availability/time-budget failures are a retryable graph
        # boundary, never a raw internal error response.  Programming defects
        # remain visible to the outer sanitized 500 handler.
        from django.db import DatabaseError

        if isinstance(exc, DatabaseError):
            raise BoundedGraphReadError("read_budget_exceeded") from None
        raise
    truncated = len(rows) > GRAPH_EVENT_LIMIT
    return rows[:GRAPH_EVENT_LIMIT], truncated, 1, len(rows)


def _annotation_value(payload: Any, output_type: str, selected: Any) -> float | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
    if not isinstance(payload, dict):
        return None
    if output_type == "float":
        raw = payload.get("rating", payload.get("value"))
        try:
            return float(raw) if raw is not None and not isinstance(raw, bool) else None
        except (TypeError, ValueError, OverflowError):
            return None
    if output_type == "bool":
        raw = payload.get("value")
        wanted = "up" if _selected_bool(selected) else "down"
        return 100.0 if str(raw).lower() == wanted else 0.0
    if output_type == "str_list":
        values = payload.get("selected") or []
        if isinstance(values, str):
            values = [values]
        return 100.0 if str(selected) in {str(value) for value in values} else 0.0
    return 1.0


def fetch_annotation_graph_ch(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    observe_type: str,
    timeout_ms: int = GRAPH_QUERY_TIMEOUT_MS,
    refresh: bool = False,
    aggregation_context: str = "trace",
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    project_id = _validated_project_id(project_id)
    filters = list(filters or [])
    label_id = str(req_data_config.get("id") or "")
    if not label_id:
        raise ValueError("Annotation label ID is required")
    normalized_observe_type = str(observe_type or "trace").strip().lower()
    normalized_aggregation_context = str(aggregation_context or "trace").strip().lower()
    if normalized_aggregation_context not in {"trace", "session", "user"}:
        raise ValueError("unsupported annotation graph aggregation context")
    del refresh, organization_id, workspace_id
    interactive_deadline_ms = min(
        int(timeout_ms),
        GRAPH_INTERACTIVE_QUERY_TIMEOUT_MS,
    )
    if interactive_deadline_ms <= 0:
        raise ValueError("graph timeout must be positive")
    bounded_analytics = _DeadlineBoundGraphAnalytics(
        analytics,
        ReadDeadline.start(interactive_deadline_ms),
    )
    try:
        response = read_exact_annotation_graph(
            analytics=bounded_analytics,
            project_id=project_id,
            filters=filters,
            interval=interval,
            req_data_config=req_data_config,
            observe_type=normalized_observe_type,
            aggregation_context=normalized_aggregation_context,
        )
    except ExactGraphReadError as exc:
        return degraded_graph_response(label_id, exc, provenance="exact_snapshot")
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        return degraded_graph_response(label_id, exc, provenance="exact_snapshot")
    response.update({"query_provenance": "exact_snapshot", "query_exact": True})
    return enforce_exact_graph_data_contract(response)


__all__ = [
    "GRAPH_READ_SETTINGS",
    "GRAPH_WALL_DEADLINE_MS",
    "annotation_output_type",
    "degraded_graph_response",
    "enforce_exact_graph_data_contract",
    "graph_payload_is_publishable",
    "fetch_all_system_metrics_ch",
    "fetch_agent_graph_ch",
    "fetch_annotation_graph_ch",
    "fetch_eval_chart_series_ch",
    "fetch_eval_graph_ch",
    "fetch_system_metric_graph_ch",
    "fetch_user_system_metric_graph_ch",
    "format_system_metric_graph",
    "normalize_eval_graph_output_type",
]
