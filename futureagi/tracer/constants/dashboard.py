"""Shared dashboard query contract constants.

Keep serializer inference and ClickHouse validation on the same aggregation
classification so a request cannot be accepted as text and fail later in the
query builder.
"""

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
