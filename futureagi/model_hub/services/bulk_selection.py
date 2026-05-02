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

Future phases will add sibling resolvers for ``observation_span``,
``trace_session``, and ``call_execution``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

import structlog
from django.db import models
from django.db.models import (
    Count,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce, Round

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from simulate.models.test_execution import CallExecution
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.filters import FilterEngine, apply_created_at_filters

logger = structlog.get_logger(__name__)


@dataclass
class ResolveResult:
    """Result of a filter-based ID resolution."""

    ids: list[UUID]
    total_matching: int
    truncated: bool


_USER_SCOPED_COLUMN_IDS = {"my_annotations", "annotator"}


def _validate_user_scoped_filters(filters, user):
    """Raise ValueError when filters reference user-scoped columns but no user is provided."""
    if user is not None:
        return
    for f in filters or []:
        col = f.get("column_id") or f.get("columnId")
        if col in _USER_SCOPED_COLUMN_IDS:
            raise ValueError(
                f"Filter references user-scoped column {col!r} but user is None"
            )


def _build_trace_base_queryset(project_id, organization, workspace=None):
    """Return org/workspace/project-scoped base Trace queryset.

    Annotates ``span_attributes`` from the root ObservationSpan because the
    frontend sends SPAN_ATTRIBUTE-typed filters that expect that attribute
    path to exist on the Trace row. ``list_traces_of_session`` and
    ``list_voice_calls`` both add this annotation before applying filters;
    without it, ``span_attributes__contains`` silently matches the entire
    project and the queue receives ALL traces.

    Raises ``Project.DoesNotExist`` if the project does not belong to the
    organization.
    """
    project = Project.objects.get(id=project_id, organization=organization)

    root_span_qs = ObservationSpan.objects.filter(
        trace_id=OuterRef("id"), parent_span_id__isnull=True
    )
    qs = Trace.objects.filter(project_id=project.id).annotate(
        # Pull span_attributes off the root span. Old rows only have
        # eval_attributes populated — Coalesce falls back to keep parity
        # with the list views.
        span_attributes=Subquery(
            root_span_qs.annotate(
                _attrs=Coalesce("span_attributes", "eval_attributes")
            ).values("_attrs")[:1]
        ),
    )

    if workspace is not None:
        qs = qs.filter(project__workspace=workspace)

    return qs


def _apply_voice_call_constraints(qs, filters: list[dict], *, remove_simulation_calls: bool = False):
    """Narrow a Trace queryset to match ``list_voice_calls``'s result set.

    Simulator/voice projects render the grid via ``list_voice_calls`` which
    constrains to traces whose root span is a conversation, applies voice
    system metrics (agent latency, turn count, etc.), and optionally hides
    the VAPI simulator calls. The filter-mode resolver mirrored only
    ``list_traces_of_session``, so for voice projects it returned a
    superset — grid shows N, queue receives N + non-conversation traces.
    This helper brings parity with the voice list view.
    """
    root_span_qs = ObservationSpan.objects.filter(
        trace_id=OuterRef("id"),
        parent_span_id__isnull=True,
    )
    qs = qs.annotate(
        has_conversation_root=Exists(
            root_span_qs.filter(observation_type="conversation")
        )
    ).filter(has_conversation_root=True)

    # Voice-specific system metrics (agent_latency / turn_count / etc.) are
    # stored as span aggregates and are NOT in the standard system-metric
    # branch applied by ``_apply_trace_filters``.
    voice_metric_conds, voice_annotations = (
        FilterEngine.get_filter_conditions_for_voice_system_metrics(filters or [])
    )
    if voice_annotations:
        qs = qs.annotate(**voice_annotations)
    if voice_metric_conds:
        qs = qs.filter(voice_metric_conds)

    if remove_simulation_calls:
        sim_q = FilterEngine.get_filter_conditions_for_simulation_calls(
            remove_simulation_calls=True
        )
        if sim_q:
            qs = qs.exclude(sim_q)

    return qs


def _apply_trace_filters(
    base_qs,
    filters: list[dict],
    *,
    user,
    organization,
):
    """Apply the same FilterEngine branches as ``list_traces_of_session``.

    Mirrors ``tracer.views.trace.ObservationTraceViewSet.list_traces_of_session``
    lines 1668-1742. Any drift here is a bug — see parity tests.
    """
    if not filters:
        return base_qs

    annotation_labels = AnnotationsLabels.objects.filter(
        organization=organization, deleted=False
    )

    combined = Q()
    qs = base_qs

    # 1. System metrics
    system_conds = FilterEngine.get_filter_conditions_for_system_metrics(filters)
    if system_conds:
        combined &= system_conds

    # 2. Separate annotation filters from eval filters (must precede #3 and #4)
    def _col_type(f):
        fc = f.get("filter_config", {})
        return fc.get("col_type", f.get("col_type", ""))

    annotation_col_types = {"ANNOTATION"}
    annotation_column_ids = {"my_annotations", "annotator"}
    non_annotation = [
        f
        for f in filters
        if _col_type(f) not in annotation_col_types
        and (f.get("column_id") or f.get("columnId"))
        not in annotation_column_ids
    ]

    # 3. Non-system (eval) metrics, excluding annotation columns
    eval_conds = FilterEngine.get_filter_conditions_for_non_system_metrics(
        non_annotation
    )
    if eval_conds:
        combined &= eval_conds

    # 4. Voice-call annotations (score / annotator / my_annotations)
    ann_conds, extra_annotations = (
        FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters, user_id=getattr(user, "id", None)
        )
    )
    if extra_annotations:
        qs = qs.annotate(**extra_annotations)
    if ann_conds:
        combined &= ann_conds

    # 5. Span attributes
    span_attr_conds = FilterEngine.get_filter_conditions_for_span_attributes(filters)
    if span_attr_conds:
        combined &= span_attr_conds

    # 6. has_eval toggle
    has_eval = FilterEngine.get_filter_conditions_for_has_eval(
        filters, observe_type="trace"
    )
    if has_eval:
        combined &= has_eval

    # 7. has_annotation toggle
    has_ann = FilterEngine.get_filter_conditions_for_has_annotation(
        filters,
        observe_type="trace",
        annotation_label_ids=[str(lbl.id) for lbl in annotation_labels],
    )
    if has_ann:
        combined &= has_ann

    if combined:
        qs = qs.filter(combined)

    return qs


