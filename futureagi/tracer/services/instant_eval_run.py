"""
An Instant eval run as the Ask page reads it: its questions, its status, its
summary and what its answers cost.

Shared by the run endpoints (``tracer.views.instant_eval_scale``), History
(``tracer.views.instant_eval``) and the eval-task worker, which counts a run
once as it finishes (``prewarm_summary``).
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta

import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.instant_eval import InstantEvalRun
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_summary as summary_service

logger = structlog.get_logger(__name__)


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        # ClickHouse returns naive UTC datetimes.
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return str(value) if value else None


def _task_ids(task, unit: str) -> list[str]:
    """The rows a task named outright, in the order it named them."""
    key = {"traces": "trace_id", "spans": "span_id"}.get(unit, "session_id")
    raw = (task.filters or {}).get(key)
    if not isinstance(raw, list | tuple):
        return []
    return list(dict.fromkeys(str(v) for v in raw if v not in (None, "")))


def asked_order(task, configs: list) -> list:
    """A run's questions in the order it asked them: as stored when it
    started, else as they were created."""
    asked = [str(c) for c in (task.filters or {}).get("instant_config_ids") or []]
    by_id = {str(c.id): c for c in configs}
    ordered = [by_id[c] for c in asked if c in by_id]
    rest = sorted((c for c in configs if c not in ordered), key=lambda c: c.created_at)
    return ordered + rest


DEFAULT_SAMPLE = 1000
MAX_QUESTIONS = 5


# A finished run's summary only changes when its answers version moves, and
# the key moves with it, so this only bounds the cache: at 100,000 rows a
# summary is about 190 KB, kept per time zone and set of questions, in a Redis
# that doesn't evict. The run is counted as it finishes, and opening it again
# after a day counts it again in about half a second.
FINISHED_SUMMARY_TTL = 24 * 60 * 60
# Outlives the summaries, so they are counted again without comparing the
# answers again. Once it expires the next count compares them once more: two
# counts over the run's own entries, and a copy only if they disagree.
ANSWERS_CHECKED_TTL = 7 * 24 * 60 * 60
TERMINAL = {EvalTaskStatus.COMPLETED, EvalTaskStatus.FAILED, EvalTaskStatus.PAUSED}
ACTIVE = (EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING)


def max_rows() -> int:
    return int(getattr(settings, "INSTANT_EVAL_MAX_ROWS", 100_000))


class CreditCheckUnavailable(Exception):
    """Billing couldn't be asked about a priced run (its Redis or config is
    down). Credits are priced only on Cloud, so this refuses the run with a
    503 rather than letting it start, and bill, unchecked."""


def credits_per_answer() -> float | None:
    """What Jev charges for one answer, in credits, or None where Instant
    eval isn't billed (off Cloud, or without ee). Every credit figure and
    credit check follows from it. A request reads it once and hands it on.
    On Cloud a billing config that fails to load raises
    CreditCheckUnavailable, as a credit check billing can't answer does."""
    from tracer.ee_boundary import jev_credits_per_answer

    try:
        return jev_credits_per_answer()
    except Exception as exc:
        logger.warning("instant_eval_billing_unavailable", exc_info=True)
        raise CreditCheckUnavailable() from exc


def _credits(rows: int, questions: int, rate: float | None) -> float | None:
    """What ``rows`` × ``questions`` answers cost at ``rate`` (credits per
    answer); None where the run isn't billed."""
    return None if rate is None else round(rows * questions * rate, 2)


# Billing's event for a Jev answer; its cap is the organization's credits.
CREDIT_EVENT = "jev_evaluator"


def check_credits(org, credits: float | None):
    """The organization's credit cap against a run costing ``credits``:
    None when it may run (or this deployment has no billing), else billing's
    CheckResult. Read-only. Raises CreditCheckUnavailable when billing can't
    answer: a priced run never starts unchecked, as with every other caller
    of check_usage."""
    from tracer.ee_boundary import check_usage

    if org is None or not credits or credits <= 0:
        return None
    try:
        result = check_usage(str(org.id), CREDIT_EVENT, amount=credits)
    except Exception as exc:
        logger.warning("instant_eval_credit_check_unavailable", exc_info=True)
        raise CreditCheckUnavailable() from exc
    return None if result.allowed else result


def _rows_for(unit: str, users_or_rows: int) -> int:
    """The rows a run reads: a user question reads up to five sessions each."""
    return users_or_rows * (rows_service.SESSIONS_PER_USER if unit == "users" else 1)


def _person(user) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": user.name or "", "email": user.email or ""}


def _question(config) -> dict:
    meta = (config.config or {}).get("instant_eval") or {}
    answer_type = meta.get("answer_type") or "yes_no"
    labels = list(
        meta.get("labels") or (["Yes", "No"] if answer_type == "yes_no" else [])
    )
    return {
        "config_id": str(config.id),
        "question": meta.get("question") or config.name or "",
        "answer_type": "category" if answer_type == "category" else "yes_no",
        "labels": labels,
    }


