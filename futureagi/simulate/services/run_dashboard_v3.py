"""Bounded dashboard read models built from recorded simulation evidence."""

from __future__ import annotations

from collections import defaultdict
import json
from typing import Any

from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    FloatField,
    Func,
    JSONField,
    Max,
    Min,
    Q,
    QuerySet,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Extract, Floor, Lower

from simulate.services.run_results_v3_expressions import (
    PercentileCont,
    _json_text,
    _safe_json_float,
)
from simulate.services.run_dashboard_v3_distributions import (
    csat_distribution,
    response_time_distribution,
)

CHART_BUCKETS = 100


def _stats(queryset: QuerySet, fields: dict[str, str]) -> dict[str, dict[str, Any]]:
    expressions = {}
    for name, field in fields.items():
        expressions[f"{name}_measured"] = Count(field)
        expressions[f"{name}_average"] = Avg(field)
        expressions[f"{name}_max"] = Max(field)
        for percentile in (50, 90, 99):
            expressions[f"{name}_p{percentile}"] = PercentileCont(
                field, percentile / 100
            )
    values = queryset.aggregate(**expressions)
    return {
        name: {
            key: values[f"{name}_{key}"]
            for key in ("measured", "average", "max", "p50", "p90", "p99")
        }
        for name in fields
    }


def _breakdown(
    queryset: QuerySet, field: str, key: str, label: str, total: int
) -> dict:
    counts = list(
        queryset.order_by()
        .values(field)
        .annotate(count=Count("id"))
        .order_by("-count", field)
    )
    segments = [
        {
            "label": str(row[field] or "Unknown"),
            "count": row["count"],
            "share": round(row["count"] * 100 / total, 2) if total else 0,
        }
        for row in counts[:12]
    ]
    remainder = sum(row["count"] for row in counts[12:])
    if remainder:
        segments.append(
            {
                "label": "Other",
                "count": remainder,
                "share": round(remainder * 100 / total, 2),
            }
        )
    preferred = {"call_success": "successful", "goal_outcome": "passed"}.get(key)
    if preferred:
        order = {
            "call_success": ["successful", "unsuccessful", "unknown"],
            "goal_outcome": ["passed", "failed", "error", "escalated", "inconclusive"],
        }[key]
        by_label = {segment["label"]: segment for segment in segments}
        segments = [
            by_label.get(value, {"label": value, "count": 0, "share": 0})
            for value in order
        ]
    if key == "call_success":
        for segment in segments:
            segment["statuses"] = {
                "successful": ["passed"],
                "unsuccessful": ["failed", "error"],
                "unknown": ["inconclusive"],
            }[segment["label"]]
    headline = next(
        (segment for segment in segments if segment["label"] == preferred), None
    )
    headline = headline or (segments[0] if segments else None)
    return {
        "key": key,
        "label": label,
        "total": total,
        "segments": segments,
        "headline": headline,
    }


def _tool_verdict(tool: dict) -> bool | None:
    """True means failed; an absent verdict remains unmeasured."""
    result = tool.get("result", tool.get("output"))
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = None
    result = result if isinstance(result, dict) else {}
    status = str(tool.get("status") or result.get("status") or "").lower()
    if (
        tool.get("error")
        or result.get("error")
        or tool.get("is_error") is True
        or status in {"failed", "error", "timeout"}
    ):
        return True
    if any(
        value is False
        for value in (
            tool.get("success"),
            result.get("success"),
            tool.get("ok"),
            result.get("ok"),
        )
    ):
        return True
    if (
        tool.get("success") is True
        or result.get("success") is True
        or tool.get("ok") is True
        or result.get("ok") is True
        or tool.get("is_error") is False
        or status in {"completed", "success", "succeeded"}
    ):
        return False
    return None


