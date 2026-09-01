"""Exact current-state aggregation reads for public Observe graphs.

ClickHouse 25.3 cannot share a snapshot across separately executed statements,
and a version predicate on ``ReplacingMergeTree`` is not time travel after a
background merge. Most aggregate readers therefore use one full-window
statement. Filtered trace graphs use finite identity batches; filtered span
graphs scan adjacent whole-hour ranges because ``toStartOfHour(start_time)`` is
part of the table's replacement identity. A late arrival or background merge
between statements can affect a refresh, so neither sequence is advertised as
MVCC. Both freeze the requested window and any PostgreSQL-resolved membership
once, prevent duplicate contribution, and fail before publication if any
required read fails. Only a complete result may replace the prior snapshot in
``exact_aggregation_cache``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import Any

import structlog
from django.conf import settings
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
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import (
    build_numeric_filter_predicate,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
    compile_span_attribute_row_predicate,
    partition_span_filter_plans,
)
from tracer.services.clickhouse.query_builders.session_filters import (
    SESSION_ID_FILTER_COLS,
    build_session_id_filter_clause,
)
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_size_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    resolved_id_expr,
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
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilderV2,
)
from tracer.utils.helper import get_annotation_labels_for_project

logger = structlog.get_logger(__name__)

# Exact graphs run only in a deduplicated background refresh and publish
# atomically; the latency-critical HTTP request returns a pending envelope and
# never waits for this work. Every database statement and the final publication
# fence consume one action-owned wall deadline, so no nested query receives a
# fresh grant. Use the reviewed background wall rather than the interactive
# request wall: production-size exact system graphs can legitimately need more
# than 9.5 seconds while still remaining bounded. Source-row volume is not
# capped; byte, memory, result, partition, and admission limits remain
# independent boundaries.
EXACT_GRAPH_WALL_DEADLINE_MS = settings.GRAPH_BACKGROUND_WALL_MS
# Keep one canonical alias for refresh-budget arithmetic and qualification
# overrides.
EXACT_GRAPH_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
# This partition size belongs to the PostgreSQL-backed annotation membership
# reader below. Most system graphs deliberately remain one ClickHouse statement
# so CH25.3 cannot stitch independently changing ReplacingMergeTree snapshots.
EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION = settings.EXACT_GRAPH_MAX_BUCKETS_PER_PARTITION
EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE = settings.EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE
# Filtered trace graphs cannot retain tenant-wide per-trace membership state in
# one ClickHouse query under the production memory envelope.  The background
# refresh exhausts a necessary raw filter-witness cursor, classifies every
# finite identity batch against latest state, then aggregates only proven trace
# identities.  Neither value is a result ceiling.
EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE = settings.EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE
# Candidate witnesses are transported in finite keyset pages. This setting is
# a per-statement page bound, not a total-row limit: a broad but selective
# multi-filter request continues until the witness is exhausted or the shared
# action deadline/resource envelope fails closed.
EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL = settings.EXACT_GRAPH_TRACE_CANDIDATE_MAX_ROWS + 1
# Broad positive scalar Map filters use an authoritative latest-state scan over
# disjoint whole-hour storage identities. A failing slice is split to
# whole-hour children without publishing its partial rows. Every attempt is
# additionally clamped to the action-owned wall below.
EXACT_GRAPH_TRACE_ANCHOR_PARTITION_WIDTH = timedelta(
    hours=settings.EXACT_GRAPH_TRACE_ANCHOR_PARTITION_HOURS
)
EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_WIDTH = timedelta(
    hours=settings.EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_HOURS
)
# Exact aggregation has one admitted background refresh at a time and the
# ClickHouse client owns a thread-safe connection pool.  Two independent
# one-thread partition reads halve the dense-retention wall clock without the
# four-to-ten-way I/O burst used by interactive fan-out endpoints.  Results are
# still withheld until every disjoint partition completes.
EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS = settings.EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS
EXACT_GRAPH_TRACE_ANCHOR_PAGE_SIZE = settings.EXACT_GRAPH_TRACE_ANCHOR_PAGE_SIZE
EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL = EXACT_GRAPH_TRACE_ANCHOR_PAGE_SIZE + 1
EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
EXACT_GRAPH_TRACE_ANCHOR_MAX_BYTES_TO_READ = settings.OBSERVABILITY_LIST_MAX_BYTES
# Scanning complete retained history is worthwhile only when the requested
# root window is both substantial in absolute terms and covers a meaningful
# fraction of project retention.  Shorter/narrower windows stay on the ordered
# root cursor, whose finite 5k-identity classifier is cheaper than replaying
# every retained hour.
EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH = timedelta(
    days=settings.EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_DAYS
)
EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION = (
    settings.EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION
)
# The bounded-bulk classifier enforces the same 5k-identity ceiling. Using
# that complete finite page avoids rescanning retained child history once per
# much smaller root page while adaptive bisection still handles a hot batch.
# Production A/B on Coletia measured the 5k classifier at 3.05--3.22 seconds
# versus 1.63--1.79 seconds for 1k; reducing roughly 70 repeated scans to 14 is
# the material wall-clock win. This changes query chunking only.
EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE = settings.EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE
# The compatibility root verifier builder has a separately proven 512-ID hard
# ceiling. The authoritative partition route normally avoids this replay, but
# alternate builders must still receive valid finite batches.
EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE = (
    settings.EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE
)
# Once exact trace membership is proven, contribution rows are additive across
# disjoint trace-ID batches.  A 5k initial batch materially reduces repeated
# range scans; resource-limited batches are bisected without publishing any
# partial aggregate.
EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE = (
    settings.EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE
)
# Production A/B on a fixed 5k Coletia population measured the one-statement
# contribution at 0.95 seconds and 1.79 GB. A hotter tenant-specific batch is
# bisected, and every retry receives only the action's remaining wall time.
EXACT_GRAPH_TRACE_CONTRIBUTION_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
EXACT_GRAPH_TRACE_CONTRIBUTION_MAX_BYTES_TO_READ = settings.OBSERVABILITY_LIST_MAX_BYTES
# Witness work runs only in the exact-snapshot background activity; public
# graph polls never wait on an individual statement. Witnesses, classifiers,
# and contributions consume the same action wall; adaptive bisection remains
# fail-closed for any batch that cannot complete inside the remaining budget.
EXACT_GRAPH_TRACE_WITNESS_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
EXACT_GRAPH_TRACE_CLASSIFIER_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
EXACT_GRAPH_TRACE_INITIAL_SLICE = timedelta(
    seconds=settings.EXACT_GRAPH_TRACE_INITIAL_SLICE_SECONDS
)
# A failed raw witness slice is retried at the same upper bound so no interval
# is skipped. The shared wall bounds retry fan-out while still allowing a hot
# five-minute partition to be divided without a schema or index dependency.
EXACT_GRAPH_TRACE_MIN_SLICE = timedelta(
    seconds=settings.EXACT_GRAPH_TRACE_MIN_SLICE_SECONDS
)
# Even proven-empty history must not collapse a long unindexed time predicate
# into an arbitrarily large statement. Two days preserves logarithmic widening
# for ordinary ranges and puts an explicit ceiling on each physical read.
EXACT_GRAPH_TRACE_MAX_SLICE = timedelta(
    seconds=settings.EXACT_GRAPH_TRACE_MAX_SLICE_SECONDS
)
# Widen only cheap, fully exhausted slices. QueryResult exposes the measured
# ClickHouse wall time; the monotonic fallback keeps alternate executors safe.
EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS = settings.EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS
# Span membership is row-local. Resolve latest state and emit additive bucket
# states over adjacent, half-open storage-identity windows. The spans table's
# replacement key contains ``toStartOfHour(start_time)`` (not exact start_time),
# and the stateless collector accepts a producer-corrected start timestamp on a
# newer version. Therefore an exact partition may never bisect an hour: every
# version that ClickHouse considers one RMT identity, including a winning
# tombstone, must reach the same argMax. Requested first/last partial-hour bounds
# are applied only after that collapse. The caller withholds all results until
# every required partition succeeds.
EXACT_GRAPH_SPAN_MIN_PARTITION_WIDTH = timedelta(hours=1)
EXACT_GRAPH_SPAN_PARTITION_WIDTH = timedelta(
    hours=settings.EXACT_GRAPH_SPAN_INITIAL_PARTITION_HOURS
)
EXACT_GRAPH_SPAN_MAX_PARTITION_WIDTH = timedelta(
    hours=settings.EXACT_GRAPH_SPAN_MAX_PARTITION_HOURS
)
# A result-row count cannot reveal how many physical rows ClickHouse scanned.
# Grow only when the executor's measured statement latency proves that the
# current slice was cheap. All growth/retry widths remain integer multiples of
# the one-hour storage-identity floor.
EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS = settings.EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS
EXACT_GRAPH_SPAN_PARTITION_QUERY_TIMEOUT_MS = EXACT_GRAPH_WALL_DEADLINE_MS
EXACT_GRAPH_MAX_RESULT_ROWS = settings.DASHBOARD_ROLLUP_MAX_POINTS
EXACT_GRAPH_RESULT_ROW_SENTINEL = EXACT_GRAPH_MAX_RESULT_ROWS + 1
EXACT_GRAPH_MAX_RESULT_BYTES = settings.DASHBOARD_ROLLUP_MAX_RESULT_BYTES
# Ordinary application reads share the same finite 36-GiB byte and memory
# envelope. Row volume remains uncapped; an exact background refresh that needs
# more than this envelope fails closed while the bounded interactive graph path
# can still publish an explicitly sampled result.
EXACT_GRAPH_MAX_BYTES_TO_READ = settings.OBSERVABILITY_LIST_MAX_BYTES
EXACT_GRAPH_READ_SETTINGS = {
    "max_threads": settings.FILTER_SELECTOR_MAX_THREADS,
    # Attribute maps are several KiB per row on the heaviest tenants.  Smaller
    # source blocks keep decompression below the fixed query-memory envelope
    # while the in-order latest-row reducer consumes them.
    "max_block_size": settings.EXACT_GRAPH_READ_BLOCK_SIZE,
    "preferred_block_size_bytes": settings.EXACT_GRAPH_READ_PREFERRED_BLOCK_BYTES,
    # Map columns can dominate a block even when the row-count limit is low.
    # This CH25 setting asks the reader to split once any single wide column
    # reaches the same byte envelope.
    "preferred_max_column_in_block_size_bytes": settings.EXACT_GRAPH_READ_PREFERRED_BLOCK_BYTES,
    # The direct-write table is ordered by the complete physical span
    # identity.  The exact builder resolves ReplacingMergeTree winners with an
    # argMax aggregation in that order, so ClickHouse can retire each logical
    # row instead of retaining every wide span in a hash table.
    "optimize_aggregation_in_order": 1,
    # Later trace/bucket reductions are not ordered by the physical primary
    # key.  Spill those compact scalar states before they threaten the worker
    # memory ceiling; no raw attribute Map/JSON value crosses the first stage.
    "max_bytes_before_external_group_by": settings.EXACT_GRAPH_READ_EXTERNAL_SPILL_BYTES,
    "max_bytes_before_external_sort": settings.EXACT_GRAPH_READ_EXTERNAL_SPILL_BYTES,
    # Exact reads collapse ReplacingMergeTree versions before applying mutable
    # value predicates.  Keep these defenses explicit for the related exact
    # readers that still use FINAL; the argMax spans source itself exposes only
    # immutable project/time predicates to PREWHERE.
    "optimize_move_to_prewhere_if_final": 0,
    "use_skip_indexes_if_final": 0,
    # Source-row volume is data, not an error condition, so there is no
    # max_rows_to_read setting. Production evidence includes 207,479,677
    # physical rows in one valid twelve-month window. Byte, time, memory,
    # thread, result, and refresh-admission limits remain independently
    # enforced. The byte ceiling is intentionally much larger than memory: it
    # limits total scan work without rejecting large, low-memory aggregations.
    "max_bytes_to_read": EXACT_GRAPH_MAX_BYTES_TO_READ,
    # The same observed seven-day read peaked at 1,055,221,165 bytes.  Preserve
    # measured headroom while spilling compact aggregation/sort state early;
    # the production exact-aggregation worker has a separate 32-GiB pod limit.
    "max_memory_usage": settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES,
    "read_overflow_mode": "throw",
    "max_result_rows": EXACT_GRAPH_RESULT_ROW_SENTINEL,
    "max_result_bytes": EXACT_GRAPH_MAX_RESULT_BYTES,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
# The all-time latest-state classifier is the one measured exception to the
# default single-thread policy. On a frozen Coletia population, four threads
# reduced an exact 1,012-ID classification from 12.15s to 3.34s, but the full
# 5,000-ID classifier still took 10.62s and therefore cannot satisfy the 9.5s
# action wall. Give this one serial, identity-bounded statement up to eight
# workers. Exact-refresh admission remains one activity, classifier batches
# remain serial, and the unchanged memory/read/result/deadline ceilings stay
# authoritative; this raises parallelism, never work or result cardinality.
EXACT_GRAPH_TRACE_CLASSIFIER_READ_SETTINGS = {
    **EXACT_GRAPH_READ_SETTINGS,
    "max_threads": settings.EXACT_GRAPH_TRACE_CLASSIFIER_MAX_THREADS,
}
EXACT_GRAPH_SPAN_PARTITION_READ_SETTINGS = {
    **EXACT_GRAPH_READ_SETTINGS,
    # Span partitions use the same large-tenant scan envelope; their time
    # partition, shared deadline, memory and result ceilings remain the tighter
    # controls.
    "max_bytes_to_read": EXACT_GRAPH_MAX_BYTES_TO_READ,
}
EXACT_GRAPH_TRACE_ANCHOR_READ_SETTINGS = {
    **EXACT_GRAPH_READ_SETTINGS,
    "max_bytes_to_read": EXACT_GRAPH_TRACE_ANCHOR_MAX_BYTES_TO_READ,
    "max_result_rows": EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL,
}
EXACT_GRAPH_TRACE_CONTRIBUTION_READ_SETTINGS = {
    **EXACT_GRAPH_READ_SETTINGS,
    "max_bytes_to_read": EXACT_GRAPH_TRACE_CONTRIBUTION_MAX_BYTES_TO_READ,
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
    elapsed_ms = max(monotonic() - started, 0.0) * 1000
    if elapsed_ms >= EXACT_GRAPH_QUERY_TIMEOUT_MS:
        raise ExactGraphReadError("exact graph refresh deadline exceeded")
    metadata = {
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_count": query_count,
        "query_rows_returned": rows_returned,
        "query_elapsed_ms": round(elapsed_ms, 3),
    }
    return metadata


def _remaining_exact_graph_timeout_ms(
    started: float,
    statement_ceiling_ms: int | None = None,
) -> int:
    """Return the time left on one authoritative exact-refresh wall.

    Background readers do builder, relation, database, formatting, and
    publication work under one reviewed graph budget. A later statement may
    consume only the remaining portion; it never receives a fresh
    per-statement grant.
    """

    elapsed_ms = max(monotonic() - started, 0.0) * 1000
    # Floor the remaining duration, rather than subtracting a floored elapsed
    # duration, so rounding can never grant a statement time beyond the wall.
    remaining_ms = int(EXACT_GRAPH_QUERY_TIMEOUT_MS - elapsed_ms)
    if remaining_ms < settings.EXACT_GRAPH_MIN_REMAINING_MS:
        raise ExactGraphReadError("exact graph refresh bounded deadline exceeded")
    if statement_ceiling_ms is None:
        return remaining_ms
    if statement_ceiling_ms <= 0:
        raise ValueError("exact graph statement timeout must be positive")
    return min(int(statement_ceiling_ms), remaining_ms)


def _execute_direct_exact_graph_query(
    *,
    analytics: Any,
    query: str,
    params: dict[str, Any],
    started: float,
    settings: dict[str, Any],
) -> Any:
    """Execute a direct publication read inside its refresh's remaining wall."""

    return analytics.execute_ch_query(
        query,
        params,
        timeout_ms=_remaining_exact_graph_timeout_ms(started),
        settings=settings,
    )


