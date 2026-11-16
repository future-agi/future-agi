"""Which statistic each published Observe system-metric series is.

Every graph response names the statistic of its series in
``metric_statistic`` so the UI can label it ("Latency (avg, ms)") instead of
guessing from the metric name. Latency is always the arithmetic mean of span
``latency_ms`` (``LATENCY_STATISTIC``), on every graph path, filtered or not.
The maps mirror what each builder computes per bucket and resolve a metric id
the same way the dispatcher resolves the series it publishes, including its
fallbacks for unknown ids. Eval and annotation series carry no
``metric_statistic``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any, Literal, ParamSpec, TypeVar, cast

Surface = Literal["trace", "session", "users"]
_P = ParamSpec("_P")
_R = TypeVar("_R")

# The one statistic every Observe latency series publishes.
LATENCY_STATISTIC = "mean"
LATENCY_METRIC = "latency"

METRIC_STATISTIC_CHOICES = ("count", "sum", "mean", "percentage")

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
    LATENCY_METRIC: LATENCY_STATISTIC,
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
_SURFACES: dict[Surface, tuple[Mapping[str, str], str | None]] = {
    # ``graph_dispatch._resolved_system_metric``: unknown ids publish latency.
    "trace": (TRACE_METRIC_STATISTICS, "latency"),
    # ``fetch_session_graph_ch`` rejects unknown ids.
    "session": (SESSION_METRIC_STATISTICS, None),
    # ``read_exact_user_system_graph``: unknown ids publish active_users.
    "users": (USER_METRIC_STATISTICS, "active_users"),
}

# The four series of the project ChartsView bundle.
CHART_BUNDLE_METRICS = ("latency", "tokens", "cost", "traffic")


def resolved_system_metric(surface: Surface, metric_id: str | None) -> str | None:
    """The series a system-metric request on ``surface`` publishes.

    ``None`` when the surface rejects the id (the session graph).
    """

    statistics, fallback = _SURFACES[surface]
    if surface == "trace":
        # The trace dispatcher normalizes the id; the others match it exactly.
        key = str(metric_id or LATENCY_METRIC).strip().lower()
    else:
        key = str(metric_id or "")
    if key in statistics:
        return key
    return fallback


def publishes_latency(surface: Surface, metric_id: str | None) -> bool:
    """Whether a system-metric request on ``surface`` publishes latency."""

    return resolved_system_metric(surface, metric_id) == LATENCY_METRIC


def system_metric_statistic(surface: Surface, metric_id: str | None) -> str | None:
    """The statistic of the series a system-metric request publishes."""

    statistics, _fallback = _SURFACES[surface]
    key = resolved_system_metric(surface, metric_id)
    return statistics.get(key) if key else None


def chart_bundle_statistics() -> dict[str, str]:
    """``{series: statistic}`` for the project ChartsView bundle."""

    return {key: TRACE_METRIC_STATISTICS[key] for key in CHART_BUNDLE_METRICS}


def with_metric_statistic(payload: _R, surface: Surface, metric_id: str | None) -> _R:
    """Return ``payload`` stamped with its series' ``metric_statistic``."""

    if not isinstance(payload, dict):
        return payload
    statistic = system_metric_statistic(surface, metric_id)
    if statistic is None:
        return payload
    return cast(_R, {**payload, "metric_statistic": statistic})


# Exact snapshot namespace of each system-metric surface.
SNAPSHOT_NAMESPACE_SURFACES: Mapping[str, Surface] = {
    "observe-system-graph": "trace",
    "observe-session-system-graph": "session",
    "observe-user-system-graph": "users",
}


def stamp_snapshot_statistic(namespace: str, metric_id: Any, payload: Any) -> Any:
    """Write the statistic into a payload a refresh worker is about to cache.

    Only workers that compute the latency mean write it, so it is the marker
    ``snapshot_names_its_statistic`` checks. Other namespaces pass through.
    """

    surface = SNAPSHOT_NAMESPACE_SURFACES.get(namespace)
    if surface is None:
        return payload
    return with_metric_statistic(payload, surface, metric_id)


def snapshot_names_its_statistic(namespace: str, metric_id: Any, payload: Any) -> bool:
    """Whether a cached system-metric snapshot may be served.

    During a rolling deploy an older worker can take a refresh job keyed by
    the new identity (it ignores ``payload_version``) and cache its own
    latency statistic under the new key for up to 30 days, and one built
    before the statistic was named writes no ``metric_statistic``. So a
    LATENCY snapshot is served only when its payload says
    ``metric_statistic == "mean"``; anything else is a cache miss that a
    current worker recomputes. The check keys on the
    series being latency, not on the statistic's name: other series (cost,
    durations) are means too, did not change meaning, and are always
    accepted, marked or not.
    """

    surface = SNAPSHOT_NAMESPACE_SURFACES.get(namespace)
    if surface is None or not publishes_latency(surface, metric_id):
        return True
    return (
        isinstance(payload, dict)
        and payload.get("metric_statistic") == LATENCY_STATISTIC
    )


def stamps_metric_statistic(
    surface: Surface,
    metric_of: Callable[[dict[str, Any]], str | None],
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Stamp every envelope a keyword-only graph entry point returns.

    Stamping at the public boundary covers the complete, cached, pending,
    degraded and refused envelopes alike, so the label never flickers while a
    series loads. ``metric_of`` maps the call's keyword arguments to the
    requested metric id, or to ``None`` when the call is not a system metric.
    The decorated function keeps its signature for the type checker.
    """

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(function)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            payload = function(*args, **kwargs)
            metric_id = metric_of(kwargs)
            if metric_id is None:
                return payload
            return with_metric_statistic(payload, surface, metric_id)

        return wrapper

    return decorate


__all__ = [
    "METRIC_STATISTIC_CHOICES",
    "SESSION_METRIC_STATISTICS",
    "TRACE_METRIC_STATISTICS",
    "USER_METRIC_STATISTICS",
    "chart_bundle_statistics",
    "publishes_latency",
    "snapshot_names_its_statistic",
    "stamp_snapshot_statistic",
    "stamps_metric_statistic",
    "system_metric_statistic",
    "with_metric_statistic",
]