def _tool_stats(queryset: QuerySet) -> dict:
    # Project only tool evidence, never full transcripts/provider payloads.
    arrays = (
        queryset.order_by()
        .annotate(
            recorded_tools=Func(
                F("provider_call_data"),
                Value("$.*.tool_calls[*]"),
                function="jsonb_path_query_array",
                output_field=JSONField(),
            )
        )
        .values_list("recorded_tools", flat=True)
        .iterator(chunk_size=2000)
    )
    counts = defaultdict(lambda: {"invocations": 0, "measured": 0, "failures": 0})
    for tools in arrays:
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            name = tool.get("name") or (
                function.get("name") if isinstance(function, dict) else None
            )
            if not name:
                continue
            count = counts[str(name)]
            count["invocations"] += 1
            verdict = _tool_verdict(tool)
            if verdict is not None:
                count["measured"] += 1
                count["failures"] += int(verdict)
    rows = [
        {
            "name": name,
            **count,
            "failure_rate": (
                round(count["failures"] * 100 / count["measured"], 2)
                if count["measured"]
                else None
            ),
            "failure_label": (
                f'{round(count["failures"] * 100 / count["measured"])}% · {count["failures"]}/{count["measured"]}'
                if count["measured"]
                else "Not measured"
            ),
        }
        for name, count in counts.items()
    ]
    return {
        "total_invocations": sum(row["invocations"] for row in rows),
        "total_tools": len(rows),
        "volume": sorted(rows, key=lambda row: (-row["invocations"], row["name"]))[:20],
        "failures": sorted(
            rows,
            key=lambda row: (
                row["failure_rate"] is None,
                -(row["failure_rate"] or 0),
                row["name"],
            ),
        )[:20],
    }


def _series(queryset: QuerySet, total: int) -> list[dict]:
    if total <= CHART_BUCKETS:
        rows = (
            queryset.order_by(F("started_at").asc(nulls_last=True), "id")
            .annotate(
                duration_ms=F("duration_seconds") * 1000.0,
                llm_cents=F("llm_cost_cents"),
                tts_cents=F("tts_cost_cents"),
                stt_cents=F("stt_cost_cents"),
                storage_cents=F("storage_cost_cents"),
            )
            .values(
                "started_at",
                "duration_ms",
                "llm_cents",
                "tts_cents",
                "stt_cents",
                "storage_cents",
            )
        )
        return [
            {"label": str(index + 1), "calls": 1, **row}
            for index, row in enumerate(rows)
        ]
    bounds = queryset.aggregate(first=Min("started_at"), last=Max("started_at"))
    if not bounds["first"]:
        return []
    start = bounds["first"].timestamp()
    width = max((bounds["last"].timestamp() - start) / (CHART_BUCKETS - 1), 1)
    rows = (
        queryset.filter(started_at__isnull=False)
        .order_by()
        .annotate(
            bucket=Floor(
                (
                    Extract("started_at", "epoch", output_field=FloatField())
                    - Value(start)
                )
                / Value(width)
            )
        )
        .values("bucket")
        .annotate(
            started_at=Min("started_at"),
            calls=Count("id"),
            duration_ms=Avg(F("duration_seconds") * 1000.0),
            llm_cents=Sum("llm_cost_cents"),
            tts_cents=Sum("tts_cost_cents"),
            stt_cents=Sum("stt_cost_cents"),
            storage_cents=Sum("storage_cost_cents"),
        )
        .order_by("bucket")
    )
    return [
        {
            "label": str(index + 1),
            **{key: value for key, value in row.items() if key != "bucket"},
        }
        for index, row in enumerate(rows)
    ]


def _tails(queryset: QuerySet, field: str) -> list[dict]:
    rows = (
        queryset.filter(**{f"{field}__isnull": False})
        .order_by(f"-{field}", "id")
        .values("id", "result_goal", "simulation_call_type", "result_provider", field)[
            :8
        ]
    )
    return [
        {
            "rank": index + 1,
            "id": str(row["id"]),
            "label": row["result_goal"] or "Untitled task",
            "axis_label": f'#{index + 1} {(row["result_goal"] or "Untitled task")[:20]}',
            "value": row[field],
            "modality": row["simulation_call_type"],
            "provider": row["result_provider"],
        }
        for index, row in enumerate(rows)
    ]