def _run_of(task):
    return (
        InstantEvalRun.objects.filter(eval_task=task)
        .select_related("created_by")
        .first()
    )


def _unit_of(task, run, params) -> str:
    if run is not None:
        return run.unit
    for config in task.evals.all():
        unit = ((config.config or {}).get("instant_eval") or {}).get("unit")
        if unit:
            return unit
    return params.get("unit") or str(task.row_type or "traces")


def _view(task, run, params) -> tuple[summary_service.RunView, list[dict]]:
    configs = [
        c for c in task.evals.all() if (c.config or {}).get("instant_eval")
    ] or list(task.evals.all())
    wanted = [c for c in (params.get("config_ids") or "").split(",") if c][
        :MAX_QUESTIONS
    ]
    by_id = {str(c.id): c for c in configs}
    ordered = [by_id[c] for c in wanted if c in by_id] or asked_order(task, configs)[
        :MAX_QUESTIONS
    ]
    questions = [_question(c) for c in ordered]
    unit = _unit_of(task, run, params)
    view = summary_service.RunView(
        task_id=str(task.id),
        row_type=rows_service.ROW_TYPE.get(unit, "traces"),
        unit=unit,
        config_ids=[q["config_id"] for q in questions],
        tz=summary_service.zone(params.get("tz")),
        date_from=run.date_from if run else None,
        date_to=run.date_to if run else None,
        cancelled=bool(run and run.cancelled_at),
        labels={q["config_id"]: q["labels"] for q in questions},
        # A user question stores each user's sessions, so its rows can't be
        # told complete from the users it asked for.
        rows_expected=run.rows_requested if run and unit != "users" else None,
        rows_version=run.answers_version if run else 0,
        live=_status(task, run) in ACTIVE,
    )
    return view, questions


# Every poll of a live run shares one count for this long, however many
# people (or tabs) watch it; only one request counts at a time.
LIVE_SNAPSHOT_S = 2.5
# A run that has landed no answer for this long while its task says it is
# running is checked for a workflow that is gone.
STALL_AFTER_S = 90
STALL_CHECK_TTL = 20
STALLED = "stalled"
# Paused, while the answers already being read still land.
PAUSING = "pausing"
# A running run that has landed nothing for this long says it is waiting.
WAITING_AFTER_S = 120
# An eval claimed (or started) longer ago than this is no longer in flight.
IN_FLIGHT_S = 300


def _live_summary_age(rows: int) -> float:
    """How stale a live run's summary may get: a few seconds, and a second
    per 20,000 rows past that (5 s at 100,000)."""
    return max(LIVE_SNAPSHOT_S, rows / 20_000)


def _status(task, run) -> str:
    if run is not None and run.cancelled_at:
        return "cancelled"
    return str(task.status or EvalTaskStatus.PENDING)


def _workflow_gone(task) -> bool:
    """Whether no Temporal workflow is draining the task (a restart or a
    crash took it), asked at most every 20 s per task. An unreachable
    Temporal is not an answer: the run is then taken to be running."""
    from tfc.temporal.eval_tasks.client import (
        WF_ABSENT,
        WF_CLOSED,
        describe_eval_task_workflow_sync,
    )

    key = f"ie-wf-gone:{task.id}"
    known = cache.get(key)
    if known is not None:
        return known
    try:
        gone = describe_eval_task_workflow_sync(task.id) in (WF_ABSENT, WF_CLOSED)
    except Exception:
        logger.warning("instant_eval_describe_failed", task_id=str(task.id))
        gone = False
    cache.set(key, gone, STALL_CHECK_TTL)
    return gone


def _run_time(run, progress: dict, running: bool) -> dict:
    """Active time (a minute per minute in which answers landed) and idle
    time (the rest of the wall time since the run started)."""
    if not progress.get("active_minutes") or not run.created_at:
        return {"active_s": None, "idle_s": None}
    end_ms = time.time() * 1000 if running else (progress["last_ms"] or 0)
    wall = max(0, (end_ms - run.created_at.timestamp() * 1000) / 1000)
    active = min(wall, progress["active_minutes"] * 60)
    return {"active_s": round(active), "idle_s": round(max(0, wall - active))}


# The eval logger's column naming a row of each row type.
_ROW_COLUMN = {
    "traces": "trace_id",
    "spans": "observation_span_id",
    "sessions": "trace_session_id",
}