def _resolve_voice_call_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    remove_simulation_calls: bool,
    annotation_label_ids: list[str],
) -> ResolveResult | None:
    """Resolve voice-call trace IDs via ClickHouse.

    Mirrors ``_list_voice_calls_clickhouse`` — uses
    ``VoiceCallListQueryBuilder`` so filter semantics (especially
    SPAN_ATTRIBUTE filters translated through ``ClickHouseFilterBuilder``)
    match the grid exactly.

    Returns ``None`` if ClickHouse is unavailable so the caller can fall
    back to the PG path.
    """
    try:
        from tracer.services.clickhouse.query_builders import (
            VoiceCallListQueryBuilder,
        )
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
            QueryType,
        )
    except ImportError:
        return None

    analytics = AnalyticsQueryService()
    if not analytics.should_use_clickhouse(QueryType.VOICE_CALL_LIST):
        return None

    builder = VoiceCallListQueryBuilder(
        project_id=str(project_id),
        page_number=0,
        page_size=cap,
        filters=filters or [],
        annotation_label_ids=annotation_label_ids,
        remove_simulation_calls=remove_simulation_calls,
    )
    # build() must run before build_count_query() because the former is
    # what populates self.params with start_date / end_date.
    ids_query, ids_params = builder.build()
    ids_result = analytics.execute_ch_query(
        ids_query, ids_params, timeout_ms=15_000
    )
    ids = [
        str(r.get("trace_id", ""))
        for r in ids_result.data
        if r.get("trace_id")
    ]

    count_query, count_params = builder.build_count_query()
    count_result = analytics.execute_ch_query(
        count_query, count_params, timeout_ms=10_000
    )
    total_matching = (
        count_result.data[0].get("total", 0) if count_result.data else 0
    )

    # VoiceCallListQueryBuilder's SQL simulation filter is a no-op (the
    # phone numbers live in the heavy span_attributes_raw blob). The list
    # view filters in Python after Phase 1b; we do the same here when the
    # toggle is on.
    if remove_simulation_calls and ids:
        ids = _filter_out_simulator_calls_ch(ids, project_id, analytics)
        total_matching = len(ids) + len(exclude_ids or set())

    if exclude_ids:
        excl = {str(i) for i in exclude_ids}
        ids = [i for i in ids if i not in excl]

    truncated = total_matching > cap
    ids = ids[:cap]

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


