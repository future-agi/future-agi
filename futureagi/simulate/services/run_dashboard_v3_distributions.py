"""Server-side score and response-time distributions for run dashboards."""

from math import ceil
from typing import Any

from django.conf import settings
from django.db.models import (
    Case,
    Count,
    F,
    FloatField,
    Max,
    Min,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.functions import Floor

from simulate.services.run_results_v3_expressions import PercentileCont


def response_target_ms(*, voice: bool) -> int:
    """Response-time target (metric list v1): 1.5 s for voice, 3 s for chat."""
    name = (
        "SIMULATION_VOICE_RESPONSE_TARGET_MS"
        if voice
        else "SIMULATION_CHAT_RESPONSE_TARGET_MS"
    )
    return int(getattr(settings, name, 1500 if voice else 3000))


def csat_distribution(queryset: QuerySet, total: int) -> dict[str, Any]:
    counts = dict(
        queryset.filter(dashboard_csat__isnull=False)
        .order_by()
        .annotate(score=Floor(F("dashboard_csat") + 0.5))
        .values("score")
        .annotate(count=Count("id"))
        .values_list("score", "count")
    )
    satisfied = queryset.filter(dashboard_csat__gte=8).count()
    comparison = queryset.filter(
        dashboard_provider_success__in=["true", "false"],
        result_eval_outcome__in=["passed", "failed"],
    ).aggregate(
        compared=Count("id"),
        agreed=Count(
            "id",
            filter=(
                Q(dashboard_provider_success="true", result_eval_outcome="passed")
                | Q(dashboard_provider_success="false", result_eval_outcome="failed")
            ),
        ),
    )
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
    queryset: QuerySet, total: int, *, voice: bool
) -> dict[str, Any]:
    target_ms = response_target_ms(voice=voice)
    measured = queryset.filter(result_latency_ms__gte=0)
    stats = measured.aggregate(
        measured=Count("id"),
        minimum=Min("result_latency_ms"),
        maximum=Max("result_latency_ms"),
        p50=PercentileCont("result_latency_ms", 0.5),
        p95=PercentileCont("result_latency_ms", 0.95),
        at_or_above_target=Count("id", filter=Q(result_latency_ms__gte=target_ms)),
    )
    # Keep the target on a bin boundary and the payload bounded for long tails.
    width = max(25, ceil((stats["maximum"] or 0) / 1000) * 25)
    bucket = Case(
        When(
            result_latency_ms__lt=target_ms,
            then=Floor(F("result_latency_ms") / float(width)) * width,
        ),
        default=Value(target_ms)
        + Floor((F("result_latency_ms") - target_ms) / float(width)) * width,
        output_field=FloatField(),
    )
    counts = dict(
        measured.order_by()
        .annotate(bucket=bucket)
        .values("bucket")
        .annotate(count=Count("id"))
        .values_list("bucket", "count")
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