def _in_flight(task, row_type: str | None = None) -> int:
    """Answers of a paused run still being read: running entries claimed or
    started in the last five minutes (older claims belong to a worker that
    is gone). With ``row_type``, the rows they belong to instead: a row
    asked three questions is one row in flight, as the page counts rows."""
    from tracer.models.observation_span import EvalEntryStatus, EvalLogger

    entries = EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
        deleted=False,
        updated_at__gte=timezone.now() - timedelta(seconds=IN_FLIGHT_S),
    )
    if row_type is None:
        return entries.count()
    column = _ROW_COLUMN.get(row_type, "trace_session_id")
    return (
        entries.exclude(**{f"{column}__isnull": True})
        .order_by()
        .values(column)
        .distinct()
        .count()
    )


# How the eval logger words every failed answer's explanation.
_ERROR_PREFIX = "Error during evaluation: "
_FAILURE_CHARS = 500


def _failure(task, view) -> dict | None:
    """Why a run that answered nothing failed: the explanation most of its
    failed answers carry (the one each row shows), whether every one of
    them carries it, and whether it is fixed under Settings → AI Providers
    (a missing or rejected TypeSafe key)."""
    from django.db.models import Count, Q, Value
    from django.db.models.functions import Coalesce, Left, NullIf

    from tracer.ee_boundary import jev_key_messages
    from tracer.models.observation_span import EvalLogger

    failed = EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        deleted=False,
        custom_eval_config_id__in=view.config_ids,
    ).filter(Q(error=True) | (~Q(skipped_reason__isnull=True) & ~Q(skipped_reason="")))
    found = (
        failed.annotate(
            reason=Left(
                Coalesce(
                    NullIf("eval_explanation", Value("")),
                    NullIf("skipped_reason", Value("")),
                ),
                _FAILURE_CHARS + len(_ERROR_PREFIX),
            )
        )
        .exclude(reason__isnull=True)
        .order_by()
        .values("reason")
        .annotate(n=Count("id"))
        .order_by("-n", "reason")
        .first()
    )
    if not found:
        return None
    message = found["reason"].removeprefix(_ERROR_PREFIX).strip()[:_FAILURE_CHARS]
    return {
        "message": message,
        "every": found["n"] == failed.count(),
        "ai_providers": message in jev_key_messages(),
    }


def _stalled(task, status: str, data: dict, progress: dict) -> bool:
    """A run stopped before finishing: its task ended (or its workflow is
    gone) while rows still wait for an answer. A run still picking its rows
    stalls too once its workflow is gone (a restart took it before that
    step stored any): it would otherwise read "Picking…" forever. While its
    workflow lives, however long a big view takes to pick, it is picking."""
    if not data["pending"]:
        return False
    if status in (EvalTaskStatus.COMPLETED, EvalTaskStatus.FAILED):
        return True
    if status not in ACTIVE:
        return False
    now_ms = time.time() * 1000
    quiet_since = max(
        progress["last_ms"] or 0,
        task.updated_at.timestamp() * 1000 if task.updated_at else 0,
    )
    return now_ms - quiet_since > STALL_AFTER_S * 1000 and _workflow_gone(task)


def _summary_key(task, run, status: str, view) -> str:
    raw = (
        f"{task.id}:{status}:{run.answers_version}:{view.unit}:"
        f"{','.join(view.config_ids)}:{view.tz.key}"
    )
    return "ie-summary:v10:" + hashlib.sha1(raw.encode()).hexdigest()