def _filter_out_simulator_calls_ch(trace_ids, project_id, analytics):
    """Post-filter the given trace_ids to drop VAPI simulator calls.

    Mirrors ``_list_voice_calls_clickhouse``'s Python-side simulation
    filter: fetch span_attributes_raw + provider for the root conversation
    span of each trace, then apply ``is_simulator_call``.
    """
    from tracer.services.clickhouse.query_builders import VoiceCallListQueryBuilder

    if not trace_ids:
        return trace_ids

    import json as _json

    # Get root conversation span IDs and attributes in CH.
    query = """
    SELECT trace_id, id AS span_id, provider, span_attributes_raw
    FROM spans
    WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0
      AND (parent_span_id IS NULL OR parent_span_id = '')
      AND observation_type = 'conversation'
      AND trace_id IN %(trace_ids)s
    """
    params = {"project_id": str(project_id), "trace_ids": tuple(str(t) for t in trace_ids)}
    result = analytics.execute_ch_query(query, params, timeout_ms=15_000)
    sim_trace_ids = set()
    for row in result.data:
        raw = row.get("span_attributes_raw") or "{}"
        try:
            attrs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (_json.JSONDecodeError, TypeError):
            attrs = {}
        if VoiceCallListQueryBuilder.is_simulator_call(attrs, row.get("provider") or ""):
            sim_trace_ids.add(str(row.get("trace_id", "")))
    return [t for t in trace_ids if t not in sim_trace_ids]


