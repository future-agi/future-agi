import math
from collections import deque
from datetime import datetime as dt_datetime
from datetime import timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

import structlog
from django.utils import timezone

logger = structlog.get_logger(__name__)
from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.utils.monitor import (
    MONITOR_CH_SETTINGS,
    build_monitor_ch_builder,
    get_interval_kind,
)

# Graphs are interactive; keep a tighter timeout than the evaluator.
GRAPH_QUERY_TIMEOUT_MS = 10_000


def _format_ch_time_series(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format ClickHouse time-series rows to the expected output format."""
    result = []
    for row in data:
        ts = row.get("timestamp")
        value = row.get("value")
        if ts is not None:
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            result.append(
                {
                    "timestamp": ts_str,
                    "value": value if value is not None else 0,
                }
            )
    return result


def _get_frequency_seconds(monitor: UserAlertMonitor) -> int:
    """Returns the frequency in seconds for a given monitor."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        frequency_seconds = 24 * 60 * 60  # 1 day
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        frequency_seconds = 30 * 24 * 60 * 60  # 30 days
    else:
        frequency_seconds = monitor.alert_frequency * 60
    return frequency_seconds


def get_graph_data(
    monitor: UserAlertMonitor,
    time_window_start: Optional[dt_datetime] = None,
    time_window_end: Optional[dt_datetime] = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Time-series graph data for a monitor, from ClickHouse."""
    if monitor.threshold_type == ThresholdCalculationMethodChoices.STATIC:
        return get_static_metric_graph_data(monitor, time_window_start, time_window_end)
    elif monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE:
        return get_percentage_change_metric_graph_data(
            monitor, time_window_start, time_window_end
        )
    else:
        raise ValueError(f"Unsupported threshold type: {monitor.threshold_type}")


def get_static_metric_graph_data(
    monitor: UserAlertMonitor,
    time_window_start: Optional[dt_datetime] = None,
    time_window_end: Optional[dt_datetime] = None,
) -> List[Dict[str, Any]]:
    """Bucketed time-series for a static-threshold monitor. Raises on CH errors."""
    frequency_seconds = _get_frequency_seconds(monitor)
    if not frequency_seconds:
        return []
    analytics = AnalyticsQueryService()

    effective_end = time_window_end or timezone.now()
    effective_start = time_window_start or (effective_end - timedelta(days=7))

    builder = build_monitor_ch_builder(monitor)
    query, params = builder.build_time_series_query(
        monitor.metric_type,
        effective_start,
        effective_end,
        frequency_seconds,
    )
    result = analytics.execute_ch_query(
        query, params, timeout_ms=GRAPH_QUERY_TIMEOUT_MS, settings=MONITOR_CH_SETTINGS
    )
    return _format_ch_time_series(result.data)


def _calculate_std_dev(data: List[float]) -> float:
    """Sample standard deviation (parity with the alert-bar contract)."""
    n = len(data)
    if n < 2:
        return 0.0
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / (n - 1)
    return math.sqrt(variance)


def _bucket_status(
    monitor: UserAlertMonitor,
    current_value: float,
    historical_mean: float,
    historical_stddev: float,
    sign: int,
) -> str:
    """critical/warning/healthy for a bucket against a mean/stddev band."""
    warning_percent = monitor.warning_threshold_value or 0
    critical_percent = monitor.critical_threshold_value or 0
    critical_threshold = historical_mean + sign * historical_stddev * (
        1 + critical_percent / 100.0
    )
    warning_threshold = historical_mean + sign * historical_stddev * (
        1 + warning_percent / 100.0
    )
    if monitor.critical_threshold_value is not None and _compare(
        current_value, monitor.threshold_operator, critical_threshold
    ):
        return "critical"
    if monitor.warning_threshold_value is not None and _compare(
        current_value, monitor.threshold_operator, warning_threshold
    ):
        return "warning"
    return "healthy"


def _process_percentage_change_buckets(
    all_buckets: List[Dict[str, Any]],
    monitor: UserAlertMonitor,
    time_window_start: Optional[dt_datetime],
    frequency_delta: timedelta,
    auto_threshold_time_window: timedelta,
    eval_band: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    """Processes aggregated buckets to generate graph and alert data.

    When ``eval_band`` (the evaluator's own mean/stddev from
    ``build_historical_stats_query``) is supplied, the alert bars use it so the
    preview matches what the evaluator would actually fire. Otherwise it falls
    back to a rolling per-bucket band (used only when the evaluator stats are
    unavailable, e.g. no history).
    """
    graph_data = []
    alert_bar_data = []
    historical_window: Deque[Dict[str, Any]] = deque()

    op = monitor.threshold_operator
    sign = 1 if op == ComparisonOperatorChoices.GREATER_THAN else -1

    comparison_time_window_start = _ensure_timezone_aware(time_window_start)

    for bucket in all_buckets:
        current_timestamp = bucket["timestamp"]
        current_value = bucket["value"] if bucket["value"] is not None else 0

        current_timestamp = _ensure_timezone_aware(current_timestamp)

        if eval_band is not None:
            status = _bucket_status(
                monitor, current_value, eval_band[0], eval_band[1], sign
            )
        else:
            while (
                historical_window
                and current_timestamp - historical_window[0]["timestamp"]
                >= auto_threshold_time_window
            ):
                historical_window.popleft()

            historical_values = [
                b["value"] for b in historical_window if b["value"] is not None
            ]

            status = "insufficient_data"
            if len(historical_values) > 1:
                status = _bucket_status(
                    monitor,
                    current_value,
                    sum(historical_values) / len(historical_values),
                    _calculate_std_dev(historical_values),
                    sign,
                )

        # Add to results only if inside the requested time window
        if (
            comparison_time_window_start is None
            or current_timestamp >= comparison_time_window_start
        ):
            graph_data.append(
                {"timestamp": current_timestamp.isoformat(), "value": current_value}
            )
            end_timestamp = current_timestamp + frequency_delta
            alert_bar_data.append(
                {
                    "start_timestamp": current_timestamp.isoformat(),
                    "end_timestamp": end_timestamp.isoformat(),
                    "status": status,
                }
            )

        # Add current bucket to historical window for the next iteration
        historical_window.append(bucket)

    return {"graph_data": graph_data, "alert_bar_data": alert_bar_data}


def get_percentage_change_metric_graph_data(
    monitor: UserAlertMonitor,
    time_window_start: Optional[dt_datetime] = None,
    time_window_end: Optional[dt_datetime] = None,
) -> Dict[str, Any]:
    """Graph + alert-bar data for a percentage-change monitor. Raises on CH errors."""
    frequency_seconds = _get_frequency_seconds(monitor)
    if not frequency_seconds:
        return {"graph_data": [], "alert_bar_data": []}
    analytics = AnalyticsQueryService()

    auto_threshold_time_window = timedelta(minutes=monitor.auto_threshold_time_window)

    effective_end = time_window_end or timezone.now()
    extended_start = None
    if time_window_start:
        extended_start = time_window_start - auto_threshold_time_window

    builder = build_monitor_ch_builder(monitor)
    ts_start = extended_start or (effective_end - timedelta(days=30))
    query, params = builder.build_time_series_query(
        monitor.metric_type,
        ts_start,
        effective_end,
        frequency_seconds,
    )
    result = analytics.execute_ch_query(
        query, params, timeout_ms=GRAPH_QUERY_TIMEOUT_MS, settings=MONITOR_CH_SETTINGS
    )

    frequency_delta = timedelta(seconds=frequency_seconds)
    # Colour the alert bars with the evaluator's own historical stats so the
    # preview matches real firing (the rolling per-bucket band under-estimated
    # stddev for per-row metrics). Falls back to the rolling band if the
    # evaluator has no history.
    eval_band = _evaluator_percentage_band(
        monitor,
        builder,
        analytics,
        hist_end=effective_end - frequency_delta,
        auto_threshold_time_window=auto_threshold_time_window,
    )

    all_buckets = []
    for row in result.data:
        ts = row.get("timestamp")
        if ts is not None:
            if isinstance(ts, str):
                ts = dt_datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts = _ensure_timezone_aware(ts)
            # NULL values stay None here; coercion/filtering happens downstream.
            all_buckets.append(
                {
                    "timestamp": ts,
                    "value": row.get("value"),
                }
            )

    if not all_buckets:
        return {"graph_data": [], "alert_bar_data": []}

    return _process_percentage_change_buckets(
        all_buckets,
        monitor,
        time_window_start,
        frequency_delta,
        auto_threshold_time_window,
        eval_band=eval_band,
    )


def _evaluator_percentage_band(
    monitor: UserAlertMonitor,
    builder: Any,
    analytics: AnalyticsQueryService,
    hist_end: dt_datetime,
    auto_threshold_time_window: timedelta,
) -> Optional[Tuple[float, float]]:
    """The (mean, stddev) the evaluator uses for its threshold, for the
    historical window ending at ``hist_end``. Returns None when unavailable
    (no history / non-finite), so the caller falls back to the rolling band.
    """
    hist_start = hist_end - auto_threshold_time_window
    query, params = builder.build_historical_stats_query(
        monitor.metric_type,
        hist_start,
        hist_end,
        interval_kind=get_interval_kind(monitor),
    )
    result = analytics.execute_ch_query(
        query, params, timeout_ms=GRAPH_QUERY_TIMEOUT_MS, settings=MONITOR_CH_SETTINGS
    )
    if not result.data:
        return None
    mean = result.data[0].get("mean")
    stddev = result.data[0].get("stddev")
    if (
        mean is None
        or stddev is None
        or not math.isfinite(mean)
        or not math.isfinite(stddev)
    ):
        return None
    return mean, stddev


def _compare(value: float, op: str, threshold: float) -> bool:
    """Helper to perform comparison based on operator."""
    if op == ComparisonOperatorChoices.GREATER_THAN:
        return value > threshold
    if op == ComparisonOperatorChoices.LESS_THAN:
        return value < threshold
    return False


def _ensure_timezone_aware(dt: Optional[dt_datetime]) -> Optional[dt_datetime]:
    """Make a datetime timezone-aware if naive."""
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