def _summary_payload(task, run, view, questions, price: float | None) -> dict:
    """The run's summary; ``price`` is credits per answer, None where the
    run isn't billed."""
    data = summary_service.summary(view)
    progress = data.pop("_progress")
    status = _status(task, run)
    # Until the task's first step stores the rows, every row it asked for is
    # still to read: while it starts, and when it was paused (it resumes
    # from that step) or cancelled before the step ran.
    unpicked = data.pop("rows_stored") == 0
    preparing = unpicked and status in (*ACTIVE, EvalTaskStatus.PAUSED)
    requested = run.rows_requested
    if preparing:
        data.update(total=requested, pending=requested)
    elif unpicked and status == "cancelled":
        data.update(total=requested, not_read=requested)
    data["preparing"] = preparing
    if _stalled(task, status, data, progress):
        status = STALLED
    in_flight = None
    if status == EvalTaskStatus.PAUSED:
        # In rows, as the page counts its progress.
        in_flight = _in_flight(task, view.row_type)
        if in_flight:
            status = PAUSING
    running = status in ACTIVE
    waiting_s = None
    if running and data["pending"] and not data.get("preparing"):
        # Quiet since the last answer, or since the run (re)started: a
        # resumed run isn't waiting for the hours it spent paused.
        started = task.start_time or run.created_at
        quiet_since = max(
            progress["last_ms"] or 0,
            started.timestamp() * 1000 if started else 0,
        )
        quiet = (time.time() * 1000 - quiet_since) / 1000 if quiet_since else 0
        waiting_s = int(quiet) if quiet >= WAITING_AFTER_S else None
    ncfg = max(1, len(view.config_ids))
    rate = None
    if running and progress["recent_entries"]:
        landing_s = (time.time() * 1000 - (progress["first_ms"] or 0)) / 1000
        window_s = min(summary_service.RATE_WINDOW_S, max(10.0, landing_s))
        rate = progress["recent_entries"] / ncfg / (window_s / 60)
    elif progress["settled_entries"] and progress["last_ms"] and progress["first_ms"]:
        minutes = max(1 / 60, (progress["last_ms"] - progress["first_ms"]) / 60000)
        rate = progress["settled_entries"] / ncfg / minutes
    pending = data["pending"]
    eta = int(pending / rate * 60) if running and rate and pending else None
    finished = (
        _iso(datetime.fromtimestamp(progress["last_ms"] / 1000, tz=UTC))
        if not running and progress["last_ms"]
        else None
    )
    population = run.population
    read = data["total"]
    is_sample = requested < population
    remaining = max(0, population - requested)
    # "Read N more" reads the sample's own size again (a custom size too),
    # up to what the view has left and the rows one run reads.
    under_cap = max(0, max_rows() // _rows_for(view.unit, 1) - requested)
    next_size = (
        min(remaining, run.sample_size or DEFAULT_SAMPLE, under_cap) if is_sample else 0
    )
    return {
        "status": status,
        "unit": view.unit,
        **data,
        "started_at": _iso(run.created_at),
        "finished_at": finished,
        **_run_time(run, progress, running),
        "rate_per_min": round(rate, 1) if rate else None,
        "eta_s": eta,
        "created_by": _person(run.created_by),
        "credits_estimate": run.credits_estimate if price is not None else None,
        # What the answers Jev gave so far cost.
        "credits_used": (
            round(progress["answers_given"] * price, 2) if price is not None else None
        ),
        "in_flight": in_flight,
        "waiting_s": waiting_s,
        # A run that finished without one answer (every row failed: a
        # rejected key, say) says why at the top, not only in each row.
        "failure": (
            _failure(task, view)
            if status == EvalTaskStatus.COMPLETED
            and data["failed"]
            and not progress["answers_given"]
            else None
        ),
        "sample": {
            "population": population,
            "population_is_lower_bound": run.population_is_lower_bound,
            "requested": requested,
            "read": read,
            "is_sample": is_sample,
            # A run stopped with rows left (paused or stalled) is resumed
            # before it reads more: the control view answers 409 until then.
            "can_extend": bool(next_size)
            and not running
            and not pending
            and status != "cancelled",
            "next_size": next_size,
        },
        "window": {
            "from": _iso(run.date_from) if run.date_from else None,
            "to": _iso(run.date_to) if run.date_to else None,
        },
        # What the run was asked about, so it can be asked again.
        "scope": {
            "filters": run.filters or [],
            "date_preset": run.date_preset,
            "label": run.label,
            "search": run.search or None,
            "sample_size": run.sample_size,
            "ids": (task.filters or {}).get("instant_ids")
            or _task_ids(task, view.unit)
            or None,
        },
        "questions": questions,
    }


def _checked_key(run) -> str:
    return f"ie-answers-checked:{run.eval_task_id}:{run.answers_version}"


def compute_summary(task, run, view, questions, price: float | None) -> dict:
    """Count a run, copying its answers over first if they weren't recorded
    as they landed (or, once it has finished, if any were missed), and keep
    the result: for a day once the run has finished."""
    from tracer.services.instant_eval_answers import ensure_synced

    status = _status(task, run)
    # Comparing a finished run's answers with the eval logger counts every
    # entry, so it is done once per answers version, not once per time zone.
    check = status in TERMINAL and not cache.get(_checked_key(run))
    ensure_synced(run, check=check)
    if check:
        cache.set(_checked_key(run), 1, ANSWERS_CHECKED_TTL)
    payload = _summary_payload(task, run, view, questions, price)
    finished = payload["status"] not in (*ACTIVE, STALLED, PAUSING)
    cache.set(
        _summary_key(task, run, payload["status"], view),
        {"payload": payload, "at": time.time()},
        # A finished run never changes until something bumps its answers
        # version; a live or stalled one is counted again shortly.
        FINISHED_SUMMARY_TTL if finished else 60,
    )
    return payload


def prewarm_summary(task_id) -> None:
    """Count a run that just finished in the time zone it was last viewed
    in, so whoever opens it next reads the count instead of waiting for it."""
    task = EvalTask.objects.filter(id=task_id).prefetch_related("evals").first()
    run = _run_of(task) if task else None
    if run is None:
        return
    view, questions = _view(task, run, {"tz": run.last_tz or "UTC"})
    compute_summary(task, run, view, questions, credits_per_answer())
