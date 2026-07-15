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

CH 25.3 migration status (KEEP-PG transitional bridge):

  All ``ObservationSpan.objects`` reads in this module remain PG-bound
  because the surrounding FilterEngine pipeline is built on Django Q
  objects + Subquery/OuterRef expressions that are FUSED into the outer
  Trace/Session queryset's ``.annotate(...)``. The subquery results
  participate directly in Django's SQL generation (``Case(When(Exists(...
  )))`` branches, ``Subquery(...)`` annotations); a ``dict[trace_id,
  CHSpan]`` returned by a reader cannot be spliced into the SQL graph
  without first lifting the whole queryset out of Django. That bridge
  refactor (FilterEngine → CH-aware filter compiler) is cross-cutting
  and out of scope for the helpers chunk.

  Hot-path production traffic already routes through ClickHouse via
  ``_resolve_trace_ids_clickhouse`` and ``_resolve_voice_call_ids_clickhouse``
  using the ``query_builders`` package and the ``ClickHouseFilterBuilder``
  translator. Trace, voice and session filter-mode all dispatch to CH
  unconditionally: a payload with no explicit time filter widens to an
  all-history window (``_has_explicit_time_filter`` → wide-open ``start_time``
  bound) instead of routing to PG, so automation rules and "select all
  matching" resolves stay on the fast columnar backend. The PG paths below
  are the CH-outage fallback only, plus span filter-mode, which has no CH
  dispatch at this layer yet.

  Reader-extension proposals (status updated wave-3):

    list_root_spans_by_trace_ids(trace_ids, *, observation_type=None)
        -> dict[trace_id, CHSpan]
        STATUS: LANDED in wave-3 (commit 93c5c415f). Still cannot replace
        the root_span_qs Subquery in ``_build_trace_base_queryset`` /
        ``_apply_voice_call_constraints`` because the Subquery is
        consumed inside the outer Trace queryset's ``.annotate(node_type
        =Case(When(Exists(...))))``. A FilterEngine-CH bridge would
        first need to materialise the outer Trace rows out of Django so
        the per-trace root-span lookup can hydrate them in Python.

    aggregate_by_session_ids(session_ids, *, project_id=None)
        -> dict[sid, {span_count, traces_count, tokens, cost,
                      start_time, end_time}]
        STATUS: LANDED in wave-3 (commit 93c5c415f). Still cannot
        replace the session-aggregate at lines 1117-1158 for the same
        FilterEngine-fusion reason: the GROUP BY result is annotated
        onto a TraceSession queryset and FilterEngine's score-based
        branches read those annotations as Django expressions.

    list_spans_by_project_with_filters(project_id, *, filters,
                                       annotation_label_ids,
                                       organization, cap, exclude_ids)
        -> list[span_id]
        STATUS: NOT YET LANDED. End-to-end CH dispatch for
        ``resolve_filtered_span_ids`` mirroring the existing
        ``TraceListQueryBuilder`` shape. Largest gap (no CH dispatch
        for span filter-mode today) and highest-impact future
        extension.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import structlog