def _finalize_exact_graph_payload(payload: Any, *, started: float) -> Any:
    """Fence publication after all formatting and result construction."""

    _remaining_exact_graph_timeout_ms(started)
    return payload


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


def _frozen_trace_membership_filters(
    filters: list[dict[str, Any]],
    *,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Canonicalize positive datetime bounds to one frozen half-open window."""

    frozen = [
        deepcopy(item)
        for item in filters or []
        if (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        or BaseQueryBuilder.is_datetime_complement_filter(item)
    ]
    frozen.append(
        {
            "column_id": "start_time",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start_date, end_date],
            },
        }
    )
    return frozen


def _normalize_driver_datetime(value: Any, reference: datetime) -> datetime:
    """Normalize native-driver timezone decoration for in-process boundaries."""

    if not isinstance(value, datetime):
        raise ExactGraphReadError(
            "Exact trace graph anchor bounds returned an invalid timestamp."
        )
    if reference.tzinfo is None and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    if reference.tzinfo is not None and value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _enumerate_authoritative_anchor_trace_ids(
    *,
    analytics: Any,
    builder: TraceListQueryBuilderV2,
    request_start: datetime,
    request_end: datetime,
    started: float,
) -> tuple[list[str], int, int] | None:
    """Resolve a broad scalar Map leaf once per physical time partition.

    The ordinary exact graph classifier is candidate-scoped but, for global
    child semantics, rereads all retained project history for every finite root
    batch.
    A broad scalar value can therefore repeat the same scan hundreds of times.
    This narrow lane instead collapses every physical span version once in
    disjoint whole-hour-aligned partitions, unions the authoritative matching
    trace identities, and then verifies live canonical roots in the frozen
    request window. It is exact, unsampled, and returns nothing until all
    partitions and root batches succeed.

    ``None`` means the filter shape is not eligible and the caller must retain
    the general exact fallback. An eligible empty project returns ``([], 1, 0)``.
    """

    supports = getattr(
        builder,
        "exact_graph_supports_authoritative_anchor_partition",
        None,
    )
    if not callable(supports) or not bool(supports()):
        return None

    request_width = request_end - request_start
    if request_width < EXACT_GRAPH_TRACE_ANCHOR_MIN_REQUEST_WIDTH:
        return None

    query_count = 0
    rows_returned = 0
    stats_lock = Lock()

    def remaining_timeout_ms(statement_ceiling_ms: int) -> int:
        return _remaining_exact_graph_timeout_ms(
            started,
            statement_ceiling_ms,
        )

    bounds_query, bounds_params = builder.build_exact_graph_anchor_scan_bounds()
    if not bounds_query:
        return None
    query_count += 1
    bounds_result = analytics.execute_ch_query(
        bounds_query,
        bounds_params,
        timeout_ms=remaining_timeout_ms(EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS),
        settings={
            **EXACT_GRAPH_TRACE_ANCHOR_READ_SETTINGS,
            "max_result_rows": 1,
        },
    )
    bounds_rows = list(bounds_result.data or [])
    rows_returned += len(bounds_rows)
    if len(bounds_rows) != 1:
        raise ExactGraphReadError(
            "Exact trace graph anchor bounds returned an invalid result."
        )
    bounds_columns = list(bounds_result.columns or [])
    min_start = _row_value(bounds_rows[0], bounds_columns, "min_start_time", None)
    max_start = _row_value(bounds_rows[0], bounds_columns, "max_start_time", None)
    if min_start is None and max_start is None:
        return [], query_count, rows_returned
    if min_start is None or max_start is None:
        raise ExactGraphReadError(
            "Exact trace graph anchor bounds returned a partial range."
        )
    min_start = _normalize_driver_datetime(min_start, request_start)
    max_start = _normalize_driver_datetime(max_start, request_start)
    if min_start > max_start:
        raise ExactGraphReadError(
            "Exact trace graph anchor bounds returned an inverted range."
        )
    scan_start = min_start.replace(minute=0, second=0, microsecond=0)
    scan_end = max_start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    retention_width = scan_end - scan_start
    if (
        EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION > 0
        and request_width.total_seconds()
        < retention_width.total_seconds()
        * EXACT_GRAPH_TRACE_ANCHOR_MIN_RETENTION_FRACTION
    ):
        # The ordered-root lane only replays retained child history for finite
        # request-window roots.  It is cheaper than a complete-retention anchor
        # when the requested root window covers only a small share of history.
        return None

    @dataclass(frozen=True)
    class AdaptivePartitionResult:
        trace_ids: set[str]
        safe_width: timedelta
        split_for_budget: bool

    def partition_lanes(
        range_start: datetime,
        range_end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Split an hour-aligned range into at most two independent lanes."""

        total_hours = int((range_end - range_start).total_seconds() // 3600)
        if total_hours <= 0:
            return []
        initial_hours = max(
            1,
            int(EXACT_GRAPH_TRACE_ANCHOR_PARTITION_WIDTH.total_seconds() // 3600),
        )
        lane_count = min(
            EXACT_GRAPH_TRACE_ANCHOR_MAX_WORKERS,
            max(1, (total_hours + initial_hours - 1) // initial_hours),
        )
        lane_count = max(1, lane_count)
        base_hours, extra_hours = divmod(total_hours, lane_count)
        lanes: list[tuple[datetime, datetime]] = []
        lane_start = range_start
        for lane_index in range(lane_count):
            lane_hours = base_hours + (1 if lane_index < extra_hours else 0)
            lane_end = lane_start + timedelta(hours=lane_hours)
            lanes.append((lane_start, lane_end))
            lane_start = lane_end
        return lanes

    def scan_adaptive_lane(
        lane_start: datetime,
        lane_end: datetime,
        scanner: Any,
    ) -> set[str]:
        """Scan one lane with exponential growth and fail-closed bisection.

        Successful slices double in width so sparse, long-retention projects do
        not issue one query per two retained hours. A read-budget failure is
        recursively and exactly covered by whole-hour children; the most
        conservative proven-safe child width becomes a ceiling for this lane so the
        same rejected width is not retried over and over.
        """

        lane_trace_ids: set[str] = set()
        cursor = lane_start
        next_width = min(
            EXACT_GRAPH_TRACE_ANCHOR_PARTITION_WIDTH,
            lane_end - lane_start,
        )
        safe_width_cap: timedelta | None = None
        while cursor < lane_end:
            remaining = lane_end - cursor
            width = min(next_width, remaining)
            result = scanner(cursor, cursor + width)
            lane_trace_ids.update(result.trace_ids)
            cursor += width
            if result.split_for_budget:
                safe_width_cap = (
                    result.safe_width
                    if safe_width_cap is None
                    else min(safe_width_cap, result.safe_width)
                )
                next_width = safe_width_cap
            else:
                next_width = width * 2
                if safe_width_cap is not None:
                    next_width = min(next_width, safe_width_cap)
            next_width = max(
                EXACT_GRAPH_TRACE_ANCHOR_MIN_PARTITION_WIDTH,
                next_width,
            )
        return lane_trace_ids

    def scan_partition(
        partition_start: datetime,
        partition_end: datetime,
    ) -> AdaptivePartitionResult:
        """Read one range, splitting only on whole-hour resource failures."""

        nonlocal query_count, rows_returned
        partition_trace_ids: set[str] = set()
        before_trace_id: str | None = None
        try:
            while True:
                query, params = builder.build_exact_graph_latest_anchor_partition(
                    partition_start=partition_start,
                    partition_end=partition_end,
                    before_trace_id=before_trace_id,
                    limit=EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL,
                )
                if not query:
                    raise ExactGraphReadError(
                        "Exact trace graph anchor partition could not be constructed."
                    )
                # Top-level partitions execute concurrently. Keep diagnostic
                # counters exact without making the result depend on their
                # completion order.
                with stats_lock:
                    query_count += 1
                result = analytics.execute_ch_query(
                    query,
                    params,
                    timeout_ms=remaining_timeout_ms(
                        EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS
                    ),
                    settings=EXACT_GRAPH_TRACE_ANCHOR_READ_SETTINGS,
                )
                page_rows = list(result.data or [])
                with stats_lock:
                    rows_returned += len(page_rows)
                if len(page_rows) > EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL:
                    raise ExactGraphReadError(
                        "Exact trace graph anchor partition exceeded its page."
                    )
                page_trace_ids: list[str] = []
                for row in page_rows:
                    trace_id = str(row.get("trace_id") or "")
                    if (
                        not trace_id
                        or trace_id in partition_trace_ids
                        or (before_trace_id is not None and trace_id <= before_trace_id)
                    ):
                        raise ExactGraphReadError(
                            "Exact trace graph anchor cursor returned an invalid identity."
                        )
                    page_trace_ids.append(trace_id)
                    partition_trace_ids.add(trace_id)
                if page_trace_ids != sorted(page_trace_ids):
                    raise ExactGraphReadError(
                        "Exact trace graph anchor cursor returned unordered identities."
                    )
                if len(page_rows) < EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL:
                    return AdaptivePartitionResult(
                        trace_ids=partition_trace_ids,
                        safe_width=partition_end - partition_start,
                        split_for_budget=False,
                    )
                next_trace_id = page_trace_ids[-1] if page_trace_ids else ""
                if not next_trace_id or next_trace_id == before_trace_id:
                    raise ExactGraphReadError(
                        "Exact trace graph anchor cursor did not make forward progress."
                    )
                before_trace_id = next_trace_id
        except Exception as exc:
            duration_hours = int(
                (partition_end - partition_start).total_seconds() // 3600
            )
            if not is_read_budget_error(exc) or duration_hours <= 1:
                raise
            # Discard every row from the failed parent attempt. Both children
            # completely cover it and share an hour boundary, so successful
            # recursive results are gap-free and safe to union.
            left_hours = max(1, duration_hours // 2)
            midpoint = partition_start + timedelta(hours=left_hours)
            if not partition_start < midpoint < partition_end:
                raise
            left_result = scan_partition(partition_start, midpoint)
            right_result = scan_partition(midpoint, partition_end)
            return AdaptivePartitionResult(
                trace_ids=left_result.trace_ids | right_result.trace_ids,
                safe_width=min(left_result.safe_width, right_result.safe_width),
                split_for_budget=True,
            )

    lanes = partition_lanes(scan_start, scan_end)

    candidate_trace_ids: set[str] = set()
    worker_count = len(lanes)
    if worker_count <= 1:
        for lane_start, lane_end in lanes:
            candidate_trace_ids.update(
                scan_adaptive_lane(lane_start, lane_end, scan_partition)
            )
    else:
        # Each future owns one disjoint whole-hour-aligned top-level range.
        # Recursive retries stay inside that range.  If any future fails, the
        # context waits only for the at-most-one other in-flight bounded read;
        # queued work is cancelled and no candidate or graph row is returned.
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="exact-graph-anchor",
        ) as executor:
            futures = [
                executor.submit(
                    scan_adaptive_lane,
                    lane_start,
                    lane_end,
                    scan_partition,
                )
                for lane_start, lane_end in lanes
            ]
            try:
                for future in as_completed(futures):
                    candidate_trace_ids.update(future.result())
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    if not candidate_trace_ids:
        return [], query_count, rows_returned

    # The production V2 builder can enumerate authoritative live roots once
    # per requested physical time partition.  Intersecting those IDs with the
    # independently authoritative attribute population avoids replaying all
    # retained project history once per finite matching-candidate batch. Keep the old
    # finite-ID verifier below as a compatibility fallback for alternate/test
    # builders that do not yet expose the partition cursor.
    root_partition_builder = getattr(
        builder,
        "build_exact_graph_latest_root_partition",
        None,
    )
    if callable(root_partition_builder):

        def scan_root_partition(
            partition_start: datetime,
            partition_end: datetime,
        ) -> AdaptivePartitionResult:
            """Return matching trace IDs whose latest live root is in range."""

            nonlocal query_count, rows_returned
            partition_verified_ids: set[str] = set()
            before_trace_id: str | None = None
            try:
                while True:
                    query, params = root_partition_builder(
                        partition_start=partition_start,
                        partition_end=partition_end,
                        request_start=request_start,
                        request_end=request_end,
                        before_trace_id=before_trace_id,
                        limit=EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL,
                    )
                    if not query:
                        raise ExactGraphReadError(
                            "Exact trace graph root partition could not be constructed."
                        )
                    with stats_lock:
                        query_count += 1
                    result = analytics.execute_ch_query(
                        query,
                        params,
                        timeout_ms=remaining_timeout_ms(
                            EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS
                        ),
                        settings=EXACT_GRAPH_TRACE_ANCHOR_READ_SETTINGS,
                    )
                    page_rows = list(result.data or [])
                    with stats_lock:
                        rows_returned += len(page_rows)
                    if len(page_rows) > EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL:
                        raise ExactGraphReadError(
                            "Exact trace graph root partition exceeded its page."
                        )
                    page_trace_ids: list[str] = []
                    page_trace_id_set: set[str] = set()
                    for row in page_rows:
                        trace_id = str(row.get("trace_id") or "")
                        if (
                            not trace_id
                            or trace_id in page_trace_id_set
                            or (
                                before_trace_id is not None
                                and trace_id <= before_trace_id
                            )
                        ):
                            raise ExactGraphReadError(
                                "Exact trace graph root cursor returned an invalid identity."
                            )
                        page_trace_ids.append(trace_id)
                        page_trace_id_set.add(trace_id)
                        if trace_id in candidate_trace_ids:
                            partition_verified_ids.add(trace_id)
                    if page_trace_ids != sorted(page_trace_ids):
                        raise ExactGraphReadError(
                            "Exact trace graph root cursor returned unordered identities."
                        )
                    if len(page_rows) < EXACT_GRAPH_TRACE_ANCHOR_RESULT_SENTINEL:
                        return AdaptivePartitionResult(
                            trace_ids=partition_verified_ids,
                            safe_width=partition_end - partition_start,
                            split_for_budget=False,
                        )
                    next_trace_id = page_trace_ids[-1] if page_trace_ids else ""
                    if not next_trace_id or next_trace_id == before_trace_id:
                        raise ExactGraphReadError(
                            "Exact trace graph root cursor did not make forward progress."
                        )
                    before_trace_id = next_trace_id
            except Exception as exc:
                duration_hours = int(
                    (partition_end - partition_start).total_seconds() // 3600
                )
                if not is_read_budget_error(exc) or duration_hours <= 1:
                    raise
                left_hours = max(1, duration_hours // 2)
                midpoint = partition_start + timedelta(hours=left_hours)
                if not partition_start < midpoint < partition_end:
                    raise
                left_result = scan_root_partition(
                    partition_start,
                    midpoint,
                )
                right_result = scan_root_partition(midpoint, partition_end)
                return AdaptivePartitionResult(
                    trace_ids=left_result.trace_ids | right_result.trace_ids,
                    safe_width=min(left_result.safe_width, right_result.safe_width),
                    split_for_budget=True,
                )

        root_scan_start = request_start.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        root_scan_end = request_end.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        if root_scan_end < request_end:
            root_scan_end += timedelta(hours=1)
        root_lanes = partition_lanes(root_scan_start, root_scan_end)

        verified_trace_ids: set[str] = set()
        root_worker_count = len(root_lanes)
        if root_worker_count <= 1:
            for root_lane_start, root_lane_end in root_lanes:
                verified_trace_ids.update(
                    scan_adaptive_lane(
                        root_lane_start,
                        root_lane_end,
                        scan_root_partition,
                    )
                )
        else:
            with ThreadPoolExecutor(
                max_workers=root_worker_count,
                thread_name_prefix="exact-graph-roots",
            ) as executor:
                futures = [
                    executor.submit(
                        scan_adaptive_lane,
                        lane_start,
                        lane_end,
                        scan_root_partition,
                    )
                    for lane_start, lane_end in root_lanes
                ]
                try:
                    for future in as_completed(futures):
                        verified_trace_ids.update(future.result())
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
        return sorted(verified_trace_ids), query_count, rows_returned

    sorted_candidates = sorted(candidate_trace_ids)
    verified_trace_ids: set[str] = set()

    def verify_root_batch(batch: list[str]) -> None:
        nonlocal query_count, rows_returned
        if not batch:
            return
        query, params = builder.build_exact_graph_root_membership_query(
            candidate_trace_ids=batch,
            request_start=request_start,
            request_end=request_end,
        )
        if not query:
            raise ExactGraphReadError(
                "Exact trace graph root verifier could not be constructed."
            )
        query_count += 1
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=remaining_timeout_ms(
                    EXACT_GRAPH_TRACE_ANCHOR_QUERY_TIMEOUT_MS
                ),
                settings={
                    **EXACT_GRAPH_READ_SETTINGS,
                    "max_result_rows": len(batch),
                },
            )
        except Exception as exc:
            if (
                is_read_budget_error(exc) or is_clickhouse_query_size_error(exc)
            ) and len(batch) > 1:
                midpoint = len(batch) // 2
                verify_root_batch(batch[:midpoint])
                verify_root_batch(batch[midpoint:])
                return
            raise
        matched_rows = list(result.data or [])
        rows_returned += len(matched_rows)
        batch_ids = set(batch)
        for row in matched_rows:
            trace_id = str(row.get("trace_id") or "")
            if (
                not trace_id
                or trace_id not in batch_ids
                or trace_id in verified_trace_ids
            ):
                raise ExactGraphReadError(
                    "Exact trace graph root verifier returned an invalid identity."
                )
            verified_trace_ids.add(trace_id)

    for offset in range(
        0, len(sorted_candidates), EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE
    ):
        verify_root_batch(
            sorted_candidates[
                offset : offset + EXACT_GRAPH_TRACE_ROOT_VERIFY_BATCH_SIZE
            ]
        )

    return sorted(verified_trace_ids), query_count, rows_returned


def _enumerate_exact_trace_ids(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    annotation_label_ids: tuple[str, ...] | None,
    started: float,
) -> tuple[list[str], int, int]:
    """Exhaust canonical roots and classify every candidate against latest state.

    Trace time filters bind to canonical roots. The root cursor is partitioned
    into adjacent half-open time slices and is exhaustive within the requested
    window; its rows never enter the result directly. Every de-duplicated,
    finite trace identity crosses a latest-state multi-filter classifier whose
    child witnesses are not time-scoped. This remains exact when a child is
    written more than a day after its in-window root, without scanning project
    history or relying on a maximum trace duration.

    De-duplication is defensive against multiple matching sibling spans and a
    merge/late arrival between statements; the read is not presented as MVCC.
    Any seed or classifier failure aborts before publication.
    """

    builder = TraceListQueryBuilderV2(
        project_id=str(project_id),
        filters=filters,
        page_number=0,
        page_size=EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE,
        annotation_label_ids=list(annotation_label_ids or ()),
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )
    if not builder.supports_bounded_filter_scan():
        raise ExactGraphReadError(
            "Filtered trace graph membership cannot be evaluated exactly."
        )

    request_start, request_end = builder.parse_time_range(filters)
    if request_start >= request_end:
        return [], 0, 0
    root_seed_start, root_seed_end = builder.exact_graph_filter_witness_range()
    if (root_seed_start, root_seed_end) != (request_start, request_end):
        raise ExactGraphReadError(
            "Exact trace graph root seed returned an invalid time range."
        )

    trace_ids: list[str] = []
    seen_matched_trace_ids: set[str] = set()
    seen_candidate_trace_ids: set[str] = set()
    seen_seed_ids: set[Any] = set()
    query_count = 0
    rows_returned = 0
    learned_classifier_batch_size = EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE

    def remaining_statement_timeout_ms(statement_ceiling_ms: int) -> int:
        return _remaining_exact_graph_timeout_ms(
            started,
            statement_ceiling_ms,
        )

    def seed_key(row: dict[str, Any]) -> tuple[datetime, Any]:
        seed_start_time = row.get("start_time")
        if not isinstance(seed_start_time, datetime):
            raise ExactGraphReadError(
                "Exact trace graph witness returned an invalid checkpoint."
            )
        if seed_start_time.tzinfo is not None:
            # The native driver can attach server timezone information even
            # when the builder's frozen request bounds are naive UTC values.
            # ClickHouse comparisons are already UTC; normalize only the
            # in-process keyset representation so the next page remains valid.
            seed_start_time = seed_start_time.replace(tzinfo=None)
        order_token = builder.bounded_filter_seed_order_token(row)
        try:
            hash(order_token)
        except TypeError:
            raise ExactGraphReadError(
                "Exact trace graph witness returned an invalid checkpoint."
            ) from None
        return seed_start_time, order_token

    def classify(candidate_rows: list[dict[str, Any]]) -> None:
        nonlocal learned_classifier_batch_size, query_count, rows_returned

        def classify_batch(batch: list[dict[str, Any]]) -> None:
            nonlocal learned_classifier_batch_size, query_count, rows_returned
            if not batch:
                return
            candidate_ids = {str(row.get("trace_id") or "") for row in batch}
            if "" in candidate_ids:
                raise ExactGraphReadError(
                    "Exact trace graph witness returned an invalid identity."
                )
            query, params = builder.build_filter_identity_match_query_from_seed_rows(
                batch
            )
            if not query:
                raise ExactGraphReadError(
                    "Exact trace graph classifier could not be constructed."
                )
            query_count += 1
            try:
                result = analytics.execute_ch_query(
                    query,
                    params,
                    timeout_ms=remaining_statement_timeout_ms(
                        EXACT_GRAPH_TRACE_CLASSIFIER_QUERY_TIMEOUT_MS
                    ),
                    settings={
                        **EXACT_GRAPH_TRACE_CLASSIFIER_READ_SETTINGS,
                        "max_result_rows": len(batch),
                    },
                )
            except Exception as exc:
                if (
                    is_read_budget_error(exc) or is_clickhouse_query_size_error(exc)
                ) and len(batch) > 1:
                    # The classifier is identity-bounded but intentionally
                    # scans child witnesses across all time: ingestion has no
                    # maximum trace duration. A tenant can therefore make a
                    # valid finite trace batch exceed the per-statement deadline
                    # even though smaller exact identity sets complete. Split
                    # the same finite batch in order and replay both halves;
                    # no identity is skipped and no failed statement can
                    # contribute a row. A one-identity failure still escapes
                    # fail-closed instead of publishing a partial graph.
                    midpoint = len(batch) // 2
                    learned_classifier_batch_size = min(
                        learned_classifier_batch_size,
                        max(midpoint, len(batch) - midpoint),
                    )
                    classify_rows(batch[:midpoint])
                    classify_rows(batch[midpoint:])
                    return
                raise
            classified_rows = list(result.data or [])
            rows_returned += len(classified_rows)
            for row in classified_rows:
                trace_id = str(row.get("trace_id") or "")
                if not trace_id or trace_id not in candidate_ids:
                    raise ExactGraphReadError(
                        "Exact trace graph classifier returned an invalid identity."
                    )
                if trace_id not in seen_matched_trace_ids:
                    seen_matched_trace_ids.add(trace_id)
                    trace_ids.append(trace_id)

        def classify_rows(rows: list[dict[str, Any]]) -> None:
            offset = 0
            while offset < len(rows):
                batch_size = min(
                    learned_classifier_batch_size,
                    len(rows) - offset,
                )
                classify_batch(rows[offset : offset + batch_size])
                offset += batch_size

        classify_rows(candidate_rows)

    # A sole compiler-proven positive any-span scalar can be resolved by one
    # authoritative latest-state pass over disjoint physical partitions. Try
    # that route before the optional candidate witness: the latter classifies
    # each finite candidate page by rescanning retained child history and was
    # observed in production to repeat the same 5k-ID classifier 149 times.
    # Unsupported or deliberately narrow windows return ``None`` and preserve
    # the existing candidate/root fallback semantics unchanged.
    authoritative_anchor = _enumerate_authoritative_anchor_trace_ids(
        analytics=analytics,
        builder=builder,
        request_start=request_start,
        request_end=request_end,
        started=started,
    )
    if authoritative_anchor is not None:
        authoritative_ids, anchor_query_count, anchor_rows_returned = (
            authoritative_anchor
        )
        return (
            authoritative_ids,
            query_count + anchor_query_count,
            rows_returned + anchor_rows_returned,
        )

    # property catalog cold DEV proof (2026-08-25) for a 12M trace graph with annotator
    # IS NULL, tokens > 1, and ai_interruption_count > 2 issued 258 sequential
    # CH statements. The first key-only candidate follow-up still hit its 1,001
    # sentinel for ai_interruption_count > 3 and fell back to 259 statements in
    # 12.10s. The optional typed-Map witness therefore retains the positive raw
    # value predicate when the missing-key default cannot satisfy it; unsafe
    # shapes keep key presence alone. Increasing a per-statement timeout cannot
    # address this measured serial fan-out.
    #
    # A selective typed-Map leaf has an all-time raw witness, while a positive
    # relational condition can provide request-window canonical roots.  Both
    # are necessary supersets of exact latest-state membership. Prove that the
    # complete population through a bounded keyset cursor, then classify only
    # those trace identities. This avoids enumerating and globally classifying
    # every root in a large tenant when the requested relation/value is sparse.
    # A resource-bounded first page may fall back to the exhaustive root walk;
    # after cursor progress, any failure aborts instead of hiding a partial
    # candidate scan. Malformed data and programming errors always fail closed.
    candidate_probe = getattr(
        builder,
        "build_exact_graph_candidate_witness_probe",
        None,
    )
    if callable(candidate_probe):
        candidate_after_trace_id: str | None = None
        candidate_before_start_time: datetime | None = None
        candidate_before_id: Any = None
        candidate_pages_completed = 0
        while True:
            candidate_page_limit = (
                EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL
                if candidate_pages_completed == 0
                else EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE
            )
            candidate_kwargs: dict[str, Any] = {
                "limit": candidate_page_limit,
            }
            if candidate_after_trace_id is not None:
                candidate_kwargs["after_trace_id"] = candidate_after_trace_id
            if candidate_before_start_time is not None:
                candidate_kwargs.update(
                    {
                        "before_start_time": candidate_before_start_time,
                        "before_id": candidate_before_id,
                    }
                )
            candidate_query, candidate_params = candidate_probe(**candidate_kwargs)
            if not candidate_query:
                break
            query_count += 1
            try:
                candidate_result = analytics.execute_ch_query(
                    candidate_query,
                    candidate_params,
                    timeout_ms=remaining_statement_timeout_ms(
                        EXACT_GRAPH_TRACE_WITNESS_QUERY_TIMEOUT_MS
                    ),
                    settings={
                        **EXACT_GRAPH_READ_SETTINGS,
                        "max_result_rows": candidate_page_limit,
                    },
                )
            except Exception as exc:
                if not is_read_budget_error(exc) or candidate_pages_completed:
                    raise
                break
            else:
                candidate_rows = list(candidate_result.data or [])
                rows_returned += len(candidate_rows)
                if len(candidate_rows) > candidate_page_limit:
                    raise ExactGraphReadError(
                        "Exact trace graph candidate witness exceeded its page limit."
                    )
                page_trace_ids: list[str] = []
                candidate_trace_ids: set[str] = set()
                for row in candidate_rows:
                    trace_id = str(row.get("trace_id") or "")
                    if (
                        not trace_id
                        or trace_id in candidate_trace_ids
                        or trace_id in seen_candidate_trace_ids
                    ):
                        raise ExactGraphReadError(
                            "Exact trace graph candidate witness returned an invalid identity."
                        )
                    page_trace_ids.append(trace_id)
                    candidate_trace_ids.add(trace_id)
                if candidate_rows and "start_time" in candidate_rows[0]:
                    ordered_keys = [seed_key(row) for row in candidate_rows]
                    if ordered_keys != sorted(ordered_keys, reverse=True):
                        raise ExactGraphReadError(
                            "Exact trace graph candidate root cursor is unordered."
                        )
                    next_start_time, next_order_token = ordered_keys[-1]
                    if (
                        candidate_before_start_time,
                        candidate_before_id,
                    ) == (next_start_time, next_order_token):
                        raise ExactGraphReadError(
                            "Exact trace graph candidate root cursor did not advance."
                        )
                    candidate_before_start_time = next_start_time
                    candidate_before_id = next_order_token
                elif candidate_rows:
                    if page_trace_ids != sorted(page_trace_ids):
                        raise ExactGraphReadError(
                            "Exact trace graph candidate cursor is unordered."
                        )
                    next_trace_id = page_trace_ids[-1]
                    if next_trace_id == candidate_after_trace_id:
                        raise ExactGraphReadError(
                            "Exact trace graph candidate cursor did not advance."
                        )
                    candidate_after_trace_id = next_trace_id
                seen_candidate_trace_ids.update(candidate_trace_ids)
                classify(candidate_rows)
                candidate_pages_completed += 1
                if len(candidate_rows) < candidate_page_limit:
                    return trace_ids, query_count, rows_returned

    slice_end = root_seed_end
    slice_width = min(EXACT_GRAPH_TRACE_INITIAL_SLICE, root_seed_end - root_seed_start)
    # Empty and sparse history widens logarithmically up to a bounded physical
    # read. Once width W fails, retry the same upper bound at W/2 and retain
    # that narrower ceiling until adjacent successful slices have completely
    # covered the failed interval. Growth may then resume below (and never
    # re-probe) that failed region.
    learned_slice_ceiling = min(
        root_seed_end - root_seed_start,
        EXACT_GRAPH_TRACE_MAX_SLICE,
    )
    failed_region_start: datetime | None = None
    while slice_end > root_seed_start:
        slice_start = max(root_seed_start, slice_end - slice_width)
        before_start_time: datetime | None = None
        before_order_token: Any = None
        retry_narrower = False
        slice_query_count = 0
        slice_query_time_ms = 0.0
        slice_rows_returned = 0

        while True:
            query, params = builder.build_filter_ordered_seed_page(
                slice_start=slice_start,
                slice_end=slice_end,
                limit=EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE,
                before_start_time=before_start_time,
                before_id=before_order_token,
            )
            query_count += 1
            query_started = monotonic()
            try:
                result = analytics.execute_ch_query(
                    query,
                    params,
                    timeout_ms=remaining_statement_timeout_ms(
                        EXACT_GRAPH_TRACE_WITNESS_QUERY_TIMEOUT_MS
                    ),
                    settings={
                        **EXACT_GRAPH_READ_SETTINGS,
                        "max_result_rows": EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE,
                    },
                )
            except Exception as exc:
                if is_read_budget_error(exc) and slice_width > min(
                    EXACT_GRAPH_TRACE_MIN_SLICE,
                    slice_end - root_seed_start,
                ):
                    # No raw row is publishable. Retrying the same upper bound
                    # with a narrower half-open slice cannot create a gap; any
                    # already classified duplicate is removed by trace identity.
                    narrowed_width = max(
                        EXACT_GRAPH_TRACE_MIN_SLICE,
                        slice_width / 2,
                    )
                    narrowed_width = min(
                        narrowed_width,
                        slice_end - root_seed_start,
                    )
                    if narrowed_width >= slice_width:
                        raise
                    failed_region_start = min(
                        failed_region_start or slice_start,
                        slice_start,
                    )
                    slice_width = narrowed_width
                    learned_slice_ceiling = min(
                        learned_slice_ceiling,
                        narrowed_width,
                    )
                    retry_narrower = True
                    break
                raise

            seed_rows = sorted(result.data or [], key=seed_key, reverse=True)
            measured_query_time_ms = getattr(result, "query_time_ms", None)
            if not isinstance(measured_query_time_ms, (int, float)):
                measured_query_time_ms = (monotonic() - query_started) * 1000
            slice_query_count += 1
            slice_query_time_ms += max(float(measured_query_time_ms), 0.0)
            slice_rows_returned += len(seed_rows)
            rows_returned += len(seed_rows)
            if len(seed_rows) > EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE:
                raise ExactGraphReadError(
                    "Exact trace graph witness exceeded its bounded page."
                )

            new_candidate_rows: list[dict[str, Any]] = []
            for row in seed_rows:
                trace_id = str(row.get("trace_id") or "")
                if not trace_id:
                    raise ExactGraphReadError(
                        "Exact trace graph witness returned an invalid identity."
                    )
                seed_identity = builder.bounded_filter_seed_identity(row)
                try:
                    hash(seed_identity)
                except TypeError:
                    raise ExactGraphReadError(
                        "Exact trace graph witness returned an invalid identity."
                    ) from None
                if seed_identity in seen_seed_ids:
                    continue
                seen_seed_ids.add(seed_identity)
                if trace_id in seen_candidate_trace_ids:
                    continue
                seen_candidate_trace_ids.add(trace_id)
                new_candidate_rows.append(row)

            classify(new_candidate_rows)
            if len(seed_rows) < EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE:
                break
            if not seed_rows:
                raise ExactGraphReadError(
                    "Exact trace graph witness cursor did not make forward progress."
                )
            next_start_time, next_order_token = seed_key(seed_rows[-1])
            if (next_start_time, next_order_token) == (
                before_start_time,
                before_order_token,
            ):
                raise ExactGraphReadError(
                    "Exact trace graph witness cursor did not make forward progress."
                )
            before_start_time, before_order_token = (
                next_start_time,
                next_order_token,
            )

        if retry_narrower:
            continue
        slice_end = slice_start
        if slice_end <= root_seed_start:
            break

        if failed_region_start is not None and slice_end <= failed_region_start:
            # The failed half-open interval is now fully covered by successful
            # narrower reads. It is safe to recover growth for older, disjoint
            # history without retrying the same failed region.
            failed_region_start = None
            learned_slice_ceiling = min(
                EXACT_GRAPH_TRACE_MAX_SLICE,
                slice_end - root_seed_start,
            )

        # A single, cheap terminal page proves this whole slice sparse enough
        # to widen. Paginated or expensive slices retain their current width;
        # growth occurs only after complete cursor exhaustion and never crosses
        # a ceiling learned from a failed statement.
        if (
            slice_query_count == 1
            and slice_rows_returned < EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE
            and slice_query_time_ms <= EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS
        ):
            slice_width = min(
                slice_width * 2,
                learned_slice_ceiling,
                EXACT_GRAPH_TRACE_MAX_SLICE,
                slice_end - root_seed_start,
            )
        else:
            slice_width = min(slice_width, slice_end - root_seed_start)

    return trace_ids, query_count, rows_returned


def _merge_exact_trace_contribution_rows(
    batches: list[tuple[list[Any], list[str]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge additive bucket states only after all required reads succeed.

    Individual statements have ClickHouse transport limits, but partitioning
    could otherwise accumulate a larger result in Python. Enforce the same
    public row/byte envelope on the final merged payload before formatting or
    cache publication.
    """

    merged: dict[Any, dict[str, Any]] = {}
    for rows, columns in batches:
        for row in rows:
            bucket = _row_value(row, columns, "time_bucket", None)
            if bucket is None:
                raise ExactGraphReadError(
                    "Exact graph contribution read returned an invalid bucket."
                )
            state = merged.setdefault(
                bucket,
                {
                    "latency_sum": 0,
                    "total_tokens": 0,
                    "cost_sum": Decimal(0),
                    "traffic_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "error_count": 0,
                },
            )
            state["latency_sum"] += int(_row_value(row, columns, "latency_sum", 0) or 0)
            state["total_tokens"] += int(
                _row_value(row, columns, "total_tokens", 0) or 0
            )
            state["cost_sum"] += Decimal(
                str(_row_value(row, columns, "cost_sum", 0) or 0)
            )
            state["traffic_count"] += int(
                _row_value(row, columns, "traffic_count", 0) or 0
            )
            state["prompt_tokens"] += int(
                _row_value(row, columns, "prompt_tokens", 0) or 0
            )
            state["completion_tokens"] += int(
                _row_value(row, columns, "completion_tokens", 0) or 0
            )
            state["error_count"] += int(_row_value(row, columns, "error_count", 0) or 0)

    if len(merged) > EXACT_GRAPH_MAX_RESULT_ROWS:
        raise ExactGraphReadError(
            "Exact graph contribution result exceeded its bounded row limit."
        )

    rows: list[dict[str, Any]] = []
    for bucket in sorted(merged):
        state = merged[bucket]
        count = int(state["traffic_count"])
        denominator = max(count, 1)
        rows.append(
            {
                "time_bucket": bucket,
                "avg_latency": state["latency_sum"] / denominator,
                "total_tokens": state["total_tokens"],
                "avg_cost": float(state["cost_sum"] / denominator),
                "traffic_count": count,
                "prompt_tokens": state["prompt_tokens"],
                "completion_tokens": state["completion_tokens"],
                "error_rate": state["error_count"] * 100.0 / denominator,
            }
        )
    encoded_size = len(
        json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    if encoded_size > EXACT_GRAPH_MAX_RESULT_BYTES:
        raise ExactGraphReadError(
            "Exact graph contribution result exceeded its bounded byte limit."
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
    return rows, columns


def _read_exact_filtered_trace_graph(
    *,
    analytics: Any,
    builder: TimeSeriesQueryBuilder,
    project_id: str,
    filters: list[dict[str, Any]],
    annotation_label_ids: tuple[str, ...] | None,
    started: float,
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=analytics,
        project_id=project_id,
        filters=filters,
        annotation_label_ids=annotation_label_ids,
        started=started,
    )
    batches: list[tuple[list[Any], list[str]]] = []

    def read_contribution_batch(batch_trace_ids: list[str]) -> None:
        nonlocal query_count, rows_returned
        if not batch_trace_ids:
            return
        query, params = builder.build_exact_trace_contribution_batch(batch_trace_ids)
        query_count += 1
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=_remaining_exact_graph_timeout_ms(
                    started,
                    EXACT_GRAPH_TRACE_CONTRIBUTION_QUERY_TIMEOUT_MS,
                ),
                settings=EXACT_GRAPH_TRACE_CONTRIBUTION_READ_SETTINGS,
            )
        except Exception as exc:
            if (
                is_read_budget_error(exc) or is_clickhouse_query_size_error(exc)
            ) and len(batch_trace_ids) > 1:
                midpoint = len(batch_trace_ids) // 2
                read_contribution_batch(batch_trace_ids[:midpoint])
                read_contribution_batch(batch_trace_ids[midpoint:])
                return
            raise
        batch_rows = list(result.data or [])
        batch_columns = list(result.columns or [])
        batches.append((batch_rows, batch_columns))
        rows_returned += len(batch_rows)

    for offset in range(0, len(trace_ids), EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE):
        read_contribution_batch(
            trace_ids[offset : offset + EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE]
        )

    merged_rows, merged_columns = _merge_exact_trace_contribution_rows(batches)
    return (
        builder.format_result(merged_rows, merged_columns),
        query_count,
        rows_returned,
    )


def _read_exact_filtered_span_graph(
    *,
    analytics: Any,
    builder: TimeSeriesQueryBuilder,
    exact_filter_plan: Any,
    start_date: datetime,
    end_date: datetime,
    started: float,
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    """Merge exact row-local span states after every bounded read succeeds.

    The result list is intentionally retained privately until the final scan
    completes. Any timeout, read-budget error, or malformed result propagates
    before formatting, so the background refresh cannot publish a partial
    graph over the prior complete snapshot. Physical scans start/end on whole
    hours, matching the RMT sorting identity. The first/last query still scans
    its complete hour, then applies the frozen request bounds to winning rows.
    Cheap successful scans may double by whole hours up to one day; a resource
    failure halves and retries the exact same cursor, and the failed width
    becomes a ceiling for the remainder of this refresh. Separate statements
    cannot be MVCC on CH25.3; any relational membership embedded in the filter
    plan was resolved once by the caller and is reused unchanged here.
    """

    batches: list[tuple[list[Any], list[str]]] = []
    query_count = 0
    rows_returned = 0
    partition_start = start_date.replace(minute=0, second=0, microsecond=0)
    partition_limit = end_date.replace(minute=0, second=0, microsecond=0)
    if partition_limit < end_date:
        partition_limit += EXACT_GRAPH_SPAN_MIN_PARTITION_WIDTH
    scan_width = partition_limit - partition_start
    partition_width = min(EXACT_GRAPH_SPAN_PARTITION_WIDTH, scan_width)
    max_partition_width = min(EXACT_GRAPH_SPAN_MAX_PARTITION_WIDTH, scan_width)
    while partition_start < partition_limit:
        partition_end = min(partition_start + partition_width, partition_limit)
        query, params = builder.build_exact_span_partition(
            partition_start=partition_start,
            partition_end=partition_end,
            exact_filter_plan=exact_filter_plan,
        )
        query_count += 1
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=_remaining_exact_graph_timeout_ms(
                    started,
                    EXACT_GRAPH_SPAN_PARTITION_QUERY_TIMEOUT_MS,
                ),
                settings=EXACT_GRAPH_SPAN_PARTITION_READ_SETTINGS,
            )
        except Exception as exc:
            if (
                is_read_budget_error(exc)
                and partition_width > EXACT_GRAPH_SPAN_MIN_PARTITION_WIDTH
            ):
                partition_width = max(
                    EXACT_GRAPH_SPAN_MIN_PARTITION_WIDTH,
                    partition_width / 2,
                )
                max_partition_width = min(max_partition_width, partition_width)
                # Retry without advancing. No failed statement contributes a
                # row, so the successful windows remain gap-free and disjoint.
                continue
            raise
        rows = list(result.data or [])
        columns = list(result.columns or [])
        batches.append((rows, columns))
        rows_returned += len(rows)
        partition_start = partition_end

        query_time_ms = getattr(result, "query_time_ms", None)
        try:
            cheap_slice = (
                query_time_ms is not None
                and 0 <= float(query_time_ms) <= EXACT_GRAPH_SPAN_GROW_BELOW_QUERY_MS
            )
        except (TypeError, ValueError):
            cheap_slice = False
        if cheap_slice and partition_width < max_partition_width:
            partition_width = min(partition_width * 2, max_partition_width)

    merged_rows, merged_columns = _merge_exact_trace_contribution_rows(batches)
    return (
        builder.format_result(merged_rows, merged_columns),
        query_count,
        rows_returned,
    )


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
        return _finalize_exact_graph_payload(
            _system_metric_payload(
                metrics,
                metric_id,
                _metadata(
                    started=started,
                    query_count=0,
                    rows_returned=0,
                ),
            ),
            started=started,
        )

    annotation_label_ids = _annotation_label_ids_for_filters(project_id, filters)
    builder = TimeSeriesQueryBuilder(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=start_date,
        end_date=end_date,
        annotation_label_ids=annotation_label_ids,
    )
    exact_filter_plan = compile_exact_graph_row_predicates(
        filters,
        project_id=str(project_id),
        observe_type=observe_type,
        annotation_label_ids=annotation_label_ids,
    )
    normalized_observe_type = str(observe_type or "").strip().lower()
    if normalized_observe_type == "trace" and exact_filter_plan.predicates:
        frozen_filters = _frozen_trace_membership_filters(
            filters,
            start_date=start_date,
            end_date=end_date,
        )
        metrics, query_count, rows_returned = _read_exact_filtered_trace_graph(
            analytics=analytics,
            builder=builder,
            project_id=str(project_id),
            filters=frozen_filters,
            annotation_label_ids=annotation_label_ids,
            started=started,
        )
        return _finalize_exact_graph_payload(
            _system_metric_payload(
                metrics,
                metric_id,
                _metadata(
                    started=started,
                    query_count=query_count,
                    rows_returned=rows_returned,
                ),
            ),
            started=started,
        )
    if normalized_observe_type == "span" and exact_filter_plan.predicates:
        metrics, query_count, rows_returned = _read_exact_filtered_span_graph(
            analytics=analytics,
            builder=builder,
            exact_filter_plan=exact_filter_plan,
            start_date=start_date,
            end_date=end_date,
            started=started,
        )
        return _finalize_exact_graph_payload(
            _system_metric_payload(
                metrics,
                metric_id,
                _metadata(
                    started=started,
                    query_count=query_count,
                    rows_returned=rows_returned,
                ),
            ),
            started=started,
        )
    query, params = builder.build()
    result = _execute_direct_exact_graph_query(
        analytics=analytics,
        query=query,
        params=params,
        started=started,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    metrics = builder.format_result(rows, columns)
    return _finalize_exact_graph_payload(
        _system_metric_payload(
            metrics,
            metric_id,
            _metadata(
                started=started,
                query_count=1,
                rows_returned=len(rows),
            ),
        ),
        started=started,
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
        return _finalize_exact_graph_payload(
            {
                **builder.format_result([], []),
                **_metadata(started=started, query_count=0, rows_returned=0),
            },
            started=started,
        )

    query, params = builder.build()
    result = _execute_direct_exact_graph_query(
        analytics=analytics,
        query=query,
        params=params,
        started=started,
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
    return _finalize_exact_graph_payload(
        {
            **builder.format_result(rows, columns),
            **_metadata(started=started, query_count=1, rows_returned=len(rows)),
        },
        started=started,
    )


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
        return _finalize_exact_graph_payload(
            {
                **builder.format_result([], []),
                **_metadata(
                    started=started,
                    query_count=0,
                    rows_returned=0,
                ),
            },
            started=started,
        )
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
    result = _execute_direct_exact_graph_query(
        analytics=analytics,
        query=query,
        params=params,
        started=started,
        settings=EXACT_GRAPH_READ_SETTINGS,
    )
    rows = list(result.data or [])
    columns = list(result.columns or [])
    return _finalize_exact_graph_payload(
        {
            **builder.format_result(rows, columns),
            **_metadata(
                started=started,
                query_count=1,
                rows_returned=len(rows),
            ),
        },
        started=started,
    )


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
        result = _execute_direct_exact_graph_query(
            analytics=analytics,
            query=query,
            params=params,
            started=started,
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
        payload: dict[str, Any] | list[dict[str, Any]] = exact_series
    elif exact_series:
        payload = exact_series[0]
    else:
        payload = {"metric_name": config_id, "data": [], **metadata}
    return _finalize_exact_graph_payload(payload, started=started)


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
    timeout_ms: int,
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
        timeout_ms=timeout_ms,
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
    timeout_ms: int,
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
        timeout_ms=timeout_ms,
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

    def remaining_statement_timeout_ms() -> int:
        return _remaining_exact_graph_timeout_ms(started)

    aggregation_context = str(aggregation_context or "trace").strip().lower()
    if aggregation_context not in {"trace", "session", "user"}:
        raise ValueError("unsupported annotation graph aggregation context")
    if aggregation_context in {"session", "user"} and observe_type != "trace":
        raise ValueError("aggregate annotation graphs require trace observation mode")
    label_id = str(req_data_config.get("id") or "")
    if connection.vendor == "postgresql":
        # Label discovery consults both authoritative Score membership and
        # label metadata. It is part of the same refresh, so give both ORM
        # statements only the time still left on that refresh wall.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                cursor.execute(
                    "SET LOCAL statement_timeout = "
                    f"'{remaining_statement_timeout_ms()}ms'"
                )
            label = get_annotation_labels_for_project(project_id).get(id=label_id)
    else:
        label = get_annotation_labels_for_project(project_id).get(id=label_id)
    # A slow successful metadata read must not start Score partition work after
    # the refresh wall has already expired.
    remaining_statement_timeout_ms()
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
        return _finalize_exact_graph_payload(
            {
                "metric_name": label_id,
                "name": label.name,
                "data": [],
                **_metadata(
                    started=started,
                    query_count=0,
                    rows_returned=0,
                ),
            },
            started=started,
        )

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
    # batch failure aborts the refresh before publication. Each partition's PG
    # statement receives only the refresh's remaining wall time; a later
    # partition can never reset the timeout back to a fresh action deadline.
    remaining_statement_timeout_ms()
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
        for partition_start, partition_end in output_bucket_partitions(
            start_date, end_date, interval
        ):
            partition_timeout_ms = remaining_statement_timeout_ms()
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    # Internal bounded integer, never request-derived SQL.
                    cursor.execute(
                        f"SET LOCAL statement_timeout = '{partition_timeout_ms}ms'"
                    )
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
                        timeout_ms=remaining_statement_timeout_ms(),
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
                        timeout_ms=remaining_statement_timeout_ms(),
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
            # PostgreSQL may return an empty partition, in which case no CH
            # membership batch calls the deadline helper. Check after every PG
            # iterator so a slow empty statement still fails the refresh.
            remaining_statement_timeout_ms()

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
    # Formatting is bounded by the result sentinel, but publication must still
    # be inside the same authoritative wall budget.
    remaining_statement_timeout_ms()
    return _finalize_exact_graph_payload(
        {
            "metric_name": label_id,
            "name": label.name,
            "data": points,
            **_metadata(
                started=started,
                query_count=query_count,
                rows_returned=rows_returned,
            ),
        },
        started=started,
    )


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

        counter += 1
        param_name = f"session_having_{counter}"
        clauses.append(
            build_numeric_filter_predicate(
                column,
                filter_op,
                filter_value,
                param_prefix=param_name,
                params=params,
            )
        )
    return " AND ".join(clauses)


@dataclass(frozen=True)
class _SessionMembershipPlan:
    scalar_aggregates: tuple[str, ...]
    scalar_predicates: tuple[str, ...]
    relational_predicates: tuple[str, ...]
    params: dict[str, Any]


def _finite_survivor_map_ctes(
    *,
    remap_table: str,
    candidate_relation: str,
    candidate_column: str,
    prefix: str,
    map_name: str,
) -> str:
    """Materialize only remap groups touched by one finite candidate relation.

    Exact entity graphs discover physical IDs inside the frozen project/time
    window.  Resolving those IDs must not construct a window over the complete
    tenant remap table.  This shape probes old IDs, treats candidate new IDs as
    possible group keys, expands only the touched groups, and materializes the
    resulting tiny map once as a scalar tuple array.  Candidate IDs themselves
    stay relational: ClickHouse does not accept a scalar array alias as the
    right-hand side of ``IN`` in ``PREWHERE``.
    """

    identifiers = (
        remap_table,
        candidate_relation,
        candidate_column,
        prefix,
        map_name,
    )
    if any(not value or not value.replace("_", "").isalnum() for value in identifiers):
        raise ValueError("finite remap identifier is invalid")
    candidate_ids_name = f"{prefix}_candidate_ids"
    target_relation = f"{prefix}_target_new_ids"
    pair_name = f"{prefix}_pairs"
    return f"""
    {candidate_ids_name} AS (
        SELECT DISTINCT
            assumeNotNull({candidate_column}) AS {candidate_column}
        FROM {candidate_relation}
        WHERE isNotNull({candidate_column})
    ),
    {target_relation} AS (
        SELECT DISTINCT new_id
        FROM {remap_table} FINAL
        PREWHERE old_id IN (
            SELECT {candidate_column}
            FROM {candidate_ids_name}
        )
        UNION DISTINCT
        SELECT {candidate_column} AS new_id
        FROM {candidate_ids_name}
    ),
    (
        SELECT groupArray(tuple(any_id, survivor_id))
        FROM (
            SELECT
                any_id,
                argMin(survivor_id, toString(survivor_id)) AS survivor_id
            FROM (
                SELECT
                    arrayJoin(arrayDistinct(arrayConcat(
                        groupArray(old_id),
                        [new_id]
                    ))) AS any_id,
                    argMin(old_id, toString(old_id)) AS survivor_id
                FROM {remap_table} FINAL
                WHERE new_id IN (SELECT new_id FROM {target_relation})
                GROUP BY new_id
            ) AS touched_groups
            GROUP BY any_id
        ) AS deduplicated_touched_map
    ) AS {pair_name},
    {map_name} AS (
        SELECT
            tupleElement(pair, 1) AS any_id,
            tupleElement(pair, 2) AS survivor_id
        FROM (SELECT arrayJoin({pair_name}) AS pair)
    )
    """


def _session_membership_plan(
    *,
    project_id: str,
    filters: list[dict[str, Any]],
) -> _SessionMembershipPlan:
    """Compile independent trace leaves for session-level intersection.

    The session list contract allows separate traces in one session to satisfy
    separate scalar/attribute/relational leaves.  Compiling the complete list
    as one trace predicate would instead require a single trace to satisfy
    every leaf.  Keep each leaf independent, namespace its bound parameters,
    and let the caller intersect them only after grouping by resolved session.

    Filter membership is frozen to the complete request snapshot.  The shared
    relational compiler names that range ``start_date``/``end_date``, but
    entity graph callers reuse those names for each output partition. Retarget
    only relational leaf predicates to the immutable ``snapshot_*`` bounds so
    a sibling match cannot disappear on page N.
    """

    root_filters = [
        item
        for item in filters
        if (item.get("column_id") or item.get("columnId")) == "end_time"
    ]
    scalar_filters = [
        item
        for item in filters
        if (item.get("column_id") or item.get("columnId")) != "end_time"
    ]
    scalar_plans, relational_filters = partition_span_filter_plans(scalar_filters)
    scalar_aggregates = [
        rewrite_v1_sql_to_v2(aggregate)
        for plan in scalar_plans
        for aggregate in plan.aggregates
    ]
    scalar_predicates = [rewrite_v1_sql_to_v2(plan.predicate) for plan in scalar_plans]
    params: dict[str, Any] = {}
    scalar_params = {
        param_name: value
        for plan in scalar_plans
        for param_name, value in plan.params.items()
    }
    for param_name, value in scalar_params.items():
        placeholder = f"%({param_name})s"
        namespaced_name = f"session_scalar_{param_name}"
        namespaced_placeholder = f"%({namespaced_name})s"
        used = False
        for index, aggregate in enumerate(scalar_aggregates):
            if placeholder in aggregate:
                scalar_aggregates[index] = aggregate.replace(
                    placeholder,
                    namespaced_placeholder,
                )
                used = True
        for index, predicate in enumerate(scalar_predicates):
            if placeholder in predicate:
                scalar_predicates[index] = predicate.replace(
                    placeholder,
                    namespaced_placeholder,
                )
                used = True
        if used:
            params[namespaced_name] = value

    relational_predicates: list[str] = []
    for leaf_index, item in enumerate(root_filters):
        predicate, leaf_params = compile_exact_graph_filter_predicates(
            [item],
            project_id=project_id,
            observe_type="span",
        )
        if not predicate:
            continue
        for param_name, value in leaf_params.items():
            placeholder = f"%({param_name})s"
            if placeholder not in predicate:
                continue
            namespaced_name = f"session_root_{leaf_index}_{param_name}"
            predicate = predicate.replace(
                placeholder,
                f"%({namespaced_name})s",
            )
            params[namespaced_name] = value
        relational_predicates.append(predicate)

    annotation_label_ids = _annotation_label_ids_for_filters(
        project_id,
        relational_filters,
    )
    for leaf_index, item in enumerate(relational_filters):
        predicate, leaf_params = compile_exact_graph_filter_predicates(
            [item],
            project_id=project_id,
            observe_type="trace",
            annotation_label_ids=annotation_label_ids,
        )
        if not predicate:
            continue
        predicate = predicate.replace(
            "%(start_date)s", "%(snapshot_start_date)s"
        ).replace("%(end_date)s", "%(snapshot_end_date)s")
        for param_name, value in leaf_params.items():
            placeholder = f"%({param_name})s"
            if placeholder not in predicate:
                continue
            namespaced_name = f"session_relational_{leaf_index}_{param_name}"
            predicate = predicate.replace(
                placeholder,
                f"%({namespaced_name})s",
            )
            params[namespaced_name] = value
        relational_predicates.append(predicate)
    return _SessionMembershipPlan(
        scalar_aggregates=tuple(scalar_aggregates),
        scalar_predicates=tuple(scalar_predicates),
        relational_predicates=tuple(relational_predicates),
        params=params,
    )


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
    trace/span leaves are intersected after grouping their independent matches
    by resolved session. Numeric aggregates and first/last messages are then
    computed from every live root in that selected session. The fixed
    ``snapshot_*`` parameters are deliberately distinct from an outer graph
    partition's dates so a session can never be split at an output-bucket
    boundary.
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
    membership_plan = _session_membership_plan(
        project_id=project_id,
        filters=span_filters,
    )
    resolved_session_id = (
        "if(ts_remap.survivor_id IS NULL OR "
        "ts_remap.survivor_id = "
        "toUUID('00000000-0000-0000-0000-000000000000'), "
        "rs.trace_session_id, ts_remap.survivor_id)"
    )
    params = {
        **membership_plan.params,
        "project_id": project_id,
        "snapshot_start_date": start_date,
        "snapshot_end_date": end_date,
    }
    snapshot_scan_start = start_date.replace(minute=0, second=0, microsecond=0)
    snapshot_scan_end = end_date.replace(minute=0, second=0, microsecond=0)
    if snapshot_scan_end < end_date:
        snapshot_scan_end += timedelta(hours=1)
    params.update(
        {
            "snapshot_scan_start_date": snapshot_scan_start,
            "snapshot_scan_end_date": snapshot_scan_end,
        }
    )
    root_datetime_predicate, root_datetime_params = (
        BaseQueryBuilder.bounded_datetime_exclusion_sql(
            filters,
            column="start_time",
            param_prefix="exact_session_time_exclusion",
        )
    )
    params.update(root_datetime_params)
    root_datetime_fragment = (
        f"\n          AND {root_datetime_predicate}" if root_datetime_predicate else ""
    )
    # ``toStartOfHour(start_time)`` is part of the deployed CH25 replacement
    # identity. A producer may correct start_time across an exact request
    # boundary while keeping the same physical identity. FINAL must therefore
    # see both complete boundary hours; apply the frozen request window only
    # after that collapse so an older live row cannot survive a newer tombstone.
    session_root_rows = f"""
        SELECT *
        FROM (
            SELECT *
            FROM spans FINAL
            PREWHERE project_id = toUUID(%(project_id)s)
              AND start_time >= %(snapshot_scan_start_date)s
              AND start_time < %(snapshot_scan_end_date)s
        ) AS snapshot_roots
        WHERE snapshot_roots.start_time >= %(snapshot_start_date)s
          AND snapshot_roots.start_time < %(snapshot_end_date)s
          {root_datetime_fragment}
          AND snapshot_roots.is_deleted = 0
          AND (snapshot_roots.parent_span_id IS NULL OR
               snapshot_roots.parent_span_id = '')
          AND snapshot_roots.trace_session_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
    """
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
    session_map_ctes = _finite_survivor_map_ctes(
        remap_table="trace_session_id_remap",
        candidate_relation="candidate_physical_session_ids",
        candidate_column="physical_session_id",
        prefix="candidate_session_remap",
        map_name="ts_survivor_map",
    )
    membership_ctes = ""
    selected_session_predicates: list[str] = []
    if membership_plan.scalar_predicates:
        scalar_datetime_predicate, scalar_datetime_params = (
            BaseQueryBuilder.bounded_datetime_exclusion_sql(
                filters,
                column="latest_start_time",
                param_prefix="exact_session_scalar_time_exclusion",
            )
        )
        params.update(scalar_datetime_params)
        scalar_datetime_fragment = (
            f"\n          AND {scalar_datetime_predicate}"
            if scalar_datetime_predicate
            else ""
        )
        scalar_aggregate_select = ",\n            ".join(
            membership_plan.scalar_aggregates
        )
        scalar_membership_having = "\n          AND ".join(
            f"countIf({predicate}) > 0"
            for predicate in membership_plan.scalar_predicates
        )
        scalar_resolved_session_id = resolved_id_expr(
            "latest_trace_session_id",
            "scalar_ts_remap",
        )
        membership_ctes += f""",
    latest_session_filter_spans AS (
        SELECT
            project_id,
            observation_type,
            service_name,
            trace_id,
            id,
            argMax(start_time, _version) AS latest_start_time,
            argMax(tuple(trace_session_id), _version).1
                AS latest_trace_session_id,
            argMax(is_deleted, _version) AS latest_is_deleted,
            {scalar_aggregate_select}
        FROM spans
        PREWHERE project_id = toUUID(%(project_id)s)
          AND start_time >= %(snapshot_scan_start_date)s
          AND start_time < %(snapshot_scan_end_date)s
        GROUP BY
            project_id,
            observation_type,
            service_name,
            toStartOfHour(start_time),
            trace_id,
            id
    ),
    resolved_session_filter_spans AS (
        SELECT
            latest_session_filter_spans.*,
            {scalar_resolved_session_id} AS session_id
        FROM latest_session_filter_spans
        LEFT JOIN ts_survivor_map AS scalar_ts_remap
          ON latest_trace_session_id = scalar_ts_remap.any_id
        WHERE latest_is_deleted = 0
          AND latest_start_time >= %(snapshot_start_date)s
          AND latest_start_time < %(snapshot_end_date)s
          {scalar_datetime_fragment}
          AND isNotNull(latest_trace_session_id)
          AND latest_trace_session_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
          AND {scalar_resolved_session_id} IN (
              SELECT session_id FROM candidate_sessions
          )
    ),
    matching_scalar_sessions AS (
        SELECT session_id
        FROM resolved_session_filter_spans
        GROUP BY session_id
        HAVING {scalar_membership_having}
    )"""
        selected_session_predicates.append(
            "session_id IN (SELECT session_id FROM matching_scalar_sessions)"
        )
    if membership_plan.relational_predicates:
        relational_membership_having = "\n          AND ".join(
            f"countIf({predicate}) > 0"
            for predicate in membership_plan.relational_predicates
        )
        membership_ctes += f""",
    matching_relational_sessions AS (
        SELECT
            {resolved_session_id} AS session_id
        FROM (
            {session_root_rows}
        ) AS rs
        LEFT JOIN ts_survivor_map AS ts_remap
          ON rs.trace_session_id = ts_remap.any_id
        WHERE {resolved_session_id} IN (
            SELECT session_id FROM candidate_sessions
        )
        GROUP BY session_id
        HAVING {relational_membership_having}
    )"""
        selected_session_predicates.append(
            "session_id IN (SELECT session_id FROM matching_relational_sessions)"
        )
    selected_session_where = (
        "WHERE " + " AND ".join(selected_session_predicates)
        if selected_session_predicates
        else ""
    )
    membership_ctes += f""",
    selected_sessions AS (
        SELECT session_id
        FROM candidate_sessions
        {selected_session_where}
    )"""
    source_where_clauses = [
        f"{resolved_session_id} IN (SELECT session_id FROM selected_sessions)"
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
    WITH
    candidate_physical_session_ids AS (
        SELECT DISTINCT
            candidate_rs.trace_session_id AS physical_session_id
        FROM (
            {session_root_rows}
        ) AS candidate_rs
        WHERE 1 = 1
          {candidate_trace_clause}
    ),
    {session_map_ctes},
    candidate_sessions AS (
        SELECT DISTINCT
            if(candidate_remap.survivor_id IS NULL OR
               candidate_remap.survivor_id =
                   toUUID('00000000-0000-0000-0000-000000000000'),
               physical_session_id,
               candidate_remap.survivor_id) AS session_id
        FROM candidate_session_remap_candidate_ids AS candidate_session_ids
        LEFT JOIN ts_survivor_map AS candidate_remap
          ON physical_session_id = candidate_remap.any_id
    ){membership_ctes}
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
        {session_root_rows}
    ) AS rs
    LEFT JOIN ts_survivor_map AS ts_remap
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
    resolved_session_id = resolved_id_expr(
        "candidate_member.trace_session_id",
        "candidate_member_remap",
    )
    session_member_rows = """
        SELECT *
        FROM (
            SELECT *
            FROM spans FINAL
            PREWHERE project_id = toUUID(%(project_id)s)
              AND start_time >= %(snapshot_scan_start_date)s
              AND start_time < %(snapshot_scan_end_date)s
        ) AS snapshot_members
        WHERE snapshot_members.start_time >= %(snapshot_start_date)s
          AND snapshot_members.start_time < %(snapshot_end_date)s
          AND snapshot_members.is_deleted = 0
    """
    member_map_ctes = _finite_survivor_map_ctes(
        remap_table="trace_session_id_remap",
        candidate_relation="candidate_member_session_ids",
        candidate_column="physical_session_id",
        prefix="candidate_member_session_remap",
        map_name="candidate_member_ts_survivor_map",
    )
    return (
        f"""
        WITH
        candidate_members AS (
            SELECT *
            FROM (
                {session_member_rows}
            ) AS candidate_member
            WHERE {candidate_clause}
        ),
        candidate_member_session_ids AS (
            SELECT DISTINCT trace_session_id AS physical_session_id
            FROM candidate_members
            WHERE isNotNull(trace_session_id)
              AND trace_session_id !=
                  toUUID('00000000-0000-0000-0000-000000000000')
        ),
        {member_map_ctes}
        SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id
        FROM candidate_members AS candidate_member
        LEFT JOIN candidate_member_ts_survivor_map AS candidate_member_remap
          ON candidate_member.trace_session_id = candidate_member_remap.any_id
        WHERE {resolved_session_id} IN (
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
    end_user_map_ctes = _finite_survivor_map_ctes(
        remap_table="end_user_id_remap",
        candidate_relation="candidate_physical_end_user_ids",
        candidate_column="physical_end_user_id",
        prefix="candidate_end_user_remap",
        map_name="eu_survivor_map",
    )
    session_map_ctes = _finite_survivor_map_ctes(
        remap_table="trace_session_id_remap",
        candidate_relation="candidate_user_session_ids",
        candidate_column="physical_session_id",
        prefix="candidate_user_session_remap",
        map_name="ts_survivor_map",
    )
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
    candidate_physical_end_user_ids AS (
        SELECT DISTINCT
            candidate_rs.end_user_id AS physical_end_user_id
        FROM spans AS candidate_rs FINAL
        PREWHERE candidate_rs.project_id = toUUID(%(project_id)s)
          AND candidate_rs.start_time >= %(snapshot_start_date)s
          AND candidate_rs.start_time < %(snapshot_end_date)s
        WHERE candidate_rs.is_deleted = 0
          AND isNotNull(candidate_rs.end_user_id)
          AND {candidate_trace_clause}
    ),
    {end_user_map_ctes},
    candidate_users AS (
        SELECT DISTINCT
            {resolved_id_expr("physical_end_user_id", "candidate_eu_remap")}
                AS end_user_id
        FROM candidate_end_user_remap_candidate_ids AS candidate_end_user_ids
        LEFT JOIN eu_survivor_map AS candidate_eu_remap
          ON physical_end_user_id = candidate_eu_remap.any_id
    ),
    candidate_physical_users AS (
        SELECT physical_end_user_id
        FROM candidate_end_user_remap_candidate_ids
        UNION DISTINCT
        SELECT any_id AS physical_end_user_id
        FROM eu_survivor_map
        WHERE survivor_id IN (SELECT end_user_id FROM candidate_users)
    ),
    candidate_user_spans AS (
        SELECT *
        FROM spans FINAL
        PREWHERE project_id = toUUID(%(project_id)s)
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
        WHERE is_deleted = 0
          AND isNotNull(end_user_id)
          AND end_user_id IN (
              SELECT physical_end_user_id FROM candidate_physical_users
          )
          AND {span_predicate}
    ),
    candidate_user_session_ids AS (
        SELECT DISTINCT trace_session_id AS physical_session_id
        FROM candidate_user_spans
        WHERE isNotNull(trace_session_id)
          AND trace_session_id !=
              toUUID('00000000-0000-0000-0000-000000000000')
    ),
    {session_map_ctes},
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
        FROM candidate_user_spans AS rs
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
          AND eu.end_user_id IN (
              SELECT physical_end_user_id FROM candidate_physical_users
          )
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


def _active_user_dimension_membership_sql() -> str:
    """Select curated live users without rebuilding full user aggregates.

    Date-only user graphs do not need total-cost/session/eval membership. The
    graph's ``latest_spans`` and ``eu_survivor_map`` CTEs already hold the
    finite request population, so joining the active dimension here preserves
    the curated-user contract without a second spans scan or session remap.
    """

    resolved_dimension_user = resolved_id_expr(
        "dimension_user.end_user_id",
        "dimension_user_remap",
    )
    return f"""
        SELECT DISTINCT {resolved_dimension_user} AS end_user_id
        FROM end_users AS dimension_user FINAL
        LEFT JOIN eu_survivor_map AS dimension_user_remap
          ON dimension_user.end_user_id = dimension_user_remap.any_id
        WHERE dimension_user.project_id = toUUID(%(project_id)s)
          AND dimension_user.is_deleted = 0
          AND notEmpty(dimension_user.user_id)
    """


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
    resolved_user_id = resolved_id_expr(
        "candidate_member.end_user_id",
        "candidate_member_remap",
    )
    member_map_ctes = _finite_survivor_map_ctes(
        remap_table="end_user_id_remap",
        candidate_relation="candidate_member_end_user_ids",
        candidate_column="physical_end_user_id",
        prefix="candidate_member_end_user_remap",
        map_name="candidate_member_eu_survivor_map",
    )
    return (
        f"""
        WITH
        candidate_members AS (
            SELECT *
            FROM spans AS candidate_member FINAL
            PREWHERE candidate_member.project_id = toUUID(%(project_id)s)
              AND candidate_member.start_time >= %(snapshot_start_date)s
              AND candidate_member.start_time < %(snapshot_end_date)s
            WHERE candidate_member.is_deleted = 0
              AND isNotNull(candidate_member.end_user_id)
              AND {candidate_clause}
        ),
        candidate_member_end_user_ids AS (
            SELECT DISTINCT end_user_id AS physical_end_user_id
            FROM candidate_members
        ),
        {member_map_ctes}
        SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id
        FROM candidate_members AS candidate_member
        LEFT JOIN candidate_member_eu_survivor_map AS candidate_member_remap
          ON candidate_member.end_user_id = candidate_member_remap.any_id
        WHERE {resolved_user_id} IN (
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
        return _finalize_exact_graph_payload(
            {
                "metric_name": metric_id,
                "data": formatted.get(metric_key, []),
                **_metadata(
                    started=started,
                    query_count=0,
                    rows_returned=0,
                ),
            },
            started=started,
        )

    has_entity_filter = any(not _is_user_date_filter(item) for item in filters)
    if has_entity_filter:
        user_membership_sql, user_membership_params, _needs_eval = (
            _user_id_membership_sql(
                project_id=str(project_id),
                filters=filters,
                start_date=start_date,
                end_date=end_date,
                # UserTimeSeriesQueryBuilderV2 defines this request-window CTE.
                # The membership selector hydrates users owning one of those
                # candidates.
                candidate_trace_ids_sql=(
                    "SELECT toString(trace_id) FROM candidate_trace_ids"
                ),
            )
        )
    else:
        user_membership_sql = _active_user_dimension_membership_sql()
        user_membership_params = {}
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
    result = _execute_direct_exact_graph_query(
        analytics=analytics,
        query=query,
        params=params,
        started=started,
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
    return _finalize_exact_graph_payload(
        {
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
        },
        started=started,
    )


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
        return _finalize_exact_graph_payload(
            {
                "metric_name": metric_id,
                "data": [],
                **_metadata(
                    started=started,
                    query_count=0,
                    rows_returned=0,
                ),
            },
            started=started,
        )
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
    result = _execute_direct_exact_graph_query(
        analytics=analytics,
        query=query,
        params=query_params,
        started=started,
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
    return _finalize_exact_graph_payload(
        {
            "metric_name": metric_id,
            "data": points,
            **_metadata(
                started=started,
                query_count=1,
                rows_returned=len(rows),
            ),
        },
        started=started,
    )


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
