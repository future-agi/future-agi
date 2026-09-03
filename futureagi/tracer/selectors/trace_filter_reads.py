"""Bounded, newest-first ClickHouse reads for filtered trace/span pages."""

from __future__ import annotations

import json
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from time import monotonic
from typing import Any, Protocol

from django.conf import settings

from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.read_budget import is_read_budget_error

_INITIAL_SLICE = timedelta(minutes=5)
_MAX_SLICE = timedelta(days=2)
_MAX_SEED_ATTEMPTS = 24
_MAX_CANDIDATES = 512
_ABSOLUTE_MAX_CANDIDATES = 512
_ABSOLUTE_MAX_QUERIES = 128
# HTTP list/graph reads intentionally retain the 128-statement contract above.
# Historical eval reconciliation runs as a heartbeating Temporal activity and
# may need to select a genuine 100k-row prefix. Its larger envelope is opt-in,
# still finite, and keeps every physical statement on the same 512-row,
# single-threaded, caller-capped ClickHouse limits. Reconciliation owns a
# three-hour activity timeout; this hard wall leaves ten minutes for buffered
# validation, materializer hand-off, heartbeats, and scheduler jitter.
_WORKFLOW_MAX_SEED_ATTEMPTS = 16_384
_WORKFLOW_MAX_QUERIES = 32_768
_WORKFLOW_MAX_DEADLINE_MS = 170 * 60 * 1000
_SELECTIVE_ANCHOR_SENTINEL = 513
_MAX_OPTIONAL_ANCHOR_STRATA = 4
# Keep one slow-but-bounded statement within the client deadline so the next
# seed/classifier can run. Production showed successful reads at 0.79-1.26 s
# under load, while constrained local qualification observed a valid 1.54 s
# read. The caller's wall deadline, query count, rows, bytes, memory, and
# single-thread settings remain the authoritative envelope. These aliases are
# retained for test and builder compatibility; Django settings own the values.
_QUERY_TIMEOUT_MS = settings.FILTER_SELECTOR_QUERY_TIMEOUT_MS
_MAX_OPT_IN_QUERY_TIMEOUT_MS = settings.FILTER_SELECTOR_MAX_OPT_IN_QUERY_TIMEOUT_MS
_MAX_BUILDER_RECOMMENDED_QUERY_TIMEOUT_MS = (
    settings.FILTER_SELECTOR_MAX_BUILDER_QUERY_TIMEOUT_MS
)
# Do not launch a resumable cursor statement with less than its full bounded
# statement envelope. A short-token final batch can time out even though an
# exact signed checkpoint already exists. Returning that checkpoint is
# deterministic and lets the next request resume without retrying or skipping
# any candidate.
_BOUNDED_CONTINUATION_MIN_QUERY_HEADROOM_MS = _QUERY_TIMEOUT_MS
_CANDIDATE_WITNESS_PREFILTER_TIMEOUT_MS = 250
_CANDIDATE_WITNESS_PREFILTER_MAX_BYTES = 96 * 1024 * 1024
_CANDIDATE_WITNESS_PREFILTER_STRATA = 8
_CANDIDATE_WITNESS_PREFILTER_MAX_ATTEMPTS = 32
_CANDIDATE_WITNESS_PREFILTER_TOTAL_MS = 2_000
_CANDIDATE_WITNESS_EXACT_RESERVE_MS = 1_000
_UNINDEXED_POSITIVE_MICRO_SEED_TIMEOUT_MS = 200
_UNINDEXED_POSITIVE_MICRO_SEED_MAX_BYTES = 96 * 1024 * 1024
_EXACT_ZERO_PROBE_TIMEOUT_MS = 1_500
_EXACT_ZERO_PROBE_MAX_BYTES = 256 * 1024 * 1024
_EXACT_ZERO_FALLBACK_RESERVE_MS = 1_000
# Trace/span list queries fetch one additional page-sized de-duplication
# margin; 5,000 is also the existing server-side result ceiling used by those
# endpoints.  Keeping one public ceiling makes numbered-page work finite for
# filtered selectors, unfiltered top-K reads, and session OFFSET reads alike.
MAX_NUMBERED_PAGE_WORK_ROWS = settings.FILTER_SELECTOR_MAX_NUMBERED_PAGE_WORK_ROWS
_READ_SETTINGS = {
    "max_threads": settings.FILTER_SELECTOR_MAX_THREADS,
    "max_block_size": settings.OBSERVABILITY_LIST_MAX_BLOCK_SIZE,
    "max_memory_usage": settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES,
    "max_bytes_to_read": settings.OBSERVABILITY_LIST_MAX_BYTES,
    "read_overflow_mode": "throw",
    "max_result_rows": _MAX_CANDIDATES,
    "result_overflow_mode": "throw",
}

PAGE_DEPTH_EXCEEDED_CODE = "page_depth_exceeded"
PAGE_DEPTH_EXCEEDED_MESSAGE = (
    "The requested page is beyond the supported numbered-page depth. "
    "Request an earlier page or narrow the filter time range."
)
CURSOR_REQUIRED_CODE = "cursor_required"
CURSOR_REQUIRED_MESSAGE = (
    "Long-range filtered lists require cursor pagination. "
    "Enable cursor mode or narrow the time range."
)


def long_filtered_read_requires_cursor(
    filters: list[dict[str, Any]],
    *,
    request_start: datetime,
    request_end: datetime,
    search: str | None = None,
) -> bool:
    """Return whether a filtered window needs a resumable cursor contract.

    Numbered pagination has no signed scan checkpoint.  Keep the legacy lane
    for time-only and short-window reads, but require cursor pagination before
    a user-supplied filter/search can start a scan spanning more than one hour.
    Internal builder predicates (for example the voice-root invariant) are not
    present in ``filters`` and therefore do not turn a time-only request into a
    filtered request.
    """

    has_non_time_filter = any(
        (item.get("column_id") or item.get("columnId"))
        not in {"created_at", "start_time"}
        for item in filters
    )
    return bool(
        (has_non_time_filter or search)
        and request_end - request_start > timedelta(hours=1)
    )


class FilterPageBuilder(Protocol):
    def parse_time_range(
        self, filters: list[dict[str, Any]]
    ) -> tuple[datetime, datetime]: ...

    def build_filter_seed_page(
        self,
        *,
        slice_start: datetime,
        slice_end: datetime,
        limit: int,
        before_start_time: datetime | None = None,
        before_id: Any = None,
    ) -> tuple[str, dict[str, Any]]: ...

    def build_filter_match_query(
        self, candidate_ids: list[str]
    ) -> tuple[str, dict[str, Any]]: ...


class QueryExecutor(Protocol):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult: ...


@dataclass(frozen=True)
class FilterReadAttempt:
    kind: str
    slice_start: datetime
    slice_end: datetime
    elapsed_ms: float
    rows_returned: int
    result_payload_bytes: int
    query_count: int = 1
    error_code: str | None = None


@dataclass(frozen=True)
class BoundedFilterPage:
    rows: list[dict[str, Any]]
    has_more: bool
    complete: bool
    status: str
    error_code: str | None
    total_rows_lower_bound: int
    elapsed_ms: float
    query_count: int
    rows_returned: int
    result_payload_bytes: int
    attempts: tuple[FilterReadAttempt, ...]
    # Graph-only two-phase reads may acquire a finite identity superset now and
    # classify the union once later. Keep raw seeds out of ``rows`` so a caller
    # can never mistake an unclassified anchor for a proven match.
    deferred_candidate_rows: tuple[dict[str, Any], ...] = ()
    classification_deferred: bool = False
    # A degraded cursor page may still contain individually classified rows.
    # These fields describe the first candidate position that was *not* fully
    # classified, so the next signed request can resume without rescanning an
    # already-published prefix or skipping unclassified candidates.
    continuation_slice_end: datetime | None = None
    continuation_slice_start: datetime | None = None
    continuation_before_start_time: datetime | None = None
    continuation_before_id: Any = None


@dataclass(frozen=True)
class BoundedFilterNeighbors:
    """Exact adjacent rows found through target-anchored bounded reads.

    ``newer`` and ``older`` follow the list's canonical descending order.  A
    complete result proves the target and both available neighbours without
    walking the list's global newest-first prefix. Failures carry only stable,
    sanitized error codes.
    """

    newer: dict[str, Any] | None
    current: dict[str, Any] | None
    older: dict[str, Any] | None
    complete: bool
    error_code: str | None
    query_count: int
    rows_scanned: int


class _BudgetExceeded(Exception):
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


def degraded_bounded_filter_page(error_code: str) -> BoundedFilterPage:
    """Return a sanitized incomplete page without issuing a database read."""

    return BoundedFilterPage(
        rows=[],
        has_more=False,
        complete=False,
        status="degraded",
        error_code=error_code,
        total_rows_lower_bound=0,
        elapsed_ms=0.0,
        query_count=0,
        rows_returned=0,
        result_payload_bytes=0,
        attempts=(),
    )


def numbered_page_depth_exceeded(
    *,
    page_number: int,
    page_size: int,
) -> bool:
    """Return whether numbered-page work exceeds the shared finite ceiling.

    Trace and span lists read a stable ordered prefix through the requested
    page plus one page-sized de-duplication margin. Session and filtered reads
    need no more work than that conservative bound, so one calculation safely
    gates every numbered-list path without changing membership below it.
    """

    if page_number < 0 or page_size <= 0:
        raise ValueError("page_number must be non-negative and page_size positive")
    return ((page_number + 2) * page_size) > MAX_NUMBERED_PAGE_WORK_ROWS


def bounded_numbered_page_depth_exceeded(
    *,
    page_number: int,
    page_size: int,
    max_seed_attempts: int = _MAX_SEED_ATTEMPTS,
    max_candidates: int = _MAX_CANDIDATES,
    max_query_count: int | None = None,
    classify_batch_size: int = 200,
    seed_batch_size: int = 200,
    reserved_query_count: int = 0,
    query_contract_limit: int = _ABSOLUTE_MAX_QUERIES,
) -> bool:
    """Return whether page N cannot fit inside the finite selector contract.

    This is a mechanical prefix-budget check only: it performs no database
    read and makes no claim that an accepted page will contain data.  Keeping
    the calculation public lets list transports reject unsupported deep
    numbered pages as a stable, non-retryable client error before ClickHouse is
    contacted.  Cursor/unbounded pagination is intentionally not implied.
    """

    if page_number < 0 or page_size <= 0:
        raise ValueError("page_number must be non-negative and page_size positive")
    if not 1 <= query_contract_limit <= _WORKFLOW_MAX_QUERIES:
        raise ValueError("query_contract_limit exceeds the hard read contract")
    if not 1 <= max_seed_attempts <= query_contract_limit:
        raise ValueError("max_seed_attempts exceeds the bounded read contract")
    if not 1 <= max_candidates <= _ABSOLUTE_MAX_CANDIDATES:
        raise ValueError("max_candidates exceeds the bounded read contract")
    if max_query_count is None:
        max_query_count = min(max_seed_attempts * 2, query_contract_limit)
    if not 1 <= max_query_count <= query_contract_limit:
        raise ValueError("max_query_count exceeds the bounded read contract")
    if not 0 <= reserved_query_count <= max_query_count:
        raise ValueError("reserved_query_count exceeds max_query_count")
    if not 1 <= classify_batch_size <= max_candidates:
        raise ValueError("classify_batch_size exceeds max_candidates")
    if not 1 <= seed_batch_size <= max_candidates:
        raise ValueError("seed_batch_size exceeds max_candidates")
    prefix_needed = ((page_number + 1) * page_size) + 1
    candidate_limit = min(max_candidates, max(seed_batch_size, prefix_needed))
    minimum_query_count = (
        reserved_query_count
        + ceil(prefix_needed / candidate_limit)
        + ceil(prefix_needed / classify_batch_size)
    )
    return (
        prefix_needed > max_seed_attempts * max_candidates
        or minimum_query_count > max_query_count
    )


