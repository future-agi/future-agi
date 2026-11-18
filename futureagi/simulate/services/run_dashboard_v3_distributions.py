"""Server-side score and response-time distributions for run dashboards."""

from collections import Counter
from math import ceil, floor
from typing import Any

from django.conf import settings
from django.db.models import Count, Max, Min, Q

from simulate.services.run_results_v3_expressions import PercentileCont


def response_target_ms(*, voice: bool) -> int:
    """Response-time target (metric list v1): 1.5 s for voice, 3 s for chat."""
    name = (
        "SIMULATION_VOICE_RESPONSE_TARGET_MS"
        if voice
        else "SIMULATION_CHAT_RESPONSE_TARGET_MS"
    )
    return int(getattr(settings, name, 1500 if voice else 3000))


def csat_scalar_expressions() -> dict[str, Any]:
    """Scalar CSAT aggregates, merged into the dashboard's single aggregate."""
    compared = Q(
        dashboard_provider_success__in=["true", "false"],
        result_eval_outcome__in=["passed", "failed"],
    )
    return {
        "csat_satisfied": Count("id", filter=Q(dashboard_csat__gte=8)),
        "csat_compared": Count("id", filter=compared),
        "csat_agreed": Count(
            "id",
            filter=compared
            & (
                Q(dashboard_provider_success="true", result_eval_outcome="passed")
                | Q(dashboard_provider_success="false", result_eval_outcome="failed")
            ),
        ),
    }


def csat_distribution(
    call_rows: list[dict[str, Any]], total: int, scalars: dict[str, Any]
) -> dict[str, Any]:
    # FLOOR(csat + 0.5) in float8, as Python floats compute it.
    counts = Counter(
        floor(row["dashboard_csat"] + 0.5)
        for row in call_rows
        if row["dashboard_csat"] is not None
    )
    satisfied = scalars["csat_satisfied"]
    comparison = {
        "compared": scalars["csat_compared"],
        "agreed": scalars["csat_agreed"],
    }
    return {
        "bins": [
            {
                "label": str(score),
                "lower": max(0, score - 0.5),
                "upper": min(10, score + 0.5),
                "count": counts.get(score, 0),
                "danger": score <= 4,
            }
            for score in range(11)
        ],
        "measured": sum(counts.values()),
        "total": total,
        "satisfied": satisfied,
        "satisfied_percent": (
            round(satisfied * 100 / sum(counts.values()), 2) if counts else None
        ),
        "agreement": {
            **comparison,
            "percent": (
                round(comparison["agreed"] * 100 / comparison["compared"], 2)
                if comparison["compared"]
                else None
            ),
        },
    }


def response_time_scalar_expressions() -> dict[str, Any]:
    """Scalar response-time aggregates for both targets; the modality picks one."""
    measured = Q(result_latency_ms__gte=0)
    return {
        "response_measured": Count("id", filter=measured),
        "response_minimum": Min("result_latency_ms", filter=measured),
        "response_maximum": Max("result_latency_ms", filter=measured),
        "response_p50": PercentileCont("result_latency_ms", 0.5, filter=measured),
        "response_p95": PercentileCont("result_latency_ms", 0.95, filter=measured),
        **{
            f"response_at_or_above_{modality}": Count(
                "id",
                filter=measured
                & Q(result_latency_ms__gte=response_target_ms(voice=is_voice)),
            )
            for modality, is_voice in (("voice", True), ("chat", False))
        },
    }


def response_time_distribution(
    call_rows: list[dict[str, Any]], total: int, scalars: dict[str, Any], *, voice: bool
) -> dict[str, Any]:
    target_ms = response_target_ms(voice=voice)
    stats = {
        "measured": scalars["response_measured"],
        "minimum": scalars["response_minimum"],
        "maximum": scalars["response_maximum"],
        "p50": scalars["response_p50"],
        "p95": scalars["response_p95"],
        "at_or_above_target": scalars[
            f"response_at_or_above_{'voice' if voice else 'chat'}"
        ],
    }
    # Keep the target on a bin boundary and the payload bounded for long tails.
    width = max(25, ceil((stats["maximum"] or 0) / 1000) * 25)

    def bucket(latency: float) -> int:
        if latency < target_ms:
            return floor(latency / float(width)) * width
        return target_ms + floor((latency - target_ms) / float(width)) * width

    counts = Counter(
        bucket(row["result_latency_ms"])
        for row in call_rows
        if row["result_latency_ms"] is not None and row["result_latency_ms"] >= 0
    )
    bounds = [*range(0, target_ms, width)]
    bounds.extend(range(target_ms, int(stats["maximum"] or 0) + width, width))
    bins = []
    for lower in bounds:
        upper = min(lower + width, target_ms) if lower < target_ms else lower + width
        if (
            stats["minimum"] is None
            or upper <= stats["minimum"]
            or lower > stats["maximum"]
        ):
            continue
        bins.append(
            {
                "label": f"{lower}ms",
                "lower": lower,
                "upper": upper,
                "count": counts.get(lower, 0),
                "danger": lower >= target_ms,
            }
        )
    return {
        "bins": bins,
        "total": total,
        "measured": stats["measured"],
        "target_ms": target_ms,
        "p50": stats["p50"],
        "p95": stats["p95"],
        "at_or_above_target": stats["at_or_above_target"],
        "at_or_above_target_percent": (
            round(stats["at_or_above_target"] * 100 / stats["measured"], 2)
            if stats["measured"]
            else None
        ),
    }
