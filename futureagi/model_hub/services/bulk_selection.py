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

  Each resolver mirrors its grid's list view through the same v2 query builders
  (or, for the bounded trace resolver, their ``ClickHouseFilterBuilder``), so
  filter semantics match the grid exactly.

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

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from django.db.models import Q

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from simulate.models.test_execution import CallExecution
from simulate.utils.persona_filtering import (
    UnsupportedPersonaFilter,
    apply_persona_filter,
    is_persona_filter_column,
)
from tracer.models.project import Project, ProjectSourceChoices
from tracer.utils.filters import (
    apply_created_at_filters,
    normalize_filter_item,
)
from tracer.utils.helper import get_annotation_labels_for_project

logger = structlog.get_logger(__name__)


@dataclass
class ResolveResult:
    """Result of a filter-based ID resolution."""

    ids: list[UUID]
    total_matching: int
    truncated: bool


class BulkSelectionIncomplete(RuntimeError):
    """A bounded ClickHouse read could not prove the requested source set."""


class BulkTraceSelectionIncomplete(BulkSelectionIncomplete):
    """The bounded ClickHouse read could not prove the requested trace set."""


_USER_SCOPED_COLUMN_IDS = {"my_annotations", "annotator"}

_BULK_TRACE_MAX_CAP = 10_000
_BULK_TRACE_TOTAL_READ_MS = 5_000
_BULK_TRACE_FAST_ATTEMPT_MS = 250
_BULK_TRACE_QUERY_MAX_MS = 750
_BULK_TRACE_MIN_QUERY_MS = 25
_BULK_TRACE_CANDIDATE_BATCH = 1_000
_BULK_TRACE_MIN_SLICE = timedelta(minutes=1)
_BULK_TRACE_INITIAL_SLICE = timedelta(minutes=5)
_BULK_TRACE_MAX_SLICE = timedelta(days=7)
_BULK_TRACE_MAX_FUTURE_SKEW = timedelta(minutes=5)
_BULK_TRACE_INCOMPLETE_MESSAGE = (
    "Could not prove the complete trace selection within the read budget. "
    "Narrow the time range or filters and retry."
)
_BULK_SPAN_INCOMPLETE_MESSAGE = (
    "Could not prove the complete span selection within the read budget. "
    "Narrow the time range or filters and retry."
)
_BULK_SESSION_INCOMPLETE_MESSAGE = (
    "Could not prove the complete session selection within the read budget. "
    "Narrow the time range or filters and retry."
)
_BULK_TRACE_READ_SETTINGS = {
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_to_read": 1024 * 1024 * 1024,
    "max_rows_to_read": 10_000_000,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "use_skip_indexes_if_final": 1,
    "optimize_use_projections": 1,
}


def _filter_column_id(filter_item: dict) -> str:
    return normalize_filter_item(filter_item)["column_id"] or ""