def _resolve_trace_ids_clickhouse(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: set,
    cap: int,
    annotation_label_ids: list[str],
) -> ResolveResult | None:
    """Resolve regular trace IDs via ClickHouse.

    Mirrors ``_list_traces_of_session_clickhouse`` — uses
    ``TraceListQueryBuilder`` so filter semantics (especially
    SPAN_ATTRIBUTE filters translated through ``ClickHouseFilterBuilder``)
    match the non-voice grid exactly.

    Returns ``None`` if ClickHouse is unavailable so the caller can fall
    back to the PG path.
    """
    try:
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
            QueryType,
        )
    except ImportError:
        return None

    analytics = AnalyticsQueryService()
    if not analytics.should_use_clickhouse(QueryType.TRACE_OF_SESSION_LIST):
        return None

    builder = TraceListQueryBuilder(
        project_id=str(project_id),
        page_number=0,
        page_size=cap,
        filters=filters or [],
        annotation_label_ids=annotation_label_ids,
        # Phase 1 light columns are all we need — we only want trace_id.
        columns=["trace_id"],
    )
    # build() must run before build_count_query() because it populates
    # self.params with start_date / end_date that the count query reads.
    ids_query, ids_params = builder.build()
    ids_result = analytics.execute_ch_query(
        ids_query, ids_params, timeout_ms=15_000
    )
    ids = [
        str(r.get("trace_id", ""))
        for r in ids_result.data
        if r.get("trace_id")
    ]

    count_query, count_params = builder.build_count_query()
    count_result = analytics.execute_ch_query(
        count_query, count_params, timeout_ms=10_000
    )
    total_matching = (
        count_result.data[0].get("total", 0) if count_result.data else 0
    )

    if exclude_ids:
        excl = {str(i) for i in exclude_ids}
        ids = [i for i in ids if i not in excl]

    truncated = total_matching > cap
    ids = ids[:cap]

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

    # Verify project exists + is in org before we try either backend. Keeps
    # the 404 contract consistent with the enumerated path.
    project = Project.objects.get(id=project_id, organization=organization)
    if workspace is not None and getattr(project, "workspace_id", None) != getattr(
        workspace, "id", None
    ):
        raise Project.DoesNotExist

    # Dispatch to ClickHouse when available so filter semantics
    # (especially SPAN_ATTRIBUTE filters translated through
    # ClickHouseFilterBuilder) match the grid exactly. Both grid paths
    # (regular traces + voice calls) are CH-first in production, and
    # PG/CH diverge on JSON span_attribute semantics — the PG fallback
    # was matching the full project instead of the filtered subset.
    annotation_label_ids = [
        str(lbl.id)
        for lbl in AnnotationsLabels.objects.filter(
            organization=organization, deleted=False, project_id=project.id
        )
    ]
    if is_voice_call:
        ch_result = _resolve_voice_call_ids_clickhouse(
            project_id=project_id,
            filters=filters or [],
            exclude_ids=set(exclude_ids or ()),
            cap=cap,
            remove_simulation_calls=remove_simulation_calls,
            annotation_label_ids=annotation_label_ids,
        )
    else:
        ch_result = _resolve_trace_ids_clickhouse(
            project_id=project_id,
            filters=filters or [],
            exclude_ids=set(exclude_ids or ()),
            cap=cap,
            annotation_label_ids=annotation_label_ids,
        )
    if ch_result is not None:
        return ch_result

    base = _build_trace_base_queryset(project_id, organization, workspace)
    qs = _apply_trace_filters(
        base, filters or [], user=user, organization=organization
    )

    if is_voice_call:
        qs = _apply_voice_call_constraints(
            qs,
            filters or [],
            remove_simulation_calls=remove_simulation_calls,
        )

    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    # Mirror the list view's `start_time` annotation so ordering is identical:
    # prefer the root span's start_time, fall back to Trace.created_at.
    qs = qs.annotate(
        start_time=Coalesce(
            Subquery(
                ObservationSpan.objects.filter(
                    trace_id=OuterRef("id"), parent_span_id__isnull=True
                )
                .order_by("start_time")
                .values("start_time")[:1]
            ),
            F("created_at"),
        )
    ).order_by("-start_time", "-id")

    # One COUNT + one SELECT for the capped IDs. Two queries total.
    total_matching = qs.count()
    ids = list(qs.values_list("id", flat=True)[:cap])
    truncated = total_matching > cap

    logger.info(
        "bulk_selection_resolve_trace",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(list(exclude_ids or [])),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


# --------------------------------------------------------------------------
# Phase 4 — source_type = observation_span
# --------------------------------------------------------------------------


def _build_span_base_queryset(project_id, organization, workspace=None):
    """Return org/workspace/project-scoped base ObservationSpan queryset.

    Mirrors the scoping at
    ``tracer.views.observation_span.ObservationSpanViewSet.list_spans_observe``
    (lines 1528-1580). Raises ``Project.DoesNotExist`` if the project is
    not in the org.
    """
    project = Project.objects.get(id=project_id, organization=organization)

    qs = ObservationSpan.objects.filter(
        project_id=project.id,
        project__organization=organization,
        deleted=False,
    )

    if workspace is not None:
        qs = qs.filter(project__workspace=workspace)

    return qs


def _apply_span_filters(base_qs, filters: list[dict], *, user, organization):
    """Apply the same FilterEngine branches as ``list_spans_observe``.

    Mirrors ``tracer/views/observation_span.py:1735-1806``. Two deltas vs
    the trace variant:

      - ``get_filter_conditions_for_voice_call_annotations`` is called with
        ``span_filter_kwargs={"observation_span_id": OuterRef("id")}``.
      - ``get_filter_conditions_for_has_eval`` / ``has_annotation`` use
        ``observe_type="span"``.
    """
    if not filters:
        return base_qs

    combined = Q()
    qs = base_qs

    # 1. System metrics
    system_conds = FilterEngine.get_filter_conditions_for_system_metrics(filters)
    if system_conds:
        combined &= system_conds

    # 2. Split annotation filters from eval filters
    annotation_col_types = {"ANNOTATION"}
    annotation_column_ids = {"my_annotations", "annotator"}
    non_annotation = [
        f
        for f in filters
        if f.get("col_type") not in annotation_col_types
        and (f.get("column_id") or f.get("columnId"))
        not in annotation_column_ids
    ]

    # 3. Non-system (eval) metrics
    eval_conds = FilterEngine.get_filter_conditions_for_non_system_metrics(
        non_annotation
    )
    if eval_conds:
        combined &= eval_conds

    # 4. Voice-call annotations — span variant uses span_filter_kwargs so
    # the annotation subquery joins on ObservationSpan.id rather than
    # Trace.id.
    ann_conds, extra_annotations = (
        FilterEngine.get_filter_conditions_for_voice_call_annotations(
            filters,
            user_id=getattr(user, "id", None),
            span_filter_kwargs={"observation_span_id": OuterRef("id")},
        )
    )
    if extra_annotations:
        qs = qs.annotate(**extra_annotations)
    if ann_conds:
        combined &= ann_conds

    # 5. Span attributes
    span_attr_conds = FilterEngine.get_filter_conditions_for_span_attributes(filters)
    if span_attr_conds:
        combined &= span_attr_conds

    # 6. has_eval — observe_type="span"
    has_eval = FilterEngine.get_filter_conditions_for_has_eval(
        filters, observe_type="span"
    )
    if has_eval:
        combined &= has_eval

    # 7. has_annotation — observe_type="span". list_spans_observe does
    # not pass annotation_label_ids, so we don't either.
    has_ann = FilterEngine.get_filter_conditions_for_has_annotation(
        filters, observe_type="span"
    )
    if has_ann:
        combined &= has_ann

    if combined:
        qs = qs.filter(combined)

    return qs


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

    Mirrors the filter semantics of ``list_spans_observe``. Shares the
    ``ResolveResult`` contract and the user-scoped-filter guard with
    :func:`resolve_filtered_trace_ids`.

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

    base = _build_span_base_queryset(project_id, organization, workspace)
    qs = _apply_span_filters(
        base, filters or [], user=user, organization=organization
    )

    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    # ObservationSpan has real start_time / id columns — order directly.
    qs = qs.order_by("-start_time", "-id")

    total_matching = qs.count()
    ids = list(qs.values_list("id", flat=True)[:cap])
    truncated = total_matching > cap

    logger.info(
        "bulk_selection_resolve_span",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(list(exclude_ids or [])),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


# --------------------------------------------------------------------------
# Phase 6 — source_type = trace_session
#
# Sessions are aggregated higher-order entities. ``list_sessions``
# (``tracer/views/trace_session.py:853-1170``) computes them by
# aggregating ObservationSpan rows grouped by ``trace__session_id`` and
# applying filters against the aggregate annotations + score subqueries.
# We mirror the non-ClickHouse path exactly so filter-mode returns the
# same session IDs as the list UI.
# --------------------------------------------------------------------------


# Shared with list_sessions — keep the field names in lockstep.
_SESSION_FIELD_MAP = {
    "total_cost": "total_cost",
    "total_tokens": "total_tokens",
    "total_traces_count": "traces_count",
    "start_time": "start_time",
    "end_time": "end_time",
    "created_at": "session_created_at",
    "session_id": "trace__session_id",
    "duration": "duration_val",
    "first_message": "first_message",
    "last_message": "last_message",
}

_SESSION_PRE_AGG_FIELDS = {"user_id": "end_user__user_id"}


def _build_session_base_queryset(project_id, organization, workspace=None):
    """Return scoped base TraceSession queryset (pre filter application)."""
    project = Project.objects.get(id=project_id, organization=organization)

    qs = TraceSession.objects.filter(project_id=project.id)
    if workspace is not None:
        qs = qs.filter(project__workspace=workspace)
    return qs


def _apply_session_filters(base_sessions_qs, filters, *, project_id, organization):
    """Apply the full session filter pipeline and return a session-id-valued
    aggregated queryset.

    Mirrors ``list_sessions`` lines 922-1157 (non-ClickHouse PG path)
    excluding pagination and sort ordering. Returns a queryset yielding
    dicts with a ``trace__session_id`` key.
    """
    trace_sessions_qs, remaining_filters = apply_created_at_filters(
        base_sessions_qs, filters or []
    )

    if not trace_sessions_qs.exists():
        return ObservationSpan.objects.none().values("trace__session_id")

    session_ids = trace_sessions_qs.values("id")

    # Pre-aggregation: user_id system filter applied before grouping.
    needs_first_last_cols = {"first_message", "last_message"}
    needs_first_last = any(
        f.get("column_id") in needs_first_last_cols for f in remaining_filters
    )

    pre_agg_q = FilterEngine.get_filter_conditions_for_system_metrics(
        [f for f in remaining_filters if f.get("column_id") in _SESSION_PRE_AGG_FIELDS],
        field_map=_SESSION_PRE_AGG_FIELDS,
    )
    remaining_filters = [
        f
        for f in remaining_filters
        if f.get("column_id") not in _SESSION_PRE_AGG_FIELDS
    ]

    aggregated = (
        ObservationSpan.objects.filter(
            pre_agg_q, trace__session_id__in=session_ids
        )
        .values("trace__session_id")
        .annotate(
            start_time=Min("start_time"),
            end_time=Max("end_time"),
            total_cost=Coalesce(
                Round(Sum("cost", output_field=FloatField()), 6),
                0.0,
            ),
            total_tokens=Coalesce(
                Sum(F("total_tokens"), output_field=models.IntegerField()),
                0,
            ),
            traces_count=Count("trace_id", distinct=True),
            session_created_at=Min("trace__session__created_at"),
        )
        .annotate(
            duration_val=ExpressionWrapper(
                F("end_time") - F("start_time"),
                output_field=DurationField(),
            ),
        )
    )

    if needs_first_last:
        aggregated = aggregated.annotate(
            first_message=Subquery(
                ObservationSpan.objects.filter(
                    trace__session_id=OuterRef("trace__session_id"),
                )
                .order_by("start_time")
                .values("input")[:1]
            ),
            last_message=Subquery(
                ObservationSpan.objects.filter(
                    trace__session_id=OuterRef("trace__session_id"),
                )
                .order_by("-start_time")
                .values("input")[:1]
            ),
        )

    # Split score filters (col_id matches a label on this project) from
    # system-metric filters operating on the aggregate field map.
    score_label_ids = (
        {
            str(lbl.id)
            for lbl in AnnotationsLabels.objects.filter(
                project_id=project_id, deleted=False
            )
        }
        if remaining_filters
        else set()
    )
    system_filters = []
    score_filters = []
    for f in remaining_filters:
        col_id = f.get("column_id", "")
        if col_id in score_label_ids:
            score_filters.append(f)
        else:
            system_filters.append(f)

    if system_filters:
        q_filters = FilterEngine.get_filter_conditions_for_system_metrics(
            system_filters, field_map=_SESSION_FIELD_MAP
        )
        if q_filters:
            aggregated = aggregated.filter(q_filters)

    # Score-based filters mirror list_sessions lines 1097-1139.
    for sf in score_filters:
        col_id = sf.get("column_id")
        fc = sf.get("filter_config", {})
        filter_op = fc.get("filter_op", "equals")
        filter_val = fc.get("filter_value")
        base_score_q = Score.objects.filter(
            trace_session_id=OuterRef("trace__session_id"),
            label_id=col_id,
            deleted=False,
        )
        if filter_op == "is_not_null":
            aggregated = aggregated.filter(Exists(base_score_q))
        elif filter_op == "is_null":
            aggregated = aggregated.exclude(Exists(base_score_q))
        else:
            if isinstance(filter_val, str) and "," in filter_val:
                filter_val = [v.strip() for v in filter_val.split(",") if v.strip()]
            if filter_op in ("equals", "is"):
                score_q = (
                    base_score_q.filter(value__in=filter_val)
                    if isinstance(filter_val, list)
                    else base_score_q.filter(value=filter_val)
                )
                aggregated = aggregated.filter(Exists(score_q))
            elif filter_op in ("not_equals", "is_not"):
                score_q = (
                    base_score_q.filter(value__in=filter_val)
                    if isinstance(filter_val, list)
                    else base_score_q.filter(value=filter_val)
                )
                aggregated = aggregated.exclude(Exists(score_q))
            elif filter_op == "contains":
                score_q = base_score_q.filter(value__icontains=filter_val)
                aggregated = aggregated.filter(Exists(score_q))
            else:
                aggregated = aggregated.filter(Exists(base_score_q))

    return aggregated


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
    """Return TraceSession IDs matching ``filters`` in ``project_id``.

    Mirrors ``list_sessions`` filter semantics (non-ClickHouse PG path).
    Ordering: ``-start_time, -trace__session_id`` for determinism.

    Raises:
        Project.DoesNotExist: if the project is not in the org.
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)

    base = _build_session_base_queryset(project_id, organization, workspace)
    aggregated = _apply_session_filters(
        base, filters or [], project_id=project_id, organization=organization
    )

    if exclude_ids:
        aggregated = aggregated.exclude(
            trace__session_id__in=[str(i) for i in exclude_ids]
        )

    aggregated = aggregated.order_by("-start_time", "-trace__session_id")

    total_matching = aggregated.count()
    ids = [
        row["trace__session_id"]
        for row in aggregated.values("trace__session_id")[:cap]
    ]
    truncated = total_matching > cap

    logger.info(
        "bulk_selection_resolve_session",
        project_id=str(project_id),
        filter_count=len(filters or []),
        exclude_count=len(list(exclude_ids or [])),
        total_matching=total_matching,
        returned=len(ids),
        truncated=truncated,
    )

    return ResolveResult(ids=ids, total_matching=total_matching, truncated=truncated)


# --------------------------------------------------------------------------
# Phase 8 — source_type = call_execution
#
# CallExecution isn't tied to an observe ``Project``. Its scope chain goes
# through test_execution → run_test → organization (+ agent_definition →
# workspace). The selection payload's ``project_id`` slot is reused to
# carry the ``agent_definition_id`` — see Phase 8 PRD.
# --------------------------------------------------------------------------


# UI column id → CallExecution ORM lookup. Mirrors the SIMULATION_RULE_FILTER_FIELDS
# the rule dialog sends. agent_definition is matched by name (icontains)
# because the dialog field is free-text. Persona is a nested JSON field on
# call_metadata, matched as a substring against the serialised JSON blob.
_CALL_EXECUTION_FIELD_MAP = {
    "status": "status",
    "simulation_call_type": "simulation_call_type",
    "call_type": "simulation_call_type",
    "duration_seconds": "duration_seconds",
    "overall_score": "overall_score",
    "agent_definition": "test_execution__agent_definition__agent_name",
    "persona": "call_metadata__row_data__persona",
}


def _apply_call_execution_filters(qs, filters):
    """Translate UI-shaped filters into CallExecution ORM lookups.

    Returns ``(qs, unsupported)`` where ``unsupported`` is the list of
    column ids the resolver couldn't map. Caller is expected to fail
    closed if any are returned.
    """
    unsupported: list[str] = []
    for f in filters:
        col = f.get("column_id") or f.get("columnId")
        cfg = f.get("filter_config") or f.get("filterConfig") or {}
        op = cfg.get("filter_op") or cfg.get("filterOp")
        value = (
            cfg.get("filter_value")
            if "filter_value" in cfg
            else cfg.get("filterValue")
        )
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

        # Persona is a nested JSON field; only support substring/equals
        # ops. Numeric ranges or in/not_in don't make sense.
        if col == "persona":
            if op in ("contains", "icontains"):
                qs = qs.filter(**{f"{orm_field}__icontains": value})
                continue
            if op in ("equals", "eq", "in"):
                values = value if isinstance(value, list) else [value]
                qs = qs.filter(**{f"{orm_field}__in": values})
                continue
            unsupported.append(col)
            continue

        try:
            if op in ("equals", "eq"):
                values = value if isinstance(value, list) else [value]
                if len(values) == 1:
                    qs = qs.filter(**{orm_field: values[0]})
                else:
                    qs = qs.filter(**{f"{orm_field}__in": values})
            elif op in ("not_equals", "ne"):
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
            elif op in ("contains", "icontains"):
                qs = qs.filter(**{f"{orm_field}__icontains": value})
            elif op in ("not_contains",):
                qs = qs.exclude(**{f"{orm_field}__icontains": value})
            elif op in ("more_than", "gt"):
                qs = qs.filter(**{f"{orm_field}__gt": value})
            elif op in ("less_than", "lt"):
                qs = qs.filter(**{f"{orm_field}__lt": value})
            elif op in ("more_than_or_equal", "gte"):
                qs = qs.filter(**{f"{orm_field}__gte": value})
            elif op in ("less_than_or_equal", "lte"):
                qs = qs.filter(**{f"{orm_field}__lte": value})
            elif op == "between":
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    qs = qs.filter(**{f"{orm_field}__range": (value[0], value[1])})
                else:
                    unsupported.append(col)
            elif op in ("not_between", "not_in_between"):
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
            qs, unsupported = _apply_call_execution_filters(qs, remaining)
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

    total_matching = qs.count()
    ids = list(qs.values_list("id", flat=True)[:cap])
    truncated = total_matching > cap

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
