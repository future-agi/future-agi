import os
from random import sample

import structlog
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *

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
from tracer.models.observation_span import (
    EvalLogger,
    EvalLoggerStatus,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.eval import (
    evaluate_observation_span_observe,
    evaluate_trace_observe,
    evaluate_trace_session_observe,
)
from tracer.utils.filters import FilterEngine

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
        eval_task_logger = EvalTaskLogger.objects.filter(
            eval_task=eval_task
        ).first()

    dispatched = (eval_task_logger.offset if eval_task_logger else 0) or 0
    eval_count = eval_task.evals.count() or 1
    expected = dispatched * eval_count

    logger_q = EvalLogger.objects.filter(
        eval_task_id=eval_task.id, deleted=False,
    ).exclude(status=EvalLoggerStatus.PENDING)
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
        _logger_ref = (
            eval_task_logger.updated_at if eval_task_logger else None
        )
        candidates = [c for c in (latest_row_at, _logger_ref) if c is not None]
        stall_ref = max(candidates) if candidates else None
        if stall_ref is not None and (now - stall_ref).total_seconds() > _DRAIN_STALL_SECONDS:
            is_stalled = True

    return {
        "dispatched": expected,
        "completed": completed,
        "missing": max(expected - completed, 0),
        "latest_row_at": latest_row_at,
        "is_fully_drained": is_fully_drained,
        "is_stalled": is_stalled,
    }


def parsing_evaltask_filters(filters: dict) -> Q:
    """
    Parses the input filters dictionary and returns a single combined Q object
    for Django ORM filtering.
    """
    combined_q = Q()

    if filters is None:
        return combined_q

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

    return combined_q


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


def entity_count_for_filters(row_type: str, parsed_filters) -> int:
    """Count entities matching *parsed_filters* for the given row_type."""
    if row_type == RowType.TRACES:
        return Trace.objects.filter(
            id__in=ObservationSpan.objects.filter(parsed_filters)
            .values("trace_id")
            .distinct()
        ).count()
    elif row_type == RowType.SESSIONS:
        matching_session_ids = (
            Trace.objects.filter(
                id__in=ObservationSpan.objects.filter(parsed_filters)
                .values("trace_id")
                .distinct()
            )
            .exclude(session__isnull=True)
            .values("session_id")
            .distinct()
        )
        return TraceSession.objects.filter(id__in=matching_session_ids).count()
    return ObservationSpan.objects.filter(parsed_filters).count()


def _entity_fk_for_row_type(row_type: str) -> str:
    if row_type == RowType.TRACES:
        return "trace_id"
    elif row_type == RowType.SESSIONS:
        return "trace_session_id"
    return "observation_span_id"


def _build_pending_rows_for_spans(
    entity_ids: list, eval_task_id: str, evals,
) -> list[EvalLogger]:
    spans = ObservationSpan.objects.filter(
        id__in=[str(eid) for eid in entity_ids]
    ).select_related("trace")
    return [
        EvalLogger(
            observation_span=span,
            trace=span.trace,
            target_type=EvalTargetType.SPAN,
            eval_task_id=eval_task_id,
            custom_eval_config=eval_config,
            status=EvalLoggerStatus.PENDING,
        )
        for span in spans
        for eval_config in evals
    ]

# TODO : Use the root span as the anchor for trace-level eval, if the root span is missing then skip it .
def _get_anchor_spans_for_traces(traces) -> dict[str, ObservationSpan]:
    """Batch-fetch one anchor span per trace (root preferred, then earliest)."""
    from django.db.models import Case, IntegerField, When

    return {
        str(a.trace_id): a
        for a in ObservationSpan.objects.filter(trace__in=traces)
        .annotate(
            _root_rank=Case(
                When(parent_span_id__isnull=True, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by("trace_id", "_root_rank", "start_time", "id")
        .distinct("trace_id")
    }


def _build_pending_rows_for_traces(
    entity_ids: list, eval_task_id: str, evals,
) -> list[EvalLogger]:
    traces = Trace.objects.filter(id__in=[str(eid) for eid in entity_ids])
    anchor_map = _get_anchor_spans_for_traces(traces)
    return [
        EvalLogger(
            trace=trace,
            observation_span=anchor,
            target_type=EvalTargetType.TRACE,
            eval_task_id=eval_task_id,
            custom_eval_config=eval_config,
            status=EvalLoggerStatus.PENDING,
        )
        for trace in traces
        for eval_config in evals
        if (anchor := anchor_map.get(str(trace.id)))
    ]


def _build_pending_rows_for_sessions(
    entity_ids: list, eval_task_id: str, evals,
) -> list[EvalLogger]:
    sessions = TraceSession.objects.filter(
        id__in=[str(eid) for eid in entity_ids]
    )
    return [
        EvalLogger(
            trace_session=session,
            target_type=EvalTargetType.SESSION,
            eval_task_id=eval_task_id,
            custom_eval_config=eval_config,
            status=EvalLoggerStatus.PENDING,
        )
        for session in sessions
        for eval_config in evals
    ]


def _precreate_pending_eval_loggers(
    eval_task: EvalTask,
    entity_ids: list,
    evals,
) -> None:
    """Pre-create PENDING EvalLogger rows so the UI can show loading state
    and the next dispatcher tick deduplicates correctly."""
    eval_task_id = str(eval_task.id)

    if eval_task.row_type in (RowType.SPANS, RowType.VOICE_CALLS):
        rows = _build_pending_rows_for_spans(entity_ids, eval_task_id, evals)
    elif eval_task.row_type == RowType.TRACES:
        rows = _build_pending_rows_for_traces(entity_ids, eval_task_id, evals)
    elif eval_task.row_type == RowType.SESSIONS:
        rows = _build_pending_rows_for_sessions(entity_ids, eval_task_id, evals)
    else:
        raise ValueError(
            f"Unhandled row_type {eval_task.row_type!r} on EvalTask {eval_task.id}"
        )

    if rows:
        EvalLogger.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)


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
                eval_task=eval_task, status=EvalTaskStatus.RUNNING
            )

        filters = Q()
        if eval_task.filters is not None:
            filters = parsing_evaltask_filters(eval_task.filters)

        # Branch the candidate queryset and dispatch activity on
        # row_type. The rest of the function operates on ``entity_qs``
        # (a Django queryset of the entities we'll evaluate) and
        # ``dispatch`` (the activity to fan out to). The span path stays
        # the original behaviour.
        if eval_task.row_type == RowType.TRACES:
            # A trace is in scope iff at least one of its spans matches
            # the existing span-level filters.
            entity_qs = Trace.objects.filter(
                id__in=ObservationSpan.objects.filter(filters)
                .values("trace_id")
                .distinct()
            )
            dispatch = evaluate_trace_observe
        elif eval_task.row_type == RowType.SESSIONS:
            # A session is in scope iff any of its traces has a matching span.
            # We resolve via two ``__in`` subqueries (spans -> trace_ids,
            # then traces -> session_ids) so the outer queryset stays a
            # plain SELECT; using ``traces__id__in`` here would force a JOIN
            # that needs ``.distinct()``, and ``DISTINCT + ORDER BY random()``
            # in the sampling step below misbehaves under PostgreSQL.
            matching_session_ids = (
                Trace.objects.filter(
                    id__in=ObservationSpan.objects.filter(filters)
                    .values("trace_id")
                    .distinct()
                )
                .exclude(session__isnull=True)
                .values("session_id")
                .distinct()
            )
            entity_qs = TraceSession.objects.filter(id__in=matching_session_ids)
            dispatch = evaluate_trace_session_observe
        elif eval_task.row_type in (RowType.SPANS, RowType.VOICE_CALLS):
            # Voice calls share the spans dispatch — the picker layer
            # already aliases voiceCalls→spans (observation_span.py:2890),
            # and any conversation-type narrowing the user wants comes
            # through ``filters`` like every other span query.
            entity_qs = ObservationSpan.objects.filter(filters)
            dispatch = evaluate_observation_span_observe
        else:
            # Fail fast on unknown / future row types instead of silently
            # dispatching down the span path. Catches both corrupt rows and
            # the case where a new RowType enum value is added without
            # updating this dispatcher.
            raise ValueError(
                f"Unhandled row_type {eval_task.row_type!r} on "
                f"EvalTask {eval_task.id}"
            )

        sampling_rate = eval_task.sampling_rate
        span_limit = eval_task.spans_limit
        cnt = None
        total_spans_count = entity_qs.count()

        if eval_task.run_type == RunType.HISTORICAL and span_limit is not None:
            # Use ``offset`` to track progress instead of counting
            # processed entities directly.
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
                eval_task.save()
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
            if eval_task.run_type == RunType.CONTINUOUS:
                filters = filters & Q(created_at__gte=eval_task_logger.updated_at)

            entity_fk = _entity_fk_for_row_type(eval_task.row_type)
            already_dispatched = (
                EvalLogger.objects.filter(
                    eval_task_id=str(eval_task.id), deleted=False,
                )
                .values(entity_fk)
                .distinct()
            )
            pending_entities = entity_qs.only("id").exclude(
                id__in=already_dispatched
            )

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
                    if is_continuous:
                        # For continuous, sampling applies to the CURRENT
                        # batch of unprocessed spans, not against accumulated
                        # offset.
                        max_samples = max(int((sampling_rate / 100) * pending_entities.count()), 1)
                    else:
                        max_samples = sample_size - runned_spans_count
                    if cnt is not None:
                        max_samples = min(max_samples, cnt)
                    # Sample at the DB level instead of materializing every
                    # candidate entity id into Python memory. ``order_by("?")``
                    # is backed by RANDOM() in PostgreSQL, which is sufficient
                    # here and bounded by ``LIMIT sample_count``.
                    total_available = pending_entities.count()
                    sample_count = min(max_samples, total_available)
                    sampled_span_ids = list(
                        pending_entities.order_by("?")
                        .values_list("id", flat=True)[:sample_count]
                    )
                    filtered_spans = sampled_span_ids
            if cnt is not None:
                filtered_spans = list(filtered_spans[:cnt])

            new_ids = [str(eid) for eid in filtered_spans]
            eval_task_logger.offset = (eval_task_logger.offset or 0) + len(new_ids)
            eval_task_logger.save(update_fields=["offset", "updated_at"])

        evals = eval_task.evals.all()

        if new_ids:
            _precreate_pending_eval_loggers(
                eval_task, filtered_spans, evals,
            )

        for entity_id in filtered_spans:
            for eval_config in evals:
                dispatch.delay(
                    str(entity_id),
                    str(eval_config.id),
                    str(eval_task.id),
                )
    except Exception as e:
        logger.exception(f"{e}")
        eval_task.status = EvalTaskStatus.FAILED
        eval_task.save()


@temporal_activity(max_retries=0, time_limit=3600, queue="tasks_s")
def run_for_processed_spans(entity_ids: list, eval_ids: list, eval_task_id: str):
    try:
        eval_task = EvalTask.objects.get(id=eval_task_id)
        evals = eval_task.evals.filter(id__in=eval_ids)

        if eval_task.row_type == RowType.TRACES:
            dispatch = evaluate_trace_observe
        elif eval_task.row_type == RowType.SESSIONS:
            dispatch = evaluate_trace_session_observe
        else:
            dispatch = evaluate_observation_span_observe

        for entity_id in entity_ids:
            for eval_config in evals:
                dispatch.delay(
                    str(entity_id),
                    str(eval_config.id),
                    str(eval_task.id),
                )

    except Exception as e:
        logger.exception(f"{e}")
        eval_task.status = EvalTaskStatus.FAILED
        eval_task.save()
