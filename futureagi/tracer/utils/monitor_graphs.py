import math
from collections import deque
from datetime import datetime as dt_datetime
from datetime import timedelta

import structlog
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import is_read_budget_error

logger = structlog.get_logger(__name__)

_MONITOR_GRAPH_CH_TIMEOUT_MS = 750
# Returning an empty graph is safer than presenting a partial aggregation as
# complete, so all ClickHouse overflow modes throw into the fail-closed path.
_MONITOR_GRAPH_CH_SETTINGS = {
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_memory_usage": 268_435_456,
    "max_bytes_to_read": 1_073_741_824,
    "read_overflow_mode": "throw",
    "max_result_rows": 2000,
    "result_overflow_mode": "throw",
}


def _degraded_graph_response(exc, *, include_alert_bar=False):
    """Return an explicit, safe failure state instead of false no-data."""
    result = {
        "graph_data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": (
            "read_budget_exceeded" if is_read_budget_error(exc) else "query_failed"
        ),
    }
    if include_alert_bar:
        result["alert_bar_data"] = []
    return result


def _build_monitor_graph_ch_builder(monitor):
    """Construct a MonitorMetricsQueryBuilder from a monitor instance."""
    project_id = getattr(monitor, "project_id", None)
    if not project_id:
        # Do not turn a legacy NULL project into the invalid UUID literal
        # "None". Graph callers already fail closed to an empty series.
        raise ValueError("Monitor has no project scope")

    eval_config_id = None
    eval_output_type = None
    if (
        monitor.metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS
        and monitor.metric
    ):
        try:
            custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
            eval_output_type = custom_eval_config.eval_template.config.get("output")
            eval_config_id = str(monitor.metric)
        except CustomEvalConfig.DoesNotExist:
            pass

    from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
        MonitorMetricsQueryBuilderV2,
    )

    # This CH path is authoritative for the CH25 spans store; routing through
    # the opt-in V1/V2 flag leaves production on the legacy timestamp/compiler
    # when the flag is absent.
    return MonitorMetricsQueryBuilderV2(
        project_id=str(project_id),
        filters=monitor.filters,
        eval_config_id=eval_config_id,
        eval_output_type=eval_output_type,
        threshold_metric_value=monitor.threshold_metric_value,
    )


def _format_ch_time_series(data):
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


