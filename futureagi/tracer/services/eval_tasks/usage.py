"""Usage-tab computations for a single eval task.

``EvalTaskView.get_usage`` owns request scoping, pagination and response
assembly; everything that turns ``EvalLogger`` rows into stats, chart buckets
and log items lives here.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from tracer.constants.eval_task_usage import (
    DEFAULT_USAGE_BUCKET_MINUTES,
    MAX_USAGE_CHART_BUCKETS,
    USAGE_BUCKET_THRESHOLDS,
    USAGE_PERIOD_DELTAS,
    UsagePeriod,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger

DEFAULT_OUTPUT_TYPE = "pass_fail"
_INPUT_SUMMARY_LIMIT = 200
_RESULT_LABEL_LIMIT = 50


@dataclass(frozen=True)
class UsageWindow:
    """Resolved time window plus the period labels echoed to the client."""

    start_date: datetime
    end_date: datetime
    requested: UsagePeriod
    used: UsagePeriod


def resolve_window(
    period: UsagePeriod,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> UsageWindow:
    """Build the initially requested window from the validated query params."""
    if start_date and end_date:
        return UsageWindow(
            start_date=start_date,
            end_date=end_date,
            requested=UsagePeriod.CUSTOM,
            used=UsagePeriod.CUSTOM,
        )

    end = timezone.now()
    return UsageWindow(
        start_date=end - USAGE_PERIOD_DELTAS[period],
        end_date=end,
        requested=period,
        used=period,
    )


def apply_window(
    base_qs: QuerySet, window: UsageWindow, total_runs: int
) -> tuple[QuerySet, int, UsageWindow]:
    """Scope ``base_qs`` to ``window``, widening to all-time when it is empty.

    A window that excludes every run leaves the user staring at an empty chart,
    so we fall back to the full run range. Both bounds are reset — pinning
    ``end_date`` to an empty custom window that sits *before* the runs would
    leave ``start_date > end_date`` and zero-fill nothing.
    """
    period_qs = base_qs.filter(
        created_at__gte=window.start_date,
        created_at__lte=window.end_date,
    )
    runs_period = period_qs.count()
    if runs_period or not total_runs:
        return period_qs, runs_period, window

    bounds = base_qs.order_by("created_at").values_list("created_at", flat=True)
    earliest = bounds.first()
    latest = bounds.last()
    return (
        base_qs,
        total_runs,
        UsageWindow(
            start_date=earliest or window.start_date,
            end_date=latest or window.end_date,
            requested=window.requested,
            used=UsagePeriod.ALL,
        ),
    )


def build_stats(
    period_qs: QuerySet, total_runs: int, runs_period: int
) -> dict:
    success_count = period_qs.filter(error=False).count()
    return {
        "total_runs": total_runs,
        "runs_period": runs_period,
        "success_count": success_count,
        "error_count": period_qs.filter(error=True).count(),
        "pass_rate": (
            round(success_count / runs_period * 100, 2) if runs_period else 0
        ),
    }


def list_configured_evals(eval_task_id: str) -> list[dict]:
    """Configured evals on the task — drives the usage-tab filter dropdown."""
    configs = CustomEvalConfig.objects.filter(
        eval_loggers__eval_task_id=eval_task_id
    ).distinct()
    return [
        {
            "id": str(config["id"]),
            "name": config.get("name") or "Evaluation",
            "output_type": config.get("eval_template__output_type_normalized")
            or DEFAULT_OUTPUT_TYPE,
            "template_id": (
                str(config["eval_template_id"])
                if config.get("eval_template_id")
                else None
            ),
            "model": config.get("model"),
        }
        for config in configs.values(
            "id",
            "name",
            "model",
            "eval_template_id",
            "eval_template__output_type_normalized",
        )
    ]


def bucket_minutes_for(window: UsageWindow) -> int:
    """Pick a bucket width from the window length, capped at a sane point count."""
    span = window.end_date - window.start_date
    minutes = DEFAULT_USAGE_BUCKET_MINUTES
    for threshold, bucket in USAGE_BUCKET_THRESHOLDS:
        if span <= threshold:
            minutes = bucket
            break

    span_minutes = max(span.total_seconds() / 60, 0)
    if span_minutes / minutes > MAX_USAGE_CHART_BUCKETS:
        minutes = int(span_minutes / MAX_USAGE_CHART_BUCKETS) + 1
    return minutes


def _floor_to_bucket(ts: datetime, bucket_minutes: int) -> datetime:
    if bucket_minutes >= 1440:
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket_minutes >= 60:
        hours_per_bucket = bucket_minutes // 60
        return ts.replace(
            hour=(ts.hour // hours_per_bucket) * hours_per_bucket,
            minute=0,
            second=0,
            microsecond=0,
        )
    return ts.replace(
        minute=(ts.minute // bucket_minutes) * bucket_minutes,
        second=0,
        microsecond=0,
    )


def _bucket_start(ts: datetime, origin: datetime, width: timedelta) -> datetime:
    """Snap ``ts`` to its bucket, counted positionally from ``origin``.

    The data keys and the zero-fill loop have to land on the same instants.
    Flooring each side independently only agrees while the bucket width divides
    the calendar unit ``_floor_to_bucket`` snaps to, and that stops holding as
    soon as the ``MAX_USAGE_CHART_BUCKETS`` cap derives a width of its own — a
    ~2400-day custom range yields 2305 minutes, which floors to midnight on one
    side and steps 1.6 days on the other. Only the first bucket would then
    match, and the chart would drop nearly every row while the stats and logs
    still counted it: TH-4805's symptom, reachable again through the custom
    picker. Counting from a shared origin makes the two sides equal by
    construction, whatever the width.
    """
    return origin + ((ts - origin) // width) * width


def build_chart(
    period_qs: QuerySet, window: UsageWindow, runs_period: int
) -> list[dict]:
    """Time series over ``window``, zero-filled so the line stays continuous."""
    if not runs_period:
        return []

    bucket_minutes = bucket_minutes_for(window)
    width = timedelta(minutes=bucket_minutes)
    origin = _floor_to_bucket(window.start_date, bucket_minutes)
    calls = defaultdict(int)
    passes = defaultdict(int)
    fails = defaultdict(int)
    scores = defaultdict(list)

    for log in period_qs.values(
        "created_at", "error", "output_bool", "output_float"
    ):
        key = _bucket_start(log["created_at"], origin, width).isoformat()
        calls[key] += 1

        if log["error"]:
            fails[key] += 1
            continue

        if log["output_bool"] is True:
            passes[key] += 1
            scores[key].append(1.0)
        elif log["output_bool"] is False:
            fails[key] += 1
            scores[key].append(0.0)
        if log["output_float"] is not None:
            scores[key].append(float(log["output_float"]))

    chart = []
    current = origin
    while current <= window.end_date:
        key = current.isoformat()
        bucket_scores = scores.get(key, [])
        chart.append(
            {
                "timestamp": key,
                "calls": calls.get(key, 0),
                "pass_count": passes.get(key, 0),
                "fail_count": fails.get(key, 0),
                "avg_score": (
                    round(sum(bucket_scores) / len(bucket_scores), 3)
                    if bucket_scores
                    else None
                ),
                "avg_latency_ms": 0,  # not tracked at logger level
            }
        )
        current += width
    return chart


def _result_from_outputs(log):
    """Derive a Pass/Fail label and 0-1 score from EvalLogger's typed columns."""
    if log.error:
        return "Error", None, "error"
    if log.output_bool is True:
        return "Passed", 1.0, "success"
    if log.output_bool is False:
        return "Failed", 0.0, "success"
    if log.output_float is not None:
        score = float(log.output_float)
        return ("Passed" if score >= 0.5 else "Failed"), score, "success"
    if log.output_str:
        return log.output_str[:_RESULT_LABEL_LIMIT], None, "success"
    return "", None, "success"


