"""Which statistic each published Observe system-metric series is.

Every graph response names the statistic of its series in
``metric_statistic`` so the UI can label it ("Latency (median)") instead of
guessing from the metric name. Latency is always the t-digest median
(``latency_statistic.LATENCY_STATISTIC``). The maps mirror what each builder
computes per bucket and resolve a metric id the same way the dispatcher
resolves the series it publishes, including its fallbacks for unknown ids.
Eval and annotation series carry no ``metric_statistic``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

from tracer.services.clickhouse.query_builders.latency_statistic import (
    LATENCY_STATISTIC,
)

METRIC_STATISTIC_CHOICES = ("count", "sum", "mean", "median", "percentage")

_TOKEN_SUMS = dict.fromkeys(
    (
        "tokens",
        "total_tokens",
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
    ),
    "sum",
)

# Trace and span graphs (``TimeSeriesQueryBuilder.format_result``), also the
# series of the project ChartsView bundle.
TRACE_METRIC_STATISTICS: Mapping[str, str] = {
    "latency": LATENCY_STATISTIC,
    **_TOKEN_SUMS,
    "traffic": "count",
    "cost": "mean",
    "error_rate": "percentage",
}
SESSION_METRIC_STATISTICS: Mapping[str, str] = {
    **TRACE_METRIC_STATISTICS,
    "session_count": "count",
    "total_cost": "sum",
    "avg_duration": "mean",
    "avg_traces_per_session": "mean",
}
USER_METRIC_STATISTICS: Mapping[str, str] = {
    **TRACE_METRIC_STATISTICS,
    "active_users": "count",
    "total_cost": "sum",
    "avg_cost_per_user": "mean",
    "avg_traces_per_user": "mean",
}

# surface -> (statistics, series published for an unknown metric id)
_SURFACES: dict[str, tuple[Mapping[str, str], str | None]] = {
    # ``graph_dispatch._resolved_system_metric``: unknown ids publish latency.
    "trace": (TRACE_METRIC_STATISTICS, "latency"),
    # ``fetch_session_graph_ch`` rejects unknown ids.
    "session": (SESSION_METRIC_STATISTICS, None),
    # ``read_exact_user_system_graph``: unknown ids publish active_users.
    "users": (USER_METRIC_STATISTICS, "active_users"),
}

# The four series of the project ChartsView bundle.
CHART_BUNDLE_METRICS = ("latency", "tokens", "cost", "traffic")


def system_metric_statistic(surface: str, metric_id: Any) -> str | None:
    """The statistic of the series a system-metric request publishes."""

    statistics, fallback = _SURFACES[surface]
    if surface == "trace":
        # The trace dispatcher normalizes the id; the others match it exactly.
        key = str(metric_id or "latency").strip().lower()
    else:
        key = str(metric_id or "")
    if key in statistics:
        return statistics[key]
    return statistics.get(fallback) if fallback else None


def chart_bundle_statistics() -> dict[str, str]:
    """``{series: statistic}`` for the project ChartsView bundle."""

    return {key: TRACE_METRIC_STATISTICS[key] for key in CHART_BUNDLE_METRICS}


def with_metric_statistic(payload: Any, surface: str, metric_id: Any) -> Any:
    """Return ``payload`` stamped with its series' ``metric_statistic``."""

    if not isinstance(payload, dict):
        return payload
    statistic = system_metric_statistic(surface, metric_id)
    if statistic is None:
        return payload
    return {**payload, "metric_statistic": statistic}


def stamps_metric_statistic(
    surface: str,
    metric_of: Callable[[dict[str, Any]], Any],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Stamp every envelope a keyword-only graph entry point returns.

    Stamping at the public boundary covers the complete, cached, pending,
    degraded and refused envelopes alike, so the label never flickers while a
    series loads. ``metric_of`` maps the call's keyword arguments to the
    requested metric id, or to ``None`` when the call is not a system metric.
    """

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            payload = function(*args, **kwargs)
            metric_id = metric_of(kwargs)
            if metric_id is None:
                return payload
            return with_metric_statistic(payload, surface, metric_id)

        return wrapper

    return decorate


__all__ = [
    "CHART_BUNDLE_METRICS",
    "METRIC_STATISTIC_CHOICES",
    "SESSION_METRIC_STATISTICS",
    "TRACE_METRIC_STATISTICS",
    "USER_METRIC_STATISTICS",
    "chart_bundle_statistics",
    "stamps_metric_statistic",
    "system_metric_statistic",
    "with_metric_statistic",
]
