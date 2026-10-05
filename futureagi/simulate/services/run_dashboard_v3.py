"""Bounded dashboard read models built from recorded simulation evidence."""

from __future__ import annotations

import json
from collections import defaultdict
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

from simulate.services.run_dashboard_v3_distributions import (
    csat_distribution,
    response_time_distribution,
)
from simulate.services.run_results_v3_expressions import (
    PercentileCont,
    _json_text,
    _safe_json_float,
)

CHART_BUCKETS = 100
NOT_REPORTED = "Not reported"
# Provider-agnostic end reasons (metric list v1 §5.1), first match wins. The hosted ALK
# reasons are listed explicitly: none of them appear in any provider's vocabulary.
END_REASONS = [
    ("Caller hung up", r"(during|before|after).*(warm.?transfer)"),
    ("Error", r"transfer.?(cancel|fail)|warm.?transfer.*(fail|error)"),
    (
        "Error",
        r"error|fail|unavailable|^sip-|twilio|busy|no-answer|did-not-answer|dial|"
        r"invalid|not-connected|not-found|join-timed-out|microphone-permission|"
        r"concurrency|payment|websocket|shutdown|scam|call-timeout|cancel|"
        r"room-deleted|spam|user-declined",
    ),
    ("Voicemail", r"voicemail|ivr|machine"),
    ("Transferred", r"transfer|forward"),
    ("Silence timeout", r"silence|inactivity|idle"),
    (
        "Time or turn limit",
        r"max.?duration|exceeded-max|duration-limit|max.?turns|conversation_timeout",
    ),
    ("Simulator ended", r"^simulator[-_]end(?:ed)?[-_]call$"),
    ("Agent ended", r"^(?:target|assistant)[-_]end(?:ed)?[-_]call$"),
    ("Agent disconnected", r"^target[-_]disconnected$"),
    ("Session closed", r"^(?:session[-_]closed|close[-_]on[-_]disconnect)$"),
    ("Disconnected", r"^(?:participant|room|provider)[-_]disconnected$"),
    (
        "Caller hung up",
        r"customer|user|caller|persona|client|human-ended|hangup-by-user",
    ),
    ("Agent ended", r"assistant|agent|end-call"),
    (
        "Completed",
        r"complete|done|script-completed|outcome_satisfied|^closing[-_]loop$",
    ),
]


def annotate_goal_outcome(queryset: QuerySet) -> QuerySet:
    """Use the same chart outcome for aggregation and call drill-downs."""
    end_reason = Case(
        *[
            When(ended_reason__iregex=pattern, then=Value(label))
            for label, pattern in END_REASONS
        ],
        When(
            Q(ended_reason__isnull=True) | Q(ended_reason=""),
            then=Value(NOT_REPORTED),
        ),
        default=Value("Unrecognised"),
        output_field=TextField(),
    )
    return queryset.annotate(dashboard_disconnection=end_reason).annotate(
        dashboard_goal=Case(
            When(
                Q(call_metadata__harness_outcome_status__in=["escalated", "handoff"])
                | Q(dashboard_disconnection="Transferred"),
                then=Value("escalated"),
            ),
            default=F("result_outcome"),
            output_field=TextField(),
        )
    )


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
            "label": str(row[field] or NOT_REPORTED),
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
    order = {
        "goal_outcome": ["passed", "failed", "error", "escalated", "inconclusive"],
    }.get(key)
    if order:
        by_label = {segment["label"]: segment for segment in segments}
        segments = [
            by_label.get(value, {"label": value, "count": 0, "share": 0})
            for value in order
        ]
    if key == "goal_outcome":
        for segment in segments:
            if segment["label"] in order:
                segment["statuses"] = [segment["label"]]
    preferred = {"goal_outcome": "passed"}.get(key)
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
                f"{round(count['failures'] * 100 / count['measured'])}% · {count['failures']}/{count['measured']}"
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
                latency_ms=Case(
                    When(result_latency_ms__gte=0, then=F("result_latency_ms")),
                    output_field=FloatField(),
                ),
                duration_ms=F("duration_seconds") * 1000.0,
                llm_cents=F("llm_cost_cents"),
                tts_cents=F("tts_cost_cents"),
                stt_cents=F("stt_cost_cents"),
                storage_cents=F("storage_cost_cents"),
            )
            .values(
                "started_at",
                "latency_ms",
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
            latency_ms=Avg("result_latency_ms", filter=Q(result_latency_ms__gte=0)),
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
        .values(
            "id", "result_scenario", "simulation_call_type", "result_provider", field
        )[:8]
    )
    return [
        {
            "rank": index + 1,
            "id": str(row["id"]),
            "label": row["result_scenario"] or "Untitled scenario",
            "axis_label": f"#{index + 1} {(row['result_scenario'] or 'Untitled scenario')[:20]}",
            "value": row[field],
            "modality": row["simulation_call_type"],
            "provider": row["result_provider"],
        }
        for index, row in enumerate(rows)
    ]