def _filter_config(filter_item: dict) -> dict:
    return normalize_filter_item(filter_item)["filter_config"]


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
) -> ResolveResult:
    """Resolve voice-call trace IDs via ClickHouse, mirroring ``list_voice_calls``.

    Voice calls use the trace resolver's bounded candidate-scoped filter path,
    with an explicit root ``conversation`` predicate. This preserves the voice
    grid's filter compiler semantics without running the wide
    ``VoiceCallListQueryBuilderV2.build()`` query that repeatedly scanned
    all-history Map membership subqueries. Exclusions and simulator candidates
    are removed before refill, under the trace resolver's single read deadline.

    ClickHouse is the sole backend for voice-call rows (the PG tracer tables
    are being dropped), so a ClickHouse failure propagates rather than silently
    resolving to a partial/empty set.
    """
    candidate_post_filter = None
    if remove_simulation_calls:

        def candidate_post_filter(ids, analytics, timeout_ms, settings):
            return _filter_out_simulator_calls_ch(
                ids,
                project_id,
                analytics,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    try:
        result = _resolve_trace_ids_clickhouse(
            project_id=project_id,
            filters=list(filters or []),
            exclude_ids=exclude_ids,
            cap=cap,
            annotation_label_ids=annotation_label_ids,
            candidate_post_filter=candidate_post_filter,
            root_observation_type="conversation",
        )
    except Exception as exc:
        logger.warning(
            "bulk_selection_resolve_voice_ch_query_failed",
            project_id=str(project_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    logger.info(
        "bulk_selection_resolve_voice_ch",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(exclude_ids or set()),
        total_matching=result.total_matching,
        returned=len(result.ids),
        truncated=result.truncated,
    )
    return result


def _filter_out_simulator_calls_ch(
    trace_ids,
    project_id,
    analytics,
    *,
    timeout_ms: int = _BULK_TRACE_QUERY_MAX_MS,
    settings: dict | None = None,
):
    """Post-filter the given trace_ids to drop VAPI simulator calls.

    Mirrors ``_list_voice_calls_clickhouse``'s Python-side simulation
    filter: fetch span_attributes_raw + provider for the root conversation
    span of each trace, then apply ``is_simulator_call``.
    """
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    if not trace_ids:
        return trace_ids

    import json as _json

    # Get root conversation span IDs and attributes in CH25. Resolve the
    # latest version only for the bounded trace-id set and reconstruct the
    # raw attribute dict from the canonical JSON/typed-map columns.
    query = """
    SELECT
        trace_id,
        id AS span_id,
        argMax(provider, _version) AS provider,
        argMax(attributes_extra, _version) AS attributes_extra,
        argMax(attrs_string, _version) AS attrs_string
    FROM spans
    PREWHERE project_id = %(project_id)s
      AND trace_id IN %(trace_ids)s
    WHERE (parent_span_id IS NULL OR parent_span_id = '')
      AND observation_type = 'conversation'
    GROUP BY trace_id, id
    HAVING argMax(is_deleted, _version) = 0
    """
    params = {
        "project_id": str(project_id),
        "trace_ids": tuple(str(t) for t in trace_ids),
    }
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=timeout_ms,
        settings=settings
        or {
            **_BULK_TRACE_READ_SETTINGS,
            "max_result_rows": max(len(trace_ids), 1),
        },
    )
    sim_trace_ids = set()
    for row in result.data:
        raw = row.get("attributes_extra") or "{}"
        try:
            attrs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (_json.JSONDecodeError, TypeError):
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}
        for key, value in (row.get("attrs_string") or {}).items():
            attrs.setdefault(key, value)
        if VoiceCallListQueryBuilderV2.is_simulator_call(
            attrs, row.get("provider") or ""
        ):
            sim_trace_ids.add(str(row.get("trace_id", "")))
    return [t for t in trace_ids if t not in sim_trace_ids]


def _resolve_trace_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    annotation_label_ids: list[str],
    candidate_post_filter=None,
    root_observation_type: str | None = None,
) -> ResolveResult:
    """Resolve regular trace IDs via ClickHouse, mirroring ``list_traces_of_session``.

    The old implementation ran one all-history raw root query, then treated
    ``cap + 1`` *rows* as ``cap + 1`` traces. ReplacingMergeTree versions and
    multi-root traces could consume that limit, while exclusions applied after
    the limit could leave matching rows undiscovered.

    This resolver gives the exact whole-window query a 250 ms healthy-path
    attempt. If that bounded attempt cannot finish, it walks adjacent root-time
    slices newest-first, resolves unique current root IDs, and applies the same
    CH25 filter compiler as the trace list to small candidate sets. User
    exclusions and already-seen candidates are removed before each SQL LIMIT,
    so neither duplicates nor exclusions consume the selection cap.

    ClickHouse is the sole backend for trace rows (the PG tracer tables are
    being dropped), so a ClickHouse failure propagates rather than silently
    resolving to a partial/empty set. In particular, exhausting the shared read
    deadline raises :class:`BulkTraceSelectionIncomplete`; the API maps that to
    its retryable source-store response instead of creating a partial queue.
    """
    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.read_budget import (
        FUTURE_TAIL_PROBE_SETTINGS,
        FUTURE_TAIL_PROBE_TIMEOUT_MS,
        build_future_tail_probe,
        is_read_budget_error,
    )
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    if cap <= 0:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if cap > _BULK_TRACE_MAX_CAP:
        raise ValueError(
            f"Trace selection cap cannot exceed {_BULK_TRACE_MAX_CAP} items."
        )

    analytics = AnalyticsQueryService()
    requested_start, requested_end = BaseQueryBuilder.parse_time_range(filters or [])
    if requested_start >= requested_end:
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    target = cap + 1
    excluded = {str(value) for value in exclude_ids if value}
    deadline = time.monotonic() + (_BULK_TRACE_TOTAL_READ_MS / 1000)

    # The task-creation failure reported in production is a canonical-root
    # ``final_status`` filter. Route only that fully-supported scalar shape to
    # the no-FINAL latest-state builder. Mixed/unsupported filter shapes keep
    # the established bounded path below instead of being approximated.
    scalar_root_builder = TraceListQueryBuilderV2(
        project_id=str(project_id),
        filters=list(filters or []),
    )
    scalar_root_supported = (
        any(_filter_column_id(item) == "final_status" for item in filters or [])
        and scalar_root_builder.supports_latest_root_id_page()
        and root_observation_type is None
    )

    def _remaining_timeout_ms(*, maximum: int = _BULK_TRACE_QUERY_MAX_MS) -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining < _BULK_TRACE_MIN_QUERY_MS:
            raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE)
        return min(maximum, remaining)

    def _settings(result_limit: int) -> dict:
        return {
            **_BULK_TRACE_READ_SETTINGS,
            "max_result_rows": max(int(result_limit), 1),
        }

    def _finalize_spans_sources(sql: str) -> str:
        """Make every filter-side spans read observe current RMT state."""

        # ClickHouse places FINAL after an alias (``spans AS sp FINAL``).
        sql = re.sub(
            r"\b(FROM|JOIN)\s+spans\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\b"
            r"(?!\s+FINAL\b)",
            r"\1 spans AS \2 FINAL",
            sql,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\b(FROM|JOIN)\s+spans\b(?!\s+(?:AS\b|FINAL\b))",
            r"\1 spans FINAL",
            sql,
            flags=re.IGNORECASE,
        )

    def _compile_filter(*, candidate_scoped: bool) -> tuple[str, dict]:
        filter_builder = ClickHouseFilterBuilderV2(
            table="spans",
            annotation_label_ids=annotation_label_ids,
            project_id=str(project_id),
            span_date_scope=True,
            span_trace_id_scope=candidate_scoped,
        )
        where, params = filter_builder.translate(filters or [])
        return _finalize_spans_sources(where), params

    def _ordered_unique_ids(rows: Iterable[dict]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for row in rows:
            trace_id = str(row.get("trace_id", ""))
            if not trace_id or trace_id in seen or trace_id in excluded:
                continue
            seen.add(trace_id)
            unique.append(trace_id)
        return unique

    def _result(ids: list[str], *, truncated: bool) -> ResolveResult:
        returned = ids[:cap]
        total_matching = len(returned) + (1 if truncated else 0)
        logger.info(
            "bulk_selection_resolve_trace_ch",
            project_id=str(project_id),
            filter_count=len(filters or []),
            exclude_count=len(excluded),
            total_matching=total_matching,
            returned=len(returned),
            truncated=truncated,
        )
        return ResolveResult(
            ids=returned,
            total_matching=total_matching,
            truncated=truncated,
        )

    def _apply_candidate_post_filter(ids: list[str]) -> list[str]:
        if not ids or candidate_post_filter is None:
            return ids
        try:
            return candidate_post_filter(
                ids,
                analytics,
                _remaining_timeout_ms(),
                _settings(len(ids)),
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
            raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE) from None

    fast_where, fast_filter_params = _compile_filter(candidate_scoped=False)
    fast_filter_fragment = f"AND {fast_where}" if fast_where else ""
    fast_exclude_fragment = (
        "AND trace_id NOT IN %(bulk_excluded_trace_ids)s" if excluded else ""
    )
    root_observation_fragment = (
        "AND observation_type = %(bulk_root_observation_type)s"
        if root_observation_type
        else ""
    )
    if scalar_root_supported:
        fast_query, fast_params = scalar_root_builder.build_latest_root_id_page(
            slice_start=requested_start,
            slice_end=requested_end,
            limit=target,
            exclude_trace_ids=excluded,
            trace_id_desc=True,
            order_by_recent_minute=False,
        )
    else:
        fast_query = f"""
        SELECT trace_id
        FROM spans FINAL
        PREWHERE project_id = %(project_id)s
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
        WHERE is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {fast_exclude_fragment}
          {root_observation_fragment}
          {fast_filter_fragment}
        GROUP BY trace_id
        ORDER BY max(start_time) DESC, trace_id DESC
        LIMIT %(bulk_target)s
        """
        fast_params = {
            "project_id": str(project_id),
            "start_date": requested_start,
            "end_date": requested_end,
            "bulk_target": target,
            **fast_filter_params,
        }
        if excluded:
            fast_params["bulk_excluded_trace_ids"] = tuple(sorted(excluded))
        if root_observation_type:
            fast_params["bulk_root_observation_type"] = root_observation_type

    fast_ids: list[str] = []
    filtered_fast_ids: list[str] = []
    try:
        fast_result = analytics.execute_ch_query(
            fast_query,
            fast_params,
            timeout_ms=_remaining_timeout_ms(maximum=_BULK_TRACE_FAST_ATTEMPT_MS),
            settings=_settings(target),
        )
    except Exception as exc:
        if not is_read_budget_error(exc):
            logger.warning(
                "bulk_selection_resolve_trace_ch_query_failed",
                project_id=str(project_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        logger.info(
            "bulk_selection_resolve_trace_ch_fast_path_exceeded_budget",
            project_id=str(project_id),
        )
    else:
        fast_ids = _ordered_unique_ids(fast_result.data)
        filtered_fast_ids = _apply_candidate_post_filter(fast_ids)
        if len(filtered_fast_ids) >= target:
            return _result(filtered_fast_ids, truncated=True)
        # GROUP BY guarantees one row per trace. A short result proves that
        # the complete filtered window (after exclusions) was exhausted.
        if len(fast_result.data) < target:
            return _result(filtered_fast_ids, truncated=False)

    probe_where, probe_filter_params = _compile_filter(candidate_scoped=True)
    probe_filter_fragment = f"AND {probe_where}" if probe_where else ""
    # Root-only filters (notably final_status) do not need the two-query
    # candidate/probe path. Applying them directly to each narrow root slice
    # lets ClickHouse use the root attribute indexes and avoids scanning every
    # unmatched trace merely to prove a sub-cap result. Use a candidate probe
    # only when the shared filter compiler actually emitted its candidate
    # placeholder; a spans/eval join that cannot consume that scope would cost
    # the same on every split and belongs on the direct sliced path instead.
    candidate_probe_needed = (
        scalar_root_supported or "%(candidate_trace_ids)s" in probe_where
    )
    slice_filter_fragment = "" if candidate_probe_needed else fast_filter_fragment
    probe_query = f"""
    SELECT DISTINCT trace_id
    FROM spans FINAL
    PREWHERE project_id = %(project_id)s
      AND trace_id IN %(candidate_trace_ids)s
    WHERE is_deleted = 0
      AND (parent_span_id IS NULL OR parent_span_id = '')
      AND start_time >= %(start_date)s
      AND start_time < %(end_date)s
      {root_observation_fragment}
      {probe_filter_fragment}
    ORDER BY trace_id
    LIMIT %(probe_limit)s
    """

    def _probe_candidates(candidate_ids: list[str]) -> set[str]:
        if not candidate_ids:
            return set()
        if not probe_where and not scalar_root_supported:
            return set(candidate_ids)

        if scalar_root_supported:
            query, params = scalar_root_builder.build_latest_filter_match_query(
                candidate_ids
            )
        else:
            query = probe_query
            params = {
                "project_id": str(project_id),
                "start_date": requested_start,
                "end_date": requested_end,
                "candidate_trace_ids": tuple(candidate_ids),
                "probe_limit": len(candidate_ids),
                **probe_filter_params,
            }
            if root_observation_type:
                params["bulk_root_observation_type"] = root_observation_type
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=_remaining_timeout_ms(),
                settings=_settings(len(candidate_ids)),
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
            # A large IN set can exceed the point-probe budget even though two
            # smaller disjoint probes finish. Split without changing candidate
            # order or accepting a partial result.
            if len(candidate_ids) > 1:
                midpoint = len(candidate_ids) // 2
                return _probe_candidates(candidate_ids[:midpoint]) | _probe_candidates(
                    candidate_ids[midpoint:]
                )
            raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE) from None
        return set(_ordered_unique_ids(result.data))

    seed_query = f"""
    SELECT trace_id, max(start_time) AS bulk_order_start_time
    FROM spans FINAL
    PREWHERE project_id = %(project_id)s
      AND start_time >= %(slice_start)s
      AND start_time < %(slice_end)s
    WHERE is_deleted = 0
      AND (parent_span_id IS NULL OR parent_span_id = '')
      AND trace_id NOT IN %(skip_trace_ids)s
      {root_observation_fragment}
      {slice_filter_fragment}
    GROUP BY trace_id
    ORDER BY bulk_order_start_time DESC, trace_id DESC
    LIMIT %(candidate_limit)s
    """

    # A voice-only post-filter (simulator exclusion) can reject candidates
    # returned by the whole-window attempt. Retain its accepted canonical
    # prefix and skip every candidate already classified when fallback slices
    # refill the result. The normal trace path has no post-filter and retains
    # its existing empty fallback state.
    matched_ids: list[str] = (
        filtered_fast_ids if candidate_post_filter is not None else []
    )
    matched_set: set[str] = set(matched_ids)
    seen_candidates: set[str] = (
        set(fast_ids) if candidate_post_filter is not None else set()
    )
    scan_now = datetime.utcnow()
    cursor = min(
        requested_end,
        scan_now + _BULK_TRACE_MAX_FUTURE_SKEW,
    )
    slice_width = _BULK_TRACE_INITIAL_SLICE

    try:
        if cursor < requested_end:
            tail_query, tail_params = build_future_tail_probe(
                start=cursor,
                end=requested_end,
                root_only=True,
                project_id=str(project_id),
            )
            try:
                tail_result = analytics.execute_ch_query(
                    tail_query,
                    tail_params,
                    timeout_ms=_remaining_timeout_ms(
                        maximum=FUTURE_TAIL_PROBE_TIMEOUT_MS
                    ),
                    settings=FUTURE_TAIL_PROBE_SETTINGS,
                )
            except Exception:
                raise BulkTraceSelectionIncomplete(
                    _BULK_TRACE_INCOMPLETE_MESSAGE
                ) from None
            tail_data = getattr(tail_result, "data", None)
            if not isinstance(tail_data, list) or tail_data:
                raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE)

        while cursor > requested_start and len(matched_ids) < target:
            slice_start = max(requested_start, cursor - slice_width)
            candidate_limit = min(
                _BULK_TRACE_CANDIDATE_BATCH,
                max(target - len(matched_ids), 1),
            )
            skip_ids = excluded | seen_candidates
            seed_params = {
                "project_id": str(project_id),
                "slice_start": slice_start,
                "slice_end": cursor,
                # Keep the tuple non-empty: clickhouse-driver renders an empty
                # tuple as ``()``, which is not valid in every CH version.
                "skip_trace_ids": tuple(sorted(skip_ids)) or ("",),
                "candidate_limit": candidate_limit,
                "start_date": requested_start,
                "end_date": requested_end,
                **fast_filter_params,
            }
            if root_observation_type:
                seed_params["bulk_root_observation_type"] = root_observation_type
            query = seed_query
            if scalar_root_supported:
                seed_builder = TraceListQueryBuilderV2(
                    project_id=str(project_id),
                    filters=[
                        {
                            "column_id": "start_time",
                            "filter_config": {
                                "col_type": "SYSTEM_METRIC",
                                "filter_type": "datetime",
                                "filter_op": "between",
                                "filter_value": [requested_start, requested_end],
                            },
                        }
                    ],
                )
                query, seed_params = seed_builder.build_latest_root_id_page(
                    slice_start=slice_start,
                    slice_end=cursor,
                    limit=candidate_limit,
                    exclude_trace_ids=skip_ids,
                    trace_id_desc=True,
                    order_by_recent_minute=False,
                )
            try:
                seed_result = analytics.execute_ch_query(
                    query,
                    seed_params,
                    timeout_ms=_remaining_timeout_ms(),
                    settings=_settings(candidate_limit),
                )
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                if slice_width > _BULK_TRACE_MIN_SLICE:
                    slice_width = max(
                        _BULK_TRACE_MIN_SLICE,
                        slice_width / 2,
                    )
                    continue
                raise BulkTraceSelectionIncomplete(
                    _BULK_TRACE_INCOMPLETE_MESSAGE
                ) from None

            candidate_ids = _ordered_unique_ids(seed_result.data)
            fresh_candidates = [
                trace_id
                for trace_id in candidate_ids
                if trace_id not in seen_candidates
            ]
            seen_candidates.update(fresh_candidates)

            matching = (
                _probe_candidates(fresh_candidates)
                if candidate_probe_needed
                else set(fresh_candidates)
            )
            ordered_matching = [
                trace_id for trace_id in fresh_candidates if trace_id in matching
            ]
            accepted_matching = set(_apply_candidate_post_filter(ordered_matching))
            for trace_id in fresh_candidates:
                if trace_id in accepted_matching and trace_id not in matched_set:
                    matched_set.add(trace_id)
                    matched_ids.append(trace_id)
                    if len(matched_ids) >= target:
                        return _result(matched_ids, truncated=True)

            # A short grouped result proves this exact slice is exhausted.
            # Move to its adjacent predecessor; widen empty/sparse intervals
            # geometrically so all-history low-volume selections do not issue
            # one query for every five-minute gap.
            if len(seed_result.data) < candidate_limit:
                cursor = slice_start
                if len(fresh_candidates) < max(candidate_limit // 4, 1):
                    slice_width = min(slice_width * 2, _BULK_TRACE_MAX_SLICE)
                else:
                    slice_width = _BULK_TRACE_INITIAL_SLICE
            elif not fresh_candidates:
                # Defensive: GROUP BY should prevent this. Failing closed
                # avoids looping forever if a driver or future query rewrite
                # violates the unique-row contract.
                raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE)
    except Exception as exc:
        if not isinstance(exc, BulkTraceSelectionIncomplete):
            logger.warning(
                "bulk_selection_resolve_trace_ch_query_failed",
                project_id=str(project_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
        else:
            logger.warning(
                "bulk_selection_resolve_trace_ch_incomplete",
                project_id=str(project_id),
            )
        raise

    if cursor <= requested_start:
        return _result(matched_ids, truncated=False)
    raise BulkTraceSelectionIncomplete(_BULK_TRACE_INCOMPLETE_MESSAGE)


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

    # Project + workspace scope are resolved in PG — the project / annotation-
    # label tables are NOT tracer tables and are not being dropped. Trace/voice
    # rows themselves are read only from ClickHouse (no PG tracer-table access),
    # so filter-mode add stays working once the PG tracer tables are dropped.
    # Verifying the project up front keeps the 404 contract consistent with the
    # enumerated path.
    project = Project.objects.get(id=project_id, organization=organization)
    if not _project_matches_workspace(project, workspace):
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    annotation_labels = get_annotation_labels_for_project(project.id, organization)
    annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]

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
    if not _has_explicit_time_filter(filters):
        ch_filters.append(_all_history_time_filter())

    if is_voice_call:
        return _resolve_voice_call_ids_clickhouse(
            project_id=project_id,
            filters=ch_filters,
            exclude_ids=set(exclude_ids or ()),
            cap=cap,
            remove_simulation_calls=remove_simulation_calls,
            annotation_label_ids=annotation_label_ids,
        )
    return _resolve_trace_ids_clickhouse(
        project_id=project_id,
        filters=ch_filters,
        exclude_ids=set(exclude_ids or ()),
        cap=cap,
        annotation_label_ids=annotation_label_ids,
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
            "filter_value": ["1971-01-01T00:00:00", "2099-12-31T23:59:59"],
        },
    }


def _resolve_span_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    annotation_label_ids: list[str],
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
    from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.read_budget import (
        FUTURE_TAIL_PROBE_SETTINGS,
        FUTURE_TAIL_PROBE_TIMEOUT_MS,
        build_future_tail_probe,
        is_read_budget_error,
    )
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    if cap <= 0:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if cap > _BULK_TRACE_MAX_CAP:
        raise ValueError(
            f"Span selection cap cannot exceed {_BULK_TRACE_MAX_CAP} items."
        )

    ch_filters = list(filters or [])
    if not _has_explicit_time_filter(ch_filters):
        ch_filters.append(_all_history_time_filter())

    BuilderCls = get_query_builder_class("SPAN_LIST")  # noqa: N806
    requested_start, requested_end = BaseQueryBuilder.parse_time_range(ch_filters)
    if requested_start >= requested_end:
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    analytics = AnalyticsQueryService()
    target = cap + 1
    excluded = {str(value) for value in exclude_ids if value}
    deadline = time.monotonic() + (_BULK_TRACE_TOTAL_READ_MS / 1000)
    scalar_span_builder = BuilderCls(
        project_id=str(project_id),
        filters=ch_filters,
        annotation_label_ids=annotation_label_ids,
    )
    scalar_span_supported = (
        any(_filter_column_id(item) == "final_status" for item in ch_filters)
        and scalar_span_builder.supports_latest_attribute_page()
    )

    def _remaining_timeout_ms(*, maximum: int = _BULK_TRACE_QUERY_MAX_MS) -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining < _BULK_TRACE_MIN_QUERY_MS:
            raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE)
        return min(maximum, remaining)

    def _settings(result_limit: int) -> dict:
        return {
            **_BULK_TRACE_READ_SETTINGS,
            "max_result_rows": max(int(result_limit), 1),
        }

    def _exclusion_filter(skip_ids: set[str]) -> dict:
        return {
            "column_id": "span_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "not_in",
                "filter_value": sorted(skip_ids),
            },
        }

    def _query_ids(
        *,
        skip_ids: set[str],
        limit: int,
        timeout_max: int = _BULK_TRACE_QUERY_MAX_MS,
        slice_bounds: tuple[datetime, datetime] | None = None,
    ) -> tuple[list[str], int]:
        if scalar_span_supported:
            scalar_start, scalar_end = slice_bounds or (
                requested_start,
                requested_end,
            )
            query, params = scalar_span_builder.build_latest_attribute_id_page(
                slice_start=scalar_start,
                slice_end=scalar_end,
                limit=limit,
                exclude_span_ids=skip_ids,
            )
        else:
            builder = BuilderCls(
                project_id=str(project_id),
                filters=[*ch_filters, _exclusion_filter(skip_ids)],
                annotation_label_ids=annotation_label_ids,
            )
            query, params = builder.build_id_query(
                # Keep the saved whole-window params in annotation/eval membership
                # subqueries. A fallback slice is an additional outer span-time
                # predicate, not a replacement UI time filter.
                limit=None if slice_bounds is not None else limit,
                order_by_recent_minute=True,
                latest_state=True,
            )
            if slice_bounds is not None:
                params = {
                    **params,
                    "bulk_slice_start": slice_bounds[0],
                    "bulk_slice_end": slice_bounds[1],
                    "bulk_slice_limit": limit,
                }
                slice_clause = """
                  AND start_time >= %(bulk_slice_start)s
                  AND start_time < %(bulk_slice_end)s
                ORDER BY toStartOfMinute(start_time) DESC, id
                LIMIT %(bulk_slice_limit)s
                """
                head, marker, tail = query.rpartition("\nSETTINGS ")
                if marker:
                    query = f"{head.rstrip()}{slice_clause}\nSETTINGS {tail}"
                else:
                    query = f"{query.rstrip()}{slice_clause}"
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_remaining_timeout_ms(maximum=timeout_max),
            settings=_settings(limit),
        )
        unique: list[str] = []
        seen: set[str] = set()
        for row in result.data:
            span_id = str(row.get("id", ""))
            if not span_id or span_id in seen or span_id in skip_ids:
                continue
            seen.add(span_id)
            unique.append(span_id)
        return unique, len(result.data)

    def _result(ids: list[str], *, truncated: bool) -> ResolveResult:
        returned = ids[:cap]
        total_matching = len(returned) + (1 if truncated else 0)
        logger.info(
            "bulk_selection_resolve_span_ch",
            project_id=str(project_id),
            filter_count=len(filters or []),
            exclude_count=len(excluded),
            total_matching=total_matching,
            returned=len(returned),
            truncated=truncated,
        )
        return ResolveResult(
            ids=returned,
            total_matching=total_matching,
            truncated=truncated,
        )

    try:
        try:
            fast_ids, fast_raw_count = _query_ids(
                skip_ids=excluded,
                limit=target,
                timeout_max=_BULK_TRACE_FAST_ATTEMPT_MS,
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
        else:
            if len(fast_ids) >= target:
                return _result(fast_ids, truncated=True)
            if fast_raw_count < target:
                return _result(fast_ids, truncated=False)

        selected: list[str] = []
        selected_set: set[str] = set()
        seen_candidates: set[str] = set()
        cursor = min(
            requested_end,
            datetime.utcnow() + _BULK_TRACE_MAX_FUTURE_SKEW,
        )
        slice_width = _BULK_TRACE_INITIAL_SLICE

        if cursor < requested_end:
            tail_query, tail_params = build_future_tail_probe(
                start=cursor,
                end=requested_end,
                root_only=False,
                project_id=str(project_id),
            )
            try:
                tail_result = analytics.execute_ch_query(
                    tail_query,
                    tail_params,
                    timeout_ms=_remaining_timeout_ms(
                        maximum=FUTURE_TAIL_PROBE_TIMEOUT_MS
                    ),
                    settings=FUTURE_TAIL_PROBE_SETTINGS,
                )
            except Exception:
                raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE) from None
            tail_data = getattr(tail_result, "data", None)
            if not isinstance(tail_data, list) or tail_data:
                raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE)

        while cursor > requested_start and len(selected) < target:
            slice_start = max(requested_start, cursor - slice_width)
            candidate_limit = max(target - len(selected), 1)
            skip_ids = excluded | seen_candidates
            try:
                candidate_ids, raw_count = _query_ids(
                    skip_ids=skip_ids,
                    limit=candidate_limit,
                    slice_bounds=(slice_start, cursor),
                )
            except Exception as exc:
                if not is_read_budget_error(exc):
                    raise
                if slice_width > _BULK_TRACE_MIN_SLICE:
                    slice_width = max(
                        _BULK_TRACE_MIN_SLICE,
                        slice_width / 2,
                    )
                    continue
                raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE) from None

            fresh = [
                span_id for span_id in candidate_ids if span_id not in seen_candidates
            ]
            seen_candidates.update(fresh)
            for span_id in fresh:
                if span_id in selected_set:
                    continue
                selected_set.add(span_id)
                selected.append(span_id)
                if len(selected) >= target:
                    return _result(selected, truncated=True)

            # Exclusions live in the builder predicate, before its LIMIT.
            # A short result proves this adjacent time slice is exhausted.
            if raw_count < candidate_limit:
                cursor = slice_start
                if len(fresh) < max(candidate_limit // 4, 1):
                    slice_width = min(slice_width * 2, _BULK_TRACE_MAX_SLICE)
                else:
                    slice_width = _BULK_TRACE_INITIAL_SLICE
            elif not fresh:
                raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE)

        if cursor <= requested_start:
            return _result(selected, truncated=False)
        raise BulkSelectionIncomplete(_BULK_SPAN_INCOMPLETE_MESSAGE)
    except Exception as exc:
        logger.warning(
            (
                "bulk_selection_resolve_span_ch_incomplete"
                if isinstance(exc, BulkSelectionIncomplete)
                else "bulk_selection_resolve_span_ch_query_failed"
            ),
            project_id=str(project_id),
            error_type=type(exc).__name__,
        )
        raise