def _get_frequency_seconds(monitor, start_time=None, end_time=None):
    """Return a bucket width that keeps ClickHouse graph results bounded."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        frequency_seconds = 24 * 60 * 60  # 1 day
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        frequency_seconds = 30 * 24 * 60 * 60  # 30 days
    else:
        frequency_seconds = max(int(monitor.alert_frequency or 1) * 60, 60)

    if start_time is not None and end_time is not None:
        window_seconds = max((end_time - start_time).total_seconds(), 0)
        minimum_minutes = max(1, math.ceil(window_seconds / (1000 * 60)))
        frequency_seconds = max(frequency_seconds, minimum_minutes * 60)

    return frequency_seconds


def get_graph_data(monitor, time_window_start=None, time_window_end=None):
    """
    Generates time-series data for a given monitor using a single, efficient
    database query.
    """
    if monitor.threshold_type == ThresholdCalculationMethodChoices.STATIC:
        return get_static_metric_graph_data(monitor, time_window_start, time_window_end)
    elif monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE:
        return get_percentage_change_metric_graph_data(
            monitor, time_window_start, time_window_end
        )
    # elif monitor.threshold_type == ThresholdCalculationMethodChoices.ANOMALY_DETECTION:
    #     return get_anomaly_detection_metric_graph_data(monitor, time_window_start, time_window_end)
    else:
        raise ValueError(f"Unsupported threshold type: {monitor.threshold_type}")


def get_static_metric_graph_data(monitor, time_window_start=None, time_window_end=None):
    """
    Generates time-series data for a given monitor using a single, efficient
    database query.

    For time-specific metrics like DAILY_TOKENS_SPENT, the bucket size is
    fixed. For others, it's based on the monitor's alert_frequency.

    Args:
        monitor: The monitor object
        time_window_start: Optional start time for the data range. If None, gets all available data.
        time_window_end: Optional end time for the data range. If None, uses current time.
    """
    # --- ClickHouse dispatch ---
    analytics = AnalyticsQueryService()
    try:
        effective_end = time_window_end or timezone.now()
        effective_start = time_window_start or (effective_end - timedelta(days=7))
        frequency_seconds = _get_frequency_seconds(
            monitor, effective_start, effective_end
        )

        builder = _build_monitor_graph_ch_builder(monitor)
        query, params = builder.build_time_series_query(
            monitor.metric_type,
            effective_start,
            effective_end,
            frequency_seconds,
        )
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_MONITOR_GRAPH_CH_TIMEOUT_MS,
            settings=_MONITOR_GRAPH_CH_SETTINGS,
        )
        return _format_ch_time_series(result.data)
    except Exception as e:
        logger.warning(
            "CH static graph query failed; returning degraded result",
            error_type=type(e).__name__,
            monitor_id=str(monitor.id),
        )
        return _degraded_graph_response(e)


def _calculate_std_dev(data):
    """Helper to calculate standard deviation."""
    n = len(data)
    if n < 2:
        return 0.0
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / (n - 1)
    return math.sqrt(variance)


def _process_percentage_change_buckets(
    all_buckets, monitor, time_window_start, frequency_delta, auto_threshold_time_window
):
    """Processes aggregated buckets to generate graph and alert data."""
    graph_data = []
    alert_bar_data = []
    historical_window = deque()

    op = monitor.threshold_operator
    sign = 1 if op == ComparisonOperatorChoices.GREATER_THAN else -1
    warning_percent = monitor.warning_threshold_value or 0
    critical_percent = monitor.critical_threshold_value or 0

    comparison_time_window_start = _ensure_timezone_aware(time_window_start)

    for bucket in all_buckets:
        current_timestamp = bucket["timestamp"]
        current_value = bucket["value"] if bucket["value"] is not None else 0

        current_timestamp = _ensure_timezone_aware(current_timestamp)

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
            historical_mean = sum(historical_values) / len(historical_values)
            historical_stddev = _calculate_std_dev(historical_values)

            warning_dev = historical_stddev * (1 + warning_percent / 100.0)
            critical_dev = historical_stddev * (1 + critical_percent / 100.0)

            critical_threshold = historical_mean + sign * critical_dev
            warning_threshold = historical_mean + sign * warning_dev

            is_critical = (
                _compare(current_value, op, critical_threshold)
                if monitor.critical_threshold_value is not None
                else False
            )
            is_warning = (
                _compare(current_value, op, warning_threshold)
                if monitor.warning_threshold_value is not None
                else False
            )

            if is_critical:
                status = "critical"
            elif is_warning:
                status = "warning"
            else:
                status = "healthy"

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
    monitor, time_window_start=None, time_window_end=None
):
    """
    Handles graph data generation for percentage change metrics.
    Returns a dictionary with two keys:
    - 'graph_data': Data for the main metric line graph.
    - 'alert_bar_data': Data for the colored alert status bar.
    """
    # --- ClickHouse dispatch ---
    analytics = AnalyticsQueryService()
    try:
        auto_threshold_time_window = timedelta(
            minutes=monitor.auto_threshold_time_window
        )

        effective_end = time_window_end or timezone.now()
        extended_start = None
        if time_window_start:
            extended_start = time_window_start - auto_threshold_time_window
        effective_start = extended_start or (effective_end - timedelta(days=30))
        frequency_seconds = _get_frequency_seconds(
            monitor, effective_start, effective_end
        )

        builder = _build_monitor_graph_ch_builder(monitor)
        query, params = builder.build_time_series_query(
            monitor.metric_type,
            effective_start,
            effective_end,
            frequency_seconds,
        )
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_MONITOR_GRAPH_CH_TIMEOUT_MS,
            settings=_MONITOR_GRAPH_CH_SETTINGS,
        )

        # Convert CH results to bucket format expected by _process_percentage_change_buckets
        all_buckets = []
        for row in result.data:
            ts = row.get("timestamp")
            if ts is not None:
                if isinstance(ts, str):
                    ts = dt_datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = _ensure_timezone_aware(ts)
                all_buckets.append(
                    {
                        "timestamp": ts,
                        "value": row.get("value", 0),
                    }
                )

        if not all_buckets:
            return {"graph_data": [], "alert_bar_data": []}

        frequency_delta = timedelta(seconds=frequency_seconds)
        return _process_percentage_change_buckets(
            all_buckets,
            monitor,
            time_window_start,
            frequency_delta,
            auto_threshold_time_window,
        )
    except Exception as e:
        logger.warning(
            "CH percentage change graph query failed; returning degraded result",
            error_type=type(e).__name__,
            monitor_id=str(monitor.id),
        )
        return _degraded_graph_response(e, include_alert_bar=True)


def _compare(value, op, threshold):
    """Helper to perform comparison based on operator."""
    if op == ComparisonOperatorChoices.GREATER_THAN:
        return value > threshold
    if op == ComparisonOperatorChoices.LESS_THAN:
        return value < threshold
    return False


def _ensure_timezone_aware(dt):
    """
    Ensures a datetime object is timezone-aware.
    Returns the datetime as-is if already timezone-aware,
    or converts it using Django's default timezone if naive.
    """
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