from django.db import models
from django.db.models import (
    Avg,
    Case,
    CharField,
    Count,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    JSONField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, JSONObject, Round

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from simulate.models.test_execution import CallExecution
from simulate.utils.persona_filtering import (
    UnsupportedPersonaFilter,
    apply_persona_filter,
    is_persona_filter_column,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.annotations import build_annotation_subqueries
from tracer.utils.filters import (
    FilterEngine,
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


_USER_SCOPED_COLUMN_IDS = {"my_annotations", "annotator"}


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


def _all_history_time_filter() -> dict:
    """A wide-open ``start_time`` bound that cancels the CH list builders'
    default now-30d narrowing so a filter resolves across ALL history.

    Start is 1971-01-01, NOT the 1970-01-01 epoch: the trace and voice list
    builders add ``created_at >= start_date - INTERVAL 1 DAY`` for partition
    pruning, and CH ``DateTime`` is a 32-bit epoch — subtracting a day from
    1970-01-01 UNDERFLOWS to ~2106, and the clause then matches nothing (an
    automation rule would resolve zero rows). A year of margin keeps the lower
    bound safely past the epoch while still covering every real timestamp. The
    session builder has no ``- INTERVAL 1 DAY`` clause and is unaffected.
    """
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["1971-01-01T00:00:00", "2099-12-31T23:59:59"],
        },
    }


def _filter_col_type(filter_item: dict) -> str:
    return _filter_config(filter_item).get("col_type") or ""


def _needs_eval_metric_annotations(filters) -> bool:
    return any(_filter_col_type(f) == "EVAL_METRIC" for f in filters or [])


def _needs_annotation_field_annotations(filters) -> bool:
    return any(_filter_col_type(f) == "ANNOTATION" for f in filters or [])


def _annotate_eval_metrics(qs, *, project_id, organization, source_type: str):
    """Mirror Observe PG list views' dynamic ``metric_<eval_id>`` annotations.

    FilterEngine evaluates eval metric filters against JSON annotations named
    ``metric_<custom_eval_config_id>`` with a nested ``score`` key. The grid
    builds those annotations before filtering; queue filter-mode needs the
    same shape so "select all matching filters" resolves the same rows.
    """
    if source_type == "observation_span":
        eval_log_scope = EvalLogger.objects.filter(
            observation_span__project_id=project_id,
            observation_span__project__organization=organization,
        )
        outer_filter = {
            "observation_span_id": OuterRef("id"),
        }
    else:
        eval_log_scope = EvalLogger.objects.filter(
            trace__project_id=project_id,
            trace__project__organization=organization,
        )
        outer_filter = {
            "trace_id": OuterRef("id"),
        }

    eval_configs = CustomEvalConfig.objects.filter(
        id__in=eval_log_scope.values("custom_eval_config_id").distinct(),
        deleted=False,
    ).select_related("eval_template")

    for config in eval_configs:
        choices = (
            config.eval_template.choices
            if getattr(config, "eval_template", None) and config.eval_template.choices
            else None
        )
        metric_qs = (
            EvalLogger.objects.filter(
                **outer_filter,
                custom_eval_config_id=config.id,
            )
            .exclude(Q(output_str="ERROR") | Q(error=True))
            .values("custom_eval_config_id")
            .annotate(
                float_score=Round(Avg("output_float") * 100, 2),
                bool_score=Round(
                    Avg(
                        Case(
                            When(output_bool=True, then=100),
                            When(output_bool=False, then=0),
                            default=None,
                            output_field=FloatField(),
                        )
                    ),
                    2,
                ),
                str_list_score=JSONObject(
                    **{
                        f"{value}": JSONObject(
                            score=Round(
                                100.0
                                * Count(
                                    Case(
                                        When(output_str_list__contains=[value], then=1),
                                        default=None,
                                        output_field=IntegerField(),
                                    )
                                )
                                / Count("output_str_list"),
                                2,
                            )
                        )
                        for value in choices or []
                    }
                ),
            )
            .values("float_score", "bool_score", "str_list_score")[:1]
        )

        exists_qs = EvalLogger.objects.filter(
            **outer_filter,
            custom_eval_config_id=config.id,
        )
        qs = qs.annotate(
            **{
                f"metric_{config.id}": Case(
                    When(
                        Exists(exists_qs.filter(output_float__isnull=False)),
                        then=JSONObject(
                            score=Subquery(metric_qs.values("float_score"))
                        ),
                    ),
                    When(
                        Exists(exists_qs.filter(output_bool__isnull=False)),
                        then=JSONObject(score=Subquery(metric_qs.values("bool_score"))),
                    ),
                    When(
                        Exists(exists_qs.filter(output_str_list__isnull=False)),
                        then=Subquery(metric_qs.values("str_list_score")),
                    ),
                    default=None,
                    output_field=JSONField(),
                ),
            }
        )
    return qs


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


def _trace_project_workspace_filter(workspace):
    if getattr(workspace, "is_default", False):
        return Q(project__workspace=workspace) | Q(project__workspace__isnull=True)
    return Q(project__workspace=workspace)


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

    # CH25-TODO(wave-3): ``list_root_spans_by_trace_ids`` now exists
    # (commit 93c5c415f) and could supply the per-trace root span
    # data, but it cannot replace the Subquery+OuterRef pattern here:
    # ``root_span_qs`` is FUSED into the outer Trace queryset's
    # ``.annotate(node_type=Case(When(Exists(root_span_qs)...)))``
    # — Django's SQL generator inlines the subquery into the SELECT.
    # A ``dict[tid, CHSpan]`` from the reader can't be spliced into
    # that SQL graph; replacing this requires lifting the entire outer
    # Trace queryset out of Django (i.e. migrating FilterEngine to a
    # CH-aware filter builder, which the v2 ``query_builders`` package
    # already does for the hot path — see ``_resolve_trace_ids_clickhouse``).
    # PG fallback only; production traffic uses the CH path.
    root_span_qs = ObservationSpan.objects.filter(
        trace_id=OuterRef("id"), parent_span_id__isnull=True
    )
    all_span_qs = ObservationSpan.objects.filter(trace_id=OuterRef("id"))
    qs = Trace.objects.filter(project_id=project.id).annotate(
        node_type=Case(
            When(
                Exists(root_span_qs),
                then=Subquery(root_span_qs.values("observation_type")[:1]),
            ),
            default=Value("unknown"),
            output_field=CharField(),
        ),
        trace_name=Case(
            When(
                Exists(root_span_qs),
                then=Subquery(root_span_qs.values("name")[:1]),
            ),
            default=Value("[ Incomplete Trace ]"),
            output_field=CharField(),
        ),
        latency=Subquery(root_span_qs.values("latency_ms")[:1]),
        total_tokens=Coalesce(
            Subquery(
                all_span_qs.values("trace_id")
                .annotate(total=Sum("total_tokens"))
                .values("total")[:1]
            ),
            0,
            output_field=IntegerField(),
        ),
        total_cost=Coalesce(
            Subquery(
                all_span_qs.values("trace_id")
                .annotate(total=Sum("cost"))
                .values("total")[:1]
            ),
            0.0,
            output_field=FloatField(),
        ),
        trace_id=F("id"),
        # Pull span_attributes off the root span. Old rows only have
        # eval_attributes populated — Coalesce falls back to keep parity
        # with the list views.
        span_attributes=Subquery(
            root_span_qs.annotate(
                _attrs=Coalesce("span_attributes", "eval_attributes")
            ).values("_attrs")[:1]
        ),
        # CH25-TODO: end-user lookup via Subquery+order_by — would need
        # a reader method like
        #   first_end_user_id_by_trace_ids(trace_ids) -> dict[tid, uid]
        # to push the per-trace MIN(start_time) selection into CH. Tied
        # to the broader FilterEngine migration; PG fallback only.
        user_id=Subquery(
            ObservationSpan.objects.filter(
                trace_id=OuterRef("id"), end_user__isnull=False
            )
            .order_by("start_time")
            .values("end_user__user_id")[:1]
        ),
        start_time=Coalesce(
            Subquery(root_span_qs.order_by("start_time").values("start_time")[:1]),
            "created_at",
        ),
        status=Case(
            When(Exists(root_span_qs.filter(status="ERROR")), then=Value("ERROR")),
            When(Exists(root_span_qs.filter(status="OK")), then=Value("OK")),
            default=Value("UNSET"),
            output_field=CharField(),
        ),
    )

    if workspace is not None:
        qs = qs.filter(_trace_project_workspace_filter(workspace))

    return qs


def _apply_voice_call_constraints(
    qs, filters: list[dict], *, remove_simulation_calls: bool = False
):
    """Narrow a Trace queryset to match ``list_voice_calls``'s result set.

    Simulator/voice projects render the grid via ``list_voice_calls`` which
    constrains to traces whose root span is a conversation, applies voice
    system metrics (agent latency, turn count, etc.), and optionally hides
    the VAPI simulator calls. The filter-mode resolver mirrored only
    ``list_traces_of_session``, so for voice projects it returned a
    superset — grid shows N, queue receives N + non-conversation traces.
    This helper brings parity with the voice list view.
    """
    # CH25-TODO(wave-3): ``list_root_spans_by_trace_ids(trace_ids,
    # observation_type='conversation')`` now exists (commit 93c5c415f)
    # and returns the per-trace conversation root. Still blocked by
    # the same FilterEngine-fusion gap as _build_trace_base_queryset:
    # ``has_conversation_root`` is annotated onto the outer Trace
    # queryset via ``Exists(root_span_qs.filter(...))`` so the
    # ``.filter(has_conversation_root=True)`` line consumes it as a
    # Django expression. Replacing requires lifting the queryset out
    # of Django. Production traffic uses the CH dispatch path
    # ``_resolve_voice_call_ids_clickhouse`` via
    # ``VoiceCallListQueryBuilder``; this is the PG fallback.
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
    annotation_label_ids: list[str] | None = None,
):
    """Apply the same FilterEngine branches as ``list_traces_of_session``.

    Mirrors ``tracer.views.trace.ObservationTraceViewSet.list_traces_of_session``
    lines 1668-1742. Any drift here is a bug — see parity tests.

    CH25-TODO: FilterEngine is the Q-object filter compiler this function
    wraps. Routing the entire branch through CH would require a CH-aware
    FilterEngine variant — the v2 ``ClickHouseFilterBuilder`` already
    handles this for SPAN_ATTRIBUTE filters in the CH dispatch path above,
    but a full migration of FilterEngine is out of scope. PG fallback only.
    """
    if not filters:
        return base_qs

    if annotation_label_ids is None:
        annotation_label_ids = list(
            AnnotationsLabels.objects.filter(
                organization=organization, deleted=False
            ).values_list("id", flat=True)
        )

    combined = Q()
    qs = base_qs

    # 1. System metrics
    system_conds = FilterEngine.get_filter_conditions_for_system_metrics(filters)
    if system_conds:
        combined &= system_conds

    # 2. Separate annotation filters from eval filters (must precede #3 and #4)
    annotation_col_types = {"ANNOTATION"}
    annotation_column_ids = {"my_annotations", "annotator"}
    non_annotation = [
        f
        for f in filters
        if _filter_col_type(f) not in annotation_col_types
        and _filter_column_id(f) not in annotation_column_ids
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
        annotation_label_ids=[str(label_id) for label_id in annotation_label_ids],
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
        from tracer.services.clickhouse.query_builders.filters import (
            FilterTranslationError,
        )
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )
    except ImportError:
        return None

    analytics = AnalyticsQueryService()
    builder = VoiceCallListQueryBuilder(
        project_id=str(project_id),
        page_number=0,
        page_size=cap,
        filters=filters or [],
        annotation_label_ids=annotation_label_ids,
        remove_simulation_calls=remove_simulation_calls,
        # See _resolve_trace_ids_clickhouse: an untranslatable filter fails loud
        # so the resolve falls back to PG instead of silently over-matching.
        strict_filters=True,
    )
    # Skip the separate `uniqExact(trace_id)` count query — on large filter
    # results it was the dominant /preview timeout. ``build()`` already adds
    # ``LIMIT cap + 1`` (voice_call_list.py:97), so the cap+1 sentinel gives
    # us "≥ cap" without a second scan.
    try:
        ids_query, ids_params = builder.build()
    except FilterTranslationError as exc:
        # Untranslatable filter → PG fallback (full operator coverage), not an
        # over-matched CH set. Expected, not an outage — log at info.
        logger.info(
            "bulk_selection_resolve_voice_ch_untranslatable_filter",
            project_id=str(project_id),
            error=str(exc),
        )
        return None
    ids_result = analytics.execute_ch_query(ids_query, ids_params, timeout_ms=15_000)
    ids = [str(r.get("trace_id", "")) for r in ids_result.data if r.get("trace_id")]
    raw_truncated = len(ids) > cap

    # VoiceCallListQueryBuilder's SQL simulation filter is a no-op (the
    # phone numbers live in the heavy span_attributes_raw blob). The list
    # view filters in Python after Phase 1b; we do the same here when the
    # toggle is on.
    if remove_simulation_calls and ids:
        ids = _filter_out_simulator_calls_ch(ids, project_id, analytics)

    if exclude_ids:
        excl = {str(i) for i in exclude_ids}
        ids = [i for i in ids if i not in excl]

    # Preserve the cap+1 sentinel from before exclusion. If an excluded row
    # occupied the sentinel slot there may still be more non-excluded rows
    # just beyond the fetched window, so do not under-report truncation.
    truncated = raw_truncated or len(ids) > cap
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
    params = {
        "project_id": str(project_id),
        "trace_ids": tuple(str(t) for t in trace_ids),
    }
    result = analytics.execute_ch_query(query, params, timeout_ms=15_000)
    sim_trace_ids = set()
    for row in result.data:
        raw = row.get("span_attributes_raw") or "{}"
        try:
            attrs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (_json.JSONDecodeError, TypeError):
            attrs = {}
        if VoiceCallListQueryBuilder.is_simulator_call(
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
        from tracer.services.clickhouse.query_builders.filters import (
            FilterTranslationError,
        )
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )
    except ImportError:
        return None

    analytics = AnalyticsQueryService()
    builder = TraceListQueryBuilder(
        project_id=str(project_id),
        page_number=0,
        page_size=cap,
        filters=filters or [],
        annotation_label_ids=annotation_label_ids,
        # Phase 1 light columns are all we need — we only want trace_id.
        columns=["trace_id"],
        # A rule resolve must add exactly the filtered set: if a filter can't be
        # translated to CH it would silently drop and over-match, so fail loud and
        # let the caller fall back to the PG FilterEngine (full operator coverage).
        strict_filters=True,
    )
    # Skip the separate count query — the builder already does ``LIMIT cap + 1``
    # (trace_list.py:134) so the cap+1 sentinel tells us "≥ cap" without a
    # second uniqExact scan that was the dominant /preview timeout source.
    try:
        ids_query, ids_params = builder.build()
    except FilterTranslationError as exc:
        # A supported-looking filter can't be translated to CH → return None so
        # the caller uses the PG FilterEngine (which covers it) rather than an
        # over-matched CH set. Expected, not an outage — log at info.
        logger.info(
            "bulk_selection_resolve_trace_ch_untranslatable_filter",
            project_id=str(project_id),
            error=str(exc),
        )
        return None
    ids_result = analytics.execute_ch_query(ids_query, ids_params, timeout_ms=15_000)
    ids = [str(r.get("trace_id", "")) for r in ids_result.data if r.get("trace_id")]
    raw_truncated = len(ids) > cap

    if exclude_ids:
        excl = {str(i) for i in exclude_ids}
        ids = [i for i in ids if i not in excl]

    # Preserve the cap+1 sentinel from before exclusion. If an excluded row
    # occupied the sentinel slot there may still be more non-excluded rows
    # just beyond the fetched window, so do not under-report truncation.
    truncated = raw_truncated or len(ids) > cap
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
    if not _project_matches_workspace(project, workspace):
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    # Dispatch to ClickHouse when available so filter semantics
    # (especially SPAN_ATTRIBUTE filters translated through
    # ClickHouseFilterBuilder) match the grid exactly. Both grid paths
    # (regular traces + voice calls) are CH-first in production, and
    # PG/CH diverge on JSON span_attribute semantics — the PG fallback
    # was matching the full project instead of the filtered subset.
    annotation_labels = get_annotation_labels_for_project(project.id, organization)
    annotation_label_ids = [str(lbl.id) for lbl in annotation_labels]

    # CH is the primary path for both grids (regular + voice); PG is the
    # outage fallback only. The CH list builders default to a now-30d window
    # when the payload sends no time bound (a dashboard-perf default in
    # parse_time_range), which would silently drop older rows a "select all
    # matching" / automation-rule resolve must include. So when there is no
    # explicit time filter, widen the window to all-history — this preserves
    # the PG path's full-history semantics while keeping the resolve on the
    # fast columnar backend. An explicit user time filter prunes normally.
    # Mirrors _resolve_session_ids_clickhouse.
    ch_filters = list(filters or [])
    if not _has_explicit_time_filter(filters):
        ch_filters.append(_all_history_time_filter())

    # A reachable-but-failing CH (timeout/transient) must not 500 the add:
    # swallow to None so the PG fallback below runs (fail-open, logged).
    try:
        if is_voice_call:
            ch_result = _resolve_voice_call_ids_clickhouse(
                project_id=project_id,
                filters=ch_filters,
                exclude_ids=set(exclude_ids or ()),
                cap=cap,
                remove_simulation_calls=remove_simulation_calls,
                annotation_label_ids=annotation_label_ids,
            )
        else:
            ch_result = _resolve_trace_ids_clickhouse(
                project_id=project_id,
                filters=ch_filters,
                exclude_ids=set(exclude_ids or ()),
                cap=cap,
                annotation_label_ids=annotation_label_ids,
            )
    except Exception:
        logger.exception(
            "bulk_selection_resolve_trace_ch_query_failed",
            project_id=str(project_id),
        )
        ch_result = None
    if ch_result is not None and ch_result.total_matching > 0:
        return ch_result
    if ch_result is not None:
        # CH returned zero — treat as a possible CH gap (e.g. replication lag)
        # and confirm on PG rather than trust the empty. A rule whose filter
        # genuinely matches nothing therefore still pays the PG cost on every
        # run (manual AND scheduled). Distinguishing "project has no CH rows"
        # (gap → PG justified) from "CH has rows, filter matched 0" (trust the 0)
        # with a cheap existence probe is a tracked follow-up.
        logger.info(
            "bulk_selection_resolve_trace_ch_empty_pg_fallback",
            project_id=str(project_id),
        )

    base = _build_trace_base_queryset(project_id, organization, workspace)
    if _needs_eval_metric_annotations(filters or []):
        base = _annotate_eval_metrics(
            base,
            project_id=project.id,
            organization=organization,
            source_type="trace",
        )
    if _needs_annotation_field_annotations(filters or []):
        base = build_annotation_subqueries(base, annotation_labels, organization)
    qs = _apply_trace_filters(
        base,
        filters or [],
        user=user,
        organization=organization,
        annotation_label_ids=annotation_label_ids,
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
    #
    # CH25-TODO: equivalent to `per_trace_root_span_start_times(trace_ids)`
    # which DOES exist in CHSpanReader, but only after the candidate trace
    # ids are known — at this point the Trace queryset hasn't been
    # materialised yet so a CH lookup would be N+1 unless we reorder the
    # whole pipeline. Defer until we restructure resolve_filtered_trace_ids
    # to materialise trace_ids first, then sort via the CH reader.
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

    # Capped fetch — one LIMIT cap+1 SELECT instead of COUNT(*) + SELECT.
    # The exact total_matching on huge results was the primary /preview timeout
    # source (full-table scan on 10M+ row trace tables); the caller only needs
    # "≥ cap" to decide truncation.
    capped = list(qs.values_list("id", flat=True)[: cap + 1])
    truncated = len(capped) > cap
    ids = capped[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

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

    CH25-TODO: this is the largest reader-surface gap for the file. The
    span filter-mode bulk selection has NO ClickHouse dispatch at all
    today (compare to trace filter-mode which has
    `_resolve_trace_ids_clickhouse`). The proposed reader extension is
        list_spans_by_project_with_filters(project_id, *, filters,
            annotation_label_ids, organization, cap, exclude_ids)
            -> list[span_id]
    mirroring `TraceListQueryBuilder` for spans. Until that lands the PG
    base queryset stays; FilterEngine + Subquery patterns below rely on
    ORM joins (e.g. `trace__name`, `end_user__user_id`) that have CH
    equivalents only via denormalised reads + Python join.
    """
    project = Project.objects.get(id=project_id, organization=organization)

    qs = ObservationSpan.objects.filter(
        project_id=project.id,
        project__organization=organization,
        deleted=False,
    ).annotate(
        node_type=F("observation_type"),
        span_id=F("id"),
        span_name=F("name"),
        trace_name=F("trace__name"),
        user_id=F("end_user__user_id"),
    )

    if workspace is not None:
        qs = qs.filter(_trace_project_workspace_filter(workspace))

    return qs


_SPAN_FIELD_MAP = {
    "latency_ms": "latency_ms",
    "latency": "latency_ms",
    "avg_latency": "latency_ms",
    "cost": "cost",
    "avg_cost": "cost",
    "total_tokens": "total_tokens",
    "tokens": "total_tokens",
    "input_tokens": "prompt_tokens",
    "prompt_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
    "completion_tokens": "completion_tokens",
    "node_type": "node_type",
    "trace_id": "trace_id",
    "span_id": "id",
    "created_at": "created_at",
    "name": "name",
    "span_name": "span_name",
    "trace_name": "trace_name",
    "user_id": "user_id",
    "status": "status",
    "start_time": "start_time",
}


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
    system_conds = FilterEngine.get_filter_conditions_for_system_metrics(
        filters,
        field_map=_SPAN_FIELD_MAP,
    )
    if system_conds:
        combined &= system_conds

    # 2. Split annotation filters from eval filters
    annotation_col_types = {"ANNOTATION"}
    annotation_column_ids = {"my_annotations", "annotator"}
    non_annotation = [
        f
        for f in filters
        if _filter_col_type(f) not in annotation_col_types
        and _filter_column_id(f) not in annotation_column_ids
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

    project = Project.objects.get(id=project_id, organization=organization)
    annotation_labels = get_annotation_labels_for_project(project.id, organization)

    base = _build_span_base_queryset(project_id, organization, workspace)
    if _needs_eval_metric_annotations(filters or []):
        base = _annotate_eval_metrics(
            base,
            project_id=project.id,
            organization=organization,
            source_type="observation_span",
        )
    if _needs_annotation_field_annotations(filters or []):
        base = build_annotation_subqueries(
            base,
            annotation_labels,
            organization,
            span_filter_kwargs={"observation_span_id": OuterRef("id")},
        )
    qs = _apply_span_filters(base, filters or [], user=user, organization=organization)

    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    # ObservationSpan has real start_time / id columns — order directly.
    qs = qs.order_by("-start_time", "-id")

    # See resolve_filtered_trace_ids — cap+1 fetch instead of COUNT(*).
    capped = list(qs.values_list("id", flat=True)[: cap + 1])
    truncated = len(capped) > cap
    ids = capped[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

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
    if project.source == ProjectSourceChoices.SIMULATOR.value:
        return TraceSession.objects.none()

    qs = TraceSession.objects.filter(project_id=project.id)
    if workspace is not None:
        qs = qs.filter(_trace_project_workspace_filter(workspace))
    return qs


def _apply_session_filters(base_sessions_qs, filters, *, project_id, organization):
    """Apply the full session filter pipeline and return a session-id-valued
    aggregated queryset.

    Mirrors ``list_sessions`` lines 922-1157 (non-ClickHouse PG path)
    excluding pagination and sort ordering. Returns a queryset yielding
    dicts with a ``trace__session_id`` key.

    CH25-TODO(wave-3): ``aggregate_by_session_ids(session_ids,
    project_id=)`` LANDED in commit 93c5c415f. Returns dict[sid,
    {span_count, traces_count, tokens, cost, start_time, end_time}].
    Still blocked by FilterEngine fusion: the ``aggregated`` queryset
    below is consumed by the FilterEngine score-based filter branches
    (lines 1189-1222) as a Django queryset with the aggregate columns
    available as fields. Replacing it would require lifting the
    score-based filtering out of Django too. The ``first_message`` /
    ``last_message`` Subqueries (lines 1258-1280 below) are not
    covered by the wave-3 reader either — those would need a
    ``first_last_messages_by_session_ids`` extension. KEEP-PG until the
    FilterEngine bridge lands.
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
        _filter_column_id(f) in needs_first_last_cols for f in remaining_filters
    )

    pre_agg_q = FilterEngine.get_filter_conditions_for_system_metrics(
        [
            f
            for f in remaining_filters
            if _filter_column_id(f) in _SESSION_PRE_AGG_FIELDS
        ],
        field_map=_SESSION_PRE_AGG_FIELDS,
    )
    remaining_filters = [
        f
        for f in remaining_filters
        if _filter_column_id(f) not in _SESSION_PRE_AGG_FIELDS
    ]

    aggregated = (
        ObservationSpan.objects.filter(pre_agg_q, trace__session_id__in=session_ids)
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
        col_id = _filter_column_id(f)
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
        col_id = _filter_column_id(sf)
        fc = _filter_config(sf)
        filter_op = fc.get("filter_op") or "equals"
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
            if filter_op == "equals":
                score_q = base_score_q.filter(value=filter_val)
                aggregated = aggregated.filter(Exists(score_q))
            elif filter_op == "not_equals":
                score_q = base_score_q.filter(value=filter_val)
                aggregated = aggregated.exclude(Exists(score_q))
            elif filter_op == "in" and isinstance(filter_val, list):
                aggregated = aggregated.filter(
                    Exists(base_score_q.filter(value__in=filter_val))
                )
            elif filter_op == "not_in" and isinstance(filter_val, list):
                aggregated = aggregated.exclude(
                    Exists(base_score_q.filter(value__in=filter_val))
                )
            elif filter_op == "contains":
                score_q = base_score_q.filter(value__icontains=filter_val)
                aggregated = aggregated.filter(Exists(score_q))
            else:
                aggregated = aggregated.filter(Exists(base_score_q))

    return aggregated


def _session_score_label_ids(project_id) -> set[str]:
    """Project-scoped annotation-label ids — the discriminator that splits a
    score-based session filter (``col_id`` is a label id) from a system-metric
    one. Mirrors ``_apply_session_filters`` / ``list_sessions`` exactly."""
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
    """Intersect a CH-derived candidate session-id list with PG ``Score``-based
    filters, preserving input order.

    Reproduces ``_apply_session_filters``'s score branches (lines 1298-1332)
    exactly, but as an explicit-id membership check rather than an ``OuterRef``
    Subquery — so it composes with the CH base set. ``Score`` keys session
    annotations by the soft ``trace_session_id`` string, so a NET-NEW session's
    scores are reachable here WITHOUT a PG ``trace_session`` row (the whole
    point of the cutover). Each successive filter narrows the surviving set.
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
) -> ResolveResult | None:
    """Re-derive the filter-matched session-id set from ClickHouse.

    P3b step2 precondition (PG_ORM_READ_MIGRATION, Slice F): the PG base
    derivation (``_build_session_base_queryset`` →
    ``TraceSession.objects.filter(project_id=…)``) goes STALE post-flip — a
    NET-NEW session (first seen after the ingest ``get_or_create`` is dropped)
    has NO ``trace_session`` row, so a "select all sessions matching this
    filter" bulk-add SILENTLY OMITTED it. The CH ``spans``-derived session list
    (the SAME ``SessionListQueryBuilder`` the live grid uses) includes it, and
    is remap-aware so a cross-cutover straddler's old + new session ids unify to
    ONE survivor row (counted once).

    Non-score filters (time / span-attribute / aggregate-metric / session-id /
    user_id) are translated by the builder. Score-label filters are applied in
    PG afterward (``_apply_session_score_filters_pg``) — the builder/CH
    ``spans`` path cannot host a session-level ``Score`` predicate (the CH
    annotation subquery matches by ``trace_id``/span ``id``, never
    ``trace_session_id``), so this preserves the PG path's score semantics while
    staying net-new-correct. Returns ``None`` when CH is unavailable so the
    caller can fall back to the PG aggregate path.
    """
    try:
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.dispatch import get_query_builder_class
    except ImportError:
        return None

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
    if not _has_explicit_time_filter(non_score_filters):
        ch_filters.append(
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        "1970-01-01T00:00:00",
                        "2099-12-31T23:59:59",
                    ],
                },
            }
        )

    analytics = AnalyticsQueryService()
    # page_size=cap → the builder's LIMIT cap+1 gives the truncation sentinel
    # without a separate COUNT scan (same trick as the voice/ trace CH paths).
    # When score filters are present we must over-fetch so the post-PG-intersect
    # set can still reach the cap — fetch the full page (no extra +k heuristic;
    # the cap is already the hard ceiling and truncation is reported honestly).
    builder = BuilderCls(
        project_id=str(project_id),
        page_number=0,
        page_size=cap,
        filters=ch_filters,
        sort_params=[],
    )
    try:
        query, params = builder.build()
        result = analytics.execute_ch_query(query, params, timeout_ms=15_000)
    except Exception:
        # CH reachable-but-failing (transient/timeout) → fall back to the PG
        # aggregate, mirroring the live grid's `except Exception` at
        # trace_session.py. Returning None lets the caller take the PG path
        # rather than 500 the bulk-add.
        logger.exception(
            "bulk_selection_resolve_session_ch_query_failed",
            project_id=str(project_id),
        )
        return None
    ids = [
        str(row.get("session_id", "")) for row in result.data if row.get("session_id")
    ]
    raw_truncated = len(ids) > cap

    if score_filters:
        ids = _apply_session_score_filters_pg(ids, score_filters)

    if exclude_ids:
        excl = {str(i) for i in exclude_ids}
        ids = [i for i in ids if i not in excl]

    truncated = raw_truncated or len(ids) > cap
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


def _resolve_filtered_session_ids_pg(
    *,
    project_id,
    filters: list[dict],
    exclude_ids: Iterable | None,
    organization,
    workspace,
    cap: int,
) -> ResolveResult:
    """The legacy PG aggregate derivation — CH-unavailable fallback ONLY.

    STALE for post-flip NET-NEW sessions (no ``trace_session`` row), so it is no
    longer the primary path; ``_resolve_session_ids_clickhouse`` is. Kept as the
    CH-outage fallback (consistent with the other bulk resolvers' PG fallbacks).
    """
    base = _build_session_base_queryset(project_id, organization, workspace)
    aggregated = _apply_session_filters(
        base, filters or [], project_id=project_id, organization=organization
    )

    if exclude_ids:
        aggregated = aggregated.exclude(
            trace__session_id__in=[str(i) for i in exclude_ids]
        )

    aggregated = aggregated.order_by("-start_time", "-trace__session_id")

    # See resolve_filtered_trace_ids — cap+1 fetch instead of COUNT(*).
    capped = [
        row["trace__session_id"]
        for row in aggregated.values("trace__session_id")[: cap + 1]
    ]
    truncated = len(capped) > cap
    ids = capped[:cap]
    total_matching = len(ids) + (1 if truncated else 0)

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
    filters are applied in PG against ``Score`` (net-new-correct via the soft
    ``trace_session_id``); everything else is translated by the CH builder. The
    PG aggregate path remains only as the CH-outage fallback.

    Raises:
        Project.DoesNotExist: if the project is not in the org.
        ValueError: if filters reference user-scoped columns but user is None.
    """
    _validate_user_scoped_filters(filters or [], user)

    # Resolve + scope-check the project up front (the CH builder keys spans by
    # project_id but does NOT enforce org membership or the SIMULATOR carve-out).
    # Raising Project.DoesNotExist here preserves the caller's 404 mapping.
    project = Project.objects.get(id=project_id, organization=organization)
    if project.source == ProjectSourceChoices.SIMULATOR.value:
        return ResolveResult(ids=[], total_matching=0, truncated=False)
    if workspace is not None and project.workspace_id != getattr(
        workspace, "id", workspace
    ):
        # Workspace mismatch — the PG base queryset would have filtered to empty.
        return ResolveResult(ids=[], total_matching=0, truncated=False)

    score_label_ids = _session_score_label_ids(project_id)
    non_score_filters, score_filters = _split_session_score_filters(
        filters or [], score_label_ids
    )

    ch_result = _resolve_session_ids_clickhouse(
        project_id=project_id,
        non_score_filters=non_score_filters,
        score_filters=score_filters,
        exclude_ids=set(exclude_ids or set()),
        organization=organization,
        cap=cap,
    )
    if ch_result is not None and ch_result.total_matching > 0:
        return ch_result

    if ch_result is None:
        logger.warning(
            "bulk_selection_resolve_session_ch_unavailable_pg_fallback",
            project_id=str(project_id),
        )
    else:
        logger.info(
            "bulk_selection_resolve_session_ch_empty_pg_fallback",
            project_id=str(project_id),
        )

    pg_result = _resolve_filtered_session_ids_pg(
        project_id=project_id,
        filters=filters or [],
        exclude_ids=exclude_ids,
        organization=organization,
        workspace=workspace,
        cap=cap,
    )
    if pg_result.total_matching > 0 or ch_result is None:
        return pg_result
    return ch_result


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
