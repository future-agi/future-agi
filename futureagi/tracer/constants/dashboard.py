"""Shared dashboard query contract constants.

Keep serializer inference and ClickHouse validation on the same aggregation
classification so a request cannot be accepted as text and fail later in the
query builder.
"""

from uuid import UUID

DASHBOARD_AGGREGATIONS = (
    "avg",
    "median",
    "max",
    "min",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "count",
    "count_distinct",
    "sum",
    "pass_rate",
    "fail_rate",
    "pass_count",
    "fail_count",
    "true_rate",
)

DASHBOARD_NUMERIC_ONLY_AGGREGATIONS = frozenset(
    {
        "avg",
        "sum",
        "median",
        "min",
        "max",
        "p25",
        "p50",
        "p75",
        "p90",
        "p95",
        "p99",
    }
)

TEXT_ANNOTATION_AGGREGATIONS = ("count", "count_distinct")


def text_annotation_aggregation_error(output_type: str, aggregation: str) -> str | None:
    """Do not silently substitute Count for a requested text operation."""
    if (
        output_type or ""
    ).lower() == "text" and aggregation not in TEXT_ANNOTATION_AGGREGATIONS:
        return "Text annotations support only count or count distinct."
    return None


def annotation_breakdown_error(
    metrics: list[dict], breakdowns: list[dict]
) -> str | None:
    """Validate trace-adapter annotation grouping without I/O or mutation.

    Dataset/simulation capabilities belong to their own adapters. Empty
    breakdowns preserve the existing ungrouped contract, including legacy
    identities. Canonical label UUIDs, never display names, bind grouped Scores.
    """
    if not breakdowns:
        return None
    annotations = [
        metric
        for metric in metrics
        if metric.get("type") == "annotation_metric"
        and metric.get("source", "traces") not in ("datasets", "simulation")
    ]
    if not annotations:
        return None
    if len(breakdowns) != 1 or breakdowns[0].get("type") != "annotation_metric":
        return "Annotation metrics support exactly one same-label annotation breakdown."
    breakdown = breakdowns[0]
    trace_sources = ("traces", "all", "both", "")
    for metric in annotations:
        if (
            metric.get("source", "traces") not in trace_sources
            or breakdown.get("source", "traces") not in trace_sources
        ):
            return "Annotation metric breakdowns require trace-compatible sources."
        try:
            metric_label = UUID(str(metric.get("label_id") or metric.get("name", "")))
            breakdown_label = UUID(
                str(breakdown.get("label_id") or breakdown.get("name", ""))
            )
        except (TypeError, ValueError, AttributeError):
            return "Annotation metric breakdowns require valid annotation label UUIDs."
        if metric_label != breakdown_label:
            return (
                "Annotation metrics can only be grouped by the same annotation label."
            )
    return None
