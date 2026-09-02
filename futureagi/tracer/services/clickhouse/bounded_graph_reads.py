"""Finite CH25 candidate reads and in-process graph aggregation.

Filtered Observe graphs must not materialize a tenant/window-wide ``IN
(SELECT ...)`` set. This module reuses the list endpoint's selective-anchor /
ordered-prefix protocol. Results within the finite graph ceiling remain exact;
larger result sets use a bounded time-stratified sample that is always marked
incomplete. Budget or query failures never become an allegedly exact graph.
"""

from __future__ import annotations

import json
from collections.abc import Hashable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

import structlog
from django.conf import settings

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

# The shared selector performs at most 24 finite 200-ID seed/classify batches.
# Keep one sentinel below that 4,800-row mechanical ceiling: results through
# 4,096 can be proven exhaustive, while row 4,097 proves a bounded degraded
# sample without ever constructing a tenant-wide Set.
GRAPH_CANDIDATE_LIMIT = 4_096
# A root-only trace classifier intentionally hydrates complete presentation
# rows in batches of 50 (the production-safe memory ceiling). Asking the
# shared selector for 4,096 rows would require 83 minimum queries including
# the sentinel seed, above its hard 48-query request contract, so even a
# one-trace equality filter would fail before ClickHouse was queried. Keep at
# most 32 classifier batches: a 50-row classifier therefore has an exact
# 1,599-row ceiling, while smaller structured classifiers scale the ceiling
# down by the same formula. Directly-indexable 512-row classifiers retain the
# 4,096 graph ceiling.
GRAPH_TRACE_CLASSIFY_BATCH_BUDGET = 32
GRAPH_TRACE_ROOT_CANDIDATE_LIMIT = (50 * GRAPH_TRACE_CLASSIFY_BATCH_BUDGET) - 1
GRAPH_CANDIDATE_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
GRAPH_DECORATION_CANDIDATE_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
GRAPH_MAX_POINTS = 10_000
GRAPH_ANY_SPAN_STRATA = 8
# Indexed trace candidates still require one or more ClickHouse decoration
# phases. Four disjoint quarters keep temporal coverage explicit while bounding
# that candidate work; unindexed trace micro-samples and span candidates retain
# eight strata.
GRAPH_TRACE_STRATA = 4
GRAPH_ANY_SPAN_ROWS_PER_STRATUM = 49
# A long-window trace graph is already an explicitly incomplete temporal sample.
# Three visible traces plus one sentinel in each of four disjoint strata preserves
# full-window temporal coverage while bounding the raw union at 16 identities.
# The union therefore needs at most four five-ID full-window classifiers, and
# at most 12 proven traces enter final child-span/eval/annotation decoration.
# Span graphs aggregate in process and retain the established 49-row ceiling.
GRAPH_TRACE_ROWS_PER_STRATUM = 3
# Trace decoration accepts at most forty identities. Unindexed discovery may
# inspect the finite raw ceiling below in each stratum, but only the newest five
# exact matches from each of eight strata may cross the candidate boundary.
# This preserves temporal coverage instead of letting the later global guard
# collapse an honest eight-stratum sample into the newest forty rows.
GRAPH_TRACE_DISTRIBUTED_RESULT_LIMIT = 40
# Unindexed trace acquisition is only a candidate sample: at most five exact
# matches per stratum can become visible. Read five raw roots plus one sentinel:
# the sixth row proves sampling instead of being silently trimmed from an exact
# stratum. The subsequent full-window replay then stays inside a worst-case
# 32-query endpoint contract even when one optional wide probe fails, every
# five-minute seed needs its one-minute retry, and decoration uses five reads.
GRAPH_TRACE_ACQUISITION_ROWS_PER_STRATUM = 5
# A long-window sparse-anchor sentinel distinguishes a common predicate before
# the ordered stratum reads begin. Common predicates deliberately switch to a
# small representative ceiling: replaying 512 identities in each of eight
# strata consumed the whole graph deadline in production before the first
# stratum completed. Span discovery retains forty-nine candidates; trace
# discovery applies the smaller resource-skew ceiling above before its
# full-window latest-state replay.
GRAPH_ANY_SPAN_DISTRIBUTED_AFTER = timedelta(hours=1)
# Unindexed structured predicates are never evaluated over an entire long
# stratum. Sample one fixed tail slice from each temporal stratum, then apply
# JSON/call_type semantics only to the finite latest-state candidates. This is
# intentionally incomplete and is always published as sampled metadata.
GRAPH_UNINDEXED_SAMPLE_SLICE = timedelta(minutes=5)
# A very dense five-minute tail can still cross the server-enforced byte-read
# ceiling before the finite seed is returned. Retry the same required stratum
# once with a one-minute tail under the original monotonic request deadline.
# The graph remains explicitly sampled; a second failure is never counted as
# temporal coverage and therefore cannot become a renderable partial graph.
GRAPH_UNINDEXED_SAMPLE_RETRY_SLICE = timedelta(minutes=1)
# A typed-Map key witness is optional discovery, not graph membership. Share a
# 100 ms wall across every long-window stratum so an old unindexed part cannot
# consume the request wall before the deterministic five-minute sample
# and exact finite classifier run. Fast indexed parts can still improve the
# sample; every timeout falls back to the established temporal lane.
GRAPH_TRACE_KEY_WITNESS_TOTAL_TIMEOUT_MS = 100
GRAPH_TRACE_KEY_WITNESS_QUERY_TIMEOUT_MS = 100

# Keep every graph-union classifier inside the same finite resource envelope as
# the shared bounded selector. Production readback under the retired 512 MiB
# profile showed four 20-trace chunks at 206-294 MiB followed by one skewed
# chunk that crossed that old ceiling. The approved 36 GiB envelope removes
# the obsolete cap; five-ID chunks still bound per-query result work. Together with
# the six-candidate per-stratum sentinel above, one failed wide probe, eight
# seeds, eight possible narrow retries, and five decoration reads fit the
# 32-query endpoint ceiling. Four-stratum indexed acquisition keeps its four
# classifiers so every finite 16-candidate sentinel can be checked. The
# expensive eight-stratum unindexed lane crosses a stricter two-classifier,
# deterministic 10-ID boundary; ten retains at least one candidate from every
# one of its eight non-empty strata.
# Relational classifiers can include one candidate-scoped ``Score FINAL``
# read. Give that finite <=5-trace batch enough time to finish while the shared
# request deadline below still clamps every later batch and remains the
# authoritative wall for the complete graph.
GRAPH_TRACE_UNION_QUERY_TIMEOUT_MS = 1_500
GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE = 5
GRAPH_TRACE_UNION_CLASSIFY_QUERY_BUDGET = 4
GRAPH_TRACE_UNION_CANDIDATE_LIMIT = (
    GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE * GRAPH_TRACE_UNION_CLASSIFY_QUERY_BUDGET
)
GRAPH_TRACE_UNINDEXED_UNION_CLASSIFY_QUERY_BUDGET = 2
GRAPH_TRACE_UNINDEXED_UNION_CANDIDATE_LIMIT = (
    GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
    * GRAPH_TRACE_UNINDEXED_UNION_CLASSIFY_QUERY_BUDGET
)
GRAPH_TRACE_UNION_MAX_QUERY_COUNT = 32
GRAPH_TRACE_UNION_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "read_overflow_mode": "throw",
    "max_result_rows": GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE,
    "result_overflow_mode": "throw",
}

