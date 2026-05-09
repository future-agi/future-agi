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
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.utils.eval import (
    evaluate_observation_span_observe,
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
        if eval_task.filters is not None:
            filters = parsing_evaltask_filters(eval_task.filters)

        sampling_rate = eval_task.sampling_rate
        span_limit = eval_task.spans_limit
        cnt = None
        total_spans_count = ObservationSpan.objects.filter(filters).count()

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
            spanids_processed = (
                eval_task_logger.spanids_processed
                if eval_task_logger.spanids_processed
                else []
            )

            if eval_task.run_type == RunType.CONTINUOUS:
                filters = filters & Q(created_at__gte=eval_task_logger.updated_at)

            if len(spanids_processed) > 0:
                spans = ObservationSpan.objects.filter(filters).only("id").exclude(
                    id__in=spanids_processed
                )
            else:
                spans = ObservationSpan.objects.filter(filters).only("id")


            filtered_spans = spans.values_list("id", flat=True)

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
                        max_samples = max(int((sampling_rate / 100) * spans.count()), 1)
                    else:
                        max_samples = sample_size - runned_spans_count
                    if cnt is not None:
                        max_samples = min(max_samples, cnt)
                    # Sample at the DB level instead of materializing every
                    # candidate span id into Python memory. ``order_by("?")``
                    # is backed by RANDOM() in PostgreSQL, which is sufficient
                    # here and bounded by ``LIMIT sample_count``.
                    total_available = spans.count()
                    sample_count = min(max_samples, total_available)
                    sampled_span_ids = list(
                        spans.order_by("?").values_list("id", flat=True)[:sample_count]
                    )
                    filtered_spans = sampled_span_ids
            if cnt is not None:
                filtered_spans = list(filtered_spans[:cnt])

            # Cap ``spanids_processed`` so the JSONField doesn't grow unbounded
            # for long-running tasks. Record the dedup-set size in ``offset``
            # BEFORE truncation so completion checks reflect actual progress
            # (the in-DB list only retains the most recent ids for future
            # dedup).
            MAX_STORED_IDS = 10000
            new_ids = list(filtered_spans)
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

        for span_id in filtered_spans:
            for eval_config in evals:
                evaluate_observation_span_observe.delay(
                    str(span_id),
                    str(eval_config.id),
                    str(eval_task.id),
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
        spans = ObservationSpan.objects.filter(id__in=span_ids)

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
