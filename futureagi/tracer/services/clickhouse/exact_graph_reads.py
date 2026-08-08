"""Exact current-state aggregation reads for public Observe graphs.

ClickHouse 25.3 cannot share a snapshot across separately executed statements,
and a version predicate on ``ReplacingMergeTree`` is not time travel after a
background merge.  Aggregate readers therefore execute each ClickHouse metric
as one full-window statement, use ``FINAL`` in the query builders, and publish
only a complete result through ``exact_aggregation_cache``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

import structlog
from django.db import DatabaseError, connection, transaction

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.score import Score
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.annotation_label_source import AnnotationScoreReadUnavailable
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder
from tracer.services.clickhouse.query_builders.agent_graph import (
    AGENT_GRAPH_MAX_RESULT_BYTES,
    AGENT_GRAPH_RESULT_ROW_SENTINEL,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
    compile_span_attribute_row_predicate,
)
from tracer.services.clickhouse.query_builders.session_filters import (
    SESSION_ID_FILTER_COLS,
    build_session_id_filter_clause,
)
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    resolved_id_expr,
    survivor_map_subquery,
)
from tracer.services.clickhouse.v2.query_builders.agent_graph import (
    AgentGraphQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    rewrite_v1_sql_to_v2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)
from tracer.utils.helper import get_annotation_labels_for_project

logger = structlog.get_logger(__name__)

# Exact graphs run only in the deduplicated ``tasks_xl`` refresh activity; the
# HTTP request schedules that work and polls the last complete snapshot.  The
# production qualification set contains valid exact queries with p95 958s and
# a 1032.479s ceiling, so the old five-minute ClickHouse deadline rejected
# healthy work.  The largest production tenant already contains more than
# 207M physical rows in a twelve-month window.  Give an exact refresh up to
# 55 minutes while retaining an independent five-minute activity shutdown
# margin; HTTP requests never wait for this work and keep serving the last
# atomically published exact snapshot.
EXACT_GRAPH_QUERY_TIMEOUT_MS = 3_300_000
# This partition size belongs to the PostgreSQL-backed annotation membership
# reader below.  System graphs deliberately remain one ClickHouse statement so
# CH25.3 cannot stitch independently changing ReplacingMergeTree snapshots.
EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION = 31
EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE = 1_000
EXACT_GRAPH_READ_SETTINGS = {
    "max_threads": 1,
    # Attribute maps are several KiB per row on the heaviest tenants.  Smaller
    # source blocks keep decompression below the fixed query-memory envelope
    # while the in-order latest-row reducer consumes them.
    "max_block_size": 512,
    "preferred_block_size_bytes": 4 * 1024 * 1024,
    # Map columns can dominate a block even when the row-count limit is low.
    # This CH25 setting asks the reader to split once any single wide column
    # reaches the same byte envelope.
    "preferred_max_column_in_block_size_bytes": 4 * 1024 * 1024,
    # The direct-write table is ordered by the complete physical span
    # identity.  The exact builder resolves ReplacingMergeTree winners with an
    # argMax aggregation in that order, so ClickHouse can retire each logical
    # row instead of retaining every wide span in a hash table.
    "optimize_aggregation_in_order": 1,
    # Later trace/bucket reductions are not ordered by the physical primary
    # key.  Spill those compact scalar states before they threaten the worker
    # memory ceiling; no raw attribute Map/JSON value crosses the first stage.
    "max_bytes_before_external_group_by": 32 * 1024 * 1024,
    "max_bytes_before_external_sort": 32 * 1024 * 1024,
    # Exact reads collapse ReplacingMergeTree versions before applying mutable
    # value predicates.  Keep these defenses explicit for the related exact
    # readers that still use FINAL; the argMax spans source itself exposes only
    # immutable project/time predicates to PREWHERE.
    "optimize_move_to_prewhere_if_final": 0,
    "use_skip_indexes_if_final": 0,
    # Row/byte volume is data, not an error condition.  Production evidence
    # shows 207,479,677 physical rows in a valid twelve-month window and a
    # seven-day attribute graph reading 68,719,963,633 bytes: the former was
    # rejected by the old 100M-row cap and the latter crossed the old 64-GiB
    # cap by 486,897 bytes.  ClickHouse defines zero as unlimited for these two
    # settings.  Time, memory, one-thread execution, refresh admission, bounded
    # result size, and atomic publication remain the operational safeguards.
    "max_rows_to_read": 0,
    "max_bytes_to_read": 0,
    # The same observed seven-day read peaked at 1,055,221,165 bytes.  Preserve
    # measured headroom while spilling compact aggregation/sort state early;
    # the tasks_xl worker has a separate 32-GiB pod limit.
    "max_memory_usage": 1536 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": 10_001,
    "max_result_bytes": 32 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


class ExactGraphReadError(RuntimeError):
    """A complete exact graph refresh could not be produced."""


def output_bucket_partitions(
    start_date: datetime,
    end_date: datetime,
    interval: str,
    *,
    max_buckets: int = EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION,
) -> tuple[tuple[datetime, datetime], ...]:
    """Split a half-open window without bisecting an output bucket."""

    if max_buckets < 1:
        raise ValueError("max_buckets must be positive")
    if start_date >= end_date:
        return ()
    bucket_starts = [
        _align_partition_boundary_timezone(boundary, start_date)
        for boundary in BaseQueryBuilder._generate_timestamp_range(
            start_date, end_date, interval
        )
    ]
    cuts = [
        boundary
        for index, boundary in enumerate(bucket_starts)
        if index > 0 and index % max_buckets == 0 and start_date < boundary < end_date
    ]
    boundaries = [start_date, *cuts, end_date]
    return tuple(zip(boundaries, boundaries[1:], strict=False))


def _snapshot_window(
    filters: list[dict[str, Any]],
) -> tuple[datetime, datetime, bool]:
    analyzed = BaseQueryBuilder.analyze_bounded_datetime_filters(filters, strict=True)
    return analyzed.start, analyzed.end, analyzed.empty


def _annotation_label_ids_for_filters(
    project_id: str,
    filters: list[dict[str, Any]],
) -> tuple[str, ...] | None:
    """Resolve the authoritative label set only for completeness filters.

    ``has_annotation`` means all configured project labels on every public
    tracing surface.  Falling back to mere Score existence makes exact graphs
    disagree with trace/span/task lists.  Metadata outages must also fail the
    refresh instead of publishing a plausible but false empty result.
    """

    needs_completeness = any(
        isinstance(item, dict)
        and (item.get("column_id") or item.get("columnId")) == "has_annotation"
        for item in filters or []
    )
    if not needs_completeness:
        return None
    try:
        return tuple(
            sorted(
                str(label.id)
                for label in get_annotation_labels_for_project(project_id)
                if getattr(label, "id", None)
            )
        )
    except (AnnotationScoreReadUnavailable, DatabaseError):
        raise ExactGraphReadError(
            "Annotation metadata is temporarily unavailable. Retry."
        ) from None


def _metadata(
    *,
    started: float,
    query_count: int,
    rows_returned: int,
) -> dict[str, Any]:
    metadata = {
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_count": query_count,
        "query_rows_returned": rows_returned,
        "query_elapsed_ms": round((monotonic() - started) * 1000, 3),
    }
    return metadata


def _align_partition_boundary_timezone(
    boundary: datetime, reference: datetime
) -> datetime:
    """Match generated bucket boundaries to the caller's datetime awareness."""

    if reference.tzinfo is not None and boundary.tzinfo is None:
        return boundary.replace(tzinfo=reference.tzinfo)
    if reference.tzinfo is None and boundary.tzinfo is not None:
        return boundary.replace(tzinfo=None)
    return boundary