def build_run_dashboard(
    queryset: QuerySet,
    summary: dict,
    evaluations: list,
    risk: list,
    reliability: dict | None = None,
    comparison: dict | None = None,
) -> dict[str, Any]:
    total = summary["total"]
    queryset = annotate_goal_outcome(queryset).annotate(
        # Provider-reported only: the platform does not compute sentiment (v1 D1).
        dashboard_sentiment=Lower(
            Coalesce(
                _json_text("analysis_data", "user_sentiment"),
                _json_text(
                    "provider_call_data",
                    "retell",
                    "call_analysis",
                    "user_sentiment",
                ),
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
            )
        ),
    ).annotate(
        dashboard_provider_success=Case(
            When(
                dashboard_success_raw__in=["true", "false"],
                then=F("dashboard_success_raw"),
            ),
            default=None,
            output_field=TextField(),
        ),
        dashboard_csat=F("result_csat"),
    )
    voice = queryset.filter(simulation_call_type="voice")
    # A run is one modality in practice; a mixed run counts as voice.
    noun = "chat" if total and not voice.exists() else "call"
    script_completed = Q(
        call_metadata__hosted_harness_receipt__call__script_completed=True
    ) | Q(call_metadata__hosted_harness_receipt__call__script_completed=False)
    values = queryset.aggregate(
        csat=Avg("dashboard_csat"),
        csat_measured=Count("dashboard_csat"),
        turns=Avg("result_turn_count"),
        turns_measured=Count("result_turn_count"),
        cost_passed=Count(
            "id", filter=Q(result_cost_cents__isnull=False, result_outcome="passed")
        ),
        connected=Count(
            "id", filter=Q(message_count__gt=0) | Q(transcript_available=True)
        ),
        drop_off_measured=Count(
            "id", filter=script_completed & Q(result_outcome__in=["passed", "failed"])
        ),
        drop_off=Count(
            "id",
            filter=Q(
                call_metadata__hosted_harness_receipt__call__script_completed=False,
                dashboard_disconnection__in=["Caller hung up", "Simulator ended"],
                result_outcome="failed",
            ),
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
    outcomes = summary["outcomes"]
    interval = (reliability or {}).get("pass_rate_interval")
    metric(
        "pass_rate",
        f"{noun.capitalize()}s passed",
        summary["pass_rate"],
        "percent",
        summary["measured"],
        (
            f"Passed every eval, out of evaluated {noun}s. 95% range "
            f"{interval['low']:.0f}–{interval['high']:.0f}% across "
            f"{interval['clusters']} scenarios."
            if interval
            else f"Passed every eval, out of evaluated {noun}s. Errored {noun}s are excluded."
        ),
    )
    metric("total", f"Total {noun}s", total, measured=total)
    metric(
        "ran_cleanly",
        f"{noun.capitalize()}s ran cleanly",
        round((total - outcomes["error"]) * 100 / total, 2) if total else None,
        "percent",
        total,
        f"{noun.capitalize()}s that did not error; errors are infrastructure, not the agent",
    )
    metric(
        "connected_rate",
        f"{noun.capitalize()}s connected (%)",
        round(values["connected"] * 100 / total, 2) if total else None,
        "percent",
        total,
        f"{noun.capitalize()}s with recorded conversation evidence",
    )
    metric(
        "drop_off",
        "Drop-off",
        (
            round(values["drop_off"] * 100 / values["drop_off_measured"], 2)
            if values["drop_off_measured"]
            else None
        ),
        "percent",
        values["drop_off_measured"],
        "Caller ended before script completion and failed evaluation; "
        "calls without completion evidence are unmeasured",
    )
    metric(
        "csat",
        "Avg CSAT (0–10)",
        values["csat"],
        measured=values["csat_measured"],
        note="Scorer CSAT on a 0–10 scale; a provider success flag is never counted",
    )
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
            "Agent share of talk time",
            voice_values["talk"],
            "percent",
            voice_values["talk_measured"],
            "Agent speaking time ÷ (agent + caller) speaking time, averaged over calls",
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
            "cost_cents": "result_cost_cents",
            "turns": "result_turn_count",
        },
    )
    metric(
        "duration_p90",
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
    cost = summary["cost_cents"]
    metric(
        "cost_per_pass",
        "Cost / pass",
        (
            cost["total_value"] / values["cost_passed"]
            if values["cost_passed"] and cost["total_value"] is not None
            else None
        ),
        "cents",
        cost["measured"],
        (
            "Target-agent provider cost divided by passing calls among calls that reported cost; "
            f"{cost['measured']} of {total} reported"
        ),
    )
    metric("total_cost", "Total cost", cost["total_value"], "cents", cost["measured"])

    # Time to first word is not emitted by any runner today, so it is listed as
    # unavailable instead of shown as an always-empty row.
    slos = {
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
    latency = queryset.filter(result_latency_ms__gte=0).aggregate(
        measured=Count("id"),
        average=Avg("result_latency_ms"),
        max=Max("result_latency_ms"),
        **{f"p{p}": PercentileCont("result_latency_ms", p / 100) for p in range(101)},
    )
    # Call length under the earlier keys, for frontend builds that still read them.
    duration_curve = queryset.aggregate(
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
    eval_errored = sum(row["errored"] for row in evaluations)
    ran_cleanly = total - outcomes["error"]
    run_health = {
        "show_banner": bool(
            total
            and (
                ran_cleanly * 100 < total * 99
                or values["connected"] * 100 < total * 99
                or outcomes["inconclusive"]
                or eval_errored
            )
        ),
        "attempted": total,
        "ran_cleanly": ran_cleanly,
        "connected": values["connected"],
        "errored": outcomes["error"],
        "not_evaluated": outcomes["inconclusive"],
        "eval_errors": eval_errored,
    }
    return {
        "run_health": run_health,
        "comparison": comparison
        or {
            "available": False,
            "previous_execution_id": None,
            "shared_scenarios": 0,
            "newly_passing": [],
            "newly_failing": [],
        },
        "csat": csat_distribution(queryset, total),
        "agent_response_time": response_time_distribution(
            queryset, total, voice=is_voice
        ),
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
            # Call level: a call passes only when every eval that ran on it passed.
            "passed": outcomes["passed"],
            "measured": summary["measured"],
            "pass_rate": summary["pass_rate"],
            "errored_checks": eval_errored,
        },
        "use_case_risk": [
            {
                "scenario": row["scenario"],
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
                queryset,
                "dashboard_goal",
                "goal_outcome",
                "Goal outcome breakdown",
                total,
            ),
            _breakdown(
                queryset,
                "dashboard_disconnection",
                "disconnection",
                "How calls ended" if is_voice else "How chats ended",
                total,
            ),
            # Provider-only verdicts appear only when some call reports them.
            *[
                _breakdown(queryset, field, key, label, total)
                for field, key, label in [
                    (
                        "dashboard_provider_success",
                        "provider_success",
                        "Provider's own success flag",
                    ),
                    ("dashboard_sentiment", "sentiment", "Provider sentiment"),
                ]
                if queryset.filter(**{f"{field}__isnull": False}).exists()
            ],
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
        "agent_latency_percentiles": [
            {"percentile": p, "value": latency[f"p{p}"]} for p in range(101)
        ],
        "latency_percentiles": [
            {
                "percentile": p,
                "value": (
                    duration_curve[f"p{p}"] * 1000
                    if duration_curve[f"p{p}"] is not None
                    else None
                ),
            }
            for p in range(101)
        ],
        "distributions": [
            {
                "key": "latency_ms",
                **{
                    key: latency[key]
                    for key in ("measured", "average", "max", "p50", "p90", "p99")
                },
            },
            *[{"key": key, **stats} for key, stats in duration_stats.items()],
            {
                "key": "end_to_end_ms",
                **{
                    key: value if key == "measured" or value is None else value * 1000
                    for key, value in duration_stats["duration_seconds"].items()
                },
            },
        ],
        "tools": _tool_stats(queryset),
        "slowest_tasks": _tails(queryset, "duration_seconds"),
        "most_expensive_tasks": _tails(queryset, "result_cost_cents"),
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
                "reason": "Time-to-first-word telemetry is not emitted by any runner yet.",
            },
            {
                "key": "transport_cost",
                "reason": "No distinct transport cost component is recorded; storage cost is shown separately.",
            },
        ],
    }
