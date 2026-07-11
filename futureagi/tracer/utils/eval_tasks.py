import os
from random import sample

import structlog
from django.core.cache import cache
from django.db import transaction
from django.db.models import OuterRef, Q
from django.utils import timezone

from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from tfc.temporal import temporal_activity
from tracer.models.eval_task import (
    EvalTask,
    EvalTaskLogger,
    EvalTaskStatus,
    RowType,
    RunType,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.utils.eval import (
    evaluate_observation_span_observe,
    evaluate_trace_observe,
    evaluate_trace_session_observe,
)
from tracer.utils.annotations import build_annotation_subqueries
from tracer.utils.filters import FilterEngine

logger = structlog.get_logger(__name__)
from tracer.utils.helper import get_annotation_labels_for_project

# Cron-side drain window — once the dispatcher has fired every
# per-span activity, the task stays in RUNNING until either the
# EvalLogger row count catches up to the expected total or no new row
# has landed for this many seconds. At that point the cron flips to
# COMPLETED and records a summary failure in ``failed_spans`` so the
# UI can surface which spans never produced a result.
#
# The threshold has to survive three stacking delays before we declare
# the drain dead: activity retries (``max_retries=3`` × ``retry_delay=60
# s`` ≈ 3 min per flaky span), Temporal task-queue backpressure when
# the worker pool is saturated (can pause a queue for many minutes
# under load), and per-activity LLM latency (multimodal audio evals
# regularly take 30–60 s each). 10 min was too tight — slow-but-live
# drains were being mis-flagged as stalled and truncated prematurely.
# 30 min is a safer default; deployments that want tighter cycles can
# override via ``EVAL_TASK_DRAIN_STALL_SECONDS``.
_DRAIN_STALL_SECONDS = int(os.environ.get("EVAL_TASK_DRAIN_STALL_SECONDS", "1800"))


def compute_drain_state(eval_task, eval_task_logger=None):
    """Summarise a historical eval task's drain state.

    Returns a dict the cron uses to decide COMPLETED vs RUNNING and
    the serializer uses to expose progress to the UI. Keeping a
    single source of truth means the "what counts as done" rule lives
    in one place and can't drift between backend decisions and
    user-facing progress displays.

    Fields:
        dispatched  int  — per-span activities fired by the dispatcher
                           (``offset × num_evals``). ``None`` until the
                           logger exists.
        completed   int  — ``EvalLogger`` rows that actually landed.
        missing     int  — ``dispatched - completed`` (>= 0).
        latest_row_at  datetime | None  — newest row's ``created_at``,
                           used to detect stalls.
        is_fully_drained  bool  — ``completed >= dispatched`` (happy
                                  path; includes the trivial
                                  ``dispatched == 0`` case).
        is_stalled  bool  — drain hasn't produced a row for
                            ``_DRAIN_STALL_SECONDS``. Set only after
                            dispatch is done, so a task with slow but
                            steady drain isn't falsely flagged.
    Only meaningful for ``run_type = HISTORICAL``. Continuous tasks
    don't have a finite "dispatched" count and should not be passed
    to this helper.
    """
    if eval_task_logger is None:
        eval_task_logger = EvalTaskLogger.objects.filter(eval_task=eval_task).first()

    dispatched = (eval_task_logger.offset if eval_task_logger else 0) or 0
    eval_count = eval_task.evals.count() or 1
    expected = dispatched * eval_count

    logger_q = EvalLogger.objects.filter(eval_task_id=eval_task.id, deleted=False)
    completed = logger_q.count()
    latest_row_at = (
        logger_q.order_by("-created_at").values_list("created_at", flat=True).first()
    )

    is_fully_drained = completed >= expected

    # Stall check has to cover three situations:
    #   1. ``expected == 0`` — dispatcher hasn't run yet; not a stall.
    #   2. Rows landed then stopped — compare against ``latest_row_at``.
    #   3. No row ever landed but dispatch is done — all activities
    #      were silently dropped; compare against the logger's
    #      ``updated_at`` (which the dispatcher bumps each tick).
    #
    # The reference timestamp is the MOST RECENT progress signal we
    # have: ``max(latest_row_at, logger.updated_at)``. Using just
    # ``latest_row_at`` breaks reruns — the task carries old rows
    # from the previous run, so their stale ``created_at`` triggers
    # the stall check the instant the re-dispatcher touches the
    # logger. Taking the max means a fresh dispatch counts as
    # progress even when all historical rows are hours old.
    is_stalled = False
    if expected > 0:
        now = timezone.now()
        _logger_ref = eval_task_logger.updated_at if eval_task_logger else None
        candidates = [c for c in (latest_row_at, _logger_ref) if c is not None]
        stall_ref = max(candidates) if candidates else None
        if (
            stall_ref is not None
            and (now - stall_ref).total_seconds() > _DRAIN_STALL_SECONDS
        ):
            is_stalled = True

    return {
        "dispatched": expected,
        "completed": completed,
        "missing": max(expected - completed, 0),
        "latest_row_at": latest_row_at,
        "is_fully_drained": is_fully_drained,
        "is_stalled": is_stalled,
    }


def annotation_source_q_for_row_type(row_type: RowType | str) -> Q:
    """Q matching Score rows against a span-outer queryset, scoped per row_type.

    Mirrors each row_type's grid so the runner selects the same rows the
    user previewed. Voice/trace annotations are stored trace-scoped, so
    those bridge trace OR span; spans and sessions match their own scope.
    """
    row_type = RowType(row_type)
    if row_type in (RowType.TRACES, RowType.VOICE_CALLS):
        return Q(trace_id=OuterRef("trace_id")) | Q(
            observation_span__trace_id=OuterRef("trace_id")
        )
    if row_type == RowType.SESSIONS:
        return Q(trace_session_id=OuterRef("trace__session_id"))
    return Q(observation_span_id=OuterRef("id"))


def parsing_evaltask_filters(
    filters: dict, *, row_type: RowType | str
) -> tuple[Q, dict]:
    """Combine eval-task filter JSON into ``(Q, annotation_kwargs)``.

    Per-handler dispatch mirrors ``list_spans_observe``
    (``tracer/views/observation_span.py:1755-1826``): one flat ``filters``
    list, each item carrying ``col_type``, passed to every handler.

    ANNOTATION predicates are scoped per ``row_type`` (see
    ``annotation_source_q_for_row_type``); everything else stays span-outer.

    Callers must apply ``.annotate(**extra_annotations).filter(combined_q)``.
    Per-label ANNOTATION filters (e.g. ``columnId == "<label_uuid>"``)
    additionally require ``build_annotation_subqueries`` (with the same
    row_type source Q) to have been applied to the queryset first —
    ``process_eval_task`` does that; other callers must mirror it if they
    accept per-label filters.

    Accepts both ``filters`` and legacy ``span_attributes_filters`` keys.
    """
    annotation_source_q = annotation_source_q_for_row_type(row_type)
    combined_q = Q()
    extra_anns: dict = {}

    if filters is None:
        return combined_q, extra_anns

    for key, value in filters.items():
        if key in ("filters", "span_attributes_filters") and isinstance(value, list):
            if key == "span_attributes_filters":
                logger.error(
                    "Eval task still uses the legacy "
                    "`span_attributes_filters` key; re-save the task to "
                    "migrate it to the canonical `filters` key."
                )

            items = value
            if not items:
                continue

            q_sys = FilterEngine.get_filter_conditions_for_system_metrics(items)
            if q_sys:
                combined_q &= q_sys

            # ANNOTATION rows (incl. my_annotations/annotator pinned to
            # col_type=ANNOTATION) are skipped inside the handler at
            # filters.py:1386-1391 — no call-site pre-filter needed.
            q_eval = FilterEngine.get_filter_conditions_for_non_system_metrics(items)
            if q_eval:
                combined_q &= q_eval

            # user_id=None: background activity, so my_annotations is a no-op.
            q_anno, anno_anns = (
                FilterEngine.get_filter_conditions_for_voice_call_annotations(
                    items,
                    user_id=None,
                    source_q=annotation_source_q,
                )
            )
            if q_anno:
                combined_q &= q_anno
            if anno_anns:
                extra_anns.update(anno_anns)

            q_span = FilterEngine.get_filter_conditions_for_span_attributes(items)
            if q_span:
                combined_q &= q_span

            q_has_eval = FilterEngine.get_filter_conditions_for_has_eval(
                items, observe_type="span"
            )
            if q_has_eval:
                combined_q &= q_has_eval
            q_has_anno = FilterEngine.get_filter_conditions_for_has_annotation(
                items, source_q=annotation_source_q
            )
            if q_has_anno:
                combined_q &= q_has_anno

        elif key == "observation_type":
            if isinstance(value, list):
                combined_q &= Q(observation_type__in=list(value))
            elif isinstance(value, str):
                combined_q &= Q(observation_type=value)
            else:
                raise Exception(
                    "Invalid value for observation_type filter; expected list or string"
                )
        elif key == "session_id":
            traces = Trace.objects.filter(session_id=value).values_list("id", flat=True)
            combined_q &= Q(trace_id__in=list(traces))
        elif key == "date_range":
            if isinstance(value, list) and len(value) == 2:
                start_date, end_date = value
                combined_q &= Q(created_at__range=[start_date, end_date])
        elif key == "created_at":
            combined_q &= Q(created_at__gte=value)
        elif key == "project_id":
            combined_q &= Q(project_id=value)

    return combined_q, extra_anns


def parsing_monitor_filters(filters: dict) -> Q:
    """Legacy filter parser kept for monitor.py and monitor_graphs.py.

    Monitors use a simpler filter UI than eval tasks — only SPAN_ATTRIBUTE
    chips, no annotation/eval-metric/system-metric col_types. Keeping the
    old single-handler dispatch here avoids monitor pages paying for the
    annotate-subqueries cost or surfacing FieldErrors for filters they
    never carry.

    Eval task create/edit and the eval-task rerun count use the richer
    ``parsing_evaltask_filters`` (above), which returns ``(Q, dict)`` and
    routes by col_type.
    """
    combined_q = Q()

    if filters is None:
        return combined_q

    def _as_list(value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [item for item in value if item not in (None, "")]
        return [value]

    for key, value in filters.items():
        if (
            key == "span_attributes_filters"
            and value is not None
            and isinstance(value, list)
        ):
            q_span = FilterEngine.get_filter_conditions_for_span_attributes(value)
            if q_span and (q_span.children or hasattr(q_span, "connector")):
                combined_q &= q_span
        elif key == "observation_type":
            if isinstance(value, list):
                combined_q &= Q(observation_type__in=list(value))
            elif isinstance(value, str):
                combined_q &= Q(observation_type=value)
            else:
                raise Exception(
                    "Invalid value for observation_type filter; expected list or string"
                )
        elif key == "session_id":
            session_ids = _as_list(value)
            traces = Trace.objects.filter(session_id__in=session_ids).values_list(
                "id", flat=True
            )
            combined_q &= Q(trace_id__in=list(traces))
        elif key == "trace_id":
            trace_ids = _as_list(value)
            combined_q &= Q(trace_id__in=trace_ids)
        elif key == "span_id":
            span_ids = _as_list(value)
            combined_q &= Q(id__in=span_ids)
        elif key == "date_range":
            if isinstance(value, list) and len(value) == 2:
                start_date, end_date = value
                combined_q &= Q(created_at__range=[start_date, end_date])
        elif key == "created_at":
            combined_q &= Q(created_at__gte=value)
        elif key == "project_id":
            combined_q &= Q(project_id=value)

    return combined_q


def _derive_session_candidate_ids(eval_task) -> list[str]:
    """Re-derive the in-scope session candidate ids from ClickHouse (P3b step2,
    Slice C / DESIGN §5) — the CH replacement for the old PG ``Trace.objects
    .filter(span matches).exclude(session__isnull=True).values('session_id')``
    subquery.

    Post-flip the ``Trace.session`` FK is ``None`` (only spans carry the
    deterministic ``trace_session_id``), so the PG subquery silently omits every
    net-new session and a session-level eval would never run on it. CH derives
    the candidate set straight off the spans the filters match:

      • ``parsing_evaltask_filters_for_ch`` produces the same narrow filter
        kwargs the PG ``parsing_evaltask_filters`` Q encodes (the companion used
        by ``eval_task.py`` for the rerun count).
      • ``project_id`` is force-scoped to the eval_task's own tenant (the spans
        table is multi-tenant — defense-in-depth, exactly like
        ``eval_task.py``'s tenant gate), so the candidate set can't leak another
        tenant's sessions even if ``filters`` carry a different project_id.
      • ``distinct_session_ids_with_filters`` is remap-aware: a straddler's old +
        new id spans collapse to ONE survivor id (the session appears once,
        under its canonical/old id) and a net-new session's deterministic id has
        no remap row → it is included.

    ``span_attributes_filters`` are NOT translated by ``_for_ch`` (they need the
    v2 FilterEngine) and — unlike the trace/span paths — sessions have no PG
    fallback to widen to. We log when a task carries them so an over-inclusive
    candidate set (the filtered-out attribute subset is ignored) is visible
    rather than silent; the CH25-TODO mirrors ``eval_task.py:1554``.
    CH25-TODO(span_attributes_filters_for_ch): wire a v2-FilterEngine-backed
    attribute predicate so 100% of session filter shapes route through CH.
    """
    from tracer.services.clickhouse.v2 import get_reader
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    raw_filters = eval_task.filters or {}
    if isinstance(raw_filters, dict) and raw_filters.get("span_attributes_filters"):
        logger.warning(
            "eval_task_session_candidates_span_attr_filters_ignored",
            eval_task_id=str(eval_task.id),
            note=(
                "span_attributes_filters are not yet translated to CH for the "
                "session candidate derivation; candidate set ignores that subset"
            ),
        )
    ch_kwargs = CHSpanReader.parsing_evaltask_filters_for_ch(raw_filters)
    # Force-scope to the eval_task's tenant regardless of any project_id the
    # payload filters carry (mirrors eval_task.py's tenant gate).
    ch_kwargs["project_id"] = str(eval_task.project_id)
    with get_reader() as reader:
        return reader.distinct_session_ids_with_filters(**ch_kwargs)


@temporal_activity(
    max_retries=0,
    time_limit=3600 * 3,
    queue="default",
)
def eval_task_cron():
    # Get the current offset from cache, default to 0 if not set
    offset = cache.get("eval_task_offset", 0)

    eval_tasks = (
        EvalTask.objects.filter(
            status__in=[EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING]
        )
        .order_by("created_at")
        .values_list("id", flat=True)
    )
    cnt = len(eval_tasks)

    if offset >= cnt:
        offset = 0

    eval_tasks = eval_tasks[offset : offset + 5]
    for eval_task_id in eval_tasks:
        process_eval_task.delay(eval_task_id)

    # Update the offset in cache
    cache.set("eval_task_offset", offset + 5)

    logger.info("EVAL TASK CRON COMPLETED")


@temporal_activity(max_retries=0, time_limit=3600, queue="tasks_s")
def process_eval_task(eval_task_id: str):
    try:
        try:
            eval_task = EvalTask.objects.get(id=eval_task_id)
        except EvalTask.DoesNotExist:
            logger.error(f"Eval task with id {eval_task_id} not found")
            return

        if eval_task.status == EvalTaskStatus.PENDING:
            properties = get_mixpanel_properties(
                org=eval_task.project.organization,
                project=eval_task.project,
                type=MixpanelTypes.EVAL_TASK.value,
                count=eval_task.spans_limit,
                uid=str(eval_task.id),
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)
        with transaction.atomic():
            eval_task = EvalTask.objects.select_for_update().get(id=eval_task.id)
            eval_task.status = EvalTaskStatus.RUNNING
            eval_task.save(update_fields=["status", "updated_at"])

        eval_task_logger = EvalTaskLogger.objects.filter(eval_task=eval_task).first()
        if not eval_task_logger:
            eval_task_logger = EvalTaskLogger.objects.create(
                eval_task=eval_task, status=EvalTaskStatus.RUNNING, spanids_processed=[]
            )

        filters = Q()
        extra_anns: dict = {}
        if eval_task.filters is not None:
            filters, extra_anns = parsing_evaltask_filters(
                eval_task.filters, row_type=eval_task.row_type
            )

        # Pre-annotate the ``annotation_<label_id>`` columns that per-label
        # ANNOTATION filters reference, scoped to the row_type's Score sources.
        annotation_labels = get_annotation_labels_for_project(eval_task.project_id)
        span_qs = build_annotation_subqueries(
            ObservationSpan.objects.all(),
            annotation_labels,
            eval_task.project.organization,
            source_q=annotation_source_q_for_row_type(eval_task.row_type),
        )
        span_qs = span_qs.annotate(**extra_anns).filter(filters)

        # Branch the candidate queryset and dispatch activity on
        # row_type. The rest of the function operates on ``entity_qs``
        # (a Django queryset of the entities we'll evaluate) and
        # ``dispatch`` (the activity to fan out to). The span path stays
        # the original behaviour. The ``spanids_processed`` JSONField
        # stores the processed entity ids generically — its name is
        # historical (it once held only span ids); a future rename to
        # ``processed_target_ids`` is intentionally deferred so this PR
        # stays focused on dispatcher behaviour.
        #
        # SESSIONS is the exception: its candidates are a Python LIST of CH-
        # derived ids (``session_candidate_ids``), not a queryset, because the
        # ``Trace.session`` FK is gone post-flip (Slice C). When it is set the
        # shared downstream branches on it instead of ``entity_qs``.
        session_candidate_ids: list[str] | None = None
        if eval_task.row_type == RowType.TRACES:
            # A trace is in scope iff at least one of its spans matches
            # the existing span-level filters.
            entity_qs = Trace.objects.filter(
                id__in=span_qs.values("trace_id").distinct()
            )
            dispatch = evaluate_trace_observe
        elif eval_task.row_type == RowType.SESSIONS:
            # A session is in scope iff any of its spans matches the filters.
            # P3b step2 / Slice C (DESIGN §5): re-derive the candidate session
            # ids from CH, NOT the ``Trace.session`` FK. Post-flip that FK is
            # ``None`` (only spans carry the deterministic ``trace_session_id``),
            # so the old ``Trace.objects…exclude(session__isnull=True)
            # .values('session_id')`` subquery silently omits EVERY net-new
            # session → session-level evals would never run on them. The CH
            # derivation is remap-aware (straddler old+new id → one survivor
            # id) and project-pinned (the spans table is multi-tenant). The
            # candidate set is a plain Python ``list`` of ids, NOT a
            # ``TraceSession`` queryset: re-wrapping it in
            # ``TraceSession.objects.filter(id__in=…)`` would re-introduce the
            # exact bug (net-new ids have no PG row → dropped again). The shared
            # downstream below branches on ``session_candidate_ids`` accordingly.
            session_candidate_ids = _derive_session_candidate_ids(eval_task)
            entity_qs = None
            dispatch = evaluate_trace_session_observe
        elif eval_task.row_type in (RowType.SPANS, RowType.VOICE_CALLS):
            # Voice calls share the spans dispatch — the picker layer
            # already aliases voiceCalls→spans (observation_span.py:2890),
            # and any conversation-type narrowing the user wants comes
            # through ``filters`` like every other span query.
            entity_qs = span_qs
            dispatch = evaluate_observation_span_observe
        else:
            # Fail fast on unknown / future row types instead of silently
            # dispatching down the span path. Catches both corrupt rows and
            # the case where a new RowType enum value is added without
            # updating this dispatcher.
            raise ValueError(
                f"Unhandled row_type {eval_task.row_type!r} on EvalTask {eval_task.id}"
            )

        sampling_rate = eval_task.sampling_rate
        span_limit = eval_task.spans_limit
        cnt = None
        # SESSIONS (Slice C) operate on a materialized Python list of CH-derived
        # ids rather than a queryset (``entity_qs`` is ``None`` for that path —
        # see the row_type branch). Sessions are few, so materializing every
        # candidate is fine; the span/trace paths keep the queryset so they can
        # sample at the DB level without pulling all ids into memory.
        is_session = session_candidate_ids is not None
        total_spans_count = (
            len(session_candidate_ids) if is_session else entity_qs.count()
        )

        if eval_task.run_type == RunType.HISTORICAL and span_limit is not None:
            # Use ``offset`` (dedup-set size recorded below, before the
            # ``MAX_STORED_IDS`` truncation) instead of
            # ``len(spanids_processed)``. The stored list is capped, so its
            # length would under-report progress once the cap is hit.
            runned_spans_count = eval_task_logger.offset or 0
            sample_size = int((sampling_rate / 100) * total_spans_count)

            if runned_spans_count >= span_limit or runned_spans_count >= sample_size:
                # Dispatch quota reached. The task is NOT done yet —
                # per-span activities drain asynchronously on ``tasks_s``
                # and can take many minutes to finish. Previously we
                # flipped to COMPLETED the moment the dispatcher
                # finished handing out work, which meant ``status``
                # lied: users saw "completed" while rows were still
                # trickling in (and sometimes never arriving because
                # activities got dropped on worker recycles). The
                # source of truth for "done" is ``EvalLogger`` rows
                # matching the expected total.
                state = compute_drain_state(eval_task, eval_task_logger)

                if state["is_fully_drained"]:
                    _drops = 0
                elif state["is_stalled"]:
                    _drops = state["missing"]
                    logger.warning(
                        "eval_task_completed_with_drops",
                        eval_task_id=str(eval_task.id),
                        expected=state["dispatched"],
                        actual=state["completed"],
                        dropped=_drops,
                    )
                    # Surface the stall in ``failed_spans`` so the user
                    # sees the same information the logs do. Without
                    # this, the UI flips to "completed" and the 705
                    # dropped spans just vanish — the user has no way
                    # to tell the run was partial. One aggregated entry
                    # (not per-span) keeps the JSONField small and
                    # plays nicely with the frontend's error-group
                    # aggregator.
                    _missing = state["missing"]
                    _expected = state["dispatched"]
                    _stall_mins = _DRAIN_STALL_SECONDS // 60
                    try:
                        with transaction.atomic():
                            _et = EvalTask.objects.select_for_update().get(
                                id=eval_task.id
                            )
                            _fs = list(_et.failed_spans or [])
                            _fs.append(
                                {
                                    "observation_span_id": None,
                                    "custom_eval_config_id": None,
                                    "error": (
                                        f"Drain stall: {_missing} of "
                                        f"{_expected} dispatched evaluations "
                                        "did not produce a result within "
                                        f"{_stall_mins} minutes. Spans were "
                                        "handed to the worker pool but their "
                                        "activities either failed upstream "
                                        "silently or were dropped on a "
                                        "worker recycle. Re-run the task to "
                                        "retry the missing spans."
                                    ),
                                }
                            )
                            _et.failed_spans = _fs
                            _et.save(update_fields=["failed_spans", "updated_at"])
                    except Exception as _save_err:
                        logger.error(
                            "eval_task_stall_summary_save_failed",
                            eval_task_id=str(eval_task.id),
                            error=str(_save_err),
                        )
                else:
                    # Still draining — keep status at RUNNING and let
                    # the next cron tick re-check. No re-dispatch
                    # needed because offset is already at the cap.
                    logger.info(
                        "eval_task_draining",
                        eval_task_id=str(eval_task.id),
                        expected=state["dispatched"],
                        actual=state["completed"],
                        missing=state["missing"],
                    )
                    return

                eval_task.status = EvalTaskStatus.COMPLETED
                eval_task_logger.status = EvalTaskStatus.COMPLETED
                eval_task_logger.save()
                eval_task.save(update_fields=["status", "updated_at"])
                properties = get_mixpanel_properties(
                    org=eval_task.project.organization,
                    project=eval_task.project,
                    type=MixpanelTypes.EVAL_TASK.value,
                    count=state["completed"],
                    uid=str(eval_task.id),
                    failed=_drops,
                )
                track_mixpanel_event(
                    MixpanelEvents.EVAL_RUN_COMPLETED.value, properties
                )
                return
            else:
                cnt = span_limit - runned_spans_count

        with transaction.atomic():
            eval_task_logger = EvalTaskLogger.objects.select_for_update().get(
                id=eval_task_logger.id
            )
            spanids_processed = (
                eval_task_logger.spanids_processed
                if eval_task_logger.spanids_processed
                else []
            )

            if eval_task.run_type == RunType.CONTINUOUS:
                filters = filters & Q(created_at__gte=eval_task_logger.updated_at)

            if is_session:
                # Sessions: the candidate set is the CH-derived id list. Dedup
                # against ``spanids_processed`` in Python (both sides stringified
                # — the CH method already returns str ids, the stored list is
                # str). ``pending_ids`` stands in for the queryset paths'
                # ``pending_entities`` below; the ``.count()``/``.order_by('?')``
                # uses are branched on ``is_session`` to ``len()``/``sample()``.
                _processed = set(spanids_processed)
                pending_ids = [
                    sid for sid in session_candidate_ids if str(sid) not in _processed
                ]
                pending_entities = None
                filtered_spans = list(pending_ids)
            else:
                # ``spanids_processed`` is stored as strings; trace/session
                # ids are UUIDs and Django coerces on ``id__in`` lookup.
                # The field name is historical (it once held only span ids);
                # for row_type=traces/sessions it now holds trace/session ids.
                if len(spanids_processed) > 0:
                    pending_entities = entity_qs.only("id").exclude(
                        id__in=spanids_processed
                    )
                else:
                    pending_entities = entity_qs.only("id")

                filtered_spans = pending_entities.values_list("id", flat=True)

            # Filter spans based on sampling rate
            if sampling_rate and sampling_rate > 0 and sampling_rate <= 100:
                sample_size = int((sampling_rate / 100) * total_spans_count)
                runned_spans_count = eval_task_logger.offset or 0
                # CONTINUOUS tasks have no sampling cap — they run forever
                # on incoming spans. The historical-style "stop when offset
                # >= sample_size" check would silently no-op once the
                # cumulative offset crosses sample_size, even though new
                # spans keep arriving
                is_continuous = eval_task.run_type == RunType.CONTINUOUS
                if not is_continuous and runned_spans_count >= sample_size:
                    filtered_spans = []
                else:
                    # ``pending_count`` abstracts the unprocessed candidate-set
                    # size: ``len(pending_ids)`` for the session list path,
                    # ``pending_entities.count()`` for the queryset paths.
                    pending_count = (
                        len(pending_ids) if is_session else pending_entities.count()
                    )
                    if is_continuous:
                        # For continuous, sampling applies to the CURRENT
                        # batch of unprocessed spans, not against accumulated
                        # offset.
                        max_samples = max(int((sampling_rate / 100) * pending_count), 1)
                    else:
                        max_samples = sample_size - runned_spans_count
                    if cnt is not None:
                        max_samples = min(max_samples, cnt)
                    # Sample at the DB level instead of materializing every
                    # candidate entity id into Python memory. ``order_by("?")``
                    # is backed by RANDOM() in PostgreSQL, which is sufficient
                    # here and bounded by ``LIMIT sample_count``. Sessions are a
                    # materialized list, so ``random.sample`` (already imported)
                    # draws the same uniform sample without a DB round-trip.
                    total_available = pending_count
                    sample_count = min(max_samples, total_available)
                    if is_session:
                        sampled_span_ids = sample(list(pending_ids), sample_count)
                    else:
                        sampled_span_ids = list(
                            pending_entities.order_by("?").values_list("id", flat=True)[
                                :sample_count
                            ]
                        )
                    filtered_spans = sampled_span_ids
            if cnt is not None:
                filtered_spans = list(filtered_spans[:cnt])

            # Cap ``spanids_processed`` so the JSONField doesn't grow unbounded
            # for long-running tasks. Record the dedup-set size in ``offset``
            # BEFORE truncation so completion checks reflect actual progress
            # (the in-DB list only retains the most recent ids for future
            # dedup).
            #
            # Stringify the entity ids before storing. ObservationSpan.id is
            # already a CharField (str), but Trace.id and TraceSession.id are
            # UUIDField — psycopg's JSONField adapter can't serialize raw
            # UUID objects, so we coerce here. Existing entries from
            # span-only tasks are already strings, so the coercion is
            # idempotent.
            MAX_STORED_IDS = 10000
            new_ids = [str(eid) for eid in filtered_spans]
            updated_spanids_processed = list(set(spanids_processed + new_ids))
            eval_task_logger.offset = len(updated_spanids_processed)
            if len(updated_spanids_processed) > MAX_STORED_IDS:
                updated_spanids_processed = updated_spanids_processed[-MAX_STORED_IDS:]
            eval_task_logger.spanids_processed = (
                updated_spanids_processed  # todo: add many to many in this
            )
            eval_task_logger.save(
                update_fields=["spanids_processed", "offset", "updated_at"]
            )

        evals = eval_task.evals.all()

        for entity_id in filtered_spans:
            for eval_config in evals:
                dispatch.delay(
                    str(entity_id),
                    str(eval_config.id),
                    str(eval_task.id),
                )

        # TH-5410: residual-completion guard.
        #
        # The dispatch-quota branch above only flips a HISTORICAL task to
        # COMPLETED when ``offset >= span_limit`` or ``offset >= sample_size``.
        # That gate can never trip when ``offset`` legitimately undershoots
        # ``sample_size`` even though every matching entity has already been
        # processed — e.g. the matching-span universe shrank between ticks
        # (spans deleted / filtered out), ``sample_size`` rounded up past the
        # number of entities that actually exist, or set-dedup collapsed the
        # processed list below the sampled count. In all of those cases the
        # task re-runs the dispatch block every cron tick, finds zero pending
        # entities, dispatches nothing, and stays RUNNING forever — the
        # "stuck running after all spans processed" symptom (TH-5410).
        #
        # Make completion derived from "processed == total" rather than from
        # the offset>=sample_size gate alone: once nothing is left to dispatch
        # AND the already-dispatched work is fully drained, the task is done.
        # Continuous tasks are exempt (they run indefinitely by design); the
        # quota branch already owns the stall/partial-drain bookkeeping, so we
        # only act on the happy "fully drained, nothing pending" case here.
        if (
            eval_task.run_type == RunType.HISTORICAL
            and not filtered_spans
            and eval_task.status != EvalTaskStatus.COMPLETED
        ):
            state = compute_drain_state(eval_task, eval_task_logger)
            if state["is_fully_drained"]:
                with transaction.atomic():
                    _et = EvalTask.objects.select_for_update().get(id=eval_task.id)
                    if _et.status not in (
                        EvalTaskStatus.COMPLETED,
                        EvalTaskStatus.DELETED,
                    ):
                        _et.status = EvalTaskStatus.COMPLETED
                        _et.save(update_fields=["status", "updated_at"])
                        eval_task_logger.status = EvalTaskStatus.COMPLETED
                        eval_task_logger.save(update_fields=["status", "updated_at"])
                        logger.info(
                            "eval_task_completed_no_pending",
                            eval_task_id=str(eval_task.id),
                            dispatched=state["dispatched"],
                            completed=state["completed"],
                        )
    except Exception as e:
        logger.exception(f"{e}")
        eval_task.status = EvalTaskStatus.FAILED
        eval_task.save()


@temporal_activity(max_retries=0, time_limit=3600, queue="tasks_s")
def run_for_processed_spans(span_ids: list, eval_ids: list, eval_task_id: str):
    try:
        eval_task = EvalTask.objects.get(id=eval_task_id)
        evals = eval_task.evals.filter(id__in=eval_ids)
        # Read from CH 25.3 (was
        # ObservationSpan.objects.filter(id__in=span_ids)). The loop only
        # reads span.id to dispatch a per-span activity; existence (i.e.
        # the implicit filter behaviour of the queryset for deleted/missing
        # spans) is preserved by CHSpanReader.list_by_ids which already
        # filters is_deleted = 0.
        from tracer.services.clickhouse.v2 import get_reader

        with get_reader() as reader:
            spans = reader.list_by_ids([str(sid) for sid in span_ids])

        # Codex consolidated review P1 (2026-05-26): under-dispatch is a
        # silent correctness gap — eval rows the caller selected can be
        # skipped if CH is lagging. Surface the divergence loudly so it
        # gets retried (Temporal will re-run on raised exceptions) or
        # triaged. Don't dispatch ANY evals from this batch if the CH
        # set doesn't match what the caller selected; partial dispatch
        # is worse than no dispatch (operator sees nothing or sees
        # complete success).
        found_ids = {str(s.id) for s in spans}
        requested_ids = {str(sid) for sid in span_ids}
        missing = requested_ids - found_ids
        if missing:
            logger.error(
                "eval_task_ch_spans_missing",
                eval_task_id=str(eval_task.id),
                requested=len(requested_ids),
                found=len(found_ids),
                missing_count=len(missing),
                missing_sample=list(missing)[:10],
            )
            raise RuntimeError(
                f"CH missing {len(missing)} of {len(span_ids)} requested "
                f"spans for eval_task {eval_task.id}; refusing partial "
                f"dispatch. Sample missing: {list(missing)[:5]}. "
                f"Temporal will retry; if misses persist, rebackfill CH "
                f"or wait for the dual-write window to converge."
            )

        for span in spans:
            for eval_config in evals:
                evaluate_observation_span_observe.delay(
                    str(span.id),
                    str(eval_config.id),
                    str(eval_task.id),
                )

    except Exception as e:
        logger.exception(f"{e}")
        eval_task.status = EvalTaskStatus.FAILED
        eval_task.save()
