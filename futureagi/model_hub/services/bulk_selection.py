"""Filter-based bulk selection resolvers for annotation queue add-items.

These functions mirror the filter application pipeline of the corresponding
list views (e.g. ``tracer.views.trace.list_traces_of_session`` for traces)
and return the matching row IDs capped at ``cap``, with the deselected-rows
set subtracted. They are the server-side equivalent of "Select all N matching
this filter" in the UI.

Do not add presentation/column logic here — this module returns IDs only.

Scope in this module:

- ``resolve_filtered_trace_ids`` — Phase 1. Mirrors
  ``list_traces_of_session`` filter semantics for ``source_type="trace"``.

Resolvers cover ``trace`` (+voice), ``observation_span``, ``trace_session``,
and ``call_execution``.

ClickHouse migration status:

  Each resolver mirrors its grid's list view by instantiating the SAME v2
  ``query_builders`` list builder (``TraceListQueryBuilder`` /
  ``VoiceCallListQueryBuilder`` / ``SpanListQueryBuilder`` /
  ``SessionListQueryBuilder``) through the ``ClickHouseFilterBuilder`` translator,
  so filter semantics match the grid exactly.

  - trace, voice, span, session: ClickHouse ONLY. No PG tracer-table read — rows
    come only from CH. When the payload sends no time bound, an all-history
    window is injected (``_all_history_time_filter``) so "select all matching"
    spans everything instead of the builders' now-30d default. Drop-safe: a CH
    failure propagates rather than falling back, and an empty CH result is
    authoritative. Session score-label filters intersect the annotation ``Score``
    table, which is NOT a tracer table.

  Project / annotation-label / ``Score`` PG lookups stay — those tables are not
  being dropped. ``call_execution`` resolves from the ``simulate`` PG tables,
  which are also not tracer tables.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.db.models import Q

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from simulate.models.test_execution import CallExecution
from simulate.utils.persona_filtering import (
    UnsupportedPersonaFilter,
    apply_persona_filter,
    is_persona_filter_column,
)
from tfc.settings.runtime_setting_specs import bounded_bulk_worst_case_query_count
from tracer.models.project import Project, ProjectSourceChoices
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.list_cursor import ListCursor, frozen_window_filter
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.utils.filters import (
    apply_created_at_filters,
    normalize_filter_item,
)
from tracer.utils.helper import get_annotation_labels_for_project

logger = structlog.get_logger(__name__)


class _ConfiguredEvalFilterBuilderV2(ClickHouseFilterBuilderV2):
    """Compile CH25 span filters against the configured authoritative eval table."""

    _eval_logger_source = staticmethod(eval_logger_source)


def _use_authoritative_eval_source(builder):
    """Pair V2 span SQL with the configured eval table on the CH25 connection.

    Production currently writes eval rows to the legacy-named
    ``tracer_eval_logger`` table on the direct CH25 cluster. ``eval_logger_source``
    owns the table-specific version and liveness predicates, while the explicit
    V2 service at each caller owns the connection. Keeping those two decisions
    separate prevents an eval-table name from routing span reads to legacy CH.
    """

    builder._FILTER_BUILDER_CLS = _ConfiguredEvalFilterBuilderV2
    if hasattr(builder, "_EVAL_LOGGER_SOURCE"):
        builder._EVAL_LOGGER_SOURCE = eval_logger_source
    return builder


@dataclass
class ResolveResult:
    """Result of a filter-based ID resolution."""

    ids: list[UUID | str]
    total_matching: int
    truncated: bool
    continuation: ListCursor | None = None


_USER_SCOPED_COLUMN_IDS = {"my_annotations", "annotator"}

# The shared bounded selector permits at most 128 finite ClickHouse queries.
# A 200-row seed is intentional: the trace/span classifiers accept at most 200
# identities, so each seed then needs exactly one candidate-scoped classifier.
# Sixty-four seed/classifier pairs can therefore prove a 12,800-row prefix
# without ever falling back to a broad list query.
_MAX_BOUNDED_BULK_CAP = settings.BULK_SELECTION_MAX_CAP
_BULK_BOUNDED_DEADLINE_MS = settings.BULK_SELECTION_DEADLINE_MS
_BULK_BOUNDED_MAX_SEED_ATTEMPTS = settings.BULK_SELECTION_MAX_SEED_ATTEMPTS
_BULK_BOUNDED_MAX_QUERY_COUNT = settings.BULK_SELECTION_MAX_QUERY_COUNT
_BULK_BOUNDED_MAX_CANDIDATES = settings.BULK_SELECTION_MAX_CANDIDATES
_BULK_BOUNDED_CLASSIFY_BATCH_SIZE = settings.BULK_SELECTION_CLASSIFY_BATCH_SIZE
# ``read_bounded_filter_page`` proves one row beyond ``page_size``.  A raw page
# of 12,799 therefore consumes the complete 12,800-row proof budget.  The
# independent exclusion ceiling protects the service from an unbounded request
# payload; the raw-page check below derives the tighter limit for a given cap
# (2,798 exclusions at the public 10,000-item cap).
_MAX_BOUNDED_BULK_RAW_PAGE_SIZE = settings.BULK_SELECTION_MAX_RAW_PAGE_SIZE
_MAX_BOUNDED_BULK_EXCLUDE_COUNT = settings.BULK_SELECTION_MAX_EXCLUDE_COUNT


class BulkSelectionReadIncomplete(RuntimeError):
    """The finite latest-state scan could not prove a complete ID prefix."""


class BulkSelectionAmbiguousIdentity(RuntimeError):
    """A bare queue-item ID maps to multiple matching physical entities."""


def _optional_deadline_kwargs(deadline: ReadDeadline | None) -> dict:
    """Preserve legacy internal call shapes when no request wall is supplied."""

    return {"deadline": deadline} if deadline is not None else {}


def _bounded_bulk_worst_case_query_count(raw_page_size: int) -> int:
    """Count per-seed classifier queries needed to prove a raw page prefix."""

    return bounded_bulk_worst_case_query_count(
        raw_page_size=raw_page_size,
        max_candidates=_BULK_BOUNDED_MAX_CANDIDATES,
        classify_batch_size=_BULK_BOUNDED_CLASSIFY_BATCH_SIZE,
    )


def _bounded_bulk_classify_batch_size(
    *, cap: int, exclude_count: int, preferred: int
) -> int:
    """Keep a preferred classifier batch while fitting the finite query budget.

    Voice simulator classification intentionally prefers 50 candidates because
    it parses ``raw_log`` JSON. A 10,000-item queue selection cannot be proven
    in 128 queries with a fixed batch of 50, however: the bounded reader would
    reject it before touching ClickHouse even when the project had no calls.
    Increase the batch only as much as the requested prefix requires, capped by
    the existing 200-candidate working-set ceiling.
    """

    raw_page_size = cap + 1 + exclude_count
    prefix_needed = raw_page_size + 1
    seed_queries = (
        prefix_needed + _BULK_BOUNDED_MAX_CANDIDATES - 1
    ) // _BULK_BOUNDED_MAX_CANDIDATES
    remaining_classifier_queries = _BULK_BOUNDED_MAX_QUERY_COUNT - seed_queries
    if remaining_classifier_queries <= 0:
        return _BULK_BOUNDED_MAX_CANDIDATES
    minimum_batch = (
        prefix_needed + remaining_classifier_queries - 1
    ) // remaining_classifier_queries
    return min(
        _BULK_BOUNDED_MAX_CANDIDATES,
        max(int(preferred), minimum_batch),
    )


def _supports_bounded_bulk_prefix(*, cap: int, exclude_count: int) -> bool:
    """Return whether cap+1 non-excluded IDs fit the finite proof budget."""

    raw_page_size = cap + 1 + exclude_count
    return (
        1 <= cap <= _MAX_BOUNDED_BULK_CAP
        and 0 <= exclude_count <= _MAX_BOUNDED_BULK_EXCLUDE_COUNT
        and raw_page_size <= _MAX_BOUNDED_BULK_RAW_PAGE_SIZE
        and _bounded_bulk_worst_case_query_count(raw_page_size)
        <= _BULK_BOUNDED_MAX_QUERY_COUNT
    )


def _cursor_order_token(cursor: ListCursor | None) -> Any:
    if cursor is None:
        return None
    if len(cursor.order) < 2 or not isinstance(cursor.order[0], datetime):
        raise ValueError("invalid bulk-selection cursor order")
    return cursor.order[1] if len(cursor.order) == 2 else tuple(cursor.order[1:])


def _bulk_row_order(*, builder, row: dict[str, Any]) -> tuple[Any, ...]:
    start_time = row.get("_seed_order_start") or row.get("start_time")
    if not isinstance(start_time, datetime):
        raise BulkSelectionReadIncomplete("selection_cursor_order_unavailable")
    token = builder.bounded_filter_row_order_token(row)
    if isinstance(token, tuple):
        return (start_time, *token)
    return start_time, token


def _bulk_partial_order(
    *,
    builder,
    key_field: str,
    rows: list[dict[str, Any]],
    page,
    cursor: ListCursor | None,
) -> tuple[Any, ...]:
    """Return the consumed raw boundary, including checkpoint-only progress."""

    if rows:
        return _bulk_row_order(builder=builder, row=rows[-1])
    if cursor is not None:
        return tuple(cursor.order)
    checkpoint_time = getattr(page, "continuation_before_start_time", None) or getattr(
        page, "continuation_slice_end", None
    )
    if not isinstance(checkpoint_time, datetime):
        raise BulkSelectionReadIncomplete("selection_cursor_checkpoint_unavailable")
    checkpoint_token = getattr(page, "continuation_before_id", None)
    if checkpoint_token is None:
        sentinel = "\U0010ffff"
        checkpoint_token = builder.bounded_filter_row_order_token(
            {
                key_field: sentinel,
                "trace_id": sentinel,
                "project_id": sentinel,
                "session_id": sentinel,
            }
        )
    if isinstance(checkpoint_token, tuple):
        return checkpoint_time, *checkpoint_token
    return checkpoint_time, checkpoint_token


def _resumable_bounded_result(
    *,
    builder,
    filters: list[dict],
    page,
    rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    key_field: str,
    cap: int,
    cursor: ListCursor | None,
) -> ResolveResult:
    """Publish one exact batch and retain every unconsumed scan boundary."""

    overflow = len(selected_rows) > cap
    published_rows = selected_rows[:cap]
    ids = [str(row[key_field]) for row in published_rows]
    checkpoint_progress = bool(
        not getattr(page, "complete", True)
        and getattr(page, "continuation_slice_end", None) is not None
    )
    has_more = bool(overflow or getattr(page, "has_more", False) or checkpoint_progress)
    seen_rows = (cursor.seen_rows if cursor is not None else 0) + len(ids)
    total_matching = seen_rows + (1 if overflow else 0)
    if not has_more:
        return ResolveResult(
            ids=ids,
            total_matching=total_matching,
            truncated=False,
        )

    # If a selected sentinel overflowed the batch, resume after the last
    # published selected row so that sentinel is reconsidered next time. If all
    # selected rows fit, every raw row has already been checked (including score
    # and exclusion predicates), so advance after the final raw row instead.
    boundary_rows = published_rows if overflow else rows
    order = _bulk_partial_order(
        builder=builder,
        key_field=key_field,
        rows=boundary_rows,
        page=page,
        cursor=cursor,
    )
    if cursor is not None:
        window_start, window_end = cursor.window_start, cursor.window_end
    else:
        window_start, window_end = builder.parse_time_range(filters)
    retain_scan_checkpoint = not getattr(page, "has_more", False) and not overflow
    continuation = ListCursor(
        window_start=window_start,
        window_end=window_end,
        order=order,
        seen_rows=seen_rows,
        scan_slice_start=(
            getattr(page, "continuation_slice_start", None)
            if retain_scan_checkpoint
            else None
        ),
        scan_slice_end=(
            getattr(page, "continuation_slice_end", None)
            if retain_scan_checkpoint
            else None
        ),
        scan_before_start_time=(
            getattr(page, "continuation_before_start_time", None)
            if retain_scan_checkpoint
            else None
        ),
        scan_before_id=(
            getattr(page, "continuation_before_id", None)
            if retain_scan_checkpoint
            else None
        ),
    )
    return ResolveResult(
        ids=ids,
        total_matching=total_matching,
        truncated=True,
        continuation=continuation,
    )


def _read_bounded_bulk_page(
    *,
    builder,
    analytics,
    filters,
    key_field,
    cap,
    exclude_count=0,
    classify_batch_size=None,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
):
    """Resolve enough raw IDs to prove a cap+1 non-excluded prefix."""

    if classify_batch_size is None:
        classify_batch_size = _BULK_BOUNDED_CLASSIFY_BATCH_SIZE
    if not _supports_bounded_bulk_prefix(cap=cap, exclude_count=exclude_count):
        raise BulkSelectionReadIncomplete("selection_prefix_too_large")

    bounded_error_code = builder.bounded_filter_degraded_error_code()
    if not builder.supports_bounded_filter_scan():
        raise BulkSelectionReadIncomplete(
            bounded_error_code or "unsupported_bounded_filter"
        )

    from tracer.selectors.trace_filter_reads import read_bounded_filter_page

    selector_deadline_ms = (
        deadline.remaining_ms(floor_ms=1)
        if deadline is not None
        else _BULK_BOUNDED_DEADLINE_MS
    )
    wide_read_retry_recommendation = getattr(
        builder,
        "should_retry_filter_wide_read_budget",
        None,
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=analytics,
        filters=filters,
        key_field=key_field,
        page_number=0,
        # At most ``exclude_count`` raw matches can disappear below. Fetching
        # cap+1+exclude_count therefore proves whether cap+1 non-excluded IDs
        # exist without incorrectly treating an excluded sentinel as overflow.
        page_size=cap + 1 + exclude_count,
        deadline_ms=selector_deadline_ms,
        max_seed_attempts=_BULK_BOUNDED_MAX_SEED_ATTEMPTS,
        max_candidates=_BULK_BOUNDED_MAX_CANDIDATES,
        max_query_count=_BULK_BOUNDED_MAX_QUERY_COUNT,
        classify_batch_size=classify_batch_size,
        cursor_start_time=cursor.order[0] if cursor is not None else None,
        cursor_order_token=_cursor_order_token(cursor),
        continuation_slice_start=(
            cursor.scan_slice_start if cursor is not None else None
        ),
        continuation_slice_end=(cursor.scan_slice_end if cursor is not None else None),
        continuation_before_start_time=(
            cursor.scan_before_start_time if cursor is not None else None
        ),
        continuation_before_id=(cursor.scan_before_id if cursor is not None else None),
        retry_wide_read_budget=bool(
            wide_read_retry_recommendation()
            if callable(wide_read_retry_recommendation)
            else False
        ),
        include_incomplete_rows=resumable,
        bounded_continuation=resumable,
    )
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    if not page.complete and (
        not resumable
        or (not page.has_more and getattr(page, "continuation_slice_end", None) is None)
    ):
        raise BulkSelectionReadIncomplete(page.error_code or "scan_budget_exceeded")
    return page


def _filter_column_id(filter_item: dict) -> str:
    return normalize_filter_item(filter_item)["column_id"] or ""


def _filter_config(filter_item: dict) -> dict:
    return normalize_filter_item(filter_item)["filter_config"]


def _needs_bounded_internal_scan(filters: list[dict] | None) -> bool:
    """Enable the bounded reader for empty/time-only task selections.

    List builders normally reserve ``bounded_internal_scan`` for internal
    consumers.  Bulk selection is identity-only and has no residual predicate
    when its request contains only a time range, so enabling it here turns the
    formerly broad time-only path into the same finite seed/replay protocol.
    """

    return not any(
        _filter_column_id(filter_item) not in {"created_at", "start_time"}
        for filter_item in filters or []
    )


def _has_explicit_time_filter(filters: list[dict] | None) -> bool:
    """Return True only when the saved filter payload includes a real time bound.

    The ClickHouse list builders need a time range and default to an all-ish
    window when the UI did not send one. That is correct for interactive lists,
    but automation rules should not inherit an implicit time window: first run
    means all matching source rows, and later runs rely on QueueItem duplicate
    checks for the delta.
    """
    for filter_item in filters or []:
        column_id = _filter_column_id(filter_item)
        if column_id not in {"created_at", "start_time"}:
            continue
        config = _filter_config(filter_item)
        filter_type = config.get("filter_type")
        if filter_type not in {"datetime", "date"}:
            continue
        value = config.get("filter_value")
        if value not in (None, "", []):
            return True
    return False


def _validate_user_scoped_filters(filters, user):
    """Raise ValueError when filters reference user-scoped columns but no user is provided."""
    if user is not None:
        return
    for f in filters or []:
        col = _filter_column_id(f)
        if col in _USER_SCOPED_COLUMN_IDS:
            raise ValueError(
                f"Filter references user-scoped column {col!r} but user is None"
            )


def _project_matches_workspace(project, workspace):
    if workspace is None:
        return True
    project_workspace_id = getattr(project, "workspace_id", None)
    if project_workspace_id == getattr(workspace, "id", None):
        return True
    return project_workspace_id is None and getattr(workspace, "is_default", False)


def _resolve_voice_call_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    remove_simulation_calls: bool,
    annotation_label_ids: list[str],
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Resolve voice-call trace IDs via ClickHouse, mirroring ``list_voice_calls``.

    Uses ``VoiceCallListQueryBuilder`` so filter semantics — SPAN_ATTRIBUTE
    filters translated through ``ClickHouseFilterBuilder``, voice system
    metrics, simulator exclusion — match the voice grid exactly.

    ClickHouse is the sole backend for voice-call rows (the PG tracer tables
    are being dropped), so a ClickHouse failure propagates rather than silently
    resolving to a partial/empty set.
    """
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    analytics = V2AnalyticsQueryService()
    builder = _use_authoritative_eval_source(
        VoiceCallListQueryBuilderV2(
            project_id=str(project_id),
            page_number=0,
            page_size=cap + 1,
            filters=filters or [],
            annotation_label_ids=annotation_label_ids,
            remove_simulation_calls=remove_simulation_calls,
        )
    )
    if not builder.supports_bounded_filter_scan():
        raise BulkSelectionReadIncomplete(
            builder.bounded_filter_degraded_error_code() or "unsupported_bounded_filter"
        )

    # Voice selection uses the same finite latest-state reader as the public
    # voice grid. In particular, simulator exclusion is compiled into the
    # candidate-scoped V2 classifier, where ``span_attributes_raw`` /
    # ``span_attr_str`` are rewritten to ``attributes_extra`` / ``attrs_string``
    # before the statement reaches CH25. The legacy broad build + raw post-read
    # both targeted columns that do not exist in a direct-write-only cluster.
    try:
        bounded_page = _read_bounded_bulk_page(
            builder=builder,
            analytics=analytics,
            filters=filters or [],
            key_field="trace_id",
            cap=cap,
            exclude_count=len(exclude_ids or set()),
            # Prefer fifty fat voice roots per simulator classifier. For large
            # selections, grow only enough to keep cap+1 proof within the
            # selector's fixed 128-query ceiling; 200 remains the hard maximum.
            classify_batch_size=(
                _bounded_bulk_classify_batch_size(
                    cap=cap,
                    exclude_count=len(exclude_ids or set()),
                    preferred=builder.recommended_filter_classify_batch_size(),
                )
                if remove_simulation_calls
                else _BULK_BOUNDED_CLASSIFY_BATCH_SIZE
            ),
            cursor=cursor,
            resumable=resumable,
            **_optional_deadline_kwargs(deadline),
        )
        rows = bounded_page.rows
        bounded_has_more = bounded_page.has_more
    except Exception as exc:
        # CH is the sole voice backend (PG tracer tables dropped); fail closed.
        # Breadcrumb for log-based alerting; the re-raise carries the Sentry
        # error, so this stays WARNING to avoid a duplicate event.
        logger.warning(
            "bulk_selection_resolve_voice_ch_query_failed",
            project_id=str(project_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    excl = {str(i) for i in (exclude_ids or set())}
    selected_rows = [
        row for row in rows if row.get("trace_id") and str(row["trace_id"]) not in excl
    ]
    ids = [str(row["trace_id"]) for row in selected_rows]

    if resumable:
        return _resumable_bounded_result(
            builder=builder,
            filters=filters or [],
            page=bounded_page,
            rows=rows,
            selected_rows=selected_rows,
            key_field="trace_id",
            cap=cap,
            cursor=cursor,
        )

    # The bounded read overscans by the complete exclusion set. Its sentinel
    # therefore describes the post-exclusion set; never publish an unproven
    # prefix if a future builder violates that contract.
    if bounded_has_more and len(ids) <= cap:
        raise BulkSelectionReadIncomplete("excluded_prefix_unproven")
    truncated = len(ids) > cap
    ids = ids[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

    logger.info(
        "bulk_selection_resolve_trace_ch",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(exclude_ids or set()),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


def _resolve_trace_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    annotation_label_ids: list[str],
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Resolve regular trace IDs via ClickHouse, mirroring ``list_traces_of_session``.

    Uses ``TraceListQueryBuilder`` so filter semantics (especially
    SPAN_ATTRIBUTE filters translated through ``ClickHouseFilterBuilder``)
    match the non-voice grid exactly.

    ClickHouse is the sole backend for trace rows (the PG tracer tables are
    being dropped), so a ClickHouse failure propagates rather than silently
    resolving to a partial/empty set.
    """
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    analytics = V2AnalyticsQueryService()
    excl = {str(value) for value in (exclude_ids or set())}
    if not _supports_bounded_bulk_prefix(cap=cap, exclude_count=len(excl)):
        # A synchronous resolver returning one in-memory list cannot represent
        # an arbitrarily large prefix under the finite ClickHouse read budget.
        # Fail closed with a stable code; never re-enter the broad list query
        # that caused the production task-preview timeout.
        raise BulkSelectionReadIncomplete("selection_prefix_too_large")
    builder = _use_authoritative_eval_source(
        TraceListQueryBuilderV2(
            project_id=str(project_id),
            page_number=0,
            # Fetch cap+1 as the page size so a >cap result trips the truncation
            # sentinel below. Unlike the voice builder, the trace ``build()`` LIMIT
            # is exactly page_size (no internal +1), so request the extra row here.
            page_size=cap + 1,
            filters=filters or [],
            annotation_label_ids=annotation_label_ids,
            # Phase 1 light columns are all we need — we only want trace_id.
            columns=["trace_id"],
            # Identity-only is a projection choice. Empty/time-only requests enable
            # the internal-scan contract so they also use finite seed/replay reads;
            # filtered requests keep it off so candidate-scoped residual compilers
            # continue to handle eval/annotation/end-user predicates exactly.
            bounded_internal_scan=_needs_bounded_internal_scan(filters),
            bounded_identity_only=True,
            bounded_bulk_scan=True,
        )
    )
    if not builder.supports_bounded_filter_scan():
        raise BulkSelectionReadIncomplete(
            builder.bounded_filter_degraded_error_code() or "unsupported_bounded_filter"
        )
    # Skip the separate count query — the cap+1 page gives the "≥ cap" sentinel
    # without a second uniqExact scan (the dominant /preview timeout source).
    # ``build()`` dedups per trace (``LIMIT 1 BY trace_id``) so ``len > cap`` is
    # an honest distinct-trace count.
    try:
        bounded_page = _read_bounded_bulk_page(
            builder=builder,
            analytics=analytics,
            filters=filters or [],
            key_field="trace_id",
            cap=cap,
            exclude_count=len(excl),
            cursor=cursor,
            resumable=resumable,
            **_optional_deadline_kwargs(deadline),
        )
        rows = bounded_page.rows
        bounded_has_more = bounded_page.has_more
    except Exception as exc:
        # CH is the sole trace backend (PG tracer tables dropped); fail closed.
        # Breadcrumb for log-based alerting; the re-raise carries the Sentry
        # error, so this stays WARNING to avoid a duplicate event.
        logger.warning(
            "bulk_selection_resolve_trace_ch_query_failed",
            project_id=str(project_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    selected_rows = [
        row for row in rows if row.get("trace_id") and str(row["trace_id"]) not in excl
    ]
    ids = [str(row["trace_id"]) for row in selected_rows]

    if resumable:
        return _resumable_bounded_result(
            builder=builder,
            filters=filters or [],
            page=bounded_page,
            rows=rows,
            selected_rows=selected_rows,
            key_field="trace_id",
            cap=cap,
            cursor=cursor,
        )

    # The bounded read overscans by the complete exclusion-set size, so its
    # sentinel now describes the post-exclusion set. A has-more result with no
    # post-exclusion sentinel would violate that proof; fail closed.
    if bounded_has_more and len(ids) <= cap:
        raise BulkSelectionReadIncomplete("excluded_prefix_unproven")
    truncated = len(ids) > cap
    ids = ids[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

    logger.info(
        "bulk_selection_resolve_trace_ch",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(exclude_ids or set()),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


def resolve_filtered_trace_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
    is_voice_call: bool = False,
    remove_simulation_calls: bool = False,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Return trace IDs matching ``filters`` in ``project_id``, minus ``exclude_ids``.

    Default path mirrors ``list_traces_of_session`` (regular trace grid).
    When ``is_voice_call=True`` the resolver additionally applies the
    constraints ``list_voice_calls`` uses — root span must be a
    conversation, voice system metrics are honored, and when
    ``remove_simulation_calls`` is also true the VAPI simulator phone
    numbers are excluded — so the resolved set matches the voice grid.

    Args:
        project_id: UUID of the project to search in. Must belong to ``organization``.
        filters: Filter dicts in the same shape the list endpoint accepts.
        exclude_ids: IDs to exclude from the result (e.g. rows the user
            deselected while select-all was active). May be None/empty.
        organization: Requesting user's organization. Required for scoping.
        workspace: Optional workspace scope.
        cap: Maximum number of IDs to return. Default 10_000.
        user: Requesting user. Required when filters reference user-scoped
            columns (``my_annotations``, ``annotator``).
        is_voice_call: When true, apply ``list_voice_calls`` constraints
            on top of the base trace filters. Set by the frontend when
            the selection came from the voice/simulator grid.
        remove_simulation_calls: Only honored when ``is_voice_call=True``.
            Mirrors the voice grid toolbar toggle.

    Returns:
        ``ResolveResult`` with ids (capped, post-exclude), total_matching
        (pre-cap, post-exclude), and truncated flag.

    Raises:
        Project.DoesNotExist: if the project is not in the org.
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    # Project + workspace scope are resolved in PG — the project / annotation-
    # label tables are NOT tracer tables and are not being dropped. Trace/voice
    # rows themselves are read only from ClickHouse (no PG tracer-table access),
    # so filter-mode add stays working once the PG tracer tables are dropped.
    # Verifying the project up front keeps the 404 contract consistent with the
    # enumerated path.
    project = Project.objects.get(id=project_id, organization=organization)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    if not _project_matches_workspace(project, workspace):
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    annotation_labels = get_annotation_labels_for_project(project.id, organization)
    annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    # The CH list builders default to a now-30d window when the payload sends no
    # time bound (a dashboard-perf default in parse_time_range), which would
    # silently drop older rows a "select all matching this filter" must include.
    # Widen to all-history so the resolve spans everything, matching the
    # enumerated path; an explicit user time filter prunes normally.
    #
    # Injected here at the caller (not inside the resolvers) so one site covers
    # both the trace and voice branches; span/session self-inject inside their
    # single resolver.
    ch_filters = list(filters or [])
    if cursor is not None:
        ch_filters.append(frozen_window_filter(cursor))
    elif not _has_explicit_time_filter(filters):
        ch_filters.append(_all_history_time_filter())

    if is_voice_call:
        return _resolve_voice_call_ids_clickhouse(
            project_id=project_id,
            filters=ch_filters,
            exclude_ids=set(exclude_ids or ()),
            cap=cap,
            remove_simulation_calls=remove_simulation_calls,
            annotation_label_ids=annotation_label_ids,
            cursor=cursor,
            resumable=resumable,
            **_optional_deadline_kwargs(deadline),
        )
    return _resolve_trace_ids_clickhouse(
        project_id=project_id,
        filters=ch_filters,
        exclude_ids=set(exclude_ids or ()),
        cap=cap,
        annotation_label_ids=annotation_label_ids,
        cursor=cursor,
        resumable=resumable,
        **_optional_deadline_kwargs(deadline),
    )


# --------------------------------------------------------------------------
# Phase 4 — source_type = observation_span
# --------------------------------------------------------------------------


def _all_history_time_filter() -> dict:
    """A wide-open ``start_time`` window that cancels the CH builders' now-30d default.

    The v2 list builders' ``parse_time_range`` defaults to now-30d when the
    payload sends no time bound (a dashboard-perf default), which would silently
    drop older rows a "select all matching this filter" must include. Injecting
    this makes the CH resolve all-history for trace, voice and span alike.

    Lower bound is ``1971`` (not ``1970``): the trace/voice builders subtract
    ``INTERVAL 1 DAY`` from the window start for partition pruning (and so do the
    span score subqueries), and a ClickHouse ``DateTime`` is a 32-bit epoch, so
    ``1970-01-01 - 1 DAY`` underflows and matches nothing.
    """
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            # Do not point the bounded newest-first reader into decades of
            # future empty slices.  A fresh upper bound is generated for each
            # request while the 1971 lower bound preserves all-history intent.
            "filter_value": ["1971-01-01T00:00:00", datetime.utcnow().isoformat()],
        },
    }


def _resolve_span_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    annotation_label_ids: list[str],
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Resolve span IDs from ClickHouse, mirroring ``list_spans_observe``.

    Uses the same ``SPAN_LIST`` builder the observe grid uses (via v2 dispatch)
    so filter semantics — span attributes, eval metrics, annotation labels,
    ``user_id`` remap — match the grid exactly. Reads ids only (no wide JSON
    columns) so a broad filtered scan can't OOM the shared cluster.

    ClickHouse is the sole backend for span rows (the PG tracer tables are being
    dropped), so a ClickHouse failure propagates rather than silently resolving
    to a partial/empty set.
    """
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    ch_filters = list(filters or [])
    if cursor is not None:
        ch_filters.append(frozen_window_filter(cursor))
    elif not _has_explicit_time_filter(ch_filters):
        ch_filters.append(_all_history_time_filter())

    analytics = V2AnalyticsQueryService()
    excl = {str(value) for value in (exclude_ids or set())}
    if not _supports_bounded_bulk_prefix(cap=cap, exclude_count=len(excl)):
        raise BulkSelectionReadIncomplete("selection_prefix_too_large")
    builder = _use_authoritative_eval_source(
        SpanListQueryBuilderV2(
            project_id=str(project_id),
            filters=ch_filters,
            annotation_label_ids=annotation_label_ids,
            bounded_internal_scan=_needs_bounded_internal_scan(ch_filters),
            bounded_identity_only=True,
        )
    )
    if not builder.supports_bounded_filter_scan():
        raise BulkSelectionReadIncomplete(
            builder.bounded_filter_degraded_error_code() or "unsupported_bounded_filter"
        )
    # build_id_query(limit=cap+1) gives the LIMIT cap+1 truncation sentinel
    # without a separate COUNT scan (same trick as the trace/voice/session CH
    # paths).
    try:
        bounded_page = _read_bounded_bulk_page(
            builder=builder,
            analytics=analytics,
            filters=ch_filters,
            key_field="id",
            cap=cap,
            exclude_count=len(excl),
            cursor=cursor,
            resumable=resumable,
            **_optional_deadline_kwargs(deadline),
        )
        rows = bounded_page.rows
        bounded_has_more = bounded_page.has_more
    except Exception as exc:
        # CH is the sole span backend (PG tracer tables dropped); fail closed.
        # Breadcrumb the outage for log-based alerting — the re-raise carries the
        # Sentry error, so this stays WARNING to avoid a duplicate event.
        logger.warning(
            "bulk_selection_resolve_span_ch_query_failed",
            project_id=str(project_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise

    ids = [str(r.get("id", "")) for r in rows if r.get("id")]
    if len(ids) != len(set(ids)):
        # QueueItem stores only the bare span id. Returning it when two live
        # physical spans match (for example, the same id under two trace ids)
        # would make the selection target ambiguous and potentially hydrate the
        # wrong entity later. Surface only a stable error code; the API layer
        # converts resolver failures to its sanitized retryable response.
        raise BulkSelectionAmbiguousIdentity("ambiguous_span_identity")
    selected_rows = [
        row for row in rows if row.get("id") and str(row["id"]) not in excl
    ]
    ids = [str(row["id"]) for row in selected_rows]

    if resumable:
        return _resumable_bounded_result(
            builder=builder,
            filters=ch_filters,
            page=bounded_page,
            rows=rows,
            selected_rows=selected_rows,
            key_field="id",
            cap=cap,
            cursor=cursor,
        )

    if bounded_has_more and len(ids) <= cap:
        raise BulkSelectionReadIncomplete("excluded_prefix_unproven")
    truncated = len(ids) > cap
    ids = ids[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

    logger.info(
        "bulk_selection_resolve_span_ch",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(exclude_ids or set()),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )
    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


def resolve_filtered_span_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Return span IDs matching ``filters`` in ``project_id``, minus ``exclude_ids``.

    Resolved entirely from ClickHouse via the same ``SPAN_LIST`` builder the
    observe grid uses, so filter semantics match the grid exactly and no PG
    tracer table is read. Shares the ``ResolveResult`` contract and the
    user-scoped-filter guard with :func:`resolve_filtered_trace_ids`.

    Args:
        project_id: UUID of the project to search in. Must belong to ``organization``.
        filters: Filter dicts in the same shape the list endpoint accepts.
        exclude_ids: Span IDs to exclude from the result.
        organization: Requesting user's organization. Required for scoping.
        workspace: Optional workspace scope.
        cap: Maximum number of IDs to return. Default 10_000.
        user: Requesting user. Required when filters reference user-scoped
            columns (``my_annotations``, ``annotator``).

    Returns:
        ``ResolveResult`` with ids (capped, post-exclude), total_matching,
        truncated flag.

    Raises:
        Project.DoesNotExist: if the project is not in the org.
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    # Project + workspace scope are resolved in PG — the project / annotation-label
    # tables are NOT tracer tables and are not being dropped. The span rows
    # themselves are read only from ClickHouse (no PG tracer-table access), so
    # filter-mode add stays working once the PG tracer tables are dropped.
    project = Project.objects.get(id=project_id, organization=organization)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    if not _project_matches_workspace(project, workspace):
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    annotation_labels = get_annotation_labels_for_project(project.id, organization)
    annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    return _resolve_span_ids_clickhouse(
        project_id=project_id,
        filters=filters or [],
        exclude_ids=set(exclude_ids or ()),
        cap=cap,
        annotation_label_ids=annotation_label_ids,
        cursor=cursor,
        resumable=resumable,
        **_optional_deadline_kwargs(deadline),
    )


# --------------------------------------------------------------------------
# Phase 6 — source_type = trace_session
#
# Sessions are resolved from ClickHouse via the same ``SessionListQueryBuilder``
# the live session grid uses (over the ``spans`` table). Score-label filters are
# intersected in PG against the annotation ``Score`` table afterward — the CH
# ``spans`` path can't host a session-level ``Score`` predicate. No PG tracer
# table is read.
# --------------------------------------------------------------------------


def _session_score_label_ids(project_id) -> set[str]:
    """Project-scoped annotation-label ids — the discriminator that splits a
    score-based session filter (``col_id`` is a label id) from a system-metric
    one, matching ``list_sessions``."""
    return {
        str(lbl.id)
        for lbl in AnnotationsLabels.objects.filter(
            project_id=project_id, deleted=False
        )
    }


def _split_session_score_filters(
    filters: list[dict], score_label_ids: set[str]
) -> tuple[list[dict], list[dict]]:
    """Partition ``filters`` into (non-score, score) by whether the filter's
    ``col_id`` names a project annotation label. Score filters are applied in PG
    against ``Score`` (which carries ``trace_session_id`` as a soft id, so it is
    net-new-correct); everything else flows to the CH session-list builder."""
    non_score: list[dict] = []
    score: list[dict] = []
    for f in filters or []:
        if _filter_column_id(f) in score_label_ids:
            score.append(f)
        else:
            non_score.append(f)
    return non_score, score


def _prepare_session_ch_filters(
    non_score_filters: list[dict],
    *,
    project_id,
    organization,
    deadline: ReadDeadline | None = None,
) -> list[dict]:
    """Translate a ``user_id`` session filter into the synthetic ``end_user_id``
    IN(...) filter the CH ``SessionListQueryBuilder`` understands, mirroring the
    live ``_list_sessions_clickhouse`` prep.

    P3b step2 precondition (PG_ORM_READ_MIGRATION, Slice B/F): the reverse
    resolve goes through the curated CH ``end_users`` dimension, NOT PG
    ``EndUser.objects`` (which is stale for a NET-NEW user post-flip). The
    resolved ids are bound to the id-remap-RESOLVED ``end_user_id`` span column
    by the builder (``_build_resolved_user_clause``), so a straddler unifies and
    a net-new user's sessions are reachable. Other filter columns (time,
    span-attribute, aggregate-metric, session-id) pass through untouched — the
    builder already routes each to the right CH predicate, remap-aware.
    """
    prepared: list[dict] = []
    user_id_values: list[str] = []
    for f in non_score_filters or []:
        col_id = _filter_column_id(f)
        cfg = _filter_config(f)
        col_type = cfg.get("col_type", "NORMAL")
        if col_id == "user_id" and col_type == "NORMAL":
            raw = cfg.get("filter_value")
            vals = raw if isinstance(raw, list) else [raw]
            user_id_values.extend(str(v) for v in vals if v)
            continue
        prepared.append(f)

    for raw_user_id in user_id_values:
        from tracer.services.clickhouse.v2.end_user_dict_reader import (
            resolve_end_user_ids_by_user_id,
        )

        ids = resolve_end_user_ids_by_user_id(
            raw_user_id,
            organization_id=getattr(organization, "id", None),
            project_id=project_id,
            timeout_ms=(
                deadline.remaining_ms(floor_ms=1) if deadline is not None else None
            ),
        )
        # Empty → match nothing (NIL_UUID sentinel), mirroring the live view.
        from tracer.services.clickhouse.query_builders.base import NIL_UUID

        prepared.append(
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ids or [NIL_UUID],
                },
            }
        )
    return prepared


def _apply_session_score_filters_pg(
    session_ids: list[str],
    score_filters: list[dict],
    *,
    deadline: ReadDeadline | None = None,
) -> list[str]:
    """Intersect a CH-derived candidate session-id list with annotation
    ``Score``-based filters, preserving input order.

    ``Score`` is the annotation-score table (not a tracer table), keyed by the
    soft ``trace_session_id`` string — so a NET-NEW session's scores are reachable
    here WITHOUT a PG ``trace_session`` row, and this stays valid once the tracer
    tables are dropped. An explicit-id membership check (not an ``OuterRef``
    Subquery) so it composes with the CH base set; each filter narrows the set.
    """
    surviving = list(session_ids)
    for sf in score_filters:
        if deadline is not None:
            deadline.remaining_ms(floor_ms=1)
        if not surviving:
            break
        col_id = _filter_column_id(sf)
        fc = _filter_config(sf)
        filter_op = fc.get("filter_op") or "equals"
        filter_val = fc.get("filter_value")

        base_q = Score.objects.filter(
            trace_session_id__in=surviving,
            label_id=col_id,
            deleted=False,
        )
        if filter_op == "is_not_null":
            match_q = base_q
            negate = False
        elif filter_op == "is_null":
            match_q = base_q
            negate = True
        elif filter_op == "equals":
            match_q = base_q.filter(value=filter_val)
            negate = False
        elif filter_op == "not_equals":
            match_q = base_q.filter(value=filter_val)
            negate = True
        elif filter_op == "in" and isinstance(filter_val, list):
            match_q = base_q.filter(value__in=filter_val)
            negate = False
        elif filter_op == "not_in" and isinstance(filter_val, list):
            match_q = base_q.filter(value__in=filter_val)
            negate = True
        elif filter_op == "contains":
            match_q = base_q.filter(value__icontains=filter_val)
            negate = False
        else:
            match_q = base_q
            negate = False

        matched = {
            str(sid) for sid in match_q.values_list("trace_session_id", flat=True)
        }
        if deadline is not None:
            deadline.remaining_ms(floor_ms=1)
        if negate:
            surviving = [s for s in surviving if s not in matched]
        else:
            surviving = [s for s in surviving if s in matched]
    return surviving


def _resolve_session_ids_clickhouse(
    *,
    project_id,
    non_score_filters: list[dict],
    score_filters: list[dict],
    exclude_ids: set,
    organization,
    cap: int,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Re-derive the filter-matched session-id set from ClickHouse.

    Uses the same remap-aware ``SessionListQueryBuilder`` the live session grid
    uses (over the CH ``spans`` table), so a "select all sessions matching this
    filter" bulk-add INCLUDES net-new sessions (first seen after the ingest
    ``get_or_create`` was dropped — no PG ``trace_session`` row) and a
    cross-cutover straddler's old + new session ids unify to ONE survivor.

    Non-score filters (time / span-attribute / aggregate-metric / session-id /
    user_id) are translated by the builder. Score-label filters are applied in
    PG afterward (``_apply_session_score_filters_pg`` against the annotation
    ``Score`` table — NOT a tracer table): the CH ``spans`` path can't host a
    session-level ``Score`` predicate (its annotation subquery matches by
    ``trace_id``/span ``id``, never ``trace_session_id``).

    ClickHouse is the sole backend for session rows (the PG tracer tables are
    being dropped), so a ClickHouse failure propagates rather than silently
    resolving to a partial/empty set.
    """
    from tracer.services.clickhouse.v2.query_builders.session_list import (
        SessionListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    ch_filters = _prepare_session_ch_filters(
        non_score_filters,
        project_id=project_id,
        organization=organization,
        **_optional_deadline_kwargs(deadline),
    )

    # Preserve select-all's all-history contract, but only as the finite reader's
    # request envelope.  No ClickHouse statement scans 1971→now: the reader
    # walks adjacent newest-first slices, seeds at most 200 session IDs, and
    # classifies only those IDs against latest physical state.
    if cursor is not None:
        ch_filters.append(frozen_window_filter(cursor))
    elif not _has_explicit_time_filter(non_score_filters):
        ch_filters.append(_all_history_time_filter())

    analytics = V2AnalyticsQueryService()
    excl = {str(value) for value in (exclude_ids or set())}
    if not _supports_bounded_bulk_prefix(cap=cap, exclude_count=len(excl)):
        raise BulkSelectionReadIncomplete("selection_prefix_too_large")
    builder = _use_authoritative_eval_source(
        SessionListQueryBuilderV2(
            project_id=str(project_id),
            page_number=0,
            page_size=cap + 1,
            filters=ch_filters,
            sort_params=[],
            bounded_internal_scan=True,
        )
    )
    if not builder.supports_bounded_filter_scan():
        raise BulkSelectionReadIncomplete(
            builder.bounded_filter_degraded_error_code() or "unsupported_bounded_filter"
        )
    try:
        bounded_page = _read_bounded_bulk_page(
            builder=builder,
            analytics=analytics,
            filters=ch_filters,
            key_field="session_id",
            cap=cap,
            exclude_count=len(excl),
            cursor=cursor,
            resumable=resumable,
            **_optional_deadline_kwargs(deadline),
        )
        rows = bounded_page.rows
        bounded_has_more = bounded_page.has_more
    except Exception as exc:
        # CH is the sole session backend (PG aggregate fallback removed); fail
        # closed. Breadcrumb for log-based alerting; the re-raise carries the
        # Sentry error, so this stays WARNING to avoid a duplicate event.
        logger.warning(
            "bulk_selection_resolve_session_ch_query_failed",
            project_id=str(project_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    ids = [str(row.get("session_id", "")) for row in rows if row.get("session_id")]

    if score_filters:
        ids = _apply_session_score_filters_pg(
            ids,
            score_filters,
            **_optional_deadline_kwargs(deadline),
        )

    selected_ids = {session_id for session_id in ids if session_id not in excl}
    selected_rows = [
        row
        for row in rows
        if row.get("session_id") and str(row["session_id"]) in selected_ids
    ]
    ids = [str(row["session_id"]) for row in selected_rows]

    if resumable:
        return _resumable_bounded_result(
            builder=builder,
            filters=ch_filters,
            page=bounded_page,
            rows=rows,
            selected_rows=selected_rows,
            key_field="session_id",
            cap=cap,
            cursor=cursor,
        )

    # With post-CH score intersection, a complete score-filtered prefix is only
    # provable when the bounded CH page itself is exhausted.  Never claim a
    # partial prefix as complete.
    if score_filters and bounded_has_more and len(ids) <= cap:
        raise BulkSelectionReadIncomplete("score_filtered_prefix_unproven")
    if bounded_has_more and not score_filters and len(ids) <= cap:
        raise BulkSelectionReadIncomplete("excluded_prefix_unproven")
    truncated = len(ids) > cap
    ids = ids[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

    logger.info(
        "bulk_selection_resolve_session_ch",
        project_id=str(project_id),
        filter_count=len(non_score_filters or []) + len(score_filters or []),
        score_filter_count=len(score_filters or []),
        exclude_count=len(exclude_ids or set()),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


def resolve_filtered_session_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Return session IDs matching ``filters`` in ``project_id``.

    P3b step2 precondition (PG_ORM_READ_MIGRATION, Slice F): the matched session
    set is re-derived from ClickHouse (``_resolve_session_ids_clickhouse``,
    backed by the same remap-aware ``SessionListQueryBuilder`` the live session
    grid uses) so a "select all sessions matching this filter" bulk-add to a
    queue INCLUDES net-new sessions (first seen after the ingest
    ``get_or_create`` is dropped, so they have NO PG ``trace_session`` row and
    were silently omitted by the old PG aggregate). A cross-cutover straddler's
    old + new session ids unify to ONE survivor (counted once). Score-label
    filters intersect the annotation ``Score`` table (net-new-correct via the
    soft ``trace_session_id``); everything else is translated by the CH builder.
    No PG tracer table is read.

    Raises:
        Project.DoesNotExist: if the project is not in the org.
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    # Resolve + scope-check the project up front (the CH builder keys spans by
    # project_id but does NOT enforce org membership or the SIMULATOR carve-out).
    # Raising Project.DoesNotExist here preserves the caller's 404 mapping. These
    # are Project / annotation tables — not tracer tables — so they stay in PG.
    project = Project.objects.get(id=project_id, organization=organization)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    if project.source == ProjectSourceChoices.SIMULATOR.value:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if workspace is not None and project.workspace_id != getattr(
        workspace, "id", workspace
    ):
        # Workspace mismatch — nothing to resolve.
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    score_label_ids = _session_score_label_ids(project_id)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    non_score_filters, score_filters = _split_session_score_filters(
        filters or [], score_label_ids
    )

    return _resolve_session_ids_clickhouse(
        project_id=project_id,
        non_score_filters=non_score_filters,
        score_filters=score_filters,
        exclude_ids=set(exclude_ids or set()),
        organization=organization,
        cap=cap,
        cursor=cursor,
        resumable=resumable,
        **_optional_deadline_kwargs(deadline),
    )


# --------------------------------------------------------------------------
# Phase 8 — source_type = call_execution
#
# CallExecution isn't tied to an observe ``Project``. Its scope chain goes
# through test_execution → run_test → organization (+ agent_definition →
# workspace). The selection payload's ``project_id`` slot is reused to
# carry the ``agent_definition_id`` — see Phase 8 PRD.
# --------------------------------------------------------------------------


# UI column id → CallExecution ORM lookup. Mirrors the simulation add-items and
# rule filter fields. Structured persona fields are handled separately because
# call_metadata.row_data.persona may store scalar or list-shaped JSON values.
_CALL_EXECUTION_FIELD_MAP = {
    "status": "status",
    "simulation_call_type": "simulation_call_type",
    "call_type": "simulation_call_type",
    "duration": "duration_seconds",
    "duration_seconds": "duration_seconds",
    "agent_latency": "avg_agent_latency_ms",
    "avg_agent_latency_ms": "avg_agent_latency_ms",
    "total_cost": "cost_cents",
    "cost_cents": "cost_cents",
    "overall_score": "overall_score",
    "agent_definition": "test_execution__agent_definition__agent_name",
}


def _is_call_execution_eval_filter(col, cfg, eval_config_ids):
    return cfg.get("col_type") == "EVAL_METRIC" and col and str(col) in eval_config_ids


def _coerce_eval_number(value):
    numeric = float(value)
    return numeric / 100.0


def _call_execution_json_output_filter(qs, output_field, eval_id, cfg):
    op = cfg.get("filter_op")
    value = cfg.get("filter_value")
    filter_type = cfg.get("filter_type")
    output_path = f"{output_field}__{eval_id}__output"
    has_key = {f"{output_field}__has_key": eval_id}

    if op == "is_null":
        return qs.filter(Q(**{f"{output_path}__isnull": True}) | ~Q(**has_key))
    if op == "is_not_null":
        return qs.filter(**has_key).filter(**{f"{output_path}__isnull": False})

    if value is None:
        return qs

    if filter_type == "number":
        if op in ("between", "not_between"):
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise ValueError("invalid numeric range")
            lo = _coerce_eval_number(value[0])
            hi = _coerce_eval_number(value[1])
            if op == "between":
                return qs.filter(
                    **has_key,
                    **{f"{output_path}__gte": lo, f"{output_path}__lte": hi},
                )
            return qs.filter(**has_key).exclude(
                **{f"{output_path}__gte": lo, f"{output_path}__lte": hi}
            )

        numeric_value = _coerce_eval_number(value)
        if op == "greater_than":
            return qs.filter(**has_key, **{f"{output_path}__gt": numeric_value})
        if op == "less_than":
            return qs.filter(**has_key, **{f"{output_path}__lt": numeric_value})
        if op == "greater_than_or_equal":
            return qs.filter(**has_key, **{f"{output_path}__gte": numeric_value})
        if op == "less_than_or_equal":
            return qs.filter(**has_key, **{f"{output_path}__lte": numeric_value})
        if op == "not_equals":
            return qs.filter(**has_key).exclude(**{output_path: numeric_value})
        return qs.filter(**has_key, **{output_path: numeric_value})

    if filter_type == "boolean":
        if isinstance(value, bool):
            bool_value = value
        else:
            bool_value = str(value).lower() in ("true", "1", "yes", "passed")
        if op == "not_equals":
            return qs.filter(**has_key).exclude(**{output_path: bool_value})
        return qs.filter(**has_key, **{output_path: bool_value})

    values = value if isinstance(value, list) else [value]
    values = [str(v) for v in values if v not in (None, "")]
    if not values:
        return qs

    if op in ("in", "equals"):
        if len(values) == 1 and op == "equals":
            return qs.filter(**has_key, **{f"{output_path}__iexact": values[0]})
        return qs.filter(**has_key, **{f"{output_path}__in": values})
    if op in ("not_in", "not_equals"):
        if len(values) == 1 and op == "not_equals":
            return qs.filter(**has_key).exclude(**{f"{output_path}__iexact": values[0]})
        return qs.filter(**has_key).exclude(**{f"{output_path}__in": values})
    if op == "contains":
        condition = Q()
        for item in values:
            condition |= Q(**{f"{output_path}__icontains": item})
        return qs.filter(**has_key).filter(condition)
    if op == "not_contains":
        condition = Q()
        for item in values:
            condition |= Q(**{f"{output_path}__icontains": item})
        return qs.filter(**has_key).exclude(condition)
    if op == "starts_with":
        return qs.filter(**has_key, **{f"{output_path}__istartswith": values[0]})
    if op == "ends_with":
        return qs.filter(**has_key, **{f"{output_path}__iendswith": values[0]})

    raise ValueError("unsupported eval filter operator")


def _apply_call_execution_filters(qs, filters, *, eval_config_ids=None):
    """Translate UI-shaped filters into CallExecution ORM lookups.

    Returns ``(qs, unsupported)`` where ``unsupported`` is the list of
    column ids the resolver couldn't map. Caller is expected to fail
    closed if any are returned.
    """
    unsupported: list[str] = []
    eval_config_ids = {str(item) for item in (eval_config_ids or set())}
    for f in filters:
        col = _filter_column_id(f)
        cfg = _filter_config(f)
        op = cfg.get("filter_op")
        value = cfg.get("filter_value")
        if _is_call_execution_eval_filter(col, cfg, eval_config_ids):
            try:
                qs = _call_execution_json_output_filter(qs, "eval_outputs", col, cfg)
            except (TypeError, ValueError):
                unsupported.append(col or "<unknown>")
            continue

        if is_persona_filter_column(col):
            try:
                qs = apply_persona_filter(
                    qs,
                    col,
                    op,
                    value,
                    cfg.get("filter_type"),
                )
            except UnsupportedPersonaFilter:
                unsupported.append(col or "<unknown>")
            continue

        orm_field = _CALL_EXECUTION_FIELD_MAP.get(col)
        if not orm_field or not op:
            unsupported.append(col or "<unknown>")
            continue

        if op in ("is_null", "is_not_null"):
            qs = (
                qs.filter(**{f"{orm_field}__isnull": True})
                if op == "is_null"
                else qs.filter(**{f"{orm_field}__isnull": False})
            )
            continue

        try:
            if op == "equals":
                values = value if isinstance(value, list) else [value]
                if len(values) == 1:
                    qs = qs.filter(**{orm_field: values[0]})
                else:
                    qs = qs.filter(**{f"{orm_field}__in": values})
            elif op == "not_equals":
                values = value if isinstance(value, list) else [value]
                if len(values) == 1:
                    qs = qs.exclude(**{orm_field: values[0]})
                else:
                    qs = qs.exclude(**{f"{orm_field}__in": values})
            elif op == "in":
                values = value if isinstance(value, list) else [value]
                qs = qs.filter(**{f"{orm_field}__in": values})
            elif op == "not_in":
                values = value if isinstance(value, list) else [value]
                qs = qs.exclude(**{f"{orm_field}__in": values})
            elif op == "contains":
                qs = qs.filter(**{f"{orm_field}__icontains": value})
            elif op == "not_contains":
                qs = qs.exclude(**{f"{orm_field}__icontains": value})
            elif op == "starts_with":
                qs = qs.filter(**{f"{orm_field}__istartswith": value})
            elif op == "ends_with":
                qs = qs.filter(**{f"{orm_field}__iendswith": value})
            elif op == "greater_than":
                qs = qs.filter(**{f"{orm_field}__gt": value})
            elif op == "less_than":
                qs = qs.filter(**{f"{orm_field}__lt": value})
            elif op == "greater_than_or_equal":
                qs = qs.filter(**{f"{orm_field}__gte": value})
            elif op == "less_than_or_equal":
                qs = qs.filter(**{f"{orm_field}__lte": value})
            elif op == "between":
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    qs = qs.filter(**{f"{orm_field}__range": (value[0], value[1])})
                else:
                    unsupported.append(col)
            elif op == "not_between":
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    qs = qs.exclude(**{f"{orm_field}__range": (value[0], value[1])})
                else:
                    unsupported.append(col)
            else:
                unsupported.append(col)
        except (TypeError, ValueError):
            unsupported.append(col)
    return qs, unsupported


def resolve_filtered_call_execution_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
    cursor: ListCursor | None = None,
    resumable: bool = False,
    deadline: ReadDeadline | None = None,
) -> ResolveResult:
    """Return CallExecution IDs under ``agent_definition_id=project_id``.

    ``project_id`` is reinterpreted here as the agent_definition_id to keep
    the serializer contract uniform across source types. The resolver
    scopes by organization + workspace through the agent_definition FK.

    Supports ``apply_created_at_filters`` in ``filters``; other filter
    shapes are currently ignored — Phase 8 is scoped to the simple case.

    Raises:
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)

    qs = CallExecution.objects.filter(
        test_execution__agent_definition_id=project_id,
        test_execution__run_test__organization=organization,
        deleted=False,
        test_execution__run_test__deleted=False,
    )
    if workspace is not None:
        qs = qs.filter(test_execution__agent_definition__workspace=workspace)

    if filters:
        qs, remaining = apply_created_at_filters(qs, filters)
        if remaining:
            from simulate.models import SimulateEvalConfig

            eval_config_ids = set(
                SimulateEvalConfig.objects.filter(
                    run_test__agent_definition_id=project_id,
                    run_test__organization=organization,
                    run_test__deleted=False,
                    deleted=False,
                ).values_list("id", flat=True)
            )
            if deadline is not None:
                deadline.remaining_ms(floor_ms=1)
            qs, unsupported = _apply_call_execution_filters(
                qs,
                remaining,
                eval_config_ids=eval_config_ids,
            )
            if unsupported:
                # Fail closed: a filter the resolver still can't apply
                # must NOT silently broaden the result to the full
                # agent_definition.
                raise ValueError(
                    "call_execution filter resolver cannot apply: "
                    + ", ".join(unsupported)
                )

    cursor_window: tuple[datetime, datetime] | None = None
    if resumable:
        if cursor is not None:
            if (
                len(cursor.order) != 2
                or not isinstance(cursor.order[0], datetime)
                or not isinstance(cursor.order[1], str)
            ):
                raise ValueError("invalid bulk-selection cursor order")
            cursor_window = cursor.window_start, cursor.window_end
        else:
            cursor_window = (
                datetime(1971, 1, 1, tzinfo=UTC),
                datetime.now(UTC),
            )
        # ``apply_created_at_filters`` above owns the PostgreSQL contract:
        # equals matches a calendar day, between includes its upper endpoint,
        # and complement-only filters retain both sides of the exclusion. Do
        # not reinterpret those predicates as one ClickHouse half-open window.
        # Freeze only the request-time upper fence so rows inserted after page
        # one cannot enter a continuation chain.
        qs = qs.filter(created_at__lte=cursor_window[1])
        if cursor is not None:
            qs = qs.filter(
                Q(created_at__lt=cursor.order[0])
                | Q(created_at=cursor.order[0], id__lt=cursor.order[1])
            )

    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    qs = qs.order_by("-created_at", "-id")

    if resumable:
        capped_rows = list(qs.values("id", "created_at")[: cap + 1])
        if deadline is not None:
            deadline.remaining_ms(floor_ms=1)
        overflow = len(capped_rows) > cap
        published_rows = capped_rows[:cap]
        ids = [row["id"] for row in published_rows]
        seen_rows = (cursor.seen_rows if cursor is not None else 0) + len(ids)
        continuation = None
        if overflow:
            last = published_rows[-1]
            assert cursor_window is not None
            continuation = ListCursor(
                window_start=cursor_window[0],
                window_end=cursor_window[1],
                order=(last["created_at"], str(last["id"])),
                seen_rows=seen_rows,
            )
        total_matching = seen_rows + (1 if overflow else 0)
        return ResolveResult(
            ids=ids,
            total_matching=total_matching,
            truncated=overflow,
            continuation=continuation,
        )

    # See resolve_filtered_trace_ids — cap+1 fetch instead of COUNT(*).
    capped = list(qs.values_list("id", flat=True)[: cap + 1])
    if deadline is not None:
        deadline.remaining_ms(floor_ms=1)
    truncated = len(capped) > cap
    ids = capped[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

    logger.info(
        "bulk_selection_resolve_call_execution",
        agent_definition_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(list(exclude_ids or [])),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)