def build_run_dashboard(
    queryset: QuerySet, summary: dict, evaluations: list, risk: list
) -> dict[str, Any]:
    total = summary["total"]
    queryset = (
        queryset.annotate(
            dashboard_csat_raw=_safe_json_float(
                "conversation_metrics_data", "csat_score"
            ),
            dashboard_sentiment=Lower(
                Coalesce(
                    _json_text("analysis_data", "user_sentiment"),
                    _json_text(
                        "provider_call_data",
                        "retell",
                        "call_analysis",
                        "user_sentiment",
                    ),
                    Value("Unknown", output_field=TextField()),
                )
            ),
            dashboard_success_raw=Lower(
                Coalesce(
                    _json_text("analysis_data", "call_successful"),
                    _json_text(
                        "provider_call_data",
                        "retell",
                        "call_analysis",
                        "call_successful",
                    ),
                    _json_text("analysis_data", "successEvaluation"),
                    Value("Unknown", output_field=TextField()),
                )
            ),
        )
        .annotate(
            dashboard_provider_success=Case(
                When(
                    dashboard_success_raw__in=["true", "false"],
                    then=F("dashboard_success_raw"),
                ),
                default=Value("unknown"),
                output_field=TextField(),
            ),
            dashboard_csat=Case(
                When(
                    dashboard_csat_raw__gte=0,
                    dashboard_csat_raw__lte=10,
                    then=F("dashboard_csat_raw"),
                ),
                output_field=FloatField(),
            ),
            dashboard_success=Case(
                When(result_outcome="passed", then=Value("successful")),
                When(
                    result_outcome__in=["failed", "error"], then=Value("unsuccessful")
                ),
                default=Value("unknown"),
                output_field=TextField(),
            ),
            dashboard_goal=Case(
                When(
                    Q(
                        call_metadata__harness_outcome_status__in=[
                            "escalated",
                            "handoff",
                        ]
                    )
                    | Q(
                        ended_reason__in=[
                            "escalated",
                            "human-handoff",
                            "assistant-forwarded-call",
                            "call-transfer",
                            "transfer",
                        ]
                    ),
                    then=Value("escalated"),
                ),
                default=F("result_outcome"),
                output_field=TextField(),
            ),
        )
        .annotate(
            dashboard_disconnection=Case(
                When(dashboard_goal="escalated", then=Value("Escalated")),
                When(
                    Q(ended_reason__icontains="timeout")
                    | Q(ended_reason__icontains="timed-out")
                    | Q(ended_reason="max-duration-reached"),
                    then=Value("Timeout"),
                ),
                When(result_outcome="error", then=Value("Error")),
                When(result_outcome="passed", then=Value("Task complete")),
                When(
                    ended_reason__in=[
                        "max-turns-reached",
                        "max_turns",
                        "incomplete",
                        "customer-ended-call",
                        "assistant-ended-call",
                    ],
                    then=Value("Incomplete"),
                ),
                default=Value("Unknown"),
                output_field=TextField(),
            ),
        )
    )
    voice = queryset.filter(simulation_call_type="voice")
    noun = "chat" if total and not voice.exists() else "call"
    values = queryset.aggregate(
        csat=Avg("dashboard_csat"),
        csat_measured=Count("dashboard_csat"),
        turns=Avg("result_turn_count"),
        turns_measured=Count("result_turn_count"),
        connected=Count(
            "id", filter=Q(message_count__gt=0) | Q(transcript_available=True)
        ),
    )
    voice_values = voice.aggregate(
        wpm=Avg("bot_wpm"),
        wpm_measured=Count("bot_wpm"),
        stop=Avg("avg_stop_time_after_interruption_ms"),
        stop_measured=Count("avg_stop_time_after_interruption_ms"),
        talk=Avg(
            Case(
                When(
                    talk_ratio__gte=0,
                    then=100.0 * F("talk_ratio") / (1.0 + F("talk_ratio")),
                ),
                output_field=FloatField(),
            )
        ),
        talk_measured=Count("talk_ratio", filter=Q(talk_ratio__gte=0)),
        interruptions=Sum("user_interruption_count"),
        interruptions_measured=Count("user_interruption_count"),
    )
    metrics = []

    def metric(key, label, value, unit="number", measured=None, note=""):
        metrics.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "unit": unit,
                "measured": measured,
                "total": total,
                "note": note,
            }
        )

    is_voice = noun == "call"
    metric("total", f"Total {noun}s", total, measured=total)
    metric(
        "connected",
        f"{noun.capitalize()}s connected",
        values["connected"],
        measured=total,
        note=f"{noun.capitalize()}s with recorded conversation evidence",
    )
    metric(
        "connected_rate",
        f"{noun.capitalize()}s connected (%)",
        round(values["connected"] * 100 / total, 2) if total else None,
        "percent",
        total,
    )
    metric("csat", "Avg CSAT score", values["csat"], measured=values["csat_measured"])
    metric(
        "agent_latency",
        "Agent latency" if is_voice else "Agent response time",
        summary["latency"]["average"],
        "ms",
        summary["latency"]["measured"],
    )
    if is_voice:
        metric(
            "wpm",
            "Agent WPM",
            voice_values["wpm"],
            measured=voice_values["wpm_measured"],
        )
        metric(
            "stop",
            "Agent stop latency",
            voice_values["stop"],
            "ms",
            voice_values["stop_measured"],
        )
        metric(
            "talk",
            "Talk ratio (agent/user)",
            voice_values["talk"],
            "ratio",
            voice_values["talk_measured"],
            "Mean agent/customer speaking share for measured voice calls",
        )
    metric(
        "duration",
        f"Avg {noun} duration",
        summary["duration"]["average"],
        "seconds",
        summary["duration"]["measured"],
    )
    metric(
        "turns",
        f"Avg turns/{noun}",
        values["turns"],
        measured=values["turns_measured"],
    )
    duration_stats = _stats(
        queryset,
        {
            "duration_seconds": "duration_seconds",
            "tokens": "result_tokens",
            "cost_cents": "cost_cents",
            "turns": "result_turn_count",
        },
    )
    metric(
        "latency_p90",
        f"{noun.capitalize()} duration p90",
        (
            duration_stats["duration_seconds"]["p90"] * 1000
            if duration_stats["duration_seconds"]["p90"] is not None
            else None
        ),
        "ms",
        duration_stats["duration_seconds"]["measured"],
        "End-to-end task wall-clock time",
    )
    passed = summary["outcomes"]["passed"]
    cost = summary["cost_cents"]
    metric(
        "cost_per_pass",
        "Cost / pass",
        cost["total_value"] / passed if passed and cost["measured"] == total else None,
        "cents",
        cost["measured"],
        "Total run cost divided by successful tasks; requires complete cost coverage",
    )
    metric("total_cost", "Total cost", cost["total_value"], "cents", cost["measured"])

    slos = {
        "ttfw": "Time to first word",
        "model": "LLM response",
        "voice": "Text-to-speech",
        "transcriber": "Speech recognition",
    }
    voice = voice.annotate(
        **{
            f"slo_{key}": Coalesce(
                _safe_json_float("customer_latency_metrics", "systemMetrics", key),
                _safe_json_float("customer_latency_metrics", key),
            )
            for key in slos
        }
    )
    slo_stats = _stats(voice, {key: f"slo_{key}" for key in slos})
    curve = queryset.aggregate(
        **{f"p{p}": PercentileCont("duration_seconds", p / 100) for p in range(101)}
    )
    costs = queryset.aggregate(
        **{
            key: Sum(field)
            for key, field in {
                "llm": "llm_cost_cents",
                "tts": "tts_cost_cents",
                "stt": "stt_cost_cents",
                "storage": "storage_cost_cents",
            }.items()
        }
    )
    component_total = sum(value or 0 for value in costs.values())
    eval_passed = sum(row["passed"] for row in evaluations)
    eval_measured = sum(row["measured"] for row in evaluations)
    return {
        "csat": csat_distribution(queryset, total),
        "agent_response_time": response_time_distribution(queryset, total),
        "pipeline_cost": [
            {
                "key": key,
                "label": label,
                "total_cents": costs[key],
                "share": (
                    round(costs[key] * 100 / component_total, 2)
                    if costs[key] is not None and component_total
                    else None
                ),
            }
            for key, label in [
                ("llm", "LLM"),
                ("tts", "TTS"),
                ("stt", "STT"),
                ("storage", "Storage"),
            ]
        ],
        "evaluation_summary": {
            "graders": len(evaluations),
            "passed": eval_passed,
            "measured": eval_measured,
            "pass_rate": (
                round(eval_passed * 100 / eval_measured, 2) if eval_measured else None
            ),
        },
        "use_case_risk": [
            {
                "goal": row["goal"],
                "passed": row["outcomes"]["passed"],
                "failed": row["outcomes"]["failed"],
                "error": row["outcomes"]["error"],
                "inconclusive": row["outcomes"]["inconclusive"],
            }
            for row in risk[:7]
        ],
        "goal_count": len(risk),
        "metrics": metrics,
        "breakdowns": [
            _breakdown(
                queryset, "dashboard_success", "call_success", "Call successful", total
            ),
            _breakdown(
                queryset,
                "dashboard_goal",
                "goal_outcome",
                "Goal outcome breakdown",
                total,
            ),
            _breakdown(
                queryset, "dashboard_sentiment", "sentiment", "User sentiment", total
            ),
            _breakdown(
                queryset,
                "dashboard_disconnection",
                "disconnection",
                "Disconnection reason",
                total,
            ),
        ],
        "voice_slos": [
            {"key": key, "label": label, **slo_stats[key]}
            for key, label in slos.items()
        ],
        "interruptions": {
            "total": voice_values["interruptions"],
            "measured": voice_values["interruptions_measured"],
            "average": (
                voice_values["interruptions"] / voice_values["interruptions_measured"]
                if voice_values["interruptions_measured"]
                else None
            ),
        },
        "series": _series(queryset, total),
        "series_mode": "calls" if total <= CHART_BUCKETS else "time_buckets",
        "series_limit": CHART_BUCKETS,
        "latency_percentiles": [
            {
                "percentile": p,
                "value": curve[f"p{p}"] * 1000 if curve[f"p{p}"] is not None else None,
            }
            for p in range(101)
        ],
        "distributions": [
            {
                "key": "end_to_end_ms",
                **{
                    key: value if key == "measured" or value is None else value * 1000
                    for key, value in duration_stats["duration_seconds"].items()
                },
            },
            *[{"key": key, **stats} for key, stats in duration_stats.items()],
        ],
        "tools": _tool_stats(queryset),
        "slowest_tasks": _tails(queryset, "duration_seconds"),
        "most_expensive_tasks": _tails(queryset, "cost_cents"),
        "unavailable_features": [
            {
                "key": "failure_attribution",
                "reason": "Error Feed failure domains and retry policies are not recorded on simulation calls.",
            },
            {
                "key": "critical_failures",
                "reason": "Error Feed criticality and release-blocker classifications are not recorded on simulation calls.",
            },
            {
                "key": "asr_word_error_rate",
                "reason": "No reference transcription or word-error measurements are recorded.",
            },
            {
                "key": "ttfw",
                "reason": "Time-to-first-word telemetry is not currently emitted; the widget remains unmeasured until it is recorded.",
            },
            {
                "key": "transport_cost",
                "reason": "No distinct transport cost component is recorded; storage cost is shown separately.",
            },
        ],
    }