def resolve_filtered_span_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
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

    # Project + workspace scope are resolved in PG — the project / annotation-label
    # tables are NOT tracer tables and are not being dropped. The span rows
    # themselves are read only from ClickHouse (no PG tracer-table access), so
    # filter-mode add stays working once the PG tracer tables are dropped.
    project = Project.objects.get(id=project_id, organization=organization)
    if not _project_matches_workspace(project, workspace):
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    annotation_labels = get_annotation_labels_for_project(project.id, organization)
    annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]

    return _resolve_span_ids_clickhouse(
        project_id=project_id,
        filters=filters or [],
        exclude_ids=set(exclude_ids or ()),
        cap=cap,
        annotation_label_ids=annotation_label_ids,
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
    session_ids: list[str], score_filters: list[dict]
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
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.read_budget import is_read_budget_error
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    if cap <= 0:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if cap > _BULK_TRACE_MAX_CAP:
        raise ValueError(
            f"Session selection cap cannot exceed {_BULK_TRACE_MAX_CAP} items."
        )

    BuilderCls = get_query_builder_class("SESSION_LIST")  # noqa: N806
    ch_filters = _prepare_session_ch_filters(
        non_score_filters, project_id=project_id, organization=organization
    )

    # Parity: the PG aggregate path imposes NO time window (it selects every
    # session unless the payload carries a `created_at` filter). The CH builder's
    # `parse_time_range` instead DEFAULTS to now-30d when no time bound is sent
    # (base.py — a dashboard-perf default), which would silently drop older
    # sessions a "select all matching this filter" must include. So when the
    # payload has no explicit time filter, inject a wide-open `start_time`
    # window to disable the default narrowing (mirrors the same all-history
    # selection the PG path gives). An explicit user time filter passes through
    # untouched and prunes normally.
    #
    # Lower bound is `1971` (not `1970`): a score-label filter routes through the
    # annotation score subqueries, which lower-bound on `created_at - INTERVAL 1
    # DAY`, and a ClickHouse `DateTime` is a 32-bit epoch — `1970-01-01 - 1 DAY`
    # underflows and matches nothing.
    if not _has_explicit_time_filter(non_score_filters):
        ch_filters.append(
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        "1971-01-01T00:00:00",
                        "2099-12-31T23:59:59",
                    ],
                },
            }
        )

    analytics = AnalyticsQueryService()
    target = cap + 1
    excluded = {str(value) for value in exclude_ids if value}
    deadline = time.monotonic() + (_BULK_TRACE_TOTAL_READ_MS / 1000)

    def _remaining_timeout_ms() -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining < _BULK_TRACE_MIN_QUERY_MS:
            raise BulkSelectionIncomplete(_BULK_SESSION_INCOMPLETE_MESSAGE)
        return min(_BULK_TRACE_QUERY_MAX_MS, remaining)

    def _finalize_spans_sources(sql: str) -> str:
        # Current session membership and aggregates must ignore stale/deleted
        # ReplacingMergeTree versions. Alias placement follows CH syntax:
        # ``FROM spans AS rs FINAL``.
        sql = re.sub(
            r"\b(FROM|JOIN)\s+spans\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\b"
            r"(?!\s+FINAL\b)",
            r"\1 spans AS \2 FINAL",
            sql,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\b(FROM|JOIN)\s+spans\b(?!\s+(?:AS\b|FINAL\b))",
            r"\1 spans FINAL",
            sql,
            flags=re.IGNORECASE,
        )

    def _session_exclusion_filter(skip_ids: set[str]) -> dict:
        return {
            "column_id": "session_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "not_in",
                "filter_value": sorted(skip_ids),
            },
        }

    def _query_candidates(*, skip_ids: set[str], limit: int) -> tuple[list[str], int]:
        builder = BuilderCls(
            project_id=str(project_id),
            filters=[*ch_filters, _session_exclusion_filter(skip_ids)],
            sort_params=[],
        )
        query, params = builder.build_id_query(limit=limit)
        query = _finalize_spans_sources(query)
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_remaining_timeout_ms(),
            settings={
                **_BULK_TRACE_READ_SETTINGS,
                "max_result_rows": max(limit, 1),
            },
        )
        unique: list[str] = []
        seen: set[str] = set()
        for row in result.data:
            session_id = str(row.get("session_id", ""))
            if not session_id or session_id in seen or session_id in skip_ids:
                continue
            seen.add(session_id)
            unique.append(session_id)
        return unique, len(result.data)

    def _result(ids: list[str], *, truncated: bool) -> ResolveResult:
        returned = ids[:cap]
        total_matching = len(returned) + (1 if truncated else 0)
        logger.info(
            "bulk_selection_resolve_session_ch",
            project_id=str(project_id),
            filter_count=len(non_score_filters or []) + len(score_filters or []),
            score_filter_count=len(score_filters or []),
            exclude_count=len(excluded),
            total_matching=total_matching,
            returned=len(returned),
            truncated=truncated,
        )
        return ResolveResult(
            ids=returned,
            total_matching=total_matching,
            truncated=truncated,
        )

    selected: list[str] = []
    selected_set: set[str] = set()
    seen_candidates: set[str] = set()
    try:
        while len(selected) < target:
            candidate_limit = max(target - len(selected), 1)
            candidates, raw_count = _query_candidates(
                skip_ids=excluded | seen_candidates,
                limit=candidate_limit,
            )
            fresh = [
                session_id
                for session_id in candidates
                if session_id not in seen_candidates
            ]
            seen_candidates.update(fresh)
            surviving = (
                _apply_session_score_filters_pg(fresh, score_filters)
                if score_filters
                else fresh
            )
            for session_id in surviving:
                if session_id in selected_set:
                    continue
                selected_set.add(session_id)
                selected.append(session_id)
                if len(selected) >= target:
                    return _result(selected, truncated=True)

            # build_id_query applies the remap-resolved NOT IN before its LIMIT.
            # A short page therefore proves the complete CH candidate set is
            # exhausted, including the rows rejected by score filters.
            if raw_count < candidate_limit:
                return _result(selected, truncated=False)
            if not fresh:
                raise BulkSelectionIncomplete(_BULK_SESSION_INCOMPLETE_MESSAGE)
            _remaining_timeout_ms()
    except Exception as exc:
        if is_read_budget_error(exc):
            exc = BulkSelectionIncomplete(_BULK_SESSION_INCOMPLETE_MESSAGE)
        logger.warning(
            (
                "bulk_selection_resolve_session_ch_incomplete"
                if isinstance(exc, BulkSelectionIncomplete)
                else "bulk_selection_resolve_session_ch_query_failed"
            ),
            project_id=str(project_id),
            error_type=type(exc).__name__,
        )
        raise exc


def resolve_filtered_session_ids(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None = None,
    organization,
    workspace=None,
    cap: int = 10_000,
    user=None,
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

    # Resolve + scope-check the project up front (the CH builder keys spans by
    # project_id but does NOT enforce org membership or the SIMULATOR carve-out).
    # Raising Project.DoesNotExist here preserves the caller's 404 mapping. These
    # are Project / annotation tables — not tracer tables — so they stay in PG.
    project = Project.objects.get(id=project_id, organization=organization)
    if project.source == ProjectSourceChoices.SIMULATOR.value:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if workspace is not None and project.workspace_id != getattr(
        workspace, "id", workspace
    ):
        # Workspace mismatch — nothing to resolve.
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    score_label_ids = _session_score_label_ids(project_id)
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

    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    qs = qs.order_by("-created_at", "-id")

    # See resolve_filtered_trace_ids — cap+1 fetch instead of COUNT(*).
    capped = list(qs.values_list("id", flat=True)[: cap + 1])
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