def _without_timezone(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _result_payload_bytes(rows: list[dict[str, Any]]) -> int:
    return len(
        json.dumps(rows, default=str, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def read_bounded_filter_page(
    *,
    builder: FilterPageBuilder,
    analytics: QueryExecutor,
    filters: list[dict[str, Any]],
    key_field: str,
    page_number: int,
    page_size: int,
    deadline_ms: int = 1_800,
    max_seed_attempts: int = _MAX_SEED_ATTEMPTS,
    max_candidates: int = _MAX_CANDIDATES,
    max_query_count: int | None = None,
    classify_batch_size: int | None = None,
    retry_wide_read_budget: bool = False,
    include_incomplete_rows: bool = False,
    cursor_start_time: datetime | None = None,
    cursor_order_token: Any = None,
    continuation_slice_start: datetime | None = None,
    continuation_slice_end: datetime | None = None,
    continuation_before_start_time: datetime | None = None,
    continuation_before_id: Any = None,
    bounded_continuation: bool = False,
    carry_continuation_slice_width: bool = False,
    read_settings: dict[str, Any] | None = None,
    classify_read_settings: dict[str, Any] | None = None,
    anchor_probe_only: bool = False,
    anchor_probe_limit: int | None = None,
    defer_classification: bool = False,
    graph_key_witness_probe: bool = False,
    workflow_exact: bool = False,
    query_timeout_ms: int | None = None,
) -> BoundedFilterPage:
    """Return one exact numbered page or an explicit sanitized degradation.

    Seed reads cover adjacent half-open time slices in descending order. Each
    seed is only an identity/order superset; every ID is reclassified against
    global latest state before it can enter the page. A failed read is never
    hidden as a complete response; even the legacy opt-in retry path remains
    explicitly degraded. A partial prefix is never exposed as page N.
    Graph callers may opt into proven-but-incomplete page-zero rows; numbered
    list/eval callers retain the exact/empty default.
    ``anchor_probe_only`` stops after a selective-anchor sentinel instead of
    entering the ordered seed loop; it is reserved for callers that provide a
    separate bounded fallback for non-sparse values.
    ``anchor_probe_limit`` lowers that sentinel for a caller whose surrounding
    protocol already partitions the complete request window. It is graph-only
    in practice: short-window numbered pages retain the 513-row sparse/common
    proof, while long-window trace builders may opt directly into ordered root
    batches. A graph stratum can classify its visible rows plus one finite
    sentinel without sorting the stratum's full match set.
    ``defer_classification`` is an internal graph-only acquisition contract. It
    returns finite seeds in ``deferred_candidate_rows`` and always leaves public
    ``rows`` empty; the caller must replay the union through the same builder's
    latest-state classifier before exposing any result.
    ``graph_key_witness_probe`` swaps the optional value predicate for a
    graph-only typed-Map key-presence superset. It is valid only with the
    finite page-zero anchor sentinel; exact list/eval callers never enable it.
    ``carry_continuation_slice_width`` preserves successful adaptive widening
    across empty cursor responses. Without it, each HTTP continuation starts
    again at the builder's initial slice width, so a sparse year-long filter
    can require thousands of otherwise successful transport pages.
    """

    if page_number < 0 or page_size <= 0 or deadline_ms <= 0:
        raise ValueError("page_number, page_size and deadline_ms must be positive")
    query_timeout_recommendation = getattr(
        builder, "recommended_filter_query_timeout_ms", None
    )
    raw_recommended_query_timeout_ms = (
        query_timeout_recommendation()
        if callable(query_timeout_recommendation)
        else None
    )
    recommended_query_timeout_ms = (
        int(raw_recommended_query_timeout_ms)
        if raw_recommended_query_timeout_ms is not None
        else None
    )
    if recommended_query_timeout_ms is not None and not (
        25 <= recommended_query_timeout_ms <= _MAX_BUILDER_RECOMMENDED_QUERY_TIMEOUT_MS
    ):
        raise ValueError("recommended query timeout exceeds the bounded read contract")
    if query_timeout_ms is None:
        query_timeout_ms = recommended_query_timeout_ms or _QUERY_TIMEOUT_MS
    max_query_timeout_ms = (
        _MAX_BUILDER_RECOMMENDED_QUERY_TIMEOUT_MS
        if recommended_query_timeout_ms is not None
        else _MAX_OPT_IN_QUERY_TIMEOUT_MS
    )
    if not 25 <= query_timeout_ms <= max_query_timeout_ms:
        raise ValueError("query_timeout_ms exceeds the bounded read contract")
    query_contract_limit = (
        _WORKFLOW_MAX_QUERIES if workflow_exact else _ABSOLUTE_MAX_QUERIES
    )
    seed_contract_limit = (
        _WORKFLOW_MAX_SEED_ATTEMPTS if workflow_exact else _ABSOLUTE_MAX_QUERIES
    )
    if workflow_exact:
        if (
            page_number != 0
            or cursor_start_time is not None
            or continuation_slice_end is not None
            or include_incomplete_rows
            or bounded_continuation
            or anchor_probe_only
            or anchor_probe_limit is not None
            or defer_classification
            or graph_key_witness_probe
        ):
            raise ValueError(
                "workflow exact reads require one fully buffered page-zero proof"
            )
        if deadline_ms > _WORKFLOW_MAX_DEADLINE_MS:
            raise ValueError("workflow exact deadline exceeds the hard read contract")
    if (cursor_start_time is None) != (cursor_order_token is None):
        raise ValueError("cursor order values must be provided together")
    if (continuation_before_start_time is None) != (continuation_before_id is None):
        raise ValueError("continuation seed values must be provided together")
    if continuation_slice_end is None and continuation_before_start_time is not None:
        raise ValueError("continuation seed requires a slice end")
    if continuation_slice_start is not None and continuation_slice_end is None:
        raise ValueError("continuation slice start requires a slice end")
    if cursor_start_time is not None and page_number != 0:
        raise ValueError("cursor reads must use page_number zero")
    if continuation_slice_end is not None and page_number != 0:
        raise ValueError("continuation reads must use page_number zero")
    if bounded_continuation and (not include_incomplete_rows or page_number != 0):
        raise ValueError(
            "bounded continuation requires page-zero classified partial rows"
        )
    if carry_continuation_slice_width and not bounded_continuation:
        raise ValueError("continuation slice width carry requires bounded continuation")
    if include_incomplete_rows and page_number != 0:
        raise ValueError("incomplete rows are available only for page zero")
    if defer_classification and (page_number != 0 or not include_incomplete_rows):
        raise ValueError(
            "deferred classification requires graph page-zero incomplete rows"
        )
    if not 1 <= max_seed_attempts <= seed_contract_limit:
        raise ValueError("max_seed_attempts exceeds the bounded read contract")
    if not 1 <= max_candidates <= _ABSOLUTE_MAX_CANDIDATES:
        raise ValueError("max_candidates exceeds the bounded read contract")
    if anchor_probe_limit is not None:
        if not anchor_probe_only:
            raise ValueError("anchor_probe_limit requires anchor_probe_only")
        if not 2 <= anchor_probe_limit <= max_candidates:
            raise ValueError("anchor_probe_limit exceeds max_candidates")
        if page_size >= anchor_probe_limit:
            raise ValueError("anchor_probe_limit must include a page sentinel")
    if graph_key_witness_probe and (
        not anchor_probe_only
        or anchor_probe_limit is None
        or page_number != 0
        or cursor_start_time is not None
        or not include_incomplete_rows
    ):
        raise ValueError(
            "graph key witness requires a finite graph page-zero anchor probe"
        )
    if defer_classification:
        bounded_anchor_acquisition = (
            anchor_probe_only and anchor_probe_limit is not None
        )
        bounded_fallback_acquisition = (
            not anchor_probe_only
            and max_seed_attempts == 1
            and max_candidates <= 50
            and page_size < max_candidates
        )
        if not (bounded_anchor_acquisition or bounded_fallback_acquisition):
            raise ValueError(
                "deferred classification requires one bounded graph acquisition"
            )
    if max_query_count is None:
        query_count_recommendation = getattr(
            builder, "recommended_filter_max_query_count", None
        )
        raw_query_count_recommendation = (
            query_count_recommendation()
            if callable(query_count_recommendation)
            else None
        )
        if raw_query_count_recommendation is None:
            max_query_count = min(max_seed_attempts * 2, query_contract_limit)
        else:
            recommended_query_count = int(raw_query_count_recommendation)
            if recommended_query_count <= 0:
                raise ValueError("recommended query count must be positive")
            max_query_count = min(recommended_query_count, query_contract_limit)
    if not 1 <= max_query_count <= query_contract_limit:
        raise ValueError("max_query_count exceeds the bounded read contract")

    recommended_batch_size: int | None = None
    batch_recommendation = getattr(
        builder, "recommended_filter_classify_batch_size", None
    )
    if callable(batch_recommendation):
        raw_recommendation = batch_recommendation()
        if raw_recommendation is not None:
            recommended_batch_size = int(raw_recommendation)
            if recommended_batch_size <= 0:
                raise ValueError("recommended classify batch size must be positive")
            recommended_batch_size = min(recommended_batch_size, max_candidates)
    if classify_batch_size is None:
        classify_batch_size = recommended_batch_size or min(200, max_candidates)
    if not 1 <= classify_batch_size <= max_candidates:
        raise ValueError("classify_batch_size exceeds max_candidates")

    effective_classify_read_settings: dict[str, int] = {}
    classify_settings_recommendation = getattr(
        builder, "recommended_filter_classify_read_settings", None
    )
    if callable(classify_settings_recommendation):
        raw_classify_read_settings = classify_settings_recommendation()
        if raw_classify_read_settings is not None:
            if not isinstance(raw_classify_read_settings, dict):
                raise ValueError("recommended classify read settings must be a dict")
            unsupported_settings = set(raw_classify_read_settings) - {
                "max_block_size",
                "preferred_max_column_in_block_size_bytes",
            }
            if unsupported_settings:
                raise ValueError("unsupported recommended classify read setting")
            for setting_name, raw_value in raw_classify_read_settings.items():
                if isinstance(raw_value, bool):
                    raise ValueError(
                        "recommended classify read settings must be positive integers"
                    )
                setting_value = int(raw_value)
                if setting_value <= 0:
                    raise ValueError(
                        "recommended classify read settings must be positive integers"
                    )
                effective_classify_read_settings[setting_name] = setting_value
    if classify_read_settings is not None:
        if not isinstance(classify_read_settings, dict):
            raise ValueError("classify read settings must be a dict")
        unsupported_settings = set(classify_read_settings) - {
            "max_block_size",
            "preferred_max_column_in_block_size_bytes",
        }
        if unsupported_settings:
            raise ValueError("unsupported classify read setting")
        for setting_name, raw_value in classify_read_settings.items():
            if isinstance(raw_value, bool):
                raise ValueError("classify read settings must be positive integers")
            setting_value = int(raw_value)
            if setting_value <= 0:
                raise ValueError("classify read settings must be positive integers")
            previous_cap = effective_classify_read_settings.get(setting_name)
            effective_classify_read_settings[setting_name] = (
                min(previous_cap, setting_value)
                if previous_cap is not None
                else setting_value
            )

    started = monotonic()
    deadline = started + (deadline_ms / 1000)
    seed_order_proof = getattr(builder, "filter_seed_proves_result_order", None)
    seed_proves_result_order = (
        bool(seed_order_proof()) if callable(seed_order_proof) else True
    )
    population_bound_proof = getattr(
        builder,
        "filter_seed_proves_population_bound",
        None,
    )
    seed_proves_population_bound = (
        bool(population_bound_proof()) if callable(population_bound_proof) else False
    )
    if seed_proves_population_bound and (
        page_number != 0
        or cursor_start_time is not None
        or include_incomplete_rows
        or defer_classification
        or anchor_probe_only
        or anchor_probe_limit is not None
    ):
        raise ValueError(
            "population-bound seeds require one exact page-zero population proof"
        )
    request_start, request_end = builder.parse_time_range(filters)
    request_start = _without_timezone(request_start)
    request_end = _without_timezone(request_end)
    if continuation_slice_start is not None:
        continuation_slice_start = _without_timezone(continuation_slice_start)
    if continuation_slice_end is not None:
        continuation_slice_end = _without_timezone(continuation_slice_end)
        if not request_start < continuation_slice_end <= request_end:
            raise ValueError("continuation slice is outside the request window")
    if continuation_slice_start is not None and not (
        request_start
        <= continuation_slice_start
        < (continuation_slice_end or request_end)
    ):
        raise ValueError("continuation slice is outside the request window")
    if continuation_before_start_time is not None:
        continuation_before_start_time = _without_timezone(
            continuation_before_start_time
        )
        if (
            not (continuation_slice_start or request_start)
            <= continuation_before_start_time
            < (continuation_slice_end or request_end)
        ):
            raise ValueError("continuation seed is outside the request window")
    cursor_key: tuple[datetime, Any] | None = None
    if cursor_start_time is not None:
        cursor_key = (_without_timezone(cursor_start_time), cursor_order_token)

    identity_classification_capability = getattr(
        builder, "use_identity_only_filter_classification", None
    )
    identity_match_builder = getattr(
        builder, "build_filter_identity_match_query_from_seed_rows", None
    )
    page_hydration_builder = getattr(builder, "build_filter_page_hydration_query", None)
    candidate_witness_probe_builder = getattr(
        builder,
        "build_filter_candidate_witness_probe",
        None,
    )
    candidate_witness_probe_preference = getattr(
        builder,
        "prefer_filter_candidate_witness_probe_first",
        None,
    )
    candidate_witness_probe_strata_builder = getattr(
        builder,
        "recommended_filter_candidate_witness_probe_strata",
        None,
    )
    candidate_witness_fallback_batch_builder = getattr(
        builder,
        "recommended_filter_candidate_witness_fallback_classify_batch_size",
        None,
    )
    unhydrated_candidate_witness_capability = getattr(
        builder,
        "supports_filter_candidate_witness_prefilter_without_hydration",
        None,
    )
    unhydrated_buffered_identity_capability = getattr(
        builder,
        "use_buffered_identity_filter_classification_without_hydration",
        None,
    )
    hydration_identity_builder = getattr(
        builder, "bounded_filter_page_hydration_identity", None
    )
    hydration_reserve_builder = getattr(
        builder, "recommended_filter_page_hydration_reserve_ms", None
    )
    identity_only_classification = (
        not defer_classification
        # A resumable cursor page may publish a genuine classified prefix
        # before the whole request window is exhausted.  Keep using the
        # identity classifier for that path, then hydrate the public page
        # below.  Ordinary graph-only incomplete reads continue to classify
        # full presentation rows because they have no continuation contract.
        and (not include_incomplete_rows or bounded_continuation)
        and callable(identity_classification_capability)
        and bool(identity_classification_capability())
    )
    unhydrated_candidate_witness_prefilter = bool(
        not include_incomplete_rows
        and not defer_classification
        and callable(unhydrated_candidate_witness_capability)
        and unhydrated_candidate_witness_capability()
    )
    unhydrated_buffered_identity_classification = bool(
        not include_incomplete_rows
        and not defer_classification
        and callable(unhydrated_buffered_identity_capability)
        and unhydrated_buffered_identity_capability()
    )
    if (
        unhydrated_buffered_identity_classification
        and not unhydrated_candidate_witness_prefilter
    ):
        raise ValueError(
            "unhydrated identity buffering requires the bounded witness prefilter"
        )
    candidate_witness_prefilter_allowed = bool(
        identity_only_classification or unhydrated_candidate_witness_prefilter
    )
    candidate_witness_fallback_batch_size = classify_batch_size
    if (
        candidate_witness_prefilter_allowed
        and callable(candidate_witness_probe_preference)
        and candidate_witness_probe_preference()
        and callable(candidate_witness_fallback_batch_builder)
    ):
        raw_fallback_batch_size = candidate_witness_fallback_batch_builder()
        if raw_fallback_batch_size is not None:
            candidate_witness_fallback_batch_size = int(raw_fallback_batch_size)
            if not 1 <= candidate_witness_fallback_batch_size <= classify_batch_size:
                raise ValueError(
                    "candidate witness fallback batch size exceeds classifier batch"
                )
    if identity_only_classification and not (
        callable(identity_match_builder)
        and callable(page_hydration_builder)
        and callable(hydration_identity_builder)
        and callable(hydration_reserve_builder)
    ):
        raise ValueError(
            "identity-only filter classification requires a hydration query"
        )
    reserved_hydration_queries = 1 if identity_only_classification else 0
    reserved_hydration_ms = (
        int(hydration_reserve_builder()) if identity_only_classification else 0
    )
    if identity_only_classification and reserved_hydration_ms < 25:
        raise ValueError("page hydration reserve must be at least 25 ms")
    classification_deadline = deadline - (reserved_hydration_ms / 1000)
    probe_limits_enforced = bool(
        getattr(analytics, "supports_per_query_read_settings", True)
    )
    if graph_key_witness_probe and not probe_limits_enforced:
        raise ValueError("graph key witness requires enforced per-query read limits")

    # Resolve the optional anchor plan once, before the numbered-page preflight,
    # and reuse it at execution time.  The probe is speculative: when it reaches
    # its sentinel the ordered seed/classifier path still has to run, so that
    # path must reserve the probe's physical query up front.
    anchor_builder = getattr(
        builder,
        (
            "build_filter_graph_key_witness_probe"
            if graph_key_witness_probe
            else "build_filter_anchor_probe"
        ),
        None,
    )
    anchor_support = getattr(
        builder,
        (
            "supports_graph_key_witness_probe"
            if graph_key_witness_probe
            else "supports_filter_anchor_probe"
        ),
        None,
    )
    anchor_population_proof_builder = getattr(
        builder,
        "filter_anchor_probe_proves_complete_population",
        None,
    )
    anchor_probe_proves_complete_population = bool(
        anchor_population_proof_builder()
        if callable(anchor_population_proof_builder)
        else True
    )
    if graph_key_witness_probe and not (
        callable(anchor_builder) and callable(anchor_support) and bool(anchor_support())
    ):
        raise ValueError("graph key witness probe is unavailable")
    ordered_seed_builder = getattr(builder, "build_filter_ordered_seed_page", None)
    candidate_seed_builder = getattr(builder, "build_filter_candidate_seed_page", None)
    candidate_seed_support = getattr(
        builder, "supports_filter_candidate_seed_page", None
    )
    candidate_seed_order_proof = getattr(
        builder, "filter_candidate_seed_proves_result_order", None
    )
    candidate_seed_can_run = bool(
        not workflow_exact
        and not anchor_probe_only
        and not defer_classification
        and not graph_key_witness_probe
        and callable(candidate_seed_builder)
        and callable(candidate_seed_support)
        and candidate_seed_support()
    )
    candidate_seed_proves_result_order = bool(
        candidate_seed_order_proof() if callable(candidate_seed_order_proof) else False
    )
    if candidate_seed_can_run and not candidate_seed_proves_result_order:
        raise ValueError("candidate-first seed must prove result order")
    if candidate_seed_can_run:
        seed_proves_result_order = True
    recommended_anchor_limit: int | None = None
    recommended_anchor_timeout_ms: int | None = None
    recommended_anchor_strata = 1
    recommended_anchor_max_bytes_to_read: int | None = None
    if anchor_probe_limit is None and not anchor_probe_only:
        anchor_limit_builder = getattr(
            builder, "recommended_filter_anchor_probe_limit", None
        )
        if callable(anchor_limit_builder):
            raw_anchor_limit = anchor_limit_builder()
            if raw_anchor_limit is not None:
                recommended_anchor_limit = int(raw_anchor_limit)
                if not 2 <= recommended_anchor_limit <= max_candidates:
                    raise ValueError(
                        "recommended anchor probe limit exceeds max_candidates"
                    )
                anchor_timeout_builder = getattr(
                    builder, "recommended_filter_anchor_probe_timeout_ms", None
                )
                if callable(anchor_timeout_builder):
                    raw_anchor_timeout = anchor_timeout_builder()
                    if raw_anchor_timeout is not None:
                        recommended_anchor_timeout_ms = int(raw_anchor_timeout)
                        if recommended_anchor_timeout_ms <= 0:
                            raise ValueError(
                                "recommended anchor probe timeout must be positive"
                            )
                anchor_strata_builder = getattr(
                    builder, "recommended_filter_anchor_probe_strata", None
                )
                if callable(anchor_strata_builder):
                    raw_anchor_strata = anchor_strata_builder()
                    if raw_anchor_strata is not None:
                        recommended_anchor_strata = int(raw_anchor_strata)
                        if (
                            not 1
                            <= recommended_anchor_strata
                            <= _MAX_OPTIONAL_ANCHOR_STRATA
                        ):
                            raise ValueError(
                                "recommended anchor probe strata exceeds bounded contract"
                            )
                anchor_bytes_builder = getattr(
                    builder,
                    "recommended_filter_anchor_probe_max_bytes_to_read",
                    None,
                )
                if callable(anchor_bytes_builder):
                    raw_anchor_bytes = anchor_bytes_builder()
                    if raw_anchor_bytes is not None:
                        recommended_anchor_max_bytes_to_read = int(raw_anchor_bytes)
                        if not (
                            0
                            < recommended_anchor_max_bytes_to_read
                            < _READ_SETTINGS["max_bytes_to_read"]
                        ):
                            raise ValueError(
                                "recommended anchor byte cap must tighten the read contract"
                            )
    anchor_limit = (
        anchor_probe_limit
        or recommended_anchor_limit
        or min(
            _SELECTIVE_ANCHOR_SENTINEL,
            max_candidates + 1,
        )
    )
    skip_full_anchor_builder = getattr(
        builder,
        "skip_full_window_filter_anchor_probe",
        None,
    )
    skip_full_window_anchor = (
        anchor_limit == _SELECTIVE_ANCHOR_SENTINEL
        and anchor_probe_limit is None
        and not anchor_probe_only
        and callable(skip_full_anchor_builder)
        and bool(skip_full_anchor_builder())
    )
    initial_continuation_anchor_builder = getattr(
        builder,
        "allow_filter_anchor_probe_for_initial_continuation",
        None,
    )
    initial_continuation_anchor_allowed = bool(
        callable(initial_continuation_anchor_builder)
        and initial_continuation_anchor_builder()
    )
    anchor_can_run = (
        not workflow_exact
        and not seed_proves_population_bound
        and (
            not bounded_continuation
            or (
                continuation_slice_end is None
                and cursor_key is None
                and initial_continuation_anchor_allowed
            )
        )
        and cursor_key is None
        and (
            anchor_limit == _SELECTIVE_ANCHOR_SENTINEL
            or anchor_probe_limit is not None
            or recommended_anchor_limit is not None
        )
        and callable(anchor_builder)
        and callable(anchor_support)
        and bool(anchor_support())
        and not skip_full_window_anchor
        # Anchor probes rely on tighter statement/byte caps. A locked executor
        # may strip per-query settings; skip speculation instead of allowing a
        # bounded optimization to inherit the server's 30-second ceiling and
        # crowd out the exact fallback.
        and probe_limits_enforced
        and (callable(ordered_seed_builder) or seed_proves_result_order)
    )

    cursor_seed_keyset_capability = getattr(
        builder, "filter_cursor_seed_keyset_is_safe", None
    )
    cursor_seed_keyset_is_safe = (
        bool(cursor_seed_keyset_capability())
        if callable(cursor_seed_keyset_capability)
        else True
    )
    if (
        cursor_key is not None
        and not cursor_seed_keyset_is_safe
        and not callable(ordered_seed_builder)
    ):
        raise ValueError("unsafe cursor seeds require an ordered seed builder")
    if request_start >= request_end:
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=(monotonic() - started) * 1000,
            query_count=0,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    prefix_needed = ((page_number + 1) * page_size) + 1
    # A builder can lower both the seed and classifier working set when its
    # candidate query is memory-heavy. The requested prefix remains a floor so
    # page zero includes its has-more sentinel in the first ordered root seed.
    seed_batch_recommendation = getattr(
        builder, "recommended_filter_seed_batch_size", None
    )
    cursor_seed_batch_recommendation = getattr(
        builder, "recommended_filter_cursor_seed_batch_size", None
    )
    if (
        bounded_continuation
        and callable(cursor_seed_batch_recommendation)
        and cursor_seed_batch_recommendation() is not None
    ):
        requested_candidate_floor = max(
            prefix_needed, int(cursor_seed_batch_recommendation())
        )
    elif bounded_continuation:
        # A cursor can commit one exact ordered prefix and resume from its
        # signed keyset. Keep each seed page to the requested page plus the
        # has-more sentinel so a memory-heavy classifier can finish the whole
        # seed page before the request deadline and publish a forward
        # checkpoint instead of repeatedly rolling back 200 candidates.
        requested_candidate_floor = prefix_needed
    elif callable(seed_batch_recommendation):
        requested_candidate_floor = int(seed_batch_recommendation())
    else:
        requested_candidate_floor = recommended_batch_size or min(200, max_candidates)
    candidate_floor = min(requested_candidate_floor, max_candidates)
    if not 1 <= candidate_floor <= max_candidates:
        raise ValueError("recommended seed batch size exceeds max_candidates")
    candidate_limit = min(max_candidates, max(candidate_floor, prefix_needed))
    page_depth_kwargs = {
        "page_number": page_number,
        "page_size": page_size,
        "max_seed_attempts": max_seed_attempts,
        "max_candidates": max_candidates,
        "max_query_count": max_query_count,
        # The optional witness probe may be unavailable, broad, or fail its
        # own read ceiling. Reserve the exact classifier's safe fallback shape
        # up front so speculation can never crowd correctness out of the
        # request's finite query budget.
        "classify_batch_size": candidate_witness_fallback_batch_size,
        "seed_batch_size": candidate_floor,
        "query_contract_limit": query_contract_limit,
    }
    # Numbered pages must prove their entire requested prefix in one finite
    # call. Cursor pages have a different exact contract: they may publish a
    # shorter, fully classified prefix together with a signed checkpoint and
    # resume the same visible page. Applying the numbered-page preflight here
    # made an otherwise valid page_size=500 cursor fail before its first read
    # when a custom-attribute classifier used the qualified ten-ID batches.
    if not bounded_continuation and bounded_numbered_page_depth_exceeded(
        **page_depth_kwargs,
    ):
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=False,
            status="degraded",
            error_code=PAGE_DEPTH_EXCEEDED_CODE,
            total_rows_lower_bound=0,
            elapsed_ms=(monotonic() - started) * 1000,
            query_count=0,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )
    if (
        anchor_can_run
        and not anchor_probe_only
        and (
            recommended_anchor_strata > max_query_count
            or bounded_numbered_page_depth_exceeded(
                **page_depth_kwargs,
                reserved_query_count=recommended_anchor_strata,
            )
        )
    ):
        # The optional probe may fall back after its last physical stratum.
        # If the fallback cannot retain its complete budget, skip speculation
        # before contacting ClickHouse.
        anchor_can_run = False

    attempts: list[FilterReadAttempt] = []
    # A trace any-span seed is a physical *span*, while the classified result
    # identity is its trace.  Keep these namespaces separate: using trace_id as
    # the seed key both breaks keyset continuation when one trace has multiple
    # matching spans and can stop a dense selective scan before it is proven
    # exhaustive.
    seen_seed_ids: set[Hashable] = set()
    seen_candidate_ids: set[Hashable] = set()
    matched_by_id: dict[Hashable, dict[str, Any]] = {}
    deferred_candidate_by_id: dict[Hashable, dict[str, Any]] = {}
    # Normal trace lists classify only identities, so sparse adjacent slices
    # can share one classifier statement without widening its per-query batch.
    # Keep each row's acquisition bounds solely for truthful attempt metadata;
    # the exact classifier is still constrained by the immutable candidate
    # identities carried by the rows themselves.
    pending_identity_candidates: dict[
        Hashable, tuple[dict[str, Any], datetime, datetime]
    ] = {}
    eager_identity_prefix_flush_used = False
    repeated_eager_flush_builder = getattr(
        builder, "allow_repeated_eager_identity_prefix_flushes", None
    )
    allow_repeated_eager_identity_prefix_flushes = bool(
        repeated_eager_flush_builder()
        if callable(repeated_eager_flush_builder)
        else False
    )
    cursor_slice_fill_builder = getattr(
        builder, "fill_bounded_cursor_page_across_slices", None
    )
    fill_bounded_cursor_page_across_slices = bool(
        bounded_continuation
        and identity_only_classification
        and callable(cursor_slice_fill_builder)
        and cursor_slice_fill_builder()
    )
    # A builder may expose a finite raw-witness query only when it is a
    # complete superset of exact latest-state membership. Run that cheap,
    # indexable prefilter before the first full-window classifier batch; the
    # exact classifier still validates every surviving identity. Unsupported
    # filter shapes return no probe and retain the existing exact path.
    candidate_witness_probe_enabled = bool(
        probe_limits_enforced
        and candidate_witness_prefilter_allowed
        and callable(candidate_witness_probe_builder)
        and callable(candidate_witness_probe_preference)
        and candidate_witness_probe_preference()
    )
    candidate_witness_probe_strata = 1
    # Slice-aware builders advertise their temporal probe contract through the
    # strata recommendation hook. Legacy one-shot probes expose only
    # ``build_filter_candidate_witness_probe(rows)`` and must fall straight
    # back to exact classification after a failed full-window attempt.
    candidate_witness_global_scope_builder = getattr(
        builder,
        "filter_candidate_witness_replays_global_membership",
        None,
    )
    candidate_witness_replays_global_membership = bool(
        candidate_witness_global_scope_builder()
        if callable(candidate_witness_global_scope_builder)
        else False
    )
    candidate_witness_probe_supports_slices = bool(
        callable(candidate_witness_probe_strata_builder)
        and not candidate_witness_replays_global_membership
    )
    if candidate_witness_probe_enabled and callable(
        candidate_witness_probe_strata_builder
    ):
        raw_candidate_witness_probe_strata = candidate_witness_probe_strata_builder()
        if raw_candidate_witness_probe_strata is not None:
            candidate_witness_probe_strata = int(raw_candidate_witness_probe_strata)
            if not (
                1
                <= candidate_witness_probe_strata
                <= _CANDIDATE_WITNESS_PREFILTER_STRATA
            ):
                raise ValueError(
                    "candidate witness probe strata exceeds bounded contract"
                )
    candidate_witness_probe_timeout_ms = _CANDIDATE_WITNESS_PREFILTER_TIMEOUT_MS
    candidate_witness_probe_timeout_builder = getattr(
        builder, "recommended_filter_candidate_witness_probe_timeout_ms", None
    )
    if candidate_witness_probe_enabled and callable(
        candidate_witness_probe_timeout_builder
    ):
        raw_probe_timeout_ms = candidate_witness_probe_timeout_builder()
        if raw_probe_timeout_ms is not None:
            requested_probe_timeout_ms = int(raw_probe_timeout_ms)
            if not 25 <= requested_probe_timeout_ms <= _QUERY_TIMEOUT_MS:
                raise ValueError("candidate witness probe timeout exceeds contract")
            candidate_witness_probe_timeout_ms = min(
                requested_probe_timeout_ms,
                max(25, deadline_ms - _CANDIDATE_WITNESS_EXACT_RESERVE_MS),
            )
    candidate_witness_probe_max_bytes = _CANDIDATE_WITNESS_PREFILTER_MAX_BYTES
    candidate_witness_probe_bytes_builder = getattr(
        builder, "recommended_filter_candidate_witness_probe_max_bytes", None
    )
    if candidate_witness_probe_enabled and callable(
        candidate_witness_probe_bytes_builder
    ):
        raw_probe_max_bytes = candidate_witness_probe_bytes_builder()
        if raw_probe_max_bytes is not None:
            candidate_witness_probe_max_bytes = int(raw_probe_max_bytes)
            if not (
                _CANDIDATE_WITNESS_PREFILTER_MAX_BYTES
                <= candidate_witness_probe_max_bytes
                <= _READ_SETTINGS["max_bytes_to_read"]
            ):
                raise ValueError("candidate witness probe bytes exceed contract")
    candidate_witness_probe_total_ms = _CANDIDATE_WITNESS_PREFILTER_TOTAL_MS
    candidate_witness_probe_total_builder = getattr(
        builder, "recommended_filter_candidate_witness_probe_total_ms", None
    )
    if candidate_witness_probe_enabled and callable(
        candidate_witness_probe_total_builder
    ):
        raw_probe_total_ms = candidate_witness_probe_total_builder()
        if raw_probe_total_ms is not None:
            requested_probe_total_ms = int(raw_probe_total_ms)
            if requested_probe_total_ms < candidate_witness_probe_timeout_ms:
                raise ValueError("candidate witness total time exceeds contract")
            candidate_witness_probe_total_ms = min(
                requested_probe_total_ms,
                max(
                    candidate_witness_probe_timeout_ms,
                    deadline_ms - _CANDIDATE_WITNESS_EXACT_RESERVE_MS,
                ),
            )
    candidate_witness_probe_abandoned = not probe_limits_enforced
    candidate_witness_probe_attempt_count = 0
    candidate_witness_probe_attempt_limit = min(
        _CANDIDATE_WITNESS_PREFILTER_MAX_ATTEMPTS,
        max(1, max_query_count // 4),
    )
    candidate_witness_probe_started: float | None = None
    if cursor_key is not None and cursor_key[0] < request_start:
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=0,
            elapsed_ms=(monotonic() - started) * 1000,
            query_count=0,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )
    # Safe seed/result orders may start directly at the signed cursor. A trace
    # ordered-root builder remains safe with tombstones because its cursor
    # predicate runs before LIMIT 1 BY trace: the older canonical physical root
    # can seed the trace even when a newer raw root was later tombstoned. Direct
    # any-span child order remains unrelated to the public trace order.
    use_cursor_seed_keyset = bool(
        cursor_key is not None
        and cursor_seed_keyset_is_safe
        and continuation_slice_end is None
    )
    slice_end = continuation_slice_end or (
        min(request_end, cursor_key[0] + timedelta(microseconds=1))
        if use_cursor_seed_keyset
        else request_end
    )
    # A scan keyset is meaningful only inside the exact half-open slice that
    # produced it. Preserve that lower boundary across requests. Legacy v3
    # cursors did not carry it, so resume from the frozen window start: slower,
    # but exact and gap-free.
    active_slice_start: datetime | None
    if (
        continuation_slice_end is not None
        and continuation_before_start_time is not None
    ):
        # Legacy v3 cursors did not carry the lower boundary for an in-slice
        # keyset. Falling back to the frozen request start may rescan, but it
        # cannot skip rows.
        active_slice_start = continuation_slice_start or request_start
    elif continuation_slice_end is not None and carry_continuation_slice_width:
        # An exhausted slice has no keyset. New cursor callers may nevertheless
        # carry the exact lower boundary of the *next* adjacent slice so the
        # successful 1h -> 2h -> 4h widening schedule survives the HTTP round
        # trip. Older cursors omit it and retain the conservative initial width.
        active_slice_start = continuation_slice_start
    else:
        active_slice_start = None
    # Five minutes remains the conservative default for every selector.  A
    # builder whose seed is both partition-pruned and newest-first may opt into
    # a wider first slice to avoid several empty/under-filled round trips on a
    # long window.  This changes only the acquisition boundary: the same
    # finite candidates still cross the exact latest-state classifier and the
    # same result-order proof before publication.
    request_width = request_end - request_start
    max_slice_width = _MAX_SLICE
    max_slice_width_builder = getattr(
        builder, "recommended_filter_max_slice_width", None
    )
    if callable(max_slice_width_builder):
        raw_max_slice_width = max_slice_width_builder()
        if raw_max_slice_width is not None:
            if (
                not isinstance(raw_max_slice_width, timedelta)
                or not _INITIAL_SLICE <= raw_max_slice_width <= request_width
            ):
                raise ValueError("recommended max slice width exceeds bounded contract")
            max_slice_width = max(max_slice_width, raw_max_slice_width)

    slice_width = _INITIAL_SLICE
    initial_slice_width_builder = getattr(
        builder, "recommended_filter_initial_slice_width", None
    )
    if callable(initial_slice_width_builder):
        raw_initial_slice_width = initial_slice_width_builder()
        if raw_initial_slice_width is not None:
            if not isinstance(
                raw_initial_slice_width, timedelta
            ) or not _INITIAL_SLICE <= raw_initial_slice_width <= min(
                max_slice_width, request_width
            ):
                raise ValueError(
                    "recommended initial slice width exceeds bounded contract"
                )
            slice_width = raw_initial_slice_width
    before_start_time: datetime | None = (
        continuation_before_start_time
        if continuation_slice_end is not None
        else (cursor_key[0] if use_cursor_seed_keyset else None)
    )
    before_id: Any = (
        continuation_before_id
        if continuation_slice_end is not None
        else (cursor_key[1] if use_cursor_seed_keyset else None)
    )
    # Once a statement proves a width unsafe, retain the narrower ceiling for
    # every older adjacent slice in this request. Resetting it after one
    # successful half-width read makes the next slice double back to the same
    # known-bad width and repeatedly burns the per-statement timeout.
    forced_width_cap: timedelta | None = None
    # A builder may deliberately seed the complete request window for a
    # one-project, time-only identity scan.  If that optimistic read exceeds
    # its statement budget, logarithmically halving a multi-year window can
    # consume the entire request deadline before reaching a known-conservative
    # slice.  Keep the failed-width ceiling for later widening, but make the
    # immediate recovery attempt use the normal five-minute slice.
    retry_slice_width: timedelta | None = None
    page_complete = False
    degraded_error_code: str | None = None
    safe_slice_end = slice_end
    safe_active_slice_start = active_slice_start
    safe_before_start_time = before_start_time
    safe_before_id = before_id
    safe_seen_seed_ids: set[Hashable] = set()
    safe_seen_candidate_ids: set[Hashable] = set()
    safe_matched_by_id: dict[Hashable, dict[str, Any]] = {}
    safe_pending_identity_candidates: dict[
        Hashable, tuple[dict[str, Any], datetime, datetime]
    ] = {}
    continuation_progressed = False
    # Hydration is the final publication gate for identity-only trace pages.
    # Remember the last committed position *before* this request's first match
    # so a failed hydration can retry that matching classifier batch instead of
    # either skipping its rows or returning the original, non-advancing token.
    pre_match_continuation: (
        tuple[
            datetime | None,
            datetime,
            datetime | None,
            Any,
            bool,
        ]
        | None
    ) = None

    def checkpoint_continuation() -> None:
        """Commit only a fully classified candidate-prefix scan position."""

        nonlocal safe_slice_end
        nonlocal safe_active_slice_start
        nonlocal safe_before_start_time
        nonlocal safe_before_id
        nonlocal safe_seen_seed_ids
        nonlocal safe_seen_candidate_ids
        nonlocal safe_matched_by_id
        nonlocal safe_pending_identity_candidates
        nonlocal continuation_progressed
        safe_slice_end = slice_end
        safe_active_slice_start = active_slice_start
        safe_before_start_time = before_start_time
        safe_before_id = before_id
        safe_seen_seed_ids = set(seen_seed_ids)
        safe_seen_candidate_ids = set(seen_candidate_ids)
        safe_matched_by_id = dict(matched_by_id)
        safe_pending_identity_candidates = dict(pending_identity_candidates)
        continuation_progressed = True

    def rollback_unhydrated_page() -> None:
        """Restore the honest scan position from before unpublished matches."""

        nonlocal safe_active_slice_start
        nonlocal safe_slice_end
        nonlocal safe_before_start_time
        nonlocal safe_before_id
        nonlocal continuation_progressed
        if pre_match_continuation is None:
            continuation_progressed = False
            return
        (
            safe_active_slice_start,
            safe_slice_end,
            safe_before_start_time,
            safe_before_id,
            continuation_progressed,
        ) = pre_match_continuation

    def execute(
        *,
        kind: str,
        query: str,
        params: dict[str, Any],
        active_start: datetime,
        active_end: datetime,
        result_limit: int = _ABSOLUTE_MAX_CANDIDATES,
        timeout_cap_ms: int | None = None,
        max_bytes_to_read_cap: int | None = None,
        use_reserved_query_budget: bool = False,
    ) -> QueryResult:
        # A resumable cursor must leave enough wall time to roll back an
        # in-flight seed batch and publish its last fully classified checkpoint,
        # even when that checkpoint contains zero matches and needs no row
        # hydration. Without this, a sparse scan can admit one final classifier
        # against the full request deadline and cross the API wall while an exact
        # continuation was already available.
        hydration_reserve_is_active = bool(matched_by_id or bounded_continuation)
        active_deadline = (
            deadline
            if use_reserved_query_budget or not hydration_reserve_is_active
            else classification_deadline
        )
        remaining_ms = int((active_deadline - monotonic()) * 1000)
        minimum_query_headroom_ms = (
            _BOUNDED_CONTINUATION_MIN_QUERY_HEADROOM_MS
            if (
                bounded_continuation
                and continuation_progressed
                and not use_reserved_query_budget
            )
            else 25
        )
        if remaining_ms < minimum_query_headroom_ms:
            raise _BudgetExceeded("deadline_exceeded")
        active_query_limit = (
            max_query_count
            if use_reserved_query_budget or not hydration_reserve_is_active
            else max_query_count - reserved_hydration_queries
        )
        if len(attempts) >= active_query_limit:
            raise _BudgetExceeded("query_budget_exceeded")
        attempt_started = monotonic()
        statement_timeout_ms = min(query_timeout_ms, remaining_ms)
        if timeout_cap_ms is not None:
            if timeout_cap_ms <= 0:
                raise ValueError("query timeout cap must be positive")
            statement_timeout_ms = min(statement_timeout_ms, timeout_cap_ms)
        try:
            settings = {
                **_READ_SETTINGS,
                **(read_settings or {}),
                "max_result_rows": result_limit,
            }
            if kind in {"classify", "prefilter"}:
                for (
                    setting_name,
                    setting_cap,
                ) in effective_classify_read_settings.items():
                    settings[setting_name] = min(
                        int(settings.get(setting_name, setting_cap)),
                        setting_cap,
                    )
            if max_bytes_to_read_cap is not None:
                if max_bytes_to_read_cap <= 0:
                    raise ValueError("query byte cap must be positive")
                settings["max_bytes_to_read"] = min(
                    int(settings["max_bytes_to_read"]),
                    max_bytes_to_read_cap,
                )
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=statement_timeout_ms,
                settings=settings,
            )
        except Exception as exc:
            if is_read_budget_error(exc):
                error_code = "read_budget_exceeded"
            elif kind in {"prefilter", "micro_seed", "zero_probe"} and isinstance(
                exc, (RuntimeError, TimeoutError)
            ):
                # The witness probe is an optional optimization. Some guarded
                # executors report their own statement timeout/resource cap as
                # a generic RuntimeError, so account and abandon only this
                # speculative read; the unchanged exact classifier still
                # decides membership. Never extend this fallback to required
                # seed/classify/hydration reads, whose unexpected failures must
                # remain visible to callers.
                error_code = "prefilter_unavailable"
            else:
                raise
            attempts.append(
                FilterReadAttempt(
                    kind=kind,
                    slice_start=active_start,
                    slice_end=active_end,
                    elapsed_ms=(monotonic() - attempt_started) * 1000,
                    rows_returned=0,
                    result_payload_bytes=0,
                    error_code=error_code,
                )
            )
            raise _BudgetExceeded(error_code) from None
        rows = list(result.data or [])
        attempts.append(
            FilterReadAttempt(
                kind=kind,
                slice_start=active_start,
                slice_end=active_end,
                elapsed_ms=(monotonic() - attempt_started) * 1000,
                rows_returned=len(rows),
                result_payload_bytes=_result_payload_bytes(rows),
            )
        )
        return result

    def row_identity(row: dict[str, Any]) -> Hashable:
        identity_builder = getattr(builder, "bounded_filter_row_identity", None)
        identity: Hashable = (
            identity_builder(row)
            if callable(identity_builder)
            else str(row.get(key_field, ""))
        )
        try:
            hash(identity)
        except TypeError as exc:
            raise ValueError("bounded filter row identity must be hashable") from exc
        return identity

    def result_row_key(row: dict[str, Any]) -> tuple[datetime, Any]:
        value = row.get("start_time")
        start_time = (
            _without_timezone(value) if isinstance(value, datetime) else datetime.min
        )
        order_builder = getattr(builder, "bounded_filter_row_order_token", None)
        order_token = (
            order_builder(row)
            if callable(order_builder)
            else str(row.get(key_field, ""))
        )
        return start_time, order_token

    def seed_identity(row: dict[str, Any]) -> Hashable:
        identity_builder = getattr(builder, "bounded_filter_seed_identity", None)
        identity: Hashable = (
            identity_builder(row) if callable(identity_builder) else row_identity(row)
        )
        try:
            hash(identity)
        except TypeError as exc:
            raise ValueError("bounded filter seed identity must be hashable") from exc
        return identity

    def seed_row_key(row: dict[str, Any]) -> tuple[datetime, Any]:
        value = row.get("start_time")
        start_time = (
            _without_timezone(value) if isinstance(value, datetime) else datetime.min
        )
        order_builder = getattr(builder, "bounded_filter_seed_order_token", None)
        if callable(order_builder):
            return start_time, order_builder(row)
        return result_row_key(row)

    def classify_seed_rows(
        candidate_rows: list[dict[str, Any]],
        *,
        active_start: datetime,
        active_end: datetime,
        stop_on_ordered_prefix: bool = False,
    ) -> bool:
        """Classify finite seeds and report an exact ordered-prefix proof.

        Root-ordered seed rows are an upper bound on the canonical live-root
        order for their trace: latest-state tombstones can move a classified
        result older, but cannot move it ahead of that trace's newest raw live
        root seed.  After each finite classifier chunk, a full public prefix
        whose cutoff is no older than the last classified raw seed therefore
        cannot be displaced by any remaining seed in the same ordered page.

        Callers must leave ``stop_on_ordered_prefix`` false for unordered
        any-span anchors, direct child seeds, and deferred graph acquisition.
        """

        candidate_seed_rows: dict[Hashable, dict[str, Any]] = {}
        for row in candidate_rows:
            public_identity = str(row.get(key_field, ""))
            if not public_identity:
                continue
            candidate_identity = row_identity(row)
            if candidate_identity in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_identity)
            candidate_seed_rows[candidate_identity] = row

        nonlocal candidate_witness_probe_abandoned
        nonlocal candidate_witness_probe_attempt_count
        nonlocal candidate_witness_probe_enabled
        nonlocal candidate_witness_probe_started
        nonlocal before_id
        nonlocal before_start_time
        nonlocal pre_match_continuation

        candidate_identities = list(candidate_seed_rows)
        if defer_classification:
            deferred_candidate_by_id.update(candidate_seed_rows)
            return False
        if not candidate_identities:
            return False

        # A stratified witness probe can require several full-window slices.
        # When the complete candidate set already fits in one bounded exact
        # classifier, that speculation cannot reduce the physical classifier
        # count and is strictly extra failure/latency surface.  Skip it for
        # this batch only; a later, larger seed batch may still benefit from
        # the probe.
        candidate_witness_probe_can_reduce_query_count = not (
            candidate_witness_probe_strata > 1
            and len(candidate_identities) <= candidate_witness_fallback_batch_size
        )
        prefilter_query_reserve = (
            1
            + ceil(len(candidate_identities) / candidate_witness_fallback_batch_size)
            + reserved_hydration_queries
        )
        prefilter_time_reserve_ms = (
            candidate_witness_probe_total_ms
            if candidate_witness_probe_strata > 1
            else candidate_witness_probe_timeout_ms
        ) + _CANDIDATE_WITNESS_EXACT_RESERVE_MS
        if (
            candidate_witness_probe_enabled
            and candidate_witness_probe_can_reduce_query_count
            and (
                candidate_witness_probe_attempt_count + candidate_witness_probe_strata
                > candidate_witness_probe_attempt_limit
                or len(attempts) + prefilter_query_reserve > max_query_count
                or int((classification_deadline - monotonic()) * 1000)
                < prefilter_time_reserve_ms
            )
        ):
            candidate_witness_probe_enabled = False
            candidate_witness_probe_abandoned = True
        if (
            candidate_witness_prefilter_allowed
            and candidate_witness_probe_enabled
            and candidate_witness_probe_can_reduce_query_count
            and callable(candidate_witness_probe_builder)
        ):
            probe_candidates = candidate_identities
            witness_identities: set[Hashable] = set()
            duration = request_end - request_start
            duration_us = (
                duration.days * 86_400_000_000
                + duration.seconds * 1_000_000
                + duration.microseconds
            )
            boundaries = [
                request_start
                + timedelta(
                    microseconds=(duration_us * index) // candidate_witness_probe_strata
                )
                for index in range(candidate_witness_probe_strata + 1)
            ]
            boundaries[0], boundaries[-1] = request_start, request_end
            probe_slices = [
                (slice_start, slice_end, 0)
                for slice_start, slice_end in reversed(
                    list(zip(boundaries[:-1], boundaries[1:], strict=False))
                )
                if slice_start < slice_end
            ]
            probe_complete = bool(probe_slices)
            if candidate_witness_probe_started is None:
                candidate_witness_probe_started = monotonic()

            while probe_slices and len(witness_identities) < len(probe_candidates):
                probe_start, probe_end, probe_depth = probe_slices.pop(0)
                remaining_exact_queries = (
                    ceil(len(probe_candidates) / candidate_witness_fallback_batch_size)
                    + reserved_hydration_queries
                )
                total_probe_elapsed_ms = int(
                    (monotonic() - candidate_witness_probe_started) * 1000
                )
                total_probe_remaining_ms = (
                    candidate_witness_probe_total_ms - total_probe_elapsed_ms
                )
                if (
                    candidate_witness_probe_attempt_count
                    >= candidate_witness_probe_attempt_limit
                    or len(attempts) + 1 + remaining_exact_queries > max_query_count
                    or total_probe_remaining_ms < 25
                ):
                    probe_complete = False
                    break

                remaining_probe_identities = [
                    identity
                    for identity in probe_candidates
                    if identity not in witness_identities
                ]
                probe_rows = [
                    candidate_seed_rows[identity]
                    for identity in remaining_probe_identities
                ]
                if candidate_witness_probe_supports_slices and (
                    candidate_witness_probe_strata > 1 or probe_depth > 0
                ):
                    probe_query, probe_params = candidate_witness_probe_builder(
                        probe_rows,
                        slice_start=probe_start,
                        slice_end=probe_end,
                    )
                else:
                    probe_query, probe_params = candidate_witness_probe_builder(
                        probe_rows
                    )
                if not probe_query:
                    probe_complete = False
                    break

                candidate_witness_probe_attempt_count += 1
                try:
                    probe_result = execute(
                        kind="prefilter",
                        query=probe_query,
                        params=probe_params,
                        active_start=probe_start,
                        active_end=probe_end,
                        result_limit=len(remaining_probe_identities),
                        timeout_cap_ms=min(
                            candidate_witness_probe_timeout_ms,
                            total_probe_remaining_ms,
                        ),
                        max_bytes_to_read_cap=candidate_witness_probe_max_bytes,
                    )
                except _BudgetExceeded as exc:
                    probe_duration_us = (
                        (probe_end - probe_start).days * 86_400_000_000
                        + (probe_end - probe_start).seconds * 1_000_000
                        + (probe_end - probe_start).microseconds
                    )
                    can_split = bool(
                        exc.error_code
                        in {"read_budget_exceeded", "prefilter_unavailable"}
                        and candidate_witness_probe_supports_slices
                        and probe_depth < 2
                        and probe_duration_us > 1
                        and candidate_witness_probe_attempt_count + 2
                        <= candidate_witness_probe_attempt_limit
                    )
                    if can_split:
                        midpoint = probe_start + timedelta(
                            microseconds=probe_duration_us // 2
                        )
                        # Newest-first is only a latency optimization. Both
                        # half-open children must succeed before raw absence is
                        # allowed to remove an exact-classifier candidate.
                        probe_slices[0:0] = [
                            (midpoint, probe_end, probe_depth + 1),
                            (probe_start, midpoint, probe_depth + 1),
                        ]
                        continue
                    probe_complete = False
                    break

                returned_witness_identities = {
                    row_identity(row) for row in probe_result.data or []
                }
                if not returned_witness_identities.issubset(
                    set(remaining_probe_identities)
                ):
                    # A raw prefilter is allowed to remove candidates, never
                    # invent them. An out-of-batch identity invalidates the
                    # entire optional proof; otherwise an extra identity could
                    # mask one missing candidate and trigger an unsafe early
                    # stop by cardinality alone.
                    probe_complete = False
                    break
                witness_identities.update(returned_witness_identities)

            if probe_complete and (
                not probe_slices or len(witness_identities) == len(probe_candidates)
            ):
                candidate_identities = [
                    identity
                    for identity in probe_candidates
                    if identity in witness_identities
                ]
                # The union covered the entire half-open request window (or
                # every candidate already has a positive witness). Only now is
                # raw absence a valid exact-classifier prefilter.
                # ``candidate_identities`` retains the ordered seed insertion
                # order.  Therefore, after each surviving classifier chunk,
                # every earlier root is resolved: it either failed this
                # necessary prefilter or crossed the full classifier.  The
                # ordinary last-classified-seed cutoff remains a valid exact
                # prefix proof; there is no need to classify every surviving
                # root in a broad 512-row batch before returning 25 rows.
                if len(candidate_identities) * 4 >= len(probe_candidates) * 3:
                    # A broad raw superset cannot amortize another speculative
                    # batch. The exact 20-identity fallback remains bounded.
                    candidate_witness_probe_enabled = False
                    candidate_witness_probe_abandoned = True
            else:
                # Partial temporal coverage proves no negative. Keep every
                # original identity and permanently use the exact fallback.
                candidate_witness_probe_enabled = False
                candidate_witness_probe_abandoned = True

        active_classify_batch_size = (
            candidate_witness_fallback_batch_size
            if (
                candidate_witness_probe_abandoned
                or not candidate_witness_probe_can_reduce_query_count
            )
            else classify_batch_size
        )
        for batch_offset in range(
            0, len(candidate_identities), active_classify_batch_size
        ):
            identity_batch = candidate_identities[
                batch_offset : batch_offset + active_classify_batch_size
            ]
            classified_identity_batch = identity_batch
            candidate_batch = [
                str(candidate_seed_rows[identity].get(key_field, ""))
                for identity in identity_batch
            ]
            seeded_match_builder = (
                identity_match_builder
                if identity_only_classification
                else getattr(
                    builder,
                    "build_filter_match_query_from_seed_rows",
                    None,
                )
            )
            if not identity_batch:
                match_result = None
            elif callable(seeded_match_builder):
                match_query, match_params = seeded_match_builder(
                    [candidate_seed_rows[identity] for identity in identity_batch]
                )
            else:
                match_query, match_params = builder.build_filter_match_query(
                    candidate_batch
                )
            if identity_batch:
                if not match_query:
                    continue
                continuation_before_query = (
                    safe_active_slice_start,
                    safe_slice_end,
                    safe_before_start_time,
                    safe_before_id,
                    continuation_progressed,
                )
                match_result = execute(
                    kind="classify",
                    query=match_query,
                    params=match_params,
                    active_start=active_start,
                    active_end=active_end,
                    result_limit=max_candidates,
                )
                had_matches_before_query = bool(matched_by_id)
                for row in match_result.data:
                    identity = row_identity(row)
                    # A classifier can return an updated ordering value different
                    # from its raw seed. Apply the signed result boundary again so
                    # continuation pages remain strictly disjoint.
                    if cursor_key is not None and result_row_key(row) >= cursor_key:
                        continue
                    if str(row.get(key_field, "")):
                        matched_by_id[identity] = row

                if (
                    identity_only_classification
                    and callable(candidate_witness_probe_builder)
                    and callable(candidate_witness_probe_preference)
                    and candidate_witness_probe_preference()
                    and not candidate_witness_probe_abandoned
                    and not match_result.data
                ):
                    # Keep the prefilter enabled after an exact zero-yield
                    # batch only for builders that explicitly prefer it. This
                    # also covers builders that elect to expose a safe probe
                    # only after observing their first batch without
                    # re-enabling a disabled interactive-list optimization.
                    candidate_witness_probe_enabled = True

                if (
                    identity_only_classification
                    and not had_matches_before_query
                    and matched_by_id
                ):
                    if bounded_continuation and pre_match_continuation is None:
                        pre_match_continuation = continuation_before_query
                    if len(attempts) > max_query_count - reserved_hydration_queries:
                        raise _BudgetExceeded("query_budget_exceeded")
                    if monotonic() > classification_deadline:
                        raise _BudgetExceeded("deadline_exceeded")

            if bounded_continuation and stop_on_ordered_prefix and identity_batch:
                # Each exact classifier chunk resolves one contiguous prefix
                # of the ordered root seed. A complete witness may have removed
                # earlier candidates, but those negatives are resolved too and
                # the surviving identities retain seed order. Commit through
                # the last classified survivor instead of rolling the whole
                # seed page back when the next chunk has insufficient deadline
                # headroom. This preserves exactness and prevents cursor
                # livelock on a broad-but-not-identical witness result.
                last_classified_seed = candidate_seed_rows[identity_batch[-1]]
                before_start_time, before_id = seed_row_key(last_classified_seed)
                checkpoint_continuation()

            if stop_on_ordered_prefix and len(matched_by_id) >= prefix_needed:
                ordered_matches = sorted(
                    matched_by_id.values(), key=result_row_key, reverse=True
                )
                cutoff = result_row_key(ordered_matches[prefix_needed - 1])
                last_classified_seed = candidate_seed_rows[
                    classified_identity_batch[-1]
                ]
                if cutoff >= seed_row_key(last_classified_seed):
                    return True
        return False

    def classify_or_buffer_seed_rows(
        candidate_rows: list[dict[str, Any]],
        *,
        active_start: datetime,
        active_end: datetime,
        stop_on_ordered_prefix: bool = False,
        force: bool = False,
    ) -> bool:
        """Amortize sparse identity classifiers across adjacent seed reads.

        Full-presentation span reads and explicit graph/eval/task identity
        consumers normally retain immediate classification. Normal trace pages
        buffer because they hydrate a final public page separately. A narrowly
        opted-in unhydrated membership selector may share the buffer without
        enabling hydration: while its optional witness prefilter is active it
        accumulates at most ``max_candidates``; after optional-probe fallback it
        flushes only the builder's independently bounded classifier batch. The
        eval-only population proof also buffers so 512-row physical seed pages
        become exact 100-trace witness batches instead of six partial queries.
        Insertion order is newest-first across an ordered seed stream. The
        population proof does not use that order: it accepts only exhaustion or
        a 10k+1 rejection sentinel.
        """

        if (
            not identity_only_classification
            and not unhydrated_buffered_identity_classification
            and not seed_proves_population_bound
        ):
            return classify_seed_rows(
                candidate_rows,
                active_start=active_start,
                active_end=active_end,
                stop_on_ordered_prefix=stop_on_ordered_prefix,
            )

        for row in candidate_rows:
            public_identity = str(row.get(key_field, ""))
            if not public_identity:
                continue
            candidate_identity = row_identity(row)
            if (
                candidate_identity in seen_candidate_ids
                or candidate_identity in pending_identity_candidates
            ):
                continue
            pending_identity_candidates[candidate_identity] = (
                row,
                active_start,
                active_end,
            )

        def flush(batch_size: int) -> bool:
            batch_identities = list(pending_identity_candidates)[:batch_size]
            batch_entries = [
                pending_identity_candidates.pop(identity)
                for identity in batch_identities
            ]
            return classify_seed_rows(
                [entry[0] for entry in batch_entries],
                active_start=min(entry[1] for entry in batch_entries),
                active_end=max(entry[2] for entry in batch_entries),
                stop_on_ordered_prefix=stop_on_ordered_prefix,
            )

        pending_flush_size = (
            max_candidates
            if candidate_witness_probe_enabled
            and callable(candidate_witness_probe_builder)
            else classify_batch_size
        )
        while len(pending_identity_candidates) >= pending_flush_size:
            if flush(pending_flush_size):
                return True
        if force and pending_identity_candidates:
            return flush(len(pending_identity_candidates))
        return False

    try:
        use_seed_loop = True
        # A builder may expose a strict negative proof for a narrowly qualified
        # positive filter conjunction. The query reads only exhaustive raw
        # witnesses: zero rows is therefore conclusive, while any row (or any
        # timeout/resource ceiling) is deliberately inconclusive and restores
        # the ordinary latest-state selector unchanged. Keep one second for
        # that fallback so this optimization can never consume the whole API
        # deadline or turn an uncertain probe into a public empty page.
        exact_zero_probe_builder = getattr(
            builder, "build_filter_exact_zero_probe", None
        )
        exact_zero_probe_support = getattr(
            builder, "supports_filter_exact_zero_probe", None
        )
        exact_zero_timeout_builder = getattr(
            builder, "recommended_filter_exact_zero_probe_timeout_ms", None
        )
        exact_zero_bytes_builder = getattr(
            builder, "recommended_filter_exact_zero_probe_max_bytes", None
        )
        exact_zero_global_scope_builder = getattr(
            builder,
            "filter_exact_zero_probe_proves_global_membership",
            None,
        )
        exact_zero_proves_global_membership = bool(
            exact_zero_global_scope_builder()
            if callable(exact_zero_global_scope_builder)
            else True
        )
        exact_zero_timeout_ms = (
            int(exact_zero_timeout_builder())
            if callable(exact_zero_timeout_builder)
            else _EXACT_ZERO_PROBE_TIMEOUT_MS
        )
        exact_zero_max_bytes = (
            int(exact_zero_bytes_builder())
            if callable(exact_zero_bytes_builder)
            else _EXACT_ZERO_PROBE_MAX_BYTES
        )
        if not 25 <= exact_zero_timeout_ms <= _QUERY_TIMEOUT_MS:
            raise ValueError("exact-zero probe timeout exceeds bounded contract")
        if not 0 < exact_zero_max_bytes <= _READ_SETTINGS["max_bytes_to_read"]:
            raise ValueError("exact-zero probe byte cap exceeds bounded contract")
        exact_zero_can_run = bool(
            probe_limits_enforced
            and not workflow_exact
            # A cursor-mode list can publish a signed exact checkpoint after
            # each finite seed/classify batch. Spending the optional proof's
            # statement budget first only delays that guaranteed progress and
            # can leave no checkpoint when a broad Map witness hits its cap.
            and not bounded_continuation
            and not defer_classification
            and not graph_key_witness_probe
            and not anchor_probe_only
            and page_number == 0
            and cursor_key is None
            and continuation_slice_end is None
            and callable(exact_zero_probe_builder)
            and callable(exact_zero_probe_support)
            and exact_zero_probe_support()
            and exact_zero_proves_global_membership
            and int((classification_deadline - monotonic()) * 1000)
            >= exact_zero_timeout_ms + _EXACT_ZERO_FALLBACK_RESERVE_MS
        )
        if exact_zero_can_run:
            try:
                zero_query, zero_params = exact_zero_probe_builder()
                zero_result = execute(
                    kind="zero_probe",
                    query=zero_query,
                    params=zero_params,
                    active_start=request_start,
                    active_end=request_end,
                    result_limit=1,
                    timeout_cap_ms=exact_zero_timeout_ms,
                    max_bytes_to_read_cap=exact_zero_max_bytes,
                )
                if not list(zero_result.data or []):
                    page_complete = True
                    use_seed_loop = False
            except _BudgetExceeded:
                # Optional proof only. Required seed/classify reads below retain
                # the full fail-closed behavior and never publish probe rows.
                pass

        # Positive call-type/JSON predicates have no skip index, so evaluating
        # them over an adaptive multi-day seed is the broad scan this reader is
        # designed to avoid. Give builders a small fixed-width raw predicate
        # seed in each deterministic temporal stratum. The distributed union
        # finds old as well as new matches without ever parsing JSON over a
        # broad slice. Its rows are only candidates and cross the exact latest-
        # state classifier before entering ``matched_by_id``. Trace order is
        # still proven by the unchanged ordered-root fallback. A span builder
        # may close directly only when the *newest* micro-slice alone contains
        # the complete public prefix sentinel; distributed samples never claim
        # absence or global order. Any resource failure restores pre-probe
        # selector state and continues through the original fail-closed path.
        micro_seed_builder = getattr(
            builder, "build_filter_unindexed_micro_seed_page", None
        )
        micro_width_builder = getattr(
            builder, "recommended_filter_unindexed_micro_seed_width", None
        )
        micro_strata_builder = getattr(
            builder, "recommended_filter_unindexed_micro_seed_strata", None
        )
        micro_order_proof = getattr(
            builder, "filter_unindexed_micro_seed_proves_result_order", None
        )
        micro_width = (
            micro_width_builder()
            if (
                not workflow_exact
                and not graph_key_witness_probe
                and not bounded_continuation
                and callable(micro_width_builder)
                and callable(micro_seed_builder)
            )
            else None
        )
        micro_strata = (
            micro_strata_builder()
            if callable(micro_strata_builder) and micro_width is not None
            else None
        )
        if (
            use_seed_loop
            and isinstance(micro_width, timedelta)
            and micro_width > timedelta(0)
            and type(micro_strata) is int
            and 1 <= micro_strata <= _MAX_OPTIONAL_ANCHOR_STRATA
        ):
            micro_limit = min(candidate_limit, prefix_needed)
            micro_classify_queries = ceil(micro_limit / classify_batch_size)
            micro_reserved_queries = micro_strata + micro_classify_queries
            micro_time_reserve_ms = (
                micro_strata * _UNINDEXED_POSITIVE_MICRO_SEED_TIMEOUT_MS
                + _CANDIDATE_WITNESS_EXACT_RESERVE_MS
            )
            micro_can_run = bool(
                probe_limits_enforced
                and cursor_key is None
                and micro_reserved_queries <= max_query_count
                and not bounded_numbered_page_depth_exceeded(
                    **page_depth_kwargs,
                    reserved_query_count=micro_reserved_queries,
                )
                and int((classification_deadline - monotonic()) * 1000)
                >= micro_time_reserve_ms
            )
            if micro_can_run:
                saved_seen_seed_ids = set(seen_seed_ids)
                saved_seen_candidate_ids = set(seen_candidate_ids)
                saved_matched_by_id = dict(matched_by_id)
                saved_pending_candidates = dict(pending_identity_candidates)
                try:
                    request_duration = request_end - request_start
                    request_duration_us = (
                        request_duration.days * 86_400_000_000
                        + request_duration.seconds * 1_000_000
                        + request_duration.microseconds
                    )
                    micro_boundaries = [
                        request_start
                        + timedelta(
                            microseconds=(request_duration_us * index) // micro_strata
                        )
                        for index in range(micro_strata + 1)
                    ]
                    micro_boundaries[0], micro_boundaries[-1] = (
                        request_start,
                        request_end,
                    )
                    micro_slices = [
                        (max(stratum_start, stratum_end - micro_width), stratum_end)
                        for stratum_start, stratum_end in reversed(
                            list(
                                zip(
                                    micro_boundaries[:-1],
                                    micro_boundaries[1:],
                                    strict=False,
                                )
                            )
                        )
                        if stratum_start < stratum_end
                    ]
                    raw_micro_rows: list[dict[str, Any]] = []
                    newest_micro_saturated = False
                    for micro_index, (micro_start, micro_end) in enumerate(
                        micro_slices
                    ):
                        remaining_limit = micro_limit - len(raw_micro_rows)
                        if remaining_limit <= 0:
                            break
                        micro_query, micro_params = micro_seed_builder(
                            slice_start=micro_start,
                            slice_end=micro_end,
                            limit=remaining_limit,
                        )
                        micro_result = execute(
                            kind="micro_seed",
                            query=micro_query,
                            params=micro_params,
                            active_start=micro_start,
                            active_end=micro_end,
                            result_limit=remaining_limit,
                            timeout_cap_ms=(_UNINDEXED_POSITIVE_MICRO_SEED_TIMEOUT_MS),
                            max_bytes_to_read_cap=(
                                _UNINDEXED_POSITIVE_MICRO_SEED_MAX_BYTES
                            ),
                        )
                        result_rows = list(micro_result.data or [])
                        if micro_index == 0 and len(result_rows) >= remaining_limit:
                            newest_micro_saturated = True
                        raw_micro_rows.extend(result_rows)
                    micro_rows = sorted(
                        raw_micro_rows,
                        key=seed_row_key,
                        reverse=True,
                    )
                    new_micro_rows: list[dict[str, Any]] = []
                    for row in micro_rows:
                        raw_identity = seed_identity(row)
                        if (
                            not str(row.get(key_field, ""))
                            or raw_identity in seen_seed_ids
                        ):
                            continue
                        seen_seed_ids.add(raw_identity)
                        new_micro_rows.append(row)
                    micro_prefix_proven = classify_or_buffer_seed_rows(
                        new_micro_rows,
                        active_start=request_start,
                        active_end=request_end,
                        stop_on_ordered_prefix=(
                            newest_micro_saturated and bool(micro_order_proof())
                            if callable(micro_order_proof)
                            else False
                        ),
                        force=True,
                    )
                    if micro_prefix_proven:
                        page_complete = True
                        use_seed_loop = False
                except _BudgetExceeded:
                    seen_seed_ids.clear()
                    seen_seed_ids.update(saved_seen_seed_ids)
                    seen_candidate_ids.clear()
                    seen_candidate_ids.update(saved_seen_candidate_ids)
                    matched_by_id.clear()
                    matched_by_id.update(saved_matched_by_id)
                    pending_identity_candidates.clear()
                    pending_identity_candidates.update(saved_pending_candidates)

        # Eligible any-span trace filters may first ask a direct key+value
        # predicate for a finite DISTINCT trace-id sentinel. The probe is a
        # positive accelerator unless the builder explicitly proves complete
        # population coverage; a temporal child probe can never prove absence
        # for an in-window root. Long windows may skip that broad sentinel and
        # start with ordered roots. After exhaustion or saturation, canonical
        # root batches plus global finite classification provide the exact
        # result-order proof without materialising a tenant-wide trace-id Set.
        seed_page_builder = (
            candidate_seed_builder
            if candidate_seed_can_run
            else builder.build_filter_seed_page
        )
        if not use_seed_loop:
            pass
        elif cursor_key is not None and not candidate_seed_can_run:
            # Continuations must start from the signed *result* tuple. Trace
            # any-span seeds are ordered by the matching physical child, not
            # by the public root trace, so use the root-ordered fallback when
            # the builder exposes it. Span seeds already share result order.
            if callable(ordered_seed_builder):
                seed_page_builder = ordered_seed_builder
                seed_proves_result_order = True
        elif anchor_can_run:
            try:
                anchor_rows_by_id: dict[Hashable, dict[str, Any]] = {}
                anchor_hit_sentinel = False
                # ``recommended_anchor_timeout_ms`` is an aggregate wall cap
                # for the optional partitioned probe, not four independent
                # allowances.  A sparse value can otherwise spend almost
                # 4 x 300 ms before discovering that the last stratum is
                # common, starving the exact ordered seed/classifier fallback
                # that would have completed inside the request deadline.
                # Explicit graph anchors remain single-query reads and keep
                # their existing statement timeout contract.
                anchor_wall_started: float | None = None
                if recommended_anchor_strata > 1 and not anchor_probe_only:
                    duration = request_end - request_start
                    duration_us = (
                        duration.days * 86_400_000_000
                        + duration.seconds * 1_000_000
                        + duration.microseconds
                    )
                    boundaries = [
                        request_start
                        + timedelta(
                            microseconds=(duration_us * index)
                            // recommended_anchor_strata
                        )
                        for index in range(recommended_anchor_strata + 1)
                    ]
                    boundaries[0], boundaries[-1] = request_start, request_end
                    anchor_slices = list(
                        reversed(
                            list(
                                zip(
                                    boundaries[:-1],
                                    boundaries[1:],
                                    strict=False,
                                )
                            )
                        )
                    )
                else:
                    anchor_slices = [(request_start, request_end)]

                for anchor_start, anchor_end in anchor_slices:
                    remaining_anchor_limit = anchor_limit - len(anchor_rows_by_id)
                    if remaining_anchor_limit <= 0:
                        anchor_hit_sentinel = True
                        break
                    anchor_timeout_cap_ms = recommended_anchor_timeout_ms
                    if (
                        recommended_anchor_strata > 1
                        and not anchor_probe_only
                        and recommended_anchor_timeout_ms is not None
                    ):
                        if anchor_wall_started is None:
                            anchor_wall_started = monotonic()
                        else:
                            anchor_elapsed_ms = int(
                                (monotonic() - anchor_wall_started) * 1000
                            )
                            anchor_remaining_ms = (
                                recommended_anchor_timeout_ms - anchor_elapsed_ms
                            )
                            if anchor_remaining_ms < 25:
                                # The probe has not covered every stratum, so
                                # its partial candidates prove nothing. Discard
                                # them and preserve the ordered exact fallback.
                                anchor_hit_sentinel = True
                                break
                            anchor_timeout_cap_ms = min(
                                recommended_anchor_timeout_ms,
                                anchor_remaining_ms,
                            )
                    if recommended_anchor_strata > 1 and not anchor_probe_only:
                        anchor_query, anchor_params = anchor_builder(
                            limit=remaining_anchor_limit,
                            slice_start=anchor_start,
                            slice_end=anchor_end,
                        )
                    else:
                        anchor_query, anchor_params = anchor_builder(
                            limit=remaining_anchor_limit
                        )
                    anchor_result = execute(
                        kind="anchor",
                        query=anchor_query,
                        params=anchor_params,
                        active_start=anchor_start,
                        active_end=anchor_end,
                        result_limit=remaining_anchor_limit,
                        timeout_cap_ms=anchor_timeout_cap_ms,
                        max_bytes_to_read_cap=(
                            recommended_anchor_max_bytes_to_read
                            if not anchor_probe_only
                            else None
                        ),
                    )
                    stratum_rows = list(anchor_result.data or [])
                    if len(stratum_rows) >= remaining_anchor_limit:
                        anchor_hit_sentinel = True
                        break
                    for row in stratum_rows:
                        anchor_rows_by_id[row_identity(row)] = row

                anchor_rows = [
                    row
                    for _, row in sorted(
                        anchor_rows_by_id.items(),
                        key=lambda item: repr(item[0]),
                    )
                ]
                if not anchor_hit_sentinel:
                    classify_seed_rows(
                        anchor_rows,
                        active_start=request_start,
                        active_end=request_end,
                    )
                    if anchor_probe_proves_complete_population:
                        page_complete = True
                        use_seed_loop = False
                    elif anchor_probe_only:
                        # Trace child anchors are temporal positive samples,
                        # never exact absence proofs.  Preserve any classified
                        # candidates but make incompleteness explicit to the
                        # graph caller instead of publishing a false-empty
                        # exact stratum.
                        degraded_error_code = "sample_limit"
                        use_seed_loop = False
                    elif callable(ordered_seed_builder):
                        # Continue with canonical roots. Their request-window
                        # order plus global finite classification is the first
                        # path that can prove an exact trace page.
                        seed_page_builder = ordered_seed_builder
                        seed_proves_result_order = True
                elif anchor_probe_only:
                    # The sentinel proves only that this value is not sparse.
                    # A partitioned graph probe classifies only this finite
                    # sentinel set and exposes at most the requested page.  It
                    # never falls into the ORDER BY seed loop; the surrounding
                    # stratum protocol supplies bounded temporal coverage and
                    # retains explicit sampled metadata.
                    if anchor_probe_limit is not None:
                        classify_seed_rows(
                            list(anchor_result.data or []),
                            active_start=request_start,
                            active_end=request_end,
                        )
                    degraded_error_code = "sample_limit"
                    use_seed_loop = False
                else:
                    if callable(ordered_seed_builder):
                        seed_page_builder = ordered_seed_builder
                        seed_proves_result_order = True
            except _BudgetExceeded as exc:
                if exc.error_code != "read_budget_exceeded":
                    raise
                if anchor_probe_only:
                    degraded_error_code = exc.error_code
                    use_seed_loop = False
                elif callable(ordered_seed_builder):
                    seed_page_builder = ordered_seed_builder
                    seed_proves_result_order = True
        elif (
            not seed_proves_population_bound
            and callable(ordered_seed_builder)
            and callable(anchor_support)
            and bool(anchor_support())
        ):
            # A caller-imposed working set smaller than the 512+1 sparse/common
            # sentinel cannot safely run the anchor probe. Fall straight back
            # to finite root-ordered batches; their order proof closes page N
            # without an extra full-slice exhaustion read.
            seed_page_builder = ordered_seed_builder
            seed_proves_result_order = True
        elif (
            not seed_proves_population_bound
            and callable(ordered_seed_builder)
            and not seed_proves_result_order
        ):
            # Some any-span predicates (notably structured JSON/call_type)
            # have no selective index. A whole-window anchor probe would be
            # the broad scan this bounded reader exists to avoid, so start
            # directly with finite newest-root batches and classify only those
            # candidate trace IDs.
            seed_page_builder = ordered_seed_builder
            seed_proves_result_order = True

        for seed_index in range(max_seed_attempts if use_seed_loop else 0):
            if slice_end <= request_start:
                page_complete = True
                break

            remaining_attempts = max_seed_attempts - seed_index
            remaining_window = slice_end - request_start
            scheduled_width = slice_width
            scheduled_coverage = timedelta(0)
            for _ in range(remaining_attempts):
                scheduled_coverage += scheduled_width
                scheduled_width = min(scheduled_width * 2, max_slice_width)
            active_width = retry_slice_width or slice_width
            retry_slice_width = None
            # Cursor-mode scans are intentionally resumable across requests.
            # Do not inflate their first finite slice merely to schedule the
            # entire request window inside this request's attempt count; that
            # turns a configured one-hour root seed into a multi-day read on
            # year-scale projects and can fail before emitting a checkpoint.
            if not bounded_continuation and scheduled_coverage < remaining_window:
                active_width = max(active_width, remaining_window / remaining_attempts)
            if forced_width_cap is not None:
                active_width = min(active_width, forced_width_cap)
            scheduled_slice_start = max(request_start, slice_end - active_width)
            slice_start = active_slice_start or scheduled_slice_start
            if active_slice_start is None:
                # Bind every in-slice keyset checkpoint to the exact lower
                # boundary that produced it. Without this assignment page N
                # encoded only the upper boundary + before key and could resume
                # from a newly scheduled wider slice, skipping older rows.
                active_slice_start = slice_start
            active_width = slice_end - slice_start

            seed_before_start_time = before_start_time
            seed_before_id = before_id
            seed_query, seed_params = seed_page_builder(
                slice_start=slice_start,
                slice_end=slice_end,
                limit=candidate_limit,
                before_start_time=seed_before_start_time,
                before_id=seed_before_id,
            )
            try:
                seed_result = execute(
                    kind="seed",
                    query=seed_query,
                    params=seed_params,
                    active_start=slice_start,
                    active_end=slice_end,
                )
            except _BudgetExceeded as exc:
                if (
                    # Cursor reads can safely retry a failed wide seed at a
                    # narrower adjacent window without changing predicates or
                    # publishing unclassified rows. This prevents a dense
                    # initial slice from repeatedly returning the same token.
                    retry_wide_read_budget
                    and exc.error_code == "read_budget_exceeded"
                    and active_width > _INITIAL_SLICE
                ):
                    reduced_width_cap = max(_INITIAL_SLICE, active_width / 2)
                    forced_width_cap = (
                        reduced_width_cap
                        if forced_width_cap is None
                        else min(forced_width_cap, reduced_width_cap)
                    )
                    retry_slice_width = _INITIAL_SLICE
                    active_slice_start = None
                    before_start_time = None
                    before_id = None
                    continue
                raise
            seed_rows = sorted(seed_result.data, key=seed_row_key, reverse=True)
            new_candidate_rows: list[dict[str, Any]] = []
            for row in seed_rows:
                public_identity = str(row.get(key_field, ""))
                raw_identity = seed_identity(row)
                if not public_identity or raw_identity in seen_seed_ids:
                    continue
                seen_seed_ids.add(raw_identity)
                new_candidate_rows.append(row)

            prefix_is_proven = classify_or_buffer_seed_rows(
                new_candidate_rows,
                active_start=slice_start,
                active_end=slice_end,
                stop_on_ordered_prefix=seed_proves_result_order,
            )
            if (
                seed_proves_population_bound
                and len(matched_by_id) + len(pending_identity_candidates)
                >= prefix_needed
            ):
                # Classify the final sub-100 candidate remainder immediately
                # when it can establish the oversize sentinel. Otherwise a
                # dense 10k+1 set would waste seed reads walking older slices
                # while one unclassified trace already proves rejection.
                classify_or_buffer_seed_rows(
                    [],
                    active_start=slice_start,
                    active_end=slice_end,
                    force=True,
                )
            if seed_proves_population_bound and len(matched_by_id) >= prefix_needed:
                # The caller needs only one of two exact proofs: complete
                # exhaustion at or below its buffer, or one classified
                # sentinel above it. The latter is enough to reject the task
                # before witness replay and before any task row can be written;
                # unordered rows are never exposed as a public list page.
                page_complete = True
                break
            if (
                not prefix_is_proven
                and seed_proves_result_order
                and pending_identity_candidates
                and (
                    not eager_identity_prefix_flush_used
                    or allow_repeated_eager_identity_prefix_flushes
                )
                and len(matched_by_id) + len(pending_identity_candidates)
                >= prefix_needed
            ):
                # Do not defer a partial classifier batch when it can close
                # the public prefix.  If that batch is rejected by latest
                # state, let the next adjacent slices accumulate another
                # sufficient batch and retry; waiting for the entire request
                # window would make a sparse twelve-month page scan months
                # after its newest exact rows were already available.
                eager_identity_prefix_flush_used = True
                prefix_is_proven = classify_or_buffer_seed_rows(
                    [],
                    active_start=slice_start,
                    active_end=slice_end,
                    stop_on_ordered_prefix=True,
                    force=True,
                )

            if prefix_is_proven:
                page_complete = True
                break

            slice_exhausted = len(seed_rows) < candidate_limit
            if (
                not pending_identity_candidates
                and seed_proves_result_order
                and len(matched_by_id) >= prefix_needed
            ):
                # A 100k background selection can require hundreds of finite
                # seed pages. Sorting the growing prefix after every page is
                # quadratic work; no cutoff can be proven before the sentinel
                # exists, so sort exactly once when it can affect control flow.
                ordered_matches = sorted(
                    matched_by_id.values(), key=result_row_key, reverse=True
                )
                cutoff = result_row_key(ordered_matches[prefix_needed - 1])
                prefix_is_proven = (
                    cutoff[0] >= slice_start
                    if slice_exhausted
                    else cutoff >= seed_row_key(seed_rows[-1])
                )
                if prefix_is_proven:
                    page_complete = True
                    break

            if not slice_exhausted:
                if not seed_rows:
                    break
                next_start_time, next_id = seed_row_key(seed_rows[-1])
                if (next_start_time, next_id) == (
                    seed_before_start_time,
                    seed_before_id,
                ):
                    break
                if bounded_continuation and pending_identity_candidates:
                    classify_or_buffer_seed_rows(
                        [],
                        active_start=slice_start,
                        active_end=slice_end,
                        stop_on_ordered_prefix=seed_proves_result_order,
                        force=True,
                    )
                before_start_time, before_id = next_start_time, next_id
                if bounded_continuation:
                    checkpoint_continuation()
                continue

            if slice_start <= request_start:
                classify_or_buffer_seed_rows(
                    [],
                    active_start=request_start,
                    active_end=request_end,
                    stop_on_ordered_prefix=seed_proves_result_order,
                    force=True,
                )
                page_complete = True
                break
            if bounded_continuation and pending_identity_candidates:
                classify_or_buffer_seed_rows(
                    [],
                    active_start=slice_start,
                    active_end=slice_end,
                    stop_on_ordered_prefix=seed_proves_result_order,
                    force=True,
                )
            slice_end = slice_start
            slice_width = min(active_width * 2, max_slice_width)
            active_slice_start = (
                max(request_start, slice_end - slice_width)
                if carry_continuation_slice_width and slice_end > request_start
                else None
            )
            before_start_time = None
            before_id = None
            if bounded_continuation:
                checkpoint_continuation()
                if (
                    identity_only_classification
                    and seed_proves_result_order
                    and matched_by_id
                    and len(matched_by_id) < prefix_needed
                    and not fill_bounded_cursor_page_across_slices
                ):
                    # Every root in this half-open slice has crossed the exact
                    # latest-state classifier. Its matches are therefore a
                    # canonical ordered prefix, even when the next older slice
                    # cannot fit in this request. Hydration below remains the
                    # publication gate and the signed checkpoint proves that
                    # the window is not exhausted.
                    break
    except _BudgetExceeded as exc:
        page_complete = False
        degraded_error_code = exc.error_code
        if bounded_continuation:
            # A classifier may have completed one sub-batch before the next
            # sub-batch hits a cap. Roll unfinished work back to the last fully
            # classified ordered prefix; the signed continuation resumes after
            # it, so no row can be skipped or published twice.
            seen_seed_ids.clear()
            seen_seed_ids.update(safe_seen_seed_ids)
            seen_candidate_ids.clear()
            seen_candidate_ids.update(safe_seen_candidate_ids)
            matched_by_id.clear()
            matched_by_id.update(safe_matched_by_id)
            pending_identity_candidates.clear()
            pending_identity_candidates.update(safe_pending_identity_candidates)
            slice_end = safe_slice_end
            active_slice_start = safe_active_slice_start
            before_start_time = safe_before_start_time
            before_id = safe_before_id

    # A response may never claim completeness after a required ClickHouse
    # statement failed, even if a later narrower fallback happened to find a
    # sufficient prefix. The exact-zero probe is the sole exception: its
    # contract treats every failure as inconclusive and deliberately restores
    # the unchanged required seed/classify path. Keep the failed probe in the
    # attempt telemetry, but do not let it invalidate a subsequently proven
    # exact page. Required seed, classify, and hydration failures remain fatal.
    failed_attempt = next(
        (
            attempt
            for attempt in attempts
            if attempt.error_code is not None and attempt.kind != "zero_probe"
        ),
        None,
    )
    if failed_attempt is not None:
        page_complete = False
        degraded_error_code = failed_attempt.error_code

    ordered_matches = sorted(matched_by_id.values(), key=result_row_key, reverse=True)
    offset = page_number * page_size
    has_more = len(ordered_matches) > offset + page_size
    page_rows = (
        ordered_matches[offset : offset + page_size]
        if page_complete or include_incomplete_rows
        else []
    )

    # The identity classifier deliberately returns only the stable ordering
    # tuple.  Partial cursor pages are publishable too, so hydrate every
    # non-empty publishable identity page rather than only complete pages.
    # Otherwise a bounded continuation can return genuine identities with
    # blank/incorrect presentation fields.
    if identity_only_classification and page_rows:
        try:
            hydration_query, hydration_params = page_hydration_builder(page_rows)
            hydration_result = execute(
                kind="hydrate",
                query=hydration_query,
                params=hydration_params,
                active_start=request_start,
                active_end=request_end,
                result_limit=page_size,
                timeout_cap_ms=reserved_hydration_ms,
                use_reserved_query_budget=True,
            )
            expected_by_id = {row_identity(row): row for row in page_rows}
            hydrated_by_id = {
                row_identity(row): row for row in hydration_result.data or []
            }
            hydration_is_stable = (
                len(expected_by_id) == len(page_rows)
                and len(hydrated_by_id) == len(hydration_result.data or [])
                and set(hydrated_by_id) == set(expected_by_id)
                and all(
                    result_row_key(hydrated_by_id[identity])
                    == result_row_key(expected_row)
                    and hydration_identity_builder(hydrated_by_id[identity])
                    == hydration_identity_builder(expected_row)
                    for identity, expected_row in expected_by_id.items()
                )
            )
            if not hydration_is_stable:
                page_complete = False
                degraded_error_code = "classification_drift"
                page_rows = []
                has_more = False
                # The committed scan checkpoint is after these identities.
                # Without a hydrated page we must not hand that checkpoint to
                # the caller or the matching rows would be skipped forever.
                rollback_unhydrated_page()
            else:
                page_rows = sorted(
                    hydrated_by_id.values(), key=result_row_key, reverse=True
                )
        except _BudgetExceeded as exc:
            page_complete = False
            degraded_error_code = exc.error_code
            page_rows = []
            has_more = False
            rollback_unhydrated_page()

    error_code = (
        None if page_complete else degraded_error_code or "scan_budget_exceeded"
    )
    return BoundedFilterPage(
        # Raw seeds are not latest-state matches. Even graph callers that opt
        # into incomplete rows may expose only the outer union classifier's
        # result, never this acquisition phase.
        rows=[] if defer_classification else page_rows,
        has_more=has_more if page_complete or include_incomplete_rows else False,
        complete=page_complete,
        status="complete" if page_complete else "degraded",
        error_code=error_code,
        total_rows_lower_bound=len(ordered_matches),
        elapsed_ms=(monotonic() - started) * 1000,
        query_count=len(attempts),
        rows_returned=sum(attempt.rows_returned for attempt in attempts),
        result_payload_bytes=sum(attempt.result_payload_bytes for attempt in attempts),
        attempts=tuple(attempts),
        deferred_candidate_rows=tuple(deferred_candidate_by_id.values()),
        classification_deferred=defer_classification,
        continuation_slice_start=(
            safe_active_slice_start
            if bounded_continuation and not page_complete and continuation_progressed
            else None
        ),
        continuation_slice_end=(
            safe_slice_end
            if bounded_continuation and not page_complete and continuation_progressed
            else None
        ),
        continuation_before_start_time=(
            safe_before_start_time
            if bounded_continuation and not page_complete and continuation_progressed
            else None
        ),
        continuation_before_id=(
            safe_before_id
            if bounded_continuation and not page_complete and continuation_progressed
            else None
        ),
    )