def _system_metric_payload(
    metrics: dict[str, Any], metric_id: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    normalized = str(metric_id or "latency").strip().lower()
    metric_key = {
        "total_tokens": "total_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
    }.get(normalized, normalized)
    if metric_key not in metrics:
        metric_key = "latency"
    points = metrics.get(metric_key, [])
    traffic = {
        point.get("timestamp"): point.get("traffic", point.get("value", 0))
        for point in metrics.get("traffic", [])
    }
    return {
        "metric_name": str(metric_id or ""),
        "data": [
            {
                "timestamp": point.get("timestamp"),
                "value": point.get("value", point.get(metric_key, 0)),
                "primary_traffic": traffic.get(point.get("timestamp"), 0),
            }
            for point in points
        ],
        **metadata,
    }


def read_exact_system_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
    observe_type: str,
) -> dict[str, Any]:
    started = monotonic()
    start_date, end_date, empty = _snapshot_window(filters)
    if empty:
        builder = TimeSeriesQueryBuilder(
            project_id=str(project_id),
            filters=filters,
            interval=interval,
            exact_snapshot=True,
            observe_type=observe_type,
            start_date=start_date,
            end_date=end_date,
        )
        metrics = builder.format_result([], [])
        return _system_metric_payload(
            metrics,
            metric_id,
            _metadata(
                started=started,
                query_count=0,
                rows_returned=0,
            ),
        )

    builder = TimeSeriesQueryBuilder(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=start_date,
        end_date=end_date,
        annotation_label_ids=_annotation_label_ids_for_filters(project_id, filters),
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    metrics = builder.format_result(rows, columns)
    return _system_metric_payload(
        metrics,
        metric_id,
        _metadata(
            started=started,
            query_count=1,
            rows_returned=len(rows),
        ),
    )


def read_exact_agent_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute exact node and recorded parent-topology projections.

    The builder emits one direct-write statement.  Keeping execution here,
    behind the shared exact-snapshot worker, prevents HTTP retries from
    multiplying a long full-window read and guarantees that only a completely
    formatted graph can replace the previous snapshot.
    """

    started = monotonic()
    builder = AgentGraphQueryBuilderV2(
        project_id=str(project_id),
        filters=list(filters or []),
        annotation_label_ids=_annotation_label_ids_for_filters(project_id, filters),
    )
    if builder.empty_window:
        return {
            **builder.format_result([], []),
            **_metadata(started=started, query_count=0, rows_returned=0),
        }

    query, params = builder.build()
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings={
            **EXACT_GRAPH_READ_SETTINGS,
            "max_threads": 1,
            # The SQL statement ranks exact node aggregates, retains the top
            # 63, and folds every remaining endpoint into an explicit Other
            # node before transport. With 64 wire nodes it can emit at most
            # N + 2*N^2 rows; this sentinel makes that proof executable and
            # prevents a future regression from allocating an unbounded Python
            # result before formatting.
            "max_result_rows": AGENT_GRAPH_RESULT_ROW_SENTINEL,
            "max_result_bytes": AGENT_GRAPH_MAX_RESULT_BYTES,
        },
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    return {
        **builder.format_result(rows, columns),
        **_metadata(started=started, query_count=1, rows_returned=len(rows)),
    }


def read_exact_all_system_metrics(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
) -> dict[str, Any]:
    started = monotonic()
    start_date, end_date, empty = _snapshot_window(filters)
    if empty:
        builder = TimeSeriesQueryBuilder(
            project_id=str(project_id),
            filters=filters,
            interval=interval,
            exact_snapshot=True,
            observe_type="span",
            start_date=start_date,
            end_date=end_date,
        )
        return {
            **builder.format_result([], []),
            **_metadata(
                started=started,
                query_count=0,
                rows_returned=0,
            ),
        }
    builder = TimeSeriesQueryBuilder(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        exact_snapshot=True,
        observe_type="span",
        start_date=start_date,
        end_date=end_date,
        annotation_label_ids=_annotation_label_ids_for_filters(project_id, filters),
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    return {
        **builder.format_result(rows, columns),
        **_metadata(
            started=started,
            query_count=1,
            rows_returned=len(rows),
        ),
    }


def _row_value(row: Any, columns: list[str], key: str, default: Any = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        index = columns.index(key)
    except ValueError:
        return default
    return row[index] if index < len(row) else default


def _add_primary_traffic(
    series: dict[str, Any], rows: list[Any], columns: list[str]
) -> dict[str, Any]:
    traffic: dict[str, int] = {}
    for row in rows:
        timestamp = _row_value(row, columns, "time_bucket", None)
        if timestamp is None:
            continue
        key = (
            timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        )
        traffic[key] = int(
            _row_value(
                row,
                columns,
                "primary_traffic",
                _row_value(row, columns, "total_count", 0),
            )
            or 0
        )
    copied = {**series}
    copied["data"] = [
        {**point, "primary_traffic": traffic.get(point.get("timestamp"), 0)}
        for point in series.get("data", [])
    ]
    return copied


_EVAL_FILTER_COLUMN_IDS = frozenset({"has_eval"})
_ANNOTATION_FILTER_COLUMN_IDS = frozenset(
    {"annotator", "has_annotation", "my_annotations"}
)


@dataclass(frozen=True)
class _FilterRelationRequirements:
    """Relations consulted by a graph filter payload.

    This describes query topology only.  It must never be used to construct a
    ReplacingMergeTree version ceiling on ClickHouse 25.3.
    """

    eval_logger: bool = False
    score: bool = False
    end_users: bool = False


def _filter_relation_requirements(
    filters: list[dict[str, Any]],
) -> _FilterRelationRequirements:
    needs_eval = False
    needs_score = False
    needs_end_users = False
    for item in filters or []:
        if not isinstance(item, dict):
            raise ExactGraphReadError("graph filter plan is invalid")
        column_id = item.get("column_id") or item.get("columnId")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if not isinstance(config, dict):
            raise ExactGraphReadError("graph filter plan is invalid")
        column_type = str(config.get("col_type") or config.get("colType") or "").upper()
        needs_eval = needs_eval or (
            column_type == ClickHouseFilterBuilderV2.EVAL_METRIC
            or column_id in _EVAL_FILTER_COLUMN_IDS
        )
        needs_score = needs_score or (
            column_type == ClickHouseFilterBuilderV2.ANNOTATION
            or column_id in _ANNOTATION_FILTER_COLUMN_IDS
        )
        needs_end_users = needs_end_users or (
            column_type == ClickHouseFilterBuilderV2.TRACE_END_USER
            or column_id in ClickHouseFilterBuilderV2._ENDUSER_STRING_COLUMNS
        )
    return _FilterRelationRequirements(
        eval_logger=needs_eval,
        score=needs_score,
        end_users=needs_end_users,
    )


def _eval_partition_trace_ids_sql() -> str:
    """Return exact trace candidates for the current eval output partition.

    The enclosing executor replaces ``start_date``/``end_date`` for every
    output window.
    """

    eval_table, eval_live = eval_logger_source(
        "candidate_eval",
        include_cdc_tombstone_guard=True,
    )
    return f"""
        SELECT DISTINCT toString(candidate_eval.trace_id)
        FROM {eval_table} AS candidate_eval FINAL
        WHERE {eval_live}
          AND candidate_eval.custom_eval_config_id =
              toUUID(%(eval_config_id)s)
          AND candidate_eval.created_at >= %(start_date)s
          AND candidate_eval.created_at < %(end_date)s
          AND isNotNull(candidate_eval.trace_id)
          AND candidate_eval.trace_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
    """


def read_exact_eval_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    observe_type: str,
    all_series: bool = False,
    aggregation_context: str = "trace",
) -> dict[str, Any] | list[dict[str, Any]]:
    started = monotonic()
    aggregation_context = str(aggregation_context or "trace").strip().lower()
    if aggregation_context not in {"trace", "session", "user"}:
        raise ValueError("unsupported eval graph aggregation context")
    if aggregation_context in {"session", "user"} and observe_type != "trace":
        raise ValueError("aggregate eval graphs require trace observation mode")
    config_id = str(req_data_config.get("id") or "")
    config = CustomEvalConfig.objects.select_related("eval_template").get(
        id=config_id,
        project_id=project_id,
        deleted=False,
    )
    start_date, end_date, empty = _snapshot_window(filters)
    output_type = req_data_config.get("eval_output_type") or req_data_config.get(
        "output_type"
    )
    if not output_type:
        output_type = config.eval_template.config.get("output", "SCORE")
    choices = list(req_data_config.get("choices") or config.eval_template.choices or [])
    if not all_series and str(output_type).upper() in {"CHOICE", "CHOICES"}:
        selected = req_data_config.get("value")
        choices = [str(selected)] if selected not in (None, "") else choices[:1]
    session_membership_sql = None
    session_membership_params = None
    user_membership_sql = None
    user_membership_params = None
    candidate_eval_trace_ids_sql = None
    if aggregation_context in {"session", "user"} and not empty:
        candidate_eval_trace_ids_sql = _eval_partition_trace_ids_sql()
    if aggregation_context == "session" and not empty:
        session_membership_sql, session_membership_params = (
            _session_trace_membership_sql(
                project_id=str(project_id),
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                candidate_trace_ids_sql=candidate_eval_trace_ids_sql,
            )
        )
    elif aggregation_context == "user" and not empty:
        user_membership_sql, user_membership_params, _needs_eval = (
            _user_trace_membership_sql(
                project_id=str(project_id),
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                candidate_trace_ids_sql=candidate_eval_trace_ids_sql,
            )
        )
    builder = EvalMetricsQueryBuilderV2(
        project_id=str(project_id),
        custom_eval_config_id=config_id,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
        eval_output_type=output_type,
        eval_name=config.name,
        choices=choices,
        # Session filters are compiled exactly once by the shared per-session
        # selector. Passing them to the generic trace builder as well would
        # reinterpret aggregate/message fields as raw span attributes.
        filters=[] if aggregation_context in {"session", "user"} else filters,
        observe_type=observe_type,
        session_trace_membership_sql=session_membership_sql,
        session_trace_membership_params=session_membership_params,
        user_trace_membership_sql=user_membership_sql,
        user_trace_membership_params=user_membership_params,
        annotation_label_ids=(
            _annotation_label_ids_for_filters(project_id, filters)
            if aggregation_context == "trace" and not empty
            else ()
        ),
    )
    if empty:
        formatted = builder.format_result([], [])
        series = formatted if isinstance(formatted, list) else [formatted]
        metadata = _metadata(
            started=started,
            query_count=0,
            rows_returned=0,
        )
    else:
        query, params = builder.build()
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
            settings=EXACT_GRAPH_READ_SETTINGS,
        )
        rows = list(result.data or [])
        columns = list(result.columns or [])
        formatted = builder.format_result(rows, columns)
        raw_series = formatted if isinstance(formatted, list) else [formatted]
        series = [_add_primary_traffic(item, rows, columns) for item in raw_series]
        metadata = _metadata(
            started=started,
            query_count=1,
            rows_returned=len(rows),
        )
    exact_series = [{**item, "metric_name": config_id, **metadata} for item in series]
    if all_series:
        return exact_series
    if exact_series:
        return exact_series[0]
    return {"metric_name": config_id, "data": [], **metadata}


def _annotation_numeric_value(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("rating", payload.get("value"))
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError, OverflowError):
        return None


def _annotation_value(payload: Any, output_type: str, selected: Any) -> float | None:
    if output_type == "float":
        return _annotation_numeric_value(payload)
    if output_type == "bool":
        if not isinstance(payload, dict):
            return None
        wanted = str(selected).lower() not in {"false", "down", "0", "no"}
        return (
            100.0
            if str(payload.get("value", "")).lower() == ("up" if wanted else "down")
            else 0.0
        )
    if output_type == "str_list":
        if not isinstance(payload, dict):
            return None
        values = payload.get("selected") or []
        if isinstance(values, str):
            values = [values]
        return 100.0 if str(selected) in {str(value) for value in values} else 0.0
    return 1.0


def _compile_membership_filter(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    observe_type: str,
    annotation_label_ids: tuple[str, ...] | None = None,
) -> tuple[str, dict[str, Any]]:
    return compile_exact_graph_filter_predicates(
        filters,
        project_id=project_id,
        observe_type=observe_type,
        annotation_label_ids=(
            _annotation_label_ids_for_filters(project_id, filters)
            if annotation_label_ids is None
            else annotation_label_ids
        ),
    )


def _span_batch_trace_ids_sql() -> str:
    """Resolve the owning trace candidates for one annotation span batch."""

    return """
        SELECT DISTINCT toString(annotation_candidate.trace_id)
        FROM spans AS annotation_candidate FINAL
        PREWHERE annotation_candidate.project_id = toUUID(%(project_id)s)
          AND annotation_candidate.start_time >= %(snapshot_start_date)s
          AND annotation_candidate.start_time < %(snapshot_end_date)s
          AND annotation_candidate.id IN %(candidate_span_ids)s
        WHERE annotation_candidate.is_deleted = 0
    """


def _matching_trace_ids(
    *,
    analytics: Any,
    project_id: str,
    trace_ids: tuple[str, ...],
    start_date: datetime,
    end_date: datetime,
    predicate: str,
    predicate_params: dict[str, Any],
    settings: dict[str, Any],
) -> set[str]:
    if not trace_ids:
        return set()
    clause = f"AND {predicate}" if predicate else ""
    result = analytics.execute_ch_query(
        f"""
        SELECT DISTINCT trace_id
        FROM spans FINAL
        PREWHERE project_id = toUUID(%(project_id)s)
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
        WHERE is_deleted = 0
          AND trace_id IN %(candidate_trace_ids)s
          {clause}
        """,
        {
            **predicate_params,
            "project_id": project_id,
            "snapshot_start_date": start_date,
            "snapshot_end_date": end_date,
            "candidate_trace_ids": trace_ids,
        },
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=settings,
    )
    return {
        str(row.get("trace_id") if isinstance(row, dict) else row[0])
        for row in result.data or []
    }


def _matching_span_ids(
    *,
    analytics: Any,
    project_id: str,
    span_ids: tuple[str, ...],
    start_date: datetime,
    end_date: datetime,
    predicate: str,
    predicate_params: dict[str, Any],
    settings: dict[str, Any],
) -> set[str]:
    if not span_ids:
        return set()
    result = analytics.execute_ch_query(
        f"""
        SELECT
            id,
            uniqExact(trace_id) AS identity_count,
            max(toUInt8({predicate if predicate else "1"})) AS matched
        FROM spans FINAL
        PREWHERE project_id = toUUID(%(project_id)s)
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
          AND id IN %(candidate_span_ids)s
        WHERE is_deleted = 0
        GROUP BY id
        """,
        {
            **predicate_params,
            "project_id": project_id,
            "snapshot_start_date": start_date,
            "snapshot_end_date": end_date,
            "candidate_span_ids": span_ids,
        },
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=settings,
    )
    matched: set[str] = set()
    for row in result.data or []:
        span_id = str(row.get("id") if isinstance(row, dict) else row[0])
        identity_count = int(
            row.get("identity_count", 0) if isinstance(row, dict) else row[1]
        )
        is_match = int(row.get("matched", 0) if isinstance(row, dict) else row[2])
        if identity_count != 1:
            raise ExactGraphReadError(
                "an annotation span identity is ambiguous within the project"
            )
        if is_match:
            matched.add(span_id)
    return matched


def read_exact_annotation_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    req_data_config: dict[str, Any],
    observe_type: str,
    aggregation_context: str = "trace",
) -> dict[str, Any]:
    started = monotonic()
    aggregation_context = str(aggregation_context or "trace").strip().lower()
    if aggregation_context not in {"trace", "session", "user"}:
        raise ValueError("unsupported annotation graph aggregation context")
    if aggregation_context in {"session", "user"} and observe_type != "trace":
        raise ValueError("aggregate annotation graphs require trace observation mode")
    label_id = str(req_data_config.get("id") or "")
    label = get_annotation_labels_for_project(project_id).get(id=label_id)
    output_type = req_data_config.get("output_type")
    if not output_type:
        annotation_type = str(label.type)
        output_type = {
            AnnotationTypeChoices.THUMBS_UP_DOWN.value: "bool",
            AnnotationTypeChoices.NUMERIC.value: "float",
            AnnotationTypeChoices.STAR.value: "float",
            AnnotationTypeChoices.CATEGORICAL.value: "str_list",
            AnnotationTypeChoices.TEXT.value: "text",
        }.get(annotation_type, "float")
    output_type = str(output_type).lower()
    selected = req_data_config.get("value")
    start_date, end_date, empty = _snapshot_window(filters)
    if empty:
        return {
            "metric_name": label_id,
            "name": label.name,
            "data": [],
            **_metadata(
                started=started,
                query_count=0,
                rows_returned=0,
            ),
        }

    settings: dict[str, Any] = {**EXACT_GRAPH_READ_SETTINGS}
    if aggregation_context == "session":
        session_trace_sql, session_trace_params = _session_trace_membership_sql(
            project_id=str(project_id),
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            candidate_trace_ids_param="candidate_trace_ids",
        )
        trace_predicate = f"trace_id IN ({session_trace_sql})"
        trace_params = session_trace_params
        # A span-attached annotation belongs to the selected session when its
        # owning trace does. Resolve that candidate trace from the finite span
        # batch, then evaluate the same full-session membership semantics.
        session_span_sql, session_span_params = _session_trace_membership_sql(
            project_id=str(project_id),
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            candidate_trace_ids_sql=_span_batch_trace_ids_sql(),
        )
        span_predicate = f"trace_id IN ({session_span_sql})"
        span_params = session_span_params
    elif aggregation_context == "user":
        user_trace_sql, user_trace_params, needs_eval = _user_trace_membership_sql(
            project_id=str(project_id),
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            candidate_trace_ids_param="candidate_trace_ids",
        )
        trace_predicate = f"trace_id IN ({user_trace_sql})"
        trace_params = user_trace_params
        # Span-attached annotations follow the owning selected user's trace.
        user_span_sql, user_span_params, span_needs_eval = _user_trace_membership_sql(
            project_id=str(project_id),
            filters=filters,
            start_date=start_date,
            end_date=end_date,
            candidate_trace_ids_sql=_span_batch_trace_ids_sql(),
        )
        if span_needs_eval != needs_eval:
            raise ExactGraphReadError("user annotation membership plan is inconsistent")
        span_predicate = f"trace_id IN ({user_span_sql})"
        span_params = user_span_params
    else:
        annotation_label_ids = _annotation_label_ids_for_filters(project_id, filters)
        trace_predicate, trace_params = _compile_membership_filter(
            project_id=project_id,
            filters=filters,
            observe_type="trace",
            annotation_label_ids=annotation_label_ids,
        )
        span_predicate, span_params = _compile_membership_filter(
            project_id=project_id,
            filters=filters,
            observe_type="span",
            annotation_label_ids=annotation_label_ids,
        )
    bucket_values: dict[datetime, list[float]] = defaultdict(list)
    query_count = 0
    rows_returned = 0

    # PostgreSQL is authoritative for Score. Hold one repeatable-read snapshot
    # while CH checks only those finite annotated identities. Any membership
    # batch failure aborts the refresh before publication.
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
        for partition_start, partition_end in output_bucket_partitions(
            start_date, end_date, interval
        ):
            queryset = (
                Score.no_workspace_objects.filter(
                    tracer_project_id=project_id,
                    label_id=label_id,
                    deleted=False,
                    created_at__gte=partition_start,
                    created_at__lt=partition_end,
                )
                .order_by("created_at", "id")
                .values("trace_id", "observation_span_id", "created_at", "value")
            )
            pending: list[dict[str, Any]] = []

            def reduce_batch(batch: list[dict[str, Any]]) -> None:
                nonlocal query_count, rows_returned
                if not batch:
                    return
                trace_ids = tuple(
                    dict.fromkeys(
                        str(row["trace_id"]) for row in batch if row.get("trace_id")
                    )
                )
                span_ids = tuple(
                    dict.fromkeys(
                        str(row["observation_span_id"])
                        for row in batch
                        if row.get("observation_span_id")
                    )
                )
                matched_traces = (
                    _matching_trace_ids(
                        analytics=analytics,
                        project_id=project_id,
                        trace_ids=trace_ids,
                        start_date=start_date,
                        end_date=end_date,
                        predicate=trace_predicate,
                        predicate_params=trace_params,
                        settings=settings,
                    )
                    if observe_type == "trace"
                    else set()
                )
                # Aggregate contexts use a span-batch candidate scope while
                # preserving whole-session/whole-user trace semantics. Plain
                # trace graphs retain their historical trace predicate.
                use_span_scope = aggregation_context in {"session", "user"}
                span_membership_predicate = (
                    span_predicate
                    if use_span_scope or observe_type == "span"
                    else trace_predicate
                )
                span_membership_params = (
                    span_params
                    if use_span_scope or observe_type == "span"
                    else trace_params
                )
                matched_spans = (
                    _matching_span_ids(
                        analytics=analytics,
                        project_id=project_id,
                        span_ids=span_ids,
                        start_date=start_date,
                        end_date=end_date,
                        predicate=span_membership_predicate,
                        predicate_params=span_membership_params,
                        settings=settings,
                    )
                    if span_ids
                    else set()
                )
                query_count += int(bool(trace_ids and observe_type == "trace"))
                query_count += int(bool(span_ids))
                rows_returned += len(batch)
                for row in batch:
                    trace_id = str(row.get("trace_id") or "")
                    span_id = str(row.get("observation_span_id") or "")
                    if observe_type == "span":
                        included = bool(span_id and span_id in matched_spans)
                    else:
                        included = bool(
                            (trace_id and trace_id in matched_traces)
                            or (span_id and span_id in matched_spans)
                        )
                    if not included:
                        continue
                    value = _annotation_value(row.get("value"), output_type, selected)
                    created_at = row.get("created_at")
                    if value is None or not isinstance(created_at, datetime):
                        continue
                    bucket = BaseQueryBuilder._normalize_timestamp(created_at, interval)
                    bucket_values[bucket].append(value)

            for row in queryset.iterator(chunk_size=EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE):
                pending.append(row)
                if len(pending) >= EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE:
                    reduce_batch(pending)
                    pending = []
            reduce_batch(pending)

    points = []
    for timestamp in BaseQueryBuilder._generate_timestamp_range(
        start_date, end_date, interval
    ):
        values = bucket_values.get(timestamp, [])
        aggregate = (
            sum(values) if output_type == "text" else sum(values) / max(len(values), 1)
        )
        points.append(
            {
                "timestamp": timestamp.isoformat(),
                "value": round(aggregate, 9),
                "primary_traffic": len(values),
            }
        )
    return {
        "metric_name": label_id,
        "name": label.name,
        "data": points,
        **_metadata(
            started=started,
            query_count=query_count,
            rows_returned=rows_returned,
        ),
    }


_SESSION_POST_AGGREGATE_FILTERS = {
    "duration",
    "total_cost",
    "total_tokens",
    "traces_count",
    "total_traces_count",
}

_SESSION_MESSAGE_FILTER_COLUMNS = {
    "first_message": "first_message",
    "last_message": "last_message",
}

_SESSION_AGGREGATE_FILTER_COLUMNS = {
    "duration": "session_duration",
    "total_cost": "session_total_cost",
    "total_tokens": "session_total_tokens",
    "traces_count": "session_traces",
    "total_traces_count": "session_traces",
}


def _session_having_clause(
    filters: list[dict[str, Any]], params: dict[str, Any]
) -> str:
    """Compile the aggregate/message filters accepted by the session list API."""

    clauses: list[str] = []
    operators = {
        "equals": "=",
        "not_equals": "!=",
        "greater_than": ">",
        "less_than": "<",
        "greater_than_or_equal": ">=",
        "less_than_or_equal": "<=",
    }
    counter = 0
    for item in filters:
        column_id = item.get("column_id") or item.get("columnId")
        column_id = str(column_id or "")
        column = _SESSION_AGGREGATE_FILTER_COLUMNS.get(column_id)
        message_column = _SESSION_MESSAGE_FILTER_COLUMNS.get(column_id)
        if column is None and message_column is None:
            continue
        config = item.get("filter_config") or item.get("filterConfig") or {}
        filter_op = config.get("filter_op") or config.get("filterOp")
        filter_value = config.get("filter_value", config.get("filterValue"))

        # Match SessionListQueryBuilderV2 exactly: first/last message are
        # argMin/argMax values of the session's root spans, so their predicates
        # belong in HAVING after the per-session GROUP BY. Treating these as raw
        # span attributes silently changes membership and usually returns an
        # empty graph.
        if message_column is not None:
            if filter_op in ("is_null", "is_not_null"):
                clauses.append(
                    f"({message_column} IS NULL OR {message_column} = '')"
                    if filter_op == "is_null"
                    else (f"({message_column} IS NOT NULL AND {message_column} != '')")
                )
                continue
            text_operator = {
                "equals": "=",
                "not_equals": "!=",
                "contains": "ILIKE",
                "not_contains": "NOT ILIKE",
                "starts_with": "ILIKE",
                "ends_with": "ILIKE",
            }.get(str(filter_op or ""))
            if text_operator is None:
                clauses.append("0 = 1")
                continue
            counter += 1
            param_name = f"session_having_{counter}"
            if filter_op in ("contains", "not_contains"):
                filter_value = f"%{filter_value}%"
            elif filter_op == "starts_with":
                filter_value = f"{filter_value}%"
            elif filter_op == "ends_with":
                filter_value = f"%{filter_value}"
            params[param_name] = filter_value
            clauses.append(f"{message_column} {text_operator} %({param_name})s")
            continue

        operator = operators.get(str(filter_op or ""))
        if operator is None:
            # Match SessionTimeSeriesQueryBuilder: a syntactically valid but
            # unsupported aggregate operation must fail closed, not broaden.
            clauses.append("0 = 1")
            continue
        counter += 1
        param_name = f"session_having_{counter}"
        params[param_name] = filter_value
        clauses.append(f"{column} {operator} %({param_name})s")
    return " AND ".join(clauses)


def _session_aggregate_source_sql(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    include_trace_ids: bool,
    anchor_by_session_start: bool = False,
    candidate_trace_ids_sql: str | None = None,
    candidate_trace_ids_param: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build one full-window, remap-resolved per-session source.

    System, eval, and annotation session graphs must agree on membership. Raw
    trace/span predicates are applied before grouping; session identity,
    numeric aggregates, and first/last messages are applied only after the
    canonical session has been assembled. The fixed ``snapshot_*`` parameters
    are deliberately distinct from an outer graph partition's dates so a
    session can never be split at an output-bucket boundary.
    """

    span_filters = [
        item
        for item in filters
        if (item.get("column_id") or item.get("columnId"))
        not in {
            *_SESSION_POST_AGGREGATE_FILTERS,
            *_SESSION_MESSAGE_FILTER_COLUMNS,
            *SESSION_ID_FILTER_COLS,
        }
    ]
    extra_where, extra_params = compile_exact_graph_filter_predicates(
        span_filters,
        project_id=project_id,
        observe_type="trace",
        annotation_label_ids=_annotation_label_ids_for_filters(
            project_id,
            span_filters,
        ),
    )
    filter_clause = f"AND {extra_where}" if extra_where else ""
    session_survivor_map = survivor_map_subquery("trace_session_id_remap")
    resolved_session_id = (
        "if(ts_remap.survivor_id IS NULL OR "
        "ts_remap.survivor_id = "
        "toUUID('00000000-0000-0000-0000-000000000000'), "
        "rs.trace_session_id, ts_remap.survivor_id)"
    )
    params = {
        **extra_params,
        "project_id": project_id,
        "snapshot_start_date": start_date,
        "snapshot_end_date": end_date,
    }
    session_id_clause = build_session_id_filter_clause(
        filters,
        params,
        session_col=resolved_session_id,
        param_prefix="exact_session_id_",
    )
    if candidate_trace_ids_sql and candidate_trace_ids_param:
        raise ValueError("only one candidate trace scope may be supplied")
    candidate_trace_clause = ""
    if candidate_trace_ids_sql:
        candidate_trace_clause = (
            f"AND toString(candidate_rs.trace_id) IN ({candidate_trace_ids_sql})"
        )
    elif candidate_trace_ids_param:
        candidate_trace_clause = (
            f"AND toString(candidate_rs.trace_id) IN %({candidate_trace_ids_param})s"
        )
    elif anchor_by_session_start:
        candidate_trace_clause = (
            "AND candidate_rs.start_time >= %(start_date)s "
            "AND candidate_rs.start_time < %(end_date)s"
        )
    else:
        raise ValueError("session source requires an entity-safe candidate scope")
    # For an outer eval/annotation candidate, entity membership may be caused
    # by a different trace in the same session.  Candidate discovery therefore
    # identifies only the session; the raw predicate is evaluated while the
    # complete frozen session is hydrated below.  The SYSTEM path anchors on
    # the earliest *filtered* root span, so it intentionally applies the raw
    # predicate during candidate discovery as well.
    candidate_filter_clause = filter_clause if anchor_by_session_start else ""

    source_where_clauses = [
        f"{resolved_session_id} IN (SELECT session_id FROM candidate_sessions)"
    ]
    if session_id_clause:
        source_where_clauses.append(session_id_clause)
    session_id_fragment = "WHERE " + " AND ".join(source_where_clauses)
    having_clause = _session_having_clause(filters, params)
    having_clauses: list[str] = []
    if anchor_by_session_start:
        having_clauses.append(
            "session_start >= %(start_date)s AND session_start < %(end_date)s"
        )
    if having_clause:
        having_clauses.append(having_clause)
    having_fragment = "HAVING " + " AND ".join(having_clauses) if having_clauses else ""
    needs_message_aggregates = any(
        (item.get("column_id") or item.get("columnId"))
        in _SESSION_MESSAGE_FILTER_COLUMNS
        for item in filters
    )
    message_aggregate_select = (
        ",\n        argMin(rs.input, rs.start_time) AS first_message,"
        "\n        argMax(rs.input, rs.start_time) AS last_message"
        if needs_message_aggregates
        else ""
    )
    trace_ids_select = (
        ",\n        groupUniqArray(toString(rs.trace_id)) AS session_trace_ids"
        if include_trace_ids
        else ""
    )
    source = f"""
    WITH candidate_sessions AS (
        SELECT DISTINCT
            if(candidate_remap.survivor_id IS NULL OR
               candidate_remap.survivor_id =
                   toUUID('00000000-0000-0000-0000-000000000000'),
               candidate_rs.trace_session_id,
               candidate_remap.survivor_id) AS session_id
        FROM spans AS candidate_rs FINAL
        LEFT JOIN ({session_survivor_map}) AS candidate_remap
          ON candidate_rs.trace_session_id = candidate_remap.any_id
        PREWHERE candidate_rs.project_id = toUUID(%(project_id)s)
          AND candidate_rs.start_time >= %(snapshot_start_date)s
          AND candidate_rs.start_time < %(snapshot_end_date)s
        WHERE candidate_rs.is_deleted = 0
          AND (candidate_rs.parent_span_id IS NULL OR
               candidate_rs.parent_span_id = '')
          AND candidate_rs.trace_session_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
          {candidate_trace_clause}
          {candidate_filter_clause.replace("rs.", "candidate_rs.")}
    )
    SELECT
        {resolved_session_id} AS session_id,
        min(rs.start_time) AS session_start,
        max(if(rs.end_time < rs.start_time, rs.start_time, rs.end_time))
            AS session_end,
        avg(rs.latency_ms) AS session_avg_latency,
        sum(rs.total_tokens) AS session_total_tokens,
        sum(rs.prompt_tokens) AS session_prompt_tokens,
        sum(rs.completion_tokens) AS session_completion_tokens,
        sum(rs.cost) AS session_total_cost,
        uniqExact(rs.trace_id) AS session_traces,
        max(toUInt8(upper(rs.status) IN ('ERROR', 'ERRORED', 'FAILED')))
            AS session_has_error,
        dateDiff('second', session_start, session_end) AS session_duration
        {message_aggregate_select}
        {trace_ids_select}
    FROM (
        SELECT *
        FROM spans FINAL
        PREWHERE project_id = toUUID(%(project_id)s)
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
        WHERE is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND trace_session_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
          {filter_clause}
    ) AS rs
    LEFT JOIN ({session_survivor_map}) AS ts_remap
      ON rs.trace_session_id = ts_remap.any_id
    {session_id_fragment}
    GROUP BY session_id
    {having_fragment}
    """
    return source, params


def _session_trace_membership_sql(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    candidate_trace_ids_sql: str | None = None,
    candidate_trace_ids_param: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return partition candidates whose complete session is selected.

    A filter may match a sibling trace in the same session.  Therefore the
    selector first evaluates the complete candidate session, then returns the
    original candidate traces belonging to selected sessions.  Returning the
    filtered aggregate's trace array would incorrectly drop such siblings.
    """

    source, params = _session_aggregate_source_sql(
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        include_trace_ids=False,
        candidate_trace_ids_sql=candidate_trace_ids_sql,
        candidate_trace_ids_param=candidate_trace_ids_param,
    )
    if candidate_trace_ids_sql:
        candidate_clause = (
            f"toString(candidate_member.trace_id) IN ({candidate_trace_ids_sql})"
        )
    elif candidate_trace_ids_param:
        candidate_clause = (
            f"toString(candidate_member.trace_id) IN %({candidate_trace_ids_param})s"
        )
    else:  # Guarded by _session_aggregate_source_sql, kept fail closed here too.
        raise ValueError("session membership requires a candidate trace scope")
    session_survivor_map = survivor_map_subquery("trace_session_id_remap")
    resolved_session_id = resolved_id_expr(
        "candidate_member.trace_session_id",
        "candidate_member_remap",
    )
    return (
        f"""
        SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id
        FROM spans AS candidate_member FINAL
        LEFT JOIN ({session_survivor_map}) AS candidate_member_remap
          ON candidate_member.trace_session_id = candidate_member_remap.any_id
        PREWHERE candidate_member.project_id = toUUID(%(project_id)s)
          AND candidate_member.start_time >= %(snapshot_start_date)s
          AND candidate_member.start_time < %(snapshot_end_date)s
        WHERE candidate_member.is_deleted = 0
          AND {candidate_clause}
          AND {resolved_session_id} IN (
              SELECT session_id
              FROM ({source}) AS selected_sessions
          )
        """,
        params,
    )


_USER_OUTPUT_FILTER_MAP = {
    **UserListQueryBuilder.OUTPUT_FILTER_MAP,
    # The Users UI exposes this historical name while the list response and
    # ClickHouse reducer call the metric bool_eval_pass_rate.
    "eval_score": "bool_eval_pass_rate",
}
_USER_EVAL_FILTER_COLUMNS = frozenset(
    {"eval_score", "bool_eval_pass_rate", "avg_output_float"}
)


def _is_user_date_filter(item: dict[str, Any]) -> bool:
    config = item.get("filter_config") or item.get("filterConfig") or {}
    return (item.get("column_id") or item.get("columnId")) in {
        "created_at",
        "start_time",
    } and (config.get("filter_type") or config.get("filterType")) in {
        "datetime",
        "date",
    }


def _user_filter_clauses(
    filters: list[dict[str, Any]],
    *,
    project_id: str,
) -> tuple[str, str, dict[str, Any], bool]:
    """Compile raw-span and post-user-aggregate predicates exactly once.

    The Users table exposes entity-level metrics. Those predicates are applied
    only after the complete full-window user has been assembled. Only fields
    outside the list-view output vocabulary are allowed to constrain physical
    span rows. Structured array/map attributes use the same type-aware compiler
    as the exact list candidate path. Any unsupported shape fails closed.
    """

    output_clauses: list[str] = []
    params: dict[str, Any] = {}
    ordinary_span_filters: list[dict[str, Any]] = []
    structured_span_filters: list[tuple[int, dict[str, Any]]] = []
    needs_eval = False

    for index, item in enumerate(filters):
        if _is_user_date_filter(item):
            continue
        column_id = item.get("column_id") or item.get("columnId")
        config = item.get("filter_config") or item.get("filterConfig") or {}
        if column_id in _USER_OUTPUT_FILTER_MAP:
            output_column = _USER_OUTPUT_FILTER_MAP[column_id]
            clause, clause_params = UserListQueryBuilder._condition(
                column=output_column,
                op=config.get("filter_op") or config.get("filterOp"),
                value=config.get("filter_value", config.get("filterValue")),
                prefix=f"user_filter_{index}",
            )
            # The serializer should reject unsupported operations first, but
            # this selector is also callable outside HTTP. Never broaden an
            # invalid filter into an unfiltered graph.
            output_clauses.append(clause or "0 = 1")
            params.update(clause_params)
            needs_eval = needs_eval or column_id in _USER_EVAL_FILTER_COLUMNS
            continue

        filter_type = str(
            config.get("filter_type") or config.get("filterType") or ""
        ).lower()
        col_type = config.get("col_type") or config.get("colType")
        if col_type == ClickHouseFilterBuilderV2.SPAN_ATTRIBUTE and filter_type in {
            "array",
            "map",
            "json",
        }:
            structured_span_filters.append((index, item))
        else:
            ordinary_span_filters.append(item)

    span_clauses: list[str] = []
    if ordinary_span_filters:
        annotation_label_ids = _annotation_label_ids_for_filters(
            project_id,
            ordinary_span_filters,
        )
        filter_builder = ClickHouseFilterBuilderV2(
            table="spans",
            project_id=project_id,
            query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN,
            span_date_scope=True,
            annotation_label_ids=list(annotation_label_ids or ()),
            annotation_label_set_known=annotation_label_ids is not None,
        )
        ordinary_clause, ordinary_params = filter_builder.translate(
            ordinary_span_filters
        )
        span_clauses.append(ordinary_clause or "0 = 1")
        params.update(ordinary_params)
    for index, item in structured_span_filters:
        try:
            clause, clause_params = compile_span_attribute_row_predicate(
                item, index=index
            )
        except (TypeError, ValueError):
            span_clauses.append("0 = 1")
            continue
        span_clauses.append(rewrite_v1_sql_to_v2(clause) or "0 = 1")
        params.update(clause_params)

    return (
        " AND ".join(span_clauses) or "1 = 1",
        " AND ".join(output_clauses) or "1 = 1",
        params,
        needs_eval,
    )


def _user_aggregate_source_sql(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    include_trace_ids: bool,
    candidate_trace_ids_sql: str | None = None,
    candidate_trace_ids_param: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    """Build the shared full-window, remap-resolved user selector.

    SYSTEM, EVAL, and ANNOTATION graphs all consume this exact same selector so
    a user cannot belong to one graph but not another. Latest-state collapse,
    both ID remaps, curated user liveness, and entity aggregate filters are
    evaluated under one frozen request window.
    """

    span_predicate, user_predicate, filter_params, needs_eval = _user_filter_clauses(
        filters, project_id=project_id
    )
    eu_survivor_map = survivor_map_subquery("end_user_id_remap")
    ts_survivor_map = survivor_map_subquery("trace_session_id_remap")
    resolved_eu = resolved_id_expr("rs.end_user_id", "span_eu_remap")
    resolved_session = resolved_id_expr("rs.trace_session_id", "span_ts_remap")
    resolved_dimension_eu = resolved_id_expr("eu.end_user_id", "eu_remap")
    params: dict[str, Any] = {
        **filter_params,
        "project_id": project_id,
        "snapshot_start_date": start_date,
        "snapshot_end_date": end_date,
    }
    if candidate_trace_ids_sql and candidate_trace_ids_param:
        raise ValueError("only one candidate trace scope may be supplied")
    if candidate_trace_ids_sql:
        candidate_trace_clause = (
            f"toString(candidate_rs.trace_id) IN ({candidate_trace_ids_sql})"
        )
    elif candidate_trace_ids_param:
        candidate_trace_clause = (
            f"toString(candidate_rs.trace_id) IN %({candidate_trace_ids_param})s"
        )
    else:
        raise ValueError("user source requires an entity-safe candidate scope")
    trace_ids_select = (
        ",\n            groupUniqArray(trace_id) AS user_trace_ids"
        if include_trace_ids
        else ""
    )

    eval_cte = ""
    eval_join = ""
    eval_columns = (
        "coalesce(ue.bool_eval_pass_rate, 0) AS bool_eval_pass_rate,\n"
        "            coalesce(ue.avg_output_float, 0) AS avg_output_float"
    )
    if needs_eval:
        eval_table, eval_live = eval_logger_source("eval_scan")
        eval_cte = f""",
        user_eval_metrics AS (
            SELECT
                ut.end_user_id AS end_user_id,
                round(
                    100.0 * countIf(eval_scan.output_bool = 1)
                    / nullIf(countIf(isNotNull(eval_scan.output_bool)), 0),
                    2
                ) AS bool_eval_pass_rate,
                round(avg(eval_scan.output_float), 2) AS avg_output_float
            FROM {eval_table} AS eval_scan FINAL
            INNER JOIN (
                SELECT
                    end_user_id,
                    arrayJoin(user_trace_ids) AS trace_id
                FROM user_span_metrics
            ) AS ut
              ON toString(eval_scan.trace_id) = ut.trace_id
            WHERE {eval_live}
            GROUP BY ut.end_user_id
        )"""
        eval_join = (
            "LEFT JOIN user_eval_metrics AS ue ON ue.end_user_id = usm.end_user_id"
        )
    else:
        eval_columns = (
            "toFloat64(0) AS bool_eval_pass_rate,\n"
            "            toFloat64(0) AS avg_output_float"
        )

    # user_trace_ids is required internally when an eval field determines
    # membership, even if the caller itself only needs canonical user IDs.
    internal_trace_ids_select = (
        ",\n            groupUniqArray(trace_id) AS user_trace_ids"
        if needs_eval and not include_trace_ids
        else trace_ids_select
    )
    final_trace_ids_select = (
        ",\n            usm.user_trace_ids" if include_trace_ids else ""
    )

    source = f"""
    WITH
    eu_survivor_map AS ({eu_survivor_map}),
    ts_survivor_map AS ({ts_survivor_map}),
    candidate_users AS (
        SELECT DISTINCT
            {resolved_id_expr("candidate_rs.end_user_id", "candidate_eu_remap")}
                AS end_user_id
        FROM spans AS candidate_rs FINAL
        LEFT JOIN eu_survivor_map AS candidate_eu_remap
          ON candidate_rs.end_user_id = candidate_eu_remap.any_id
        PREWHERE candidate_rs.project_id = toUUID(%(project_id)s)
          AND candidate_rs.start_time >= %(snapshot_start_date)s
          AND candidate_rs.start_time < %(snapshot_end_date)s
        WHERE candidate_rs.is_deleted = 0
          AND isNotNull(candidate_rs.end_user_id)
          AND {candidate_trace_clause}
    ),
    candidate_physical_users AS (
        SELECT end_user_id FROM candidate_users
        UNION DISTINCT
        SELECT any_id AS end_user_id
        FROM eu_survivor_map
        WHERE survivor_id IN (SELECT end_user_id FROM candidate_users)
    ),
    resolved_spans AS (
        SELECT
            {resolved_eu} AS end_user_id,
            {resolved_session} AS trace_session_id,
            toString(rs.trace_id) AS trace_id,
            rs.start_time AS start_time,
            rs.end_time AS end_time,
            rs.cost AS cost,
            rs.total_tokens AS total_tokens,
            rs.prompt_tokens AS prompt_tokens,
            rs.completion_tokens AS completion_tokens,
            rs.latency_ms AS latency_ms,
            rs.observation_type AS observation_type,
            rs.status AS status
        FROM (
            SELECT *
            FROM spans FINAL
            PREWHERE project_id = toUUID(%(project_id)s)
              AND start_time >= %(snapshot_start_date)s
              AND start_time < %(snapshot_end_date)s
            WHERE is_deleted = 0
              AND isNotNull(end_user_id)
              AND end_user_id IN (SELECT end_user_id FROM candidate_physical_users)
              AND {span_predicate}
        ) AS rs
        LEFT JOIN eu_survivor_map AS span_eu_remap
          ON rs.end_user_id = span_eu_remap.any_id
        LEFT JOIN ts_survivor_map AS span_ts_remap
          ON rs.trace_session_id = span_ts_remap.any_id
    ),
    user_dimensions_raw AS (
        SELECT
            {resolved_dimension_eu} AS end_user_id,
            eu.end_user_id AS physical_end_user_id,
            eu.user_id AS user_id,
            eu.user_id_type AS user_id_type,
            eu.user_id_hash AS user_id_hash,
            eu.first_seen AS first_seen,
            eu.project_id AS project_id,
            eu.version AS version
        FROM end_users AS eu FINAL
        LEFT JOIN eu_survivor_map AS eu_remap
          ON eu.end_user_id = eu_remap.any_id
        WHERE eu.project_id = toUUID(%(project_id)s)
          AND eu.is_deleted = 0
          AND notEmpty(eu.user_id)
          AND {resolved_dimension_eu} IN (SELECT end_user_id FROM candidate_users)
    ),
    user_dimensions AS (
        SELECT
            end_user_id,
            argMax(
                user_id,
                tuple(physical_end_user_id = end_user_id, version)
            ) AS user_id,
            argMax(
                user_id_type,
                tuple(physical_end_user_id = end_user_id, version)
            ) AS user_id_type,
            argMax(
                user_id_hash,
                tuple(physical_end_user_id = end_user_id, version)
            ) AS user_id_hash,
            min(first_seen) AS activated_at,
            argMax(
                project_id,
                tuple(physical_end_user_id = end_user_id, version)
            ) AS project_id
        FROM user_dimensions_raw
        GROUP BY end_user_id
    ),
    user_span_metrics AS (
        SELECT
            end_user_id,
            sum(ifNull(cost, 0)) AS total_cost,
            sum(toInt64(ifNull(total_tokens, 0))) AS total_tokens,
            sum(toInt64(ifNull(prompt_tokens, 0))) AS input_tokens,
            sum(toInt64(ifNull(completion_tokens, 0))) AS output_tokens,
            uniqExact(trace_id) AS num_traces,
            uniqExactIf(
                trace_session_id,
                isNotNull(trace_session_id)
                AND trace_session_id !=
                    toUUID('00000000-0000-0000-0000-000000000000')
            ) AS num_sessions,
            coalesce(round(avgIf(latency_ms, isNotNull(latency_ms)), 2), 0)
                AS avg_trace_latency,
            countIf(observation_type = 'llm') AS num_llm_calls,
            uniqExactIf(trace_id, observation_type = 'guardrail')
                AS num_guardrails_triggered,
            uniqExact(toDate(start_time)) AS num_active_days,
            uniqExactIf(
                trace_id,
                upper(status) IN ('ERROR', 'ERRORED', 'FAILED')
            ) AS num_traces_with_errors,
            max(end_time) AS last_active
            {internal_trace_ids_select}
        FROM resolved_spans
        GROUP BY end_user_id
    )
    {eval_cte},
    user_rows AS (
        SELECT
            ud.user_id AS user_id,
            usm.total_cost AS total_cost,
            usm.total_tokens AS total_tokens,
            usm.input_tokens AS input_tokens,
            usm.output_tokens AS output_tokens,
            usm.num_traces AS num_traces,
            usm.num_sessions AS num_sessions,
            usm.avg_trace_latency AS avg_trace_latency,
            usm.num_llm_calls AS num_llm_calls,
            usm.num_guardrails_triggered AS num_guardrails_triggered,
            usm.num_active_days AS num_active_days,
            usm.num_traces_with_errors AS num_traces_with_errors,
            ud.activated_at AS activated_at,
            usm.last_active AS last_active,
            ud.project_id AS project_id,
            ud.user_id_type AS user_id_type,
            ud.user_id_hash AS user_id_hash,
            usm.end_user_id AS end_user_id,
            {eval_columns}
            {final_trace_ids_select}
        FROM user_span_metrics AS usm
        INNER JOIN user_dimensions AS ud
          ON ud.end_user_id = usm.end_user_id
        {eval_join}
    )
    SELECT *
    FROM user_rows
    WHERE {user_predicate}
    """
    return source, params, needs_eval


def _user_id_membership_sql(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    candidate_trace_ids_sql: str | None = None,
    candidate_trace_ids_param: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    source, params, needs_eval = _user_aggregate_source_sql(
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        include_trace_ids=False,
        candidate_trace_ids_sql=candidate_trace_ids_sql,
        candidate_trace_ids_param=candidate_trace_ids_param,
    )
    return (
        f"SELECT end_user_id FROM ({source}) AS selected_users",
        params,
        needs_eval,
    )


def _user_trace_membership_sql(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
    candidate_trace_ids_sql: str | None = None,
    candidate_trace_ids_param: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    """Return partition candidates whose complete user is selected."""

    source, params, needs_eval = _user_aggregate_source_sql(
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        include_trace_ids=False,
        candidate_trace_ids_sql=candidate_trace_ids_sql,
        candidate_trace_ids_param=candidate_trace_ids_param,
    )
    if candidate_trace_ids_sql:
        candidate_clause = (
            f"toString(candidate_member.trace_id) IN ({candidate_trace_ids_sql})"
        )
    elif candidate_trace_ids_param:
        candidate_clause = (
            f"toString(candidate_member.trace_id) IN %({candidate_trace_ids_param})s"
        )
    else:  # Guarded by _user_aggregate_source_sql, kept fail closed here too.
        raise ValueError("user membership requires a candidate trace scope")
    eu_survivor_map = survivor_map_subquery("end_user_id_remap")
    resolved_user_id = resolved_id_expr(
        "candidate_member.end_user_id",
        "candidate_member_remap",
    )
    return (
        f"""
        SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id
        FROM spans AS candidate_member FINAL
        LEFT JOIN ({eu_survivor_map}) AS candidate_member_remap
          ON candidate_member.end_user_id = candidate_member_remap.any_id
        PREWHERE candidate_member.project_id = toUUID(%(project_id)s)
          AND candidate_member.start_time >= %(snapshot_start_date)s
          AND candidate_member.start_time < %(snapshot_end_date)s
        WHERE candidate_member.is_deleted = 0
          AND isNotNull(candidate_member.end_user_id)
          AND {candidate_clause}
          AND {resolved_user_id} IN (
              SELECT end_user_id
              FROM ({source}) AS selected_users
          )
        """,
        params,
        needs_eval,
    )


def read_exact_user_system_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
) -> dict[str, Any]:
    """Aggregate the complete latest-live span population at user grain."""

    started = monotonic()
    start_date, end_date, empty = _snapshot_window(filters)
    if empty:
        builder = UserTimeSeriesQueryBuilderV2(
            project_id=str(project_id),
            filters=filters,
            interval=interval,
        )
        builder.start_date = start_date
        builder.end_date = end_date
        formatted = builder.format_result([], [])
        metric_key = metric_id if metric_id in formatted else "active_users"
        return {
            "metric_name": metric_id,
            "data": formatted.get(metric_key, []),
            **_metadata(
                started=started,
                query_count=0,
                rows_returned=0,
            ),
        }

    user_membership_sql, user_membership_params, _needs_eval = _user_id_membership_sql(
        project_id=str(project_id),
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        # UserTimeSeriesQueryBuilderV2 defines this request-window CTE. The
        # membership selector hydrates users owning one of those candidates.
        candidate_trace_ids_sql=("SELECT toString(trace_id) FROM candidate_trace_ids"),
    )
    builder = UserTimeSeriesQueryBuilderV2(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        user_membership_sql=user_membership_sql,
        user_membership_params=user_membership_params,
        exact_snapshot_start=start_date,
        exact_snapshot_end=end_date,
    )
    query, params = builder.build()
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    formatted = builder.format_result(rows, columns)
    metric_key = metric_id if metric_id in formatted else "active_users"
    traffic = {
        point.get("timestamp"): point.get("traffic", 0)
        for point in formatted.get("traffic", [])
    }
    return {
        "metric_name": metric_id,
        "data": [
            {
                "timestamp": point.get("timestamp"),
                "value": point.get("value", 0),
                "primary_traffic": traffic.get(point.get("timestamp"), 0),
            }
            for point in formatted.get(metric_key, [])
        ],
        **_metadata(
            started=started,
            query_count=1,
            rows_returned=len(rows),
        ),
    }


def read_exact_session_system_graph(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
) -> dict[str, Any]:
    started = monotonic()
    start_date, end_date, empty = _snapshot_window(filters)
    if empty:
        return {
            "metric_name": metric_id,
            "data": [],
            **_metadata(
                started=started,
                query_count=0,
                rows_returned=0,
            ),
        }
    bucket_fn = BaseQueryBuilder.time_bucket_expr(interval)
    session_value = {
        "latency": "avg(session_avg_latency)",
        "tokens": "sum(session_total_tokens)",
        "total_tokens": "sum(session_total_tokens)",
        "prompt_tokens": "sum(session_prompt_tokens)",
        "input_tokens": "sum(session_prompt_tokens)",
        "completion_tokens": "sum(session_completion_tokens)",
        "output_tokens": "sum(session_completion_tokens)",
        "cost": "avg(session_total_cost)",
        "total_cost": "sum(session_total_cost)",
        "traffic": "count()",
        "session_count": "count()",
        "error_rate": "avg(session_has_error) * 100.0",
        "avg_duration": "avg(session_duration)",
        "avg_traces_per_session": "avg(session_traces)",
    }.get(metric_id)
    if session_value is None:
        raise ValueError("Unsupported session system metric")
    session_source, query_params = _session_aggregate_source_sql(
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
        include_trace_ids=False,
        anchor_by_session_start=True,
    )
    query_params = {
        **query_params,
        "start_date": start_date,
        "end_date": end_date,
    }
    query = f"""
    SELECT
        {bucket_fn}(session_start) AS time_bucket,
        {session_value} AS value,
        count() AS primary_traffic
    FROM ({session_source}) AS exact_sessions
    WHERE session_start >= %(start_date)s
      AND session_start < %(end_date)s
    GROUP BY time_bucket
    ORDER BY time_bucket
    """
    result = analytics.execute_ch_query(
        query,
        query_params,
        timeout_ms=EXACT_GRAPH_QUERY_TIMEOUT_MS,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    values: dict[str, tuple[float, int]] = {}
    for row in rows:
        timestamp = _row_value(row, columns, "time_bucket", None)
        if timestamp is None:
            continue
        key = (
            timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        )
        values[key] = (
            float(_row_value(row, columns, "value", 0) or 0),
            int(_row_value(row, columns, "primary_traffic", 0) or 0),
        )
    points = []
    for timestamp in BaseQueryBuilder._generate_timestamp_range(
        start_date, end_date, interval
    ):
        value, traffic = values.get(timestamp.isoformat(), (0.0, 0))
        points.append(
            {
                "timestamp": timestamp.isoformat(),
                "value": round(value, 9),
                "primary_traffic": traffic,
            }
        )
    return {
        "metric_name": metric_id,
        "data": points,
        **_metadata(
            started=started,
            query_count=1,
            rows_returned=len(rows),
        ),
    }


__all__ = [
    "EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION",
    "ExactGraphReadError",
    "output_bucket_partitions",
    "read_exact_agent_graph",
    "read_exact_all_system_metrics",
    "read_exact_annotation_graph",
    "read_exact_eval_graph",
    "read_exact_session_system_graph",
    "read_exact_system_graph",
    "read_exact_user_system_graph",
]
