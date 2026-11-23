"""Server-side score and response-time distributions for run dashboards."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from math import ceil, floor
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from simulate.services.run_dashboard_v3 import DashboardCallRow


def response_target_ms(*, voice: bool) -> int:
    """Response-time target (metric list v1): 1.5 s for voice, 3 s for chat."""
    name = (
        "SIMULATION_VOICE_RESPONSE_TARGET_MS"
        if voice
        else "SIMULATION_CHAT_RESPONSE_TARGET_MS"
    )
    return int(getattr(settings, name, 1500 if voice else 3000))


def _percentile_cont(ordered: Sequence[float], fraction: float) -> float | None:
    """PostgreSQL ``percentile_cont`` over float8 values sorted ascending."""
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    first, second = floor(position), ceil(position)
    if first == second:
        return ordered[first]
    # float8_lerp: lo + (pct * (hi - lo)).
    low = ordered[first]
    return low + ((position - first) * (ordered[second] - low))


def csat_distribution(
    call_rows: Sequence[DashboardCallRow], total: int
) -> dict[str, Any]:
    # Bins, satisfied and agreement share one read (the same call rows), not one
    # population: bins round, satisfied reads raw scores, and agreement counts
    # only comparable pairs.
    scores = [
        row["dashboard_csat"] for row in call_rows if row["dashboard_csat"] is not None
    ]
    # FLOOR(csat + 0.5) in float8, as Python floats compute it.
    counts = Counter(floor(score + 0.5) for score in scores)
    satisfied = sum(score >= 8 for score in scores)
    verdicts = [
        (row["dashboard_provider_success"], row["result_eval_outcome"])
        for row in call_rows
        if row["dashboard_provider_success"] in ("true", "false")
        and row["result_eval_outcome"] in ("passed", "failed")
    ]
    comparison = {
        "compared": len(verdicts),
        "agreed": sum(
            verdict in (("true", "passed"), ("false", "failed")) for verdict in verdicts
        ),
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


def response_time_distribution(
    call_rows: Sequence[DashboardCallRow], total: int, *, voice: bool
) -> dict[str, Any]:
    target_ms = response_target_ms(voice=voice)
    # Stats, bin bounds and bin counts all read the same rows.
    latencies = sorted(
        row["result_latency_ms"]
        for row in call_rows
        if row["result_latency_ms"] is not None and row["result_latency_ms"] >= 0
    )
    stats: dict[str, Any] = {
        "measured": len(latencies),
        "minimum": latencies[0] if latencies else None,
        "maximum": latencies[-1] if latencies else None,
        "p50": _percentile_cont(latencies, 0.5),
        "p95": _percentile_cont(latencies, 0.95),
        "at_or_above_target": sum(latency >= target_ms for latency in latencies),
    }
    # Keep the target on a bin boundary and the payload bounded for long tails.
    width = max(25, ceil((stats["maximum"] or 0) / 1000) * 25)

    def bucket(latency: float) -> int:
        if latency < target_ms:
            return floor(latency / float(width)) * width
        return target_ms + floor((latency - target_ms) / float(width)) * width

    counts = Counter(bucket(latency) for latency in latencies)
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