def read_bounded_filter_neighbors(
    *,
    builder: FilterPageBuilder,
    analytics: QueryExecutor,
    filters: list[dict[str, Any]],
    key_field: str,
    target_id: str,
    deadline_ms: int,
    scan_limit: int,
    page_size: int = 200,
    max_query_count: int = 128,
    require_unique_target: bool = False,
    read_settings: dict[str, Any] | None = None,
) -> BoundedFilterNeighbors:
    """Resolve one target and its exact neighbours from target-anchored seeds.

    Each direction starts at the target's canonical order key.  The builder
    emits root/span seeds in the direction's order and the existing finite
    latest-state classifier applies the complete filter vector.  A side closes
    only after its best classified row crosses the raw seed cutoff or the
    current adjacent time slice is exhausted.  No partial side is published.
    """

    if not key_field or not target_id:
        raise ValueError("navigation key and target must be non-empty")
    if deadline_ms <= 0 or scan_limit <= 0 or page_size <= 0:
        raise ValueError("navigation bounds must be positive")
    if page_size > _ABSOLUTE_MAX_CANDIDATES:
        raise ValueError("navigation page exceeds bounded candidate limit")
    if not 1 <= max_query_count <= _ABSOLUTE_MAX_QUERIES:
        raise ValueError("navigation query count exceeds bounded read contract")

    target_query_builder = getattr(
        builder, "build_filter_navigation_target_query", None
    )
    seed_page_builder = getattr(builder, "build_filter_navigation_seed_page", None)
    match_query_builder = getattr(
        builder, "build_filter_match_query_from_seed_rows", None
    )
    order_token_builder = getattr(builder, "bounded_filter_row_order_token", None)
    seed_order_token_builder = getattr(
        builder, "bounded_filter_seed_order_token", order_token_builder
    )
    row_identity_builder = getattr(builder, "bounded_filter_row_identity", None)
    seed_identity_builder = getattr(
        builder, "bounded_filter_seed_identity", row_identity_builder
    )
    if not all(
        callable(value)
        for value in (
            target_query_builder,
            seed_page_builder,
            match_query_builder,
            order_token_builder,
            seed_order_token_builder,
            row_identity_builder,
            seed_identity_builder,
        )
    ):
        raise ValueError("navigation builder is missing its bounded protocol")

    request_start, request_end = builder.parse_time_range(filters)
    request_start = _without_timezone(request_start)
    request_end = _without_timezone(request_end)
    started = monotonic()
    deadline = started + (deadline_ms / 1000)
    query_count = 0
    rows_scanned = 0
    newer: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    older: dict[str, Any] | None = None

    def result(*, complete: bool, error_code: str | None) -> BoundedFilterNeighbors:
        return BoundedFilterNeighbors(
            newer=newer,
            current=current,
            older=older,
            complete=complete,
            error_code=error_code,
            query_count=query_count,
            rows_scanned=rows_scanned,
        )

    def execute(
        query: str,
        params: dict[str, Any],
        *,
        result_limit: int,
        active_deadline: float,
        side_query_limit: int | None = None,
        side_query_start: int = 0,
    ) -> QueryResult:
        nonlocal query_count
        remaining_ms = int((min(deadline, active_deadline) - monotonic()) * 1000)
        if remaining_ms < 25:
            raise _BudgetExceeded("deadline_exceeded")
        if query_count >= max_query_count:
            raise _BudgetExceeded("query_budget_exceeded")
        if (
            side_query_limit is not None
            and query_count - side_query_start >= side_query_limit
        ):
            raise _BudgetExceeded("query_budget_exceeded")
        query_count += 1
        settings = {
            **_READ_SETTINGS,
            **(read_settings or {}),
            "max_result_rows": result_limit,
        }
        try:
            return analytics.execute_ch_query(
                query,
                params,
                timeout_ms=min(_QUERY_TIMEOUT_MS, remaining_ms),
                settings=settings,
            )
        except Exception as exc:
            code = (
                "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
            )
            raise _BudgetExceeded(code) from None

    try:
        target_query, target_params = target_query_builder(
            target_id=target_id,
            result_limit=2,
        )
        if not target_query:
            return result(complete=False, error_code="target_not_found")
        target_result = execute(
            target_query,
            target_params,
            result_limit=2,
            active_deadline=deadline,
        )
    except _BudgetExceeded as exc:
        return result(complete=False, error_code=exc.error_code)

    target_rows = list(target_result.data or [])
    if not target_rows:
        return result(complete=False, error_code="target_not_found")
    if len(target_rows) > 1 or (require_unique_target and len(target_rows) != 1):
        return result(complete=False, error_code="ambiguous_identity")
    current = target_rows[0]
    if str(current.get(key_field) or "") != target_id:
        return result(complete=False, error_code="classification_drift")
    current_start = current.get("start_time")
    if not isinstance(current_start, datetime):
        return result(complete=False, error_code="invalid_cursor_identity")
    current_key = (
        _without_timezone(current_start),
        order_token_builder(current),
    )
    if not request_start <= current_key[0] < request_end:
        return result(complete=False, error_code="classification_drift")

    recommendation = getattr(builder, "recommended_filter_classify_batch_size", None)
    raw_batch_size = recommendation() if callable(recommendation) else 200
    classify_batch_size = min(page_size, int(raw_batch_size or 200))
    if classify_batch_size <= 0:
        return result(complete=False, error_code="invalid_read_contract")

    def read_side(
        direction: str,
        *,
        side_deadline: float,
        side_query_limit: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        nonlocal rows_scanned
        side_query_start = query_count
        seen_identities: set[Hashable] = set()
        best_row: dict[str, Any] | None = None
        best_key: tuple[datetime, Any] | None = None
        side_rows_scanned = 0
        side_seed_queries = 0
        width = _INITIAL_SLICE
        if direction == "older":
            slice_end = min(request_end, current_key[0] + timedelta(microseconds=1))
            slice_start = max(request_start, slice_end - width)
        else:
            slice_start = max(request_start, current_key[0])
            slice_end = min(request_end, slice_start + width)
        cursor_start_time: datetime | None = current_key[0]
        cursor_order_token: Any = current_key[1]

        def next_coverage_width(remaining_window: timedelta) -> timedelta:
            """Schedule the remaining side inside its finite query envelope.

            The normal two-day slice ceiling is useful for dense windows, but
            would need roughly 183 empty probes to prove a one-year boundary.
            Navigation reserves enough statements to classify one full final
            seed page, then distributes the unvisited time across every seed
            statement still available to this side.  The adaptive width may
            therefore exceed two days while every individual query retains the
            existing time, row, byte, memory, and single-thread limits.
            """

            remaining_queries = max(
                side_query_limit - (query_count - side_query_start),
                1,
            )
            remaining_rows = max(scan_limit - side_rows_scanned, 1)
            next_seed_limit = min(page_size, remaining_rows)
            queries_per_full_page = 1 + ceil(next_seed_limit / classify_batch_size)
            query_page_attempts = max(
                remaining_queries // queries_per_full_page,
                1,
            )
            row_page_attempts = max(ceil(remaining_rows / page_size), 1)
            bounded_seed_attempts = max(
                _MAX_SEED_ATTEMPTS - side_seed_queries,
                1,
            )
            remaining_seed_attempts = min(
                query_page_attempts,
                row_page_attempts,
                bounded_seed_attempts,
            )
            return max(width, remaining_window / remaining_seed_attempts)

        while slice_start < slice_end and side_rows_scanned < scan_limit:
            remaining_rows = scan_limit - side_rows_scanned
            seed_limit = min(page_size, remaining_rows)
            try:
                seed_query, seed_params = seed_page_builder(
                    direction=direction,
                    slice_start=slice_start,
                    slice_end=slice_end,
                    limit=seed_limit,
                    cursor_start_time=cursor_start_time,
                    cursor_order_token=cursor_order_token,
                )
                seed_result = execute(
                    seed_query,
                    seed_params,
                    result_limit=seed_limit,
                    active_deadline=side_deadline,
                    side_query_limit=side_query_limit,
                    side_query_start=side_query_start,
                )
                side_seed_queries += 1
            except _BudgetExceeded as exc:
                return None, exc.error_code

            seed_rows = list(seed_result.data or [])
            if len(seed_rows) > seed_limit:
                return None, "row_limit_exceeded"
            side_rows_scanned += len(seed_rows)
            rows_scanned += len(seed_rows)
            seed_keys: list[tuple[datetime, Any]] = []
            for seed_row in seed_rows:
                seed_start = seed_row.get("start_time")
                if not isinstance(seed_start, datetime):
                    return None, "invalid_cursor_identity"
                seed_keys.append(
                    (
                        _without_timezone(seed_start),
                        seed_order_token_builder(seed_row),
                    )
                )
            if any(
                (left <= right if direction == "older" else left >= right)
                for left, right in zip(seed_keys, seed_keys[1:], strict=False)
            ):
                return None, "seed_order_drift"

            unseen_rows: list[dict[str, Any]] = []
            for seed_row in seed_rows:
                identity = seed_identity_builder(seed_row)
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                unseen_rows.append(seed_row)

            for offset in range(0, len(unseen_rows), classify_batch_size):
                batch = unseen_rows[offset : offset + classify_batch_size]
                match_query, match_params = match_query_builder(batch)
                if not match_query:
                    continue
                try:
                    match_result = execute(
                        match_query,
                        match_params,
                        result_limit=len(batch),
                        active_deadline=side_deadline,
                        side_query_limit=side_query_limit,
                        side_query_start=side_query_start,
                    )
                except _BudgetExceeded as exc:
                    return None, exc.error_code
                batch_identities = {seed_identity_builder(row) for row in batch}
                for match_row in match_result.data or []:
                    if row_identity_builder(match_row) not in batch_identities:
                        return None, "classification_drift"
                    match_start = match_row.get("start_time")
                    if not isinstance(match_start, datetime):
                        return None, "classification_drift"
                    match_key = (
                        _without_timezone(match_start),
                        order_token_builder(match_row),
                    )
                    if direction == "older":
                        if not match_key < current_key:
                            continue
                        if best_key is None or match_key > best_key:
                            best_row, best_key = match_row, match_key
                    else:
                        if not match_key > current_key:
                            continue
                        if best_key is None or match_key < best_key:
                            best_row, best_key = match_row, match_key

            slice_exhausted = len(seed_rows) < seed_limit
            cutoff = seed_keys[-1] if seed_keys else None
            cutoff_closes = bool(
                best_key is not None
                and cutoff is not None
                and (best_key >= cutoff if direction == "older" else best_key <= cutoff)
            )
            best_in_active_slice = bool(
                best_key is not None and slice_start <= best_key[0] < slice_end
            )
            if cutoff_closes or (
                slice_exhausted and best_row is not None and best_in_active_slice
            ):
                return best_row, None

            if side_rows_scanned >= scan_limit:
                return None, PAGE_DEPTH_EXCEEDED_CODE

            if not slice_exhausted:
                if not seed_rows:
                    return None, "cursor_stalled"
                cursor_start_time, cursor_order_token = seed_keys[-1]
                continue

            if direction == "older":
                if slice_start <= request_start:
                    return None, None
                slice_end = slice_start
                width = min(width * 2, _MAX_SLICE)
                active_width = next_coverage_width(slice_end - request_start)
                slice_start = max(request_start, slice_end - active_width)
            else:
                if slice_end >= request_end:
                    return None, None
                slice_start = slice_end
                width = min(width * 2, _MAX_SLICE)
                active_width = next_coverage_width(request_end - slice_start)
                slice_end = min(request_end, slice_start + active_width)
            cursor_start_time = None
            cursor_order_token = None

        if side_rows_scanned >= scan_limit:
            return None, PAGE_DEPTH_EXCEEDED_CODE
        return None, None

    remaining_seconds = max(deadline - monotonic(), 0.0)
    side_seconds = remaining_seconds / 2
    side_query_limit = max((max_query_count - query_count) // 2, 1)
    older, older_error = read_side(
        "older",
        side_deadline=min(deadline, monotonic() + side_seconds),
        side_query_limit=side_query_limit,
    )
    if older_error:
        return result(complete=False, error_code=older_error)
    newer, newer_error = read_side(
        "newer",
        side_deadline=deadline,
        # The older side retains its fixed half-budget so the newer side can
        # always start. Once it finishes, every unused statement is safely
        # available to the final side under the unchanged global ceiling.
        side_query_limit=max(max_query_count - query_count, 1),
    )
    if newer_error:
        return result(complete=False, error_code=newer_error)
    return result(complete=True, error_code=None)


__all__ = [
    "BoundedFilterNeighbors",
    "BoundedFilterPage",
    "FilterReadAttempt",
    "PAGE_DEPTH_EXCEEDED_CODE",
    "PAGE_DEPTH_EXCEEDED_MESSAGE",
    "MAX_NUMBERED_PAGE_WORK_ROWS",
    "bounded_numbered_page_depth_exceeded",
    "degraded_bounded_filter_page",
    "numbered_page_depth_exceeded",
    "read_bounded_filter_neighbors",
    "read_bounded_filter_page",
]