logger = structlog.get_logger(__name__)


class BoundedGraphReadError(RuntimeError):
    """A sanitized graph-read failure safe to map into an API error code."""

    def __init__(self, error_code: str, *, retryable: bool = False):
        self.error_code = error_code
        # ``query_failed`` is intentionally the stable public graph metadata for
        # both an unavailable transport and a private reducer invariant.  Keep
        # the transport provenance out-of-band so HTTP boundaries can retry the
        # former (503) without misclassifying the latter (500).
        self.retryable = retryable
        super().__init__(error_code)


@dataclass(frozen=True)
class GraphCandidateSample:
    rows: tuple[dict[str, Any], ...]
    query_complete: bool
    query_status: str
    query_error_code: str | None
    window_start: datetime
    window_end: datetime
    elapsed_ms: float
    query_count: int
    rows_returned: int
    result_payload_bytes: int
    total_rows_lower_bound: int
    sampling_strategy: str | None = None
    sampling_strata: int = 0
    sampling_strata_completed: int = 0

    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "query_complete": self.query_complete,
            "query_status": self.query_status,
            "query_window_start": self.window_start.isoformat(),
            "query_window_end": self.window_end.isoformat(),
            "query_sample_size": len(self.rows),
            "query_count": self.query_count,
            "query_elapsed_ms": round(self.elapsed_ms, 3),
            "query_rows_returned": self.rows_returned,
            "query_result_bytes": self.result_payload_bytes,
            "query_total_rows_lower_bound": self.total_rows_lower_bound,
            "query_sampled": self.query_status == "sampled",
        }
        if self.sampling_strategy:
            result["query_sampling_strategy"] = self.sampling_strategy
            result["query_sampling_strata"] = self.sampling_strata
            result["query_sampling_strata_completed"] = self.sampling_strata_completed
        if self.query_error_code:
            result["query_error_code"] = self.query_error_code
        return result


@dataclass(frozen=True)
class _DeferredTraceStratum:
    """Finite graph-only candidates awaiting full-window classification."""

    builder: Any
    candidate_rows: tuple[dict[str, Any], ...]


def _active_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in filters
        if (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        or BaseQueryBuilder.is_datetime_complement_filter(item)
    ]


def _identity_seed_filter(observe_type: str) -> dict[str, Any]:
    return {
        "column_id": "trace_id" if observe_type == "trace" else "id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "is_not_null",
            "filter_value": None,
        },
    }


def _incomplete_error_code(error_code: str | None) -> str:
    """Map internal selector reasons onto the public graph error contract."""

    if error_code in {"deadline_exceeded", "read_budget_exceeded"}:
        return "read_budget_exceeded"
    return "sample_limit"


def _filters_for_window(
    filters: list[dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """Replace every time predicate with one canonical half-open stratum.

    ``read_graph_candidates`` has already intersected the request's datetime
    predicates into ``window_start``/``window_end``.  Keeping an original
    ``greater_than``/``less_than`` operator while replacing its scalar value
    with a two-value range makes the bounded builder reject an otherwise valid
    long-window request.  Remove all original time leaves and append the exact
    stratum as ``between`` so every advertised datetime form follows the same
    finite distributed-read path.
    """

    # Positive time leaves are replaced by the exact stratum. Complements are
    # residual predicates and must survive every stratum; dropping them would
    # make a long-window graph disagree with the corresponding list.
    narrowed = deepcopy(_active_filters(filters))
    narrowed.append(
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [window_start.isoformat(), window_end.isoformat()],
            },
        }
    )
    return narrowed


def _candidate_row_key(
    row: dict[str, Any], *, key_field: str
) -> tuple[datetime, Hashable]:
    start_time = row.get("start_time")
    if not isinstance(start_time, datetime):
        start_time = datetime.min
    elif start_time.tzinfo is not None:
        start_time = start_time.replace(tzinfo=None)
    if key_field == "id":
        return start_time, (
            str(row.get("id") or ""),
            str(row.get("trace_id") or ""),
        )
    return start_time, str(row.get(key_field) or "")