def _input_summary(obs_span, trace_session):
    """Short input preview. Trace-target rows carry the root span; session rows
    have neither span nor trace and fall back to the session name."""
    if obs_span:
        attrs = obs_span.span_attributes or {}
        value = (
            attrs.get("input") or attrs.get("input.value") or obs_span.name or ""
        )
        if isinstance(value, dict):
            return json.dumps(value)[:_INPUT_SUMMARY_LIMIT]
        return str(value)[:_INPUT_SUMMARY_LIMIT]
    if trace_session:
        return (trace_session.name or "")[:_INPUT_SUMMARY_LIMIT]
    return ""


def build_log_item(log: EvalLogger, input_variables: dict) -> dict:
    """One row of the paginated logs table, plus its side-panel detail."""
    result_label, score, status = _result_from_outputs(log)
    obs_span = log.observation_span
    trace_session = log.trace_session
    config = log.custom_eval_config

    metadata = log.output_metadata or {}
    warnings = (metadata.get("warnings") if isinstance(metadata, dict) else None) or []
    span_id = str(obs_span.id) if obs_span else None
    trace_id = str(obs_span.trace_id) if obs_span and obs_span.trace_id else None
    session_id = str(trace_session.id) if trace_session else None

    return {
        "id": str(log.id),
        "input": _input_summary(obs_span, trace_session),
        "result": result_label,
        "score": score,
        "reason": log.eval_explanation or log.error_message or "",
        "status": status,
        "source": "eval_task",
        "warnings": warnings,
        "created_at": log.created_at.isoformat() if log.created_at else "",
        # Cross-references so the side panel can jump back to the source row
        # in observe.
        "span_id": span_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "eval_id": str(config.id) if config else None,
        "eval_name": config.name if config else None,
        "model": config.model if config else None,
        "detail": {
            "eval_name": config.name if config else None,
            "model": config.model if config else None,
            "warnings": warnings,
            "output_type": (
                config.eval_template.output_type_normalized
                if config and config.eval_template
                else None
            ),
            "target_type": log.target_type,
            "span_name": obs_span.name if obs_span else None,
            "span_id": span_id,
            "trace_id": trace_id,
            "session_id": session_id,
            "session_name": trace_session.name if trace_session else None,
            "output_bool": log.output_bool,
            "output_float": log.output_float,
            "output_str": log.output_str,
            "results_explanation": log.results_explanation,
            "error_message": log.error_message,
            "input_variables": input_variables,
        },
    }