def _result_payload_bytes(rows: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _classify_deferred_trace_strata(
    *,
    analytics: Any,
    strata: list[_DeferredTraceStratum],
    distributed_started: float,
    deadline_ms: int,
    acquisition_query_count: int,
    candidate_rows_per_stratum: int,
    visible_rows_per_stratum: int,
) -> tuple[dict[Hashable, dict[str, Any]], float, int, int, int, int, bool]:
    """Classify a de-duplicated trace union in bounded full-window chunks.

    Every input row is still only an untrusted anchor/root seed. This helper is
    the sole boundary that turns those finite identities into graph candidates.
    A trace is never divided by time or filter leaf: each chunk reuses the trace
    list builder's complete latest-state, multi-filter query. Results stay local
    until every chunk succeeds, then each stratum's visible ceiling is reapplied.
    """

    union_by_id: dict[str, dict[str, Any]] = {}
    for stratum in strata:
        for row in stratum.candidate_rows:
            trace_id = str(row.get("trace_id") or "")
            if trace_id:
                union_by_id.setdefault(trace_id, row)
    if not union_by_id:
        return {}, 0.0, 0, 0, 0, 0, False

    absolute_ceiling = len(strata) * (candidate_rows_per_stratum + 1)
    if len(union_by_id) > absolute_ceiling:
        raise AssertionError("trace graph union exceeds its finite identity ceiling")

    unindexed_temporal_union = len(strata) >= GRAPH_ANY_SPAN_STRATA
    classifier_query_budget = (
        GRAPH_TRACE_UNINDEXED_UNION_CLASSIFY_QUERY_BUDGET
        if unindexed_temporal_union
        else GRAPH_TRACE_UNION_CLASSIFY_QUERY_BUDGET
    )
    classifier_candidate_limit = (
        GRAPH_TRACE_UNINDEXED_UNION_CANDIDATE_LIMIT
        if unindexed_temporal_union
        else GRAPH_TRACE_UNION_CANDIDATE_LIMIT
    )
    union_sampled = len(union_by_id) > classifier_candidate_limit
    if union_sampled:
        # The surrounding graph is already an explicitly incomplete temporal
        # sample. Interleave newest candidates from every non-empty stratum so
        # the full-window classifier has a fixed lane-specific ceiling without
        # collapsing the result onto the newest part of the request window.
        ordered_strata: list[list[dict[str, Any]]] = []
        for stratum in strata:
            stratum_by_id: dict[str, dict[str, Any]] = {}
            for row in stratum.candidate_rows:
                trace_id = str(row.get("trace_id") or "")
                if trace_id:
                    stratum_by_id.setdefault(trace_id, row)
            ordered_strata.append(
                sorted(
                    stratum_by_id.values(),
                    key=lambda row: _candidate_row_key(
                        row,
                        key_field="trace_id",
                    ),
                    reverse=True,
                )
            )

        bounded_union_by_id: dict[str, dict[str, Any]] = {}
        max_stratum_depth = max((len(rows) for rows in ordered_strata), default=0)
        for depth in range(max_stratum_depth):
            for stratum_rows in ordered_strata:
                if depth >= len(stratum_rows):
                    continue
                row = stratum_rows[depth]
                trace_id = str(row.get("trace_id") or "")
                if trace_id:
                    bounded_union_by_id.setdefault(trace_id, row)
                if len(bounded_union_by_id) >= classifier_candidate_limit:
                    break
            if len(bounded_union_by_id) >= classifier_candidate_limit:
                break
        union_by_id = bounded_union_by_id

    union_rows = list(union_by_id.values())
    classifier_query_count = (
        len(union_rows) + GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE - 1
    ) // GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
    if classifier_query_count > classifier_query_budget:
        raise AssertionError("trace graph union exceeds its classifier budget")
    if (
        acquisition_query_count + classifier_query_count
        > GRAPH_TRACE_UNION_MAX_QUERY_COUNT
    ):
        raise AssertionError("trace graph union exceeds its finite query ceiling")

    classifier_builder = strata[0].builder
    classified_by_id: dict[str, dict[str, Any]] = {}
    classifier_elapsed_ms = 0.0
    classifier_rows_returned = 0
    classifier_payload_bytes = 0
    executed_query_count = 0
    for batch_offset in range(
        0,
        len(union_rows),
        GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE,
    ):
        candidate_batch = union_rows[
            batch_offset : batch_offset + GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE
        ]
        remaining_ms = deadline_ms - int((monotonic() - distributed_started) * 1000)
        if remaining_ms < 25:
            raise BoundedGraphReadError("read_budget_exceeded")
        classifier_query, classifier_params = (
            classifier_builder.build_filter_match_query_from_seed_rows(
                candidate_batch,
                # Eval/task selection needs a physical witness for each
                # any-span leaf. Graph membership consumes only the proven
                # trace/root identity, so avoid duplicate argMinIf work over
                # every classified child span.
                include_filter_witnesses=False,
            )
        )
        if not classifier_query:  # pragma: no cover - guarded by non-empty IDs
            continue
        classifier_started = monotonic()
        try:
            classifier_result = analytics.execute_ch_query(
                classifier_query,
                classifier_params,
                timeout_ms=min(GRAPH_TRACE_UNION_QUERY_TIMEOUT_MS, remaining_ms),
                settings={
                    **GRAPH_TRACE_UNION_READ_SETTINGS,
                    "max_result_rows": len(candidate_batch),
                },
            )
        except Exception as exc:
            if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                raise
            logger.warning(
                "trace graph union classifier degraded",
                batch_index=(batch_offset // GRAPH_TRACE_UNION_CLASSIFY_BATCH_SIZE),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            public_code = (
                "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
            )
            # No classified rows have crossed this helper's return boundary, so
            # a late chunk failure remains atomic.
            raise BoundedGraphReadError(public_code, retryable=True) from None

        classifier_elapsed_ms += (monotonic() - classifier_started) * 1000
        classified_rows = list(classifier_result.data or [])
        candidate_ids = {str(row.get("trace_id") or "") for row in candidate_batch}
        for row in classified_rows:
            trace_id = str(row.get("trace_id") or "")
            if trace_id and trace_id not in candidate_ids:
                raise AssertionError(
                    "trace classifier returned an unrequested identity"
                )
            if trace_id:
                classified_by_id[trace_id] = row
        executed_query_count += 1
        classifier_rows_returned += len(classified_rows)
        classifier_payload_bytes += _result_payload_bytes(classified_rows)

    visible_by_id: dict[Hashable, dict[str, Any]] = {}
    total_rows_lower_bound = 0
    for stratum in strata:
        stratum_ids = {
            str(row.get("trace_id") or "")
            for row in stratum.candidate_rows
            if row.get("trace_id")
        }
        stratum_matches = sorted(
            (
                classified_by_id[trace_id]
                for trace_id in stratum_ids
                if trace_id in classified_by_id
            ),
            key=lambda row: _candidate_row_key(row, key_field="trace_id"),
            reverse=True,
        )
        total_rows_lower_bound += len(stratum_matches)
        for row in stratum_matches[:visible_rows_per_stratum]:
            trace_id = str(row.get("trace_id") or "")
            if trace_id:
                visible_by_id[trace_id] = row

    return (
        visible_by_id,
        classifier_elapsed_ms,
        executed_query_count,
        classifier_rows_returned,
        classifier_payload_bytes,
        total_rows_lower_bound,
        union_sampled,
    )


def _read_time_distributed_candidates(
    *,
    analytics: Any,
    builder_class: type,
    project_id: str,
    filters: list[dict[str, Any]],
    mode: str,
    window_start: datetime,
    window_end: datetime,
    deadline_ms: int,
    classify_batch_size: int,
    stratum_ceiling: int = GRAPH_ANY_SPAN_STRATA,
    rows_per_stratum: int = GRAPH_ANY_SPAN_ROWS_PER_STRATUM,
    synthetic_time_only_seed: bool = False,
) -> GraphCandidateSample:
    """Read arbitrary child-span filters across bounded full-window strata.

    Trace attributes may live on any child span.  A single newest-first scan
    can consume its budget in the latest dense slice and show no older shape.
    Disjoint time strata keep the work finite and temporally distributed.
    Indexed trace graphs use four quarters so candidate work leaves time for
    required decoration. Unindexed trace micro-samples retain eight slices,
    as do span graphs that aggregate candidates in process. A stratum is marked
    complete only when its seed was exhausted, so the combined graph can never
    advertise a sample as exact.
    """

    if not 1 <= rows_per_stratum <= GRAPH_ANY_SPAN_ROWS_PER_STRATUM:
        raise ValueError("graph rows_per_stratum exceeds the bounded contract")
    if not 1 <= stratum_ceiling <= GRAPH_ANY_SPAN_STRATA:
        raise ValueError("graph stratum ceiling exceeds the bounded contract")
    stratum_count = min(
        stratum_ceiling,
        max(1, deadline_ms // 250),
    )
    acquisition_rows_per_stratum = rows_per_stratum
    visible_rows_per_stratum = rows_per_stratum
    if mode == "trace":
        acquisition_rows_per_stratum = min(
            rows_per_stratum,
            GRAPH_TRACE_ACQUISITION_ROWS_PER_STRATUM,
        )
        visible_rows_per_stratum = min(
            acquisition_rows_per_stratum,
            max(1, GRAPH_TRACE_DISTRIBUTED_RESULT_LIMIT // stratum_count),
        )
    distributed_started = monotonic()
    window_width = window_end - window_start
    key_field = "trace_id" if mode == "trace" else "id"
    rows_by_id: dict[Hashable, dict[str, Any]] = {}
    complete = True
    elapsed_ms = 0.0
    query_count = 0
    rows_returned = 0
    result_payload_bytes = 0
    total_rows_lower_bound = 0
    sampling_strata_completed = 0
    sampling_error_code: str | None = None
    probe_limits_enforced = bool(
        getattr(analytics, "supports_per_query_read_settings", True)
    )
    # Freeze the outer request window into an explicit positive time leaf.
    # When the caller omits a date filter, each builder otherwise derives its
    # own ``now - 30 days`` default a few microseconds apart.  Passing the raw
    # filters as the membership window can then make membership_start newer
    # than the first stratum_start and fail the containment guard before any
    # ClickHouse query runs.  Complements remain intact via
    # ``_filters_for_window`` while every stratum shares these exact bounds.
    membership_filters = _filters_for_window(
        filters,
        window_start=window_start,
        window_end=window_end,
    )
    force_temporal_sample = False
    graph_key_witness_elapsed_ms = 0.0
    # Every trace path acquires all disjoint strata first, then replays the
    # de-duplicated finite union in <=5-ID full-window chunks. This includes
    # the locked-executor five-minute/one-minute temporal lane: classifying 50
    # roots independently in every stratum made the narrower retry issue the
    # same 14-day query again and reproduce Code 307. Raw anchors/root seeds
    # never enter ``rows_by_id`` directly.
    defer_trace_classification = mode == "trace"
    deferred_trace_strata: list[_DeferredTraceStratum] = []

    for index in range(stratum_count):
        remaining_ms = deadline_ms - int((monotonic() - distributed_started) * 1000)
        if remaining_ms < 25:
            complete = False
            sampling_error_code = "read_budget_exceeded"
            break
        stratum_start = window_start + (window_width * index / stratum_count)
        stratum_end = (
            window_end
            if index == stratum_count - 1
            else window_start + (window_width * (index + 1) / stratum_count)
        )
        stratum_filters = _filters_for_window(
            filters,
            window_start=stratum_start,
            window_end=stratum_end,
        )
        stratum_builder_kwargs: dict[str, Any] = {
            "project_id": project_id,
            "page_number": 0,
            "page_size": acquisition_rows_per_stratum,
            "filters": stratum_filters,
        }
        if mode == "trace":
            stratum_builder_kwargs["bounded_identity_only"] = True
            # The stratum constrains root seed/order only. Classification must
            # replay each finite trace across the original request window so a
            # root in one stratum can match children in another.
            stratum_builder_kwargs["bounded_membership_filters"] = membership_filters
        else:
            # Span anchor probes are an explicit graph opt-in.  Long-window
            # strata use their finite sentinel instead of sorting every match
            # in the stratum just to obtain the first 49 candidate identities.
            stratum_builder_kwargs["bounded_anchor_probe"] = True
        stratum_builder = builder_class(**stratum_builder_kwargs)
        unindexed_sample_support = getattr(
            stratum_builder,
            "requires_unindexed_graph_sample_slice",
            None,
        )
        requires_temporal_sample = bool(
            callable(unindexed_sample_support) and unindexed_sample_support()
        )
        anchor_support = getattr(stratum_builder, "supports_filter_anchor_probe", None)
        builder_anchor_supported = bool(callable(anchor_support) and anchor_support())
        graph_key_support = getattr(
            stratum_builder,
            "supports_graph_key_witness_probe",
            None,
        )
        graph_key_supported = bool(callable(graph_key_support) and graph_key_support())
        if builder_anchor_supported and not probe_limits_enforced:
            # A locked executor strips the anchor's tight timeout/read caps.
            # The selector would silently suppress that speculative probe and
            # enter the full-stratum ordered seed, which reproduced the US
            # production Code 158 failure. Route it to the same explicit
            # five-minute temporal sample as an unselective predicate instead.
            requires_temporal_sample = True
        use_stratum_anchor = (
            not force_temporal_sample
            and not requires_temporal_sample
            and builder_anchor_supported
            and probe_limits_enforced
        )
        # Unselective positive typed-Map values first discover candidates by
        # key presence across the complete stratum. This avoids both the broad
        # value comparison and the false-empty five-minute tail observed in
        # production. The finite sentinel/classifier contract remains graph-
        # only; resource failure falls back to the existing temporal sample.
        graph_key_witness_remaining_ms = GRAPH_TRACE_KEY_WITNESS_TOTAL_TIMEOUT_MS - int(
            graph_key_witness_elapsed_ms
        )
        graph_key_witness_budget_available = graph_key_witness_remaining_ms >= 25
        if (
            not force_temporal_sample
            and requires_temporal_sample
            and graph_key_supported
            and probe_limits_enforced
            and not graph_key_witness_budget_available
        ):
            # The optional witness has spent its complete shared allowance.
            # Route this and every older stratum directly to the intentional
            # temporal sample instead of launching another broad statement.
            force_temporal_sample = True
        use_graph_key_witness = bool(
            not force_temporal_sample
            and requires_temporal_sample
            and graph_key_supported
            and probe_limits_enforced
            and graph_key_witness_budget_available
        )
        if mode == "trace" and (use_stratum_anchor or use_graph_key_witness):
            defer_trace_classification = True
        temporal_sample = (
            not synthetic_time_only_seed
            and (force_temporal_sample or requires_temporal_sample)
            and not use_graph_key_witness
            and stratum_end - stratum_start > GRAPH_UNINDEXED_SAMPLE_SLICE
        )
        if temporal_sample:
            # A full-stratum ORDER BY with a predicate ClickHouse cannot prune
            # can still scan tens of hours before returning zero rows. Restrict
            # the ordered seed to one fixed five-minute tail slice per stratum.
            # The trace classifier retains the complete request membership
            # window; the result remains explicitly sampled in every case.
            sample_start = stratum_end - GRAPH_UNINDEXED_SAMPLE_SLICE
            stratum_filters = _filters_for_window(
                filters,
                window_start=sample_start,
                window_end=stratum_end,
            )
            stratum_builder_kwargs["filters"] = stratum_filters
            stratum_builder = builder_class(**stratum_builder_kwargs)
        # One extra identity is the finite has-more sentinel. Keeping the
        # stratum working set at the caller-specific sample ceiling avoids the
        # oversized classifier that exceeded the production graph deadline.
        candidate_limit = acquisition_rows_per_stratum + 1
        max_seed_attempts = (
            acquisition_rows_per_stratum + 1 + candidate_limit - 1
        ) // candidate_limit
        bounded_classify_batch_size = min(
            classify_batch_size,
            candidate_limit,
        )
        classifiers_per_seed = (
            candidate_limit + bounded_classify_batch_size - 1
        ) // bounded_classify_batch_size
        max_query_count = max_seed_attempts * (1 + classifiers_per_seed)

        def read_page(
            *,
            active_builder,
            active_filters,
            use_anchor: bool,
            active_remaining_ms: int,
            seed_attempts: int,
            query_limit: int,
            candidate_count: int,
            classify_size: int,
            defer_classify: bool,
            graph_key_witness: bool = False,
            query_timeout_ms: int | None = None,
        ):
            return read_bounded_filter_page(
                builder=active_builder,
                analytics=analytics,
                filters=active_filters,
                key_field=key_field,
                page_number=0,
                page_size=acquisition_rows_per_stratum,
                # Share one monotonic deadline across the complete stratified
                # read instead of assigning one eighth up front. Per-query
                # caps in the selector still bound a slow ClickHouse read, but
                # a healthy classifier may use the otherwise-idle budget from
                # adjacent strata.
                deadline_ms=active_remaining_ms,
                max_seed_attempts=seed_attempts,
                max_query_count=query_limit,
                # The visible rows plus one has-more sentinel stay finite. A
                # sparse/unattested path retains the caller-specific
                # representative ceiling.
                max_candidates=candidate_count,
                classify_batch_size=classify_size,
                include_incomplete_rows=True,
                # An indexed graph stratum is one disjoint piece of the frozen
                # request window. Its extra raw identity is therefore a local
                # sample sentinel: classify that finite set and never enter the
                # ORDER BY seed path for directly anchorable predicates.
                anchor_probe_only=use_anchor,
                anchor_probe_limit=(candidate_count if use_anchor else None),
                defer_classification=defer_classify,
                graph_key_witness_probe=graph_key_witness,
                query_timeout_ms=query_timeout_ms,
            )

        page = None
        anchor_failure: Exception | str | None = None
        graph_key_failure: Exception | str | None = None
        try:
            page = read_page(
                active_builder=stratum_builder,
                active_filters=stratum_filters,
                use_anchor=(use_stratum_anchor or use_graph_key_witness),
                active_remaining_ms=remaining_ms,
                seed_attempts=max_seed_attempts,
                query_limit=max_query_count,
                candidate_count=candidate_limit,
                classify_size=bounded_classify_batch_size,
                defer_classify=defer_trace_classification,
                graph_key_witness=use_graph_key_witness,
                query_timeout_ms=(
                    min(
                        GRAPH_TRACE_KEY_WITNESS_QUERY_TIMEOUT_MS,
                        graph_key_witness_remaining_ms,
                    )
                    if use_graph_key_witness
                    else None
                ),
            )
        except Exception as exc:
            if use_stratum_anchor and is_read_budget_error(exc):
                anchor_failure = exc
            elif use_graph_key_witness and is_read_budget_error(exc):
                graph_key_failure = exc
            else:
                # Compiler/programming defects are not degradable. They must reach
                # the API boundary, where the generic 500 contract hides private
                # SQL details. Only typed resource and transport failures may be
                # represented by stable graph error metadata here.
                if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                    raise
                logger.warning(
                    "graph candidate stratum degraded",
                    stratum_index=index,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                public_code = (
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                )
                raise BoundedGraphReadError(public_code, retryable=True) from None

        if use_graph_key_witness and page is not None:
            graph_key_witness_elapsed_ms += page.elapsed_ms

        if (
            page is not None
            and mode == "span"
            and use_stratum_anchor
            and page.error_code == "sample_limit"
            and not page.rows
        ):
            # A full raw anchor sentinel can consist entirely of stale physical
            # versions. The latest-state classifier must discard those rows,
            # but an empty classified sentinel is not evidence that the
            # stratum has no live matches. Preserve the cheap probe accounting
            # and use the existing bounded ordered seed/classifier path for
            # this stratum only. This closes the false-empty case without a
            # broad scan or weakening the shared monotonic deadline.
            elapsed_ms += page.elapsed_ms
            query_count += page.query_count
            rows_returned += page.rows_returned
            result_payload_bytes += page.result_payload_bytes
            total_rows_lower_bound += page.total_rows_lower_bound
            logger.info(
                "graph span anchor contained no live matches; using ordered fallback",
                stratum_index=index,
            )
            use_stratum_anchor = False
            remaining_ms = deadline_ms - int((monotonic() - distributed_started) * 1000)
            if remaining_ms < 25:
                complete = False
                sampling_error_code = "read_budget_exceeded"
                break
            try:
                page = read_page(
                    active_builder=stratum_builder,
                    active_filters=stratum_filters,
                    use_anchor=False,
                    active_remaining_ms=remaining_ms,
                    seed_attempts=max_seed_attempts,
                    query_limit=max_query_count,
                    candidate_count=candidate_limit,
                    classify_size=bounded_classify_batch_size,
                    defer_classify=False,
                )
            except Exception as exc:
                if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                    raise
                logger.warning(
                    "graph candidate ordered fallback degraded",
                    stratum_index=index,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                public_code = (
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                )
                raise BoundedGraphReadError(public_code, retryable=True) from None

        if (
            page is not None
            and use_stratum_anchor
            and page.error_code == "read_budget_exceeded"
            and any(
                getattr(attempt, "kind", None) == "anchor"
                and getattr(attempt, "error_code", None) == "read_budget_exceeded"
                for attempt in page.attempts
            )
        ):
            anchor_failure = "read_budget_exceeded"

        if (
            page is not None
            and use_graph_key_witness
            and page.error_code == "read_budget_exceeded"
        ):
            graph_key_failure = "read_budget_exceeded"

        if anchor_failure is not None:
            # An index may be declared but not materialized on old parts, or a
            # positive key can be common while its value is rare. A 159/241/307
            # anchor failure therefore says nothing about graph membership.
            # Preserve its accounting, then switch this and every remaining
            # stratum to the fixed five-minute lane under the same wall deadline.
            if page is not None:
                elapsed_ms += page.elapsed_ms
                query_count += page.query_count
                rows_returned += page.rows_returned
                result_payload_bytes += page.result_payload_bytes
                total_rows_lower_bound += page.total_rows_lower_bound
            logger.warning(
                "graph candidate anchor exceeded budget; using temporal sample",
                stratum_index=index,
                error_type=(
                    type(anchor_failure).__name__
                    if isinstance(anchor_failure, Exception)
                    else str(anchor_failure)
                ),
                exc_info=isinstance(anchor_failure, Exception),
            )
            force_temporal_sample = True
            use_stratum_anchor = False
            temporal_sample = stratum_end - stratum_start > GRAPH_UNINDEXED_SAMPLE_SLICE
            sample_start = (
                stratum_end - GRAPH_UNINDEXED_SAMPLE_SLICE
                if temporal_sample
                else stratum_start
            )
            stratum_filters = _filters_for_window(
                filters,
                window_start=sample_start,
                window_end=stratum_end,
            )
            stratum_builder_kwargs["filters"] = stratum_filters
            stratum_builder = builder_class(**stratum_builder_kwargs)
            remaining_ms = deadline_ms - int((monotonic() - distributed_started) * 1000)
            if remaining_ms < 25:
                complete = False
                sampling_error_code = "read_budget_exceeded"
                break
            try:
                page = read_page(
                    active_builder=stratum_builder,
                    active_filters=stratum_filters,
                    use_anchor=False,
                    active_remaining_ms=remaining_ms,
                    seed_attempts=max_seed_attempts,
                    query_limit=max_query_count,
                    candidate_count=candidate_limit,
                    classify_size=bounded_classify_batch_size,
                    defer_classify=defer_trace_classification,
                )
            except Exception as exc:
                if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                    raise
                logger.warning(
                    "graph candidate temporal fallback degraded",
                    stratum_index=index,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                public_code = (
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                )
                raise BoundedGraphReadError(public_code, retryable=True) from None

        if graph_key_failure is not None:
            # A full-stratum key probe is optional candidate discovery. If old
            # parts cannot prune it within the read ceiling, preserve its
            # accounting and retry the same stratum through the established
            # five-minute raw sample (and one-minute dense retry below).
            if page is not None:
                elapsed_ms += page.elapsed_ms
                query_count += page.query_count
                rows_returned += page.rows_returned
                result_payload_bytes += page.result_payload_bytes
                total_rows_lower_bound += page.total_rows_lower_bound
            logger.warning(
                "graph key witness exceeded budget; using temporal sample",
                stratum_index=index,
                error_type=(
                    type(graph_key_failure).__name__
                    if isinstance(graph_key_failure, Exception)
                    else str(graph_key_failure)
                ),
                exc_info=isinstance(graph_key_failure, Exception),
            )
            force_temporal_sample = True
            use_graph_key_witness = False
            temporal_sample = stratum_end - stratum_start > GRAPH_UNINDEXED_SAMPLE_SLICE
            sample_start = (
                stratum_end - GRAPH_UNINDEXED_SAMPLE_SLICE
                if temporal_sample
                else stratum_start
            )
            stratum_filters = _filters_for_window(
                filters,
                window_start=sample_start,
                window_end=stratum_end,
            )
            stratum_builder_kwargs["filters"] = stratum_filters
            stratum_builder = builder_class(**stratum_builder_kwargs)
            remaining_ms = deadline_ms - int((monotonic() - distributed_started) * 1000)
            if remaining_ms < 25:
                complete = False
                sampling_error_code = "read_budget_exceeded"
                break
            try:
                page = read_page(
                    active_builder=stratum_builder,
                    active_filters=stratum_filters,
                    use_anchor=False,
                    active_remaining_ms=remaining_ms,
                    seed_attempts=max_seed_attempts,
                    query_limit=max_query_count,
                    candidate_count=candidate_limit,
                    classify_size=bounded_classify_batch_size,
                    defer_classify=defer_trace_classification,
                )
            except Exception as exc:
                if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
                    raise
                logger.warning(
                    "graph key witness temporal fallback degraded",
                    stratum_index=index,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                public_code = (
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                )
                raise BoundedGraphReadError(public_code, retryable=True) from None

        if (
            page is not None
            and temporal_sample
            and not page.complete
            and not page.has_more
            and _incomplete_error_code(page.error_code) == "read_budget_exceeded"
        ):
            # The fixed five-minute tail is already an explicitly incomplete
            # representation of this long stratum. On unusually dense parts it
            # can still hit the server's 307/159/241 read ceiling. Preserve the
            # failed attempt's accounting and retry the *same* stratum with a
            # deterministic one-minute tail. We do not skip to another stratum
            # or count this one complete unless the retry is classified.
            retry_start = max(
                stratum_start,
                stratum_end - GRAPH_UNINDEXED_SAMPLE_RETRY_SLICE,
            )
            if retry_start > sample_start:
                remaining_ms = deadline_ms - int(
                    (monotonic() - distributed_started) * 1000
                )
                if remaining_ms >= 25:
                    elapsed_ms += page.elapsed_ms
                    query_count += page.query_count
                    rows_returned += page.rows_returned
                    result_payload_bytes += page.result_payload_bytes
                    total_rows_lower_bound += page.total_rows_lower_bound
                    retry_filters = _filters_for_window(
                        filters,
                        window_start=retry_start,
                        window_end=stratum_end,
                    )
                    stratum_builder_kwargs["filters"] = retry_filters
                    stratum_builder = builder_class(**stratum_builder_kwargs)
                    logger.warning(
                        "graph temporal sample exceeded budget; retrying narrower slice",
                        stratum_index=index,
                    )
                    try:
                        page = read_page(
                            active_builder=stratum_builder,
                            active_filters=retry_filters,
                            use_anchor=False,
                            active_remaining_ms=remaining_ms,
                            seed_attempts=max_seed_attempts,
                            query_limit=max_query_count,
                            candidate_count=candidate_limit,
                            classify_size=bounded_classify_batch_size,
                            defer_classify=defer_trace_classification,
                        )
                    except Exception as exc:
                        if not (
                            is_read_budget_error(exc) or is_clickhouse_query_error(exc)
                        ):
                            raise
                        logger.warning(
                            "graph narrower temporal sample degraded",
                            stratum_index=index,
                            error_type=type(exc).__name__,
                            exc_info=True,
                        )
                        public_code = (
                            "read_budget_exceeded"
                            if is_read_budget_error(exc)
                            else "query_failed"
                        )
                        raise BoundedGraphReadError(
                            public_code,
                            retryable=True,
                        ) from None

        if page is None:  # pragma: no cover - defensive exhaustiveness
            raise BoundedGraphReadError("query_failed")

        elapsed_ms += page.elapsed_ms
        query_count += page.query_count
        rows_returned += page.rows_returned
        result_payload_bytes += page.result_payload_bytes
        total_rows_lower_bound += page.total_rows_lower_bound
        public_code = None
        if not page.complete or page.has_more:
            complete = False
            public_code = (
                "sample_limit"
                if page.has_more
                else _incomplete_error_code(page.error_code)
            )
            if public_code != "sample_limit":
                sampling_error_code = public_code
        elif temporal_sample:
            # Exhausting the bounded micro-slice proves only that slice, never
            # the surrounding long stratum.
            complete = False
            public_code = "sample_limit"
        # A resource/transport failure is not temporal coverage. Only an
        # exhausted page or a bounded candidate/sample-limit response proves
        # that this stratum was actually classified. This prevents eight
        # failed reads from being advertised as an intentional sample.
        if page.complete or page.has_more or public_code == "sample_limit":
            sampling_strata_completed += 1
        if page.classification_deferred:
            deferred_trace_strata.append(
                _DeferredTraceStratum(
                    builder=stratum_builder,
                    candidate_rows=page.deferred_candidate_rows,
                )
            )
        visible_page_rows = page.rows
        if mode == "trace":
            visible_page_rows = sorted(
                page.rows,
                key=lambda row: _candidate_row_key(row, key_field=key_field),
                reverse=True,
            )[:visible_rows_per_stratum]
        for row in visible_page_rows:
            if mode == "trace":
                identity: Hashable = str(row.get("trace_id") or "")
            else:
                identity = stratum_builder.bounded_filter_row_identity(row)
            identity_is_valid = (
                all(value not in (None, "") for value in identity)
                if isinstance(identity, tuple)
                else bool(identity)
            )
            if identity_is_valid:
                rows_by_id[identity] = row

    # Only the latest-state classifier may publish trace rows. One bounded union
    # replay preserves cross-stratum root/child and multi-leaf semantics while
    # removing the prior eight repeated large full-window classifier scans.
    if deferred_trace_strata:
        (
            classified_rows_by_id,
            classifier_elapsed_ms,
            classifier_query_count,
            classifier_rows_returned,
            classifier_payload_bytes,
            classifier_total_rows_lower_bound,
            classifier_union_sampled,
        ) = _classify_deferred_trace_strata(
            analytics=analytics,
            strata=deferred_trace_strata,
            distributed_started=distributed_started,
            deadline_ms=deadline_ms,
            acquisition_query_count=query_count,
            candidate_rows_per_stratum=acquisition_rows_per_stratum,
            visible_rows_per_stratum=visible_rows_per_stratum,
        )
        rows_by_id.update(classified_rows_by_id)
        elapsed_ms += classifier_elapsed_ms
        query_count += classifier_query_count
        rows_returned += classifier_rows_returned
        result_payload_bytes += classifier_payload_bytes
        total_rows_lower_bound += classifier_total_rows_lower_bound
        if classifier_union_sampled:
            complete = False
            if sampling_error_code is None:
                sampling_error_code = "sample_limit"

    rows = sorted(
        rows_by_id.values(),
        key=lambda row: _candidate_row_key(row, key_field=key_field),
        reverse=True,
    )
    full_strata_coverage = sampling_strata_completed == stratum_count
    error_code: str | None = None
    if not complete:
        error_code = sampling_error_code or "sample_limit"
    query_status = (
        "complete" if complete else "sampled" if full_strata_coverage else "degraded"
    )
    return GraphCandidateSample(
        rows=tuple(rows),
        query_complete=complete,
        query_status=query_status,
        query_error_code=error_code,
        window_start=window_start,
        window_end=window_end,
        elapsed_ms=elapsed_ms,
        query_count=query_count,
        rows_returned=rows_returned,
        result_payload_bytes=result_payload_bytes,
        total_rows_lower_bound=max(len(rows), total_rows_lower_bound),
        sampling_strategy=(None if complete else "time_stratified_latest_state"),
        sampling_strata=stratum_count if not complete else 0,
        sampling_strata_completed=(sampling_strata_completed if not complete else 0),
    )


def read_graph_candidates(
    *,
    analytics: Any,
    project_id: str,
    filters: list[dict[str, Any]],
    observe_type: str,
    deadline_ms: int = GRAPH_CANDIDATE_DEADLINE_MS,
    allow_time_only_seed: bool = False,
) -> GraphCandidateSample:
    """Return an exact finite set or an explicitly incomplete graph sample.

    ``allow_time_only_seed`` lets chart callers prove project/window ownership
    with a finite identity predicate when no user row filter exists.  Long
    windows still read one capped page per temporal stratum, but the synthetic
    ``is_not_null`` leaf must not be mistaken for an unindexed user predicate
    and reduced to a five-minute micro-slice.
    """

    mode = str(observe_type or "").strip().lower()
    if mode not in {"trace", "span"}:
        raise ValueError("observe_type must be trace or span")
    if deadline_ms <= 0:
        raise ValueError("deadline_ms must be positive")

    effective_filters = list(filters or [])
    synthetic_time_only_seed = False
    if not _active_filters(effective_filters):
        if not allow_time_only_seed:
            raise ValueError("a bounded graph candidate read needs a row filter")
        effective_filters.append(_identity_seed_filter(mode))
        synthetic_time_only_seed = True

    builder_class = (
        TraceListQueryBuilderV2 if mode == "trace" else SpanListQueryBuilderV2
    )
    builder_kwargs: dict[str, Any] = {
        "project_id": str(project_id),
        "page_number": 0,
        "page_size": GRAPH_CANDIDATE_LIMIT,
        "filters": effective_filters,
    }
    # Trace graph decoration performs its own finite metric replay after the
    # trace set is proven. Candidate discovery therefore needs identities and
    # root order only, avoiding needless presentation-column hydration.
    if mode == "trace":
        builder_kwargs["bounded_identity_only"] = True
    else:
        builder_kwargs["bounded_anchor_probe"] = True
    builder = builder_class(
        **builder_kwargs,
    )
    if not builder.supports_bounded_filter_scan():
        error_code = builder.bounded_filter_degraded_error_code()
        if error_code == "unsupported_filter_shape":
            raise UnsupportedFilterShapeError(
                "The filter cannot be evaluated by the bounded graph reader"
            )
        raise BoundedGraphReadError(error_code or "unsupported_filter_shape")

    window_start, window_end = builder.parse_time_range(effective_filters)
    classify_batch_size = builder.recommended_filter_classify_batch_size()
    if window_end - window_start > GRAPH_ANY_SPAN_DISTRIBUTED_AFTER:
        probe_limits_enforced = bool(
            getattr(analytics, "supports_per_query_read_settings", True)
        )
        anchor_support = getattr(builder, "supports_filter_anchor_probe", None)
        unindexed_sample_support = getattr(
            builder,
            "requires_unindexed_graph_sample_slice",
            None,
        )
        indexed_trace_sample = (
            mode == "trace"
            and probe_limits_enforced
            and callable(anchor_support)
            and bool(anchor_support())
            and not (
                callable(unindexed_sample_support) and bool(unindexed_sample_support())
            )
        )
        return _read_time_distributed_candidates(
            analytics=analytics,
            builder_class=builder_class,
            project_id=str(project_id),
            filters=effective_filters,
            mode=mode,
            window_start=window_start,
            window_end=window_end,
            deadline_ms=deadline_ms,
            classify_batch_size=int(classify_batch_size or 50),
            stratum_ceiling=(
                GRAPH_TRACE_STRATA if indexed_trace_sample else GRAPH_ANY_SPAN_STRATA
            ),
            rows_per_stratum=(
                GRAPH_TRACE_ROWS_PER_STRATUM
                if indexed_trace_sample
                else GRAPH_ANY_SPAN_ROWS_PER_STRATUM
            ),
            synthetic_time_only_seed=synthetic_time_only_seed,
        )

    candidate_limit = GRAPH_CANDIDATE_LIMIT
    if mode == "trace":
        trace_classify_batch_size = int(classify_batch_size or 50)
        candidate_limit = min(
            GRAPH_CANDIDATE_LIMIT,
            (trace_classify_batch_size * GRAPH_TRACE_CLASSIFY_BATCH_BUDGET) - 1,
        )
        if candidate_limit != GRAPH_CANDIDATE_LIMIT:
            builder = builder_class(
                project_id=str(project_id),
                page_number=0,
                page_size=candidate_limit,
                filters=effective_filters,
                bounded_identity_only=True,
            )

    try:
        page = read_bounded_filter_page(
            builder=builder,
            analytics=analytics,
            filters=effective_filters,
            key_field="trace_id" if mode == "trace" else "id",
            page_number=0,
            page_size=candidate_limit,
            deadline_ms=deadline_ms,
            # Graph page zero may render proven candidate rows only when its
            # metadata remains explicitly incomplete. Numbered list and eval
            # task callers retain the selector default (False), so this does
            # not weaken their exactness contract.
            include_incomplete_rows=True,
        )
    except BoundedGraphReadError:
        raise
    except Exception as exc:
        if not (is_read_budget_error(exc) or is_clickhouse_query_error(exc)):
            raise
        logger.warning(
            "graph candidate read degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        public_code = (
            "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
        )
        raise BoundedGraphReadError(public_code, retryable=True) from None
    if not page.complete:
        error_code = _incomplete_error_code(page.error_code)
        if error_code != "sample_limit" or not page.rows:
            raise BoundedGraphReadError(error_code)
        return GraphCandidateSample(
            rows=tuple(page.rows),
            query_complete=False,
            query_status="sampled",
            query_error_code=error_code,
            window_start=window_start,
            window_end=window_end,
            elapsed_ms=page.elapsed_ms,
            query_count=page.query_count,
            rows_returned=page.rows_returned,
            result_payload_bytes=page.result_payload_bytes,
            total_rows_lower_bound=page.total_rows_lower_bound,
            sampling_strategy="bounded_latest_state_prefix",
            sampling_strata=1,
            sampling_strata_completed=1,
        )

    # Every supported filter shape, including structured overflow arrays/maps,
    # is replayed against latest state for only the finite seed candidates.
    # Therefore an exhausted scan is exact; only this short-window path's
    # cardinality sentinel makes the visible prefix incomplete.
    sampled = page.has_more
    return GraphCandidateSample(
        rows=tuple(page.rows),
        query_complete=not sampled,
        query_status="sampled" if sampled else "complete",
        query_error_code="sample_limit" if sampled else None,
        window_start=window_start,
        window_end=window_end,
        elapsed_ms=page.elapsed_ms,
        query_count=page.query_count,
        rows_returned=page.rows_returned,
        result_payload_bytes=page.result_payload_bytes,
        total_rows_lower_bound=page.total_rows_lower_bound,
        sampling_strategy="bounded_latest_state_prefix" if sampled else None,
        sampling_strata=1 if sampled else 0,
        sampling_strata_completed=1 if sampled else 0,
    )


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _metric_value(metric_id: str, state: dict[str, Any]) -> float:
    if metric_id == "traffic":
        return float(state["traffic"])
    if metric_id in {"tokens", "total_tokens"}:
        return state["total_tokens"]
    if metric_id in {"prompt_tokens", "input_tokens"}:
        return state["prompt_tokens"]
    if metric_id in {"completion_tokens", "output_tokens"}:
        return state["completion_tokens"]
    if metric_id == "cost":
        return state["cost_sum"] / max(state["cost_count"], 1)
    if metric_id == "error_rate":
        return (state["error_count"] * 100.0) / max(state["traffic"], 1)
    return state["latency_sum"] / max(state["latency_count"], 1)


def aggregate_system_candidate_graph(
    sample: GraphCandidateSample,
    *,
    metric_id: str,
    interval: str,
) -> dict[str, Any]:
    """Aggregate finite latest-state rows without another ClickHouse scan.

    Exact and explicitly sampled candidates use the same reducer. The response
    metadata remains authoritative: sampled values are never labelled exact.
    """

    if not sample.query_complete and sample.query_status != "sampled":
        return {
            "metric_name": metric_id,
            "data": [],
            **sample.metadata(),
        }

    if sample.window_start >= sample.window_end:
        return {
            "metric_name": metric_id,
            "data": [],
            **sample.metadata(),
        }

    buckets: dict[datetime, dict[str, Any]] = {}
    for row in sample.rows:
        timestamp = row.get("start_time")
        if not isinstance(timestamp, datetime):
            continue
        bucket = BaseQueryBuilder._normalize_timestamp(timestamp, interval)
        state = buckets.setdefault(
            bucket,
            {
                "traffic": 0,
                "latency_sum": 0.0,
                "latency_count": 0,
                "cost_sum": 0.0,
                "cost_count": 0,
                "total_tokens": 0.0,
                "prompt_tokens": 0.0,
                "completion_tokens": 0.0,
                "error_count": 0,
            },
        )
        state["traffic"] += 1
        latency = _numeric(row.get("latency_ms"))
        if latency is not None:
            state["latency_sum"] += latency
            state["latency_count"] += 1
        cost = _numeric(row.get("cost"))
        if cost is not None:
            state["cost_sum"] += cost
            state["cost_count"] += 1
        state["total_tokens"] += _numeric(row.get("total_tokens")) or 0.0
        state["prompt_tokens"] += _numeric(row.get("prompt_tokens")) or 0.0
        state["completion_tokens"] += _numeric(row.get("completion_tokens")) or 0.0
        if str(row.get("status") or "").upper() in {"ERROR", "ERRORED", "FAILED"}:
            state["error_count"] += 1

    timestamps = list(
        BaseQueryBuilder._generate_timestamp_range(
            sample.window_start,
            sample.window_end,
            interval,
        )
    )
    if len(timestamps) > GRAPH_MAX_POINTS:
        raise BoundedGraphReadError("sample_limit")

    normalized_metric = str(metric_id or "latency").strip().lower()
    data: list[dict[str, Any]] = []
    for timestamp in timestamps:
        state = buckets.get(timestamp)
        data.append(
            {
                "timestamp": timestamp.isoformat(),
                "value": round(_metric_value(normalized_metric, state), 9)
                if state
                else 0,
                "primary_traffic": state["traffic"] if state else 0,
            }
        )
    return {
        "metric_name": metric_id,
        "data": data,
        **sample.metadata(),
    }


__all__ = [
    "BoundedGraphReadError",
    "GRAPH_CANDIDATE_LIMIT",
    "GRAPH_CANDIDATE_DEADLINE_MS",
    "GRAPH_DECORATION_CANDIDATE_DEADLINE_MS",
    "GRAPH_MAX_POINTS",
    "GRAPH_TRACE_STRATA",
    "GraphCandidateSample",
    "aggregate_system_candidate_graph",
    "read_graph_candidates",
]
