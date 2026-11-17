"""The earlier threshold rule, for monitors saved before the threshold update.

A percentage-change monitor saved before the update and not edited since
(``threshold_rule`` "earlier", ``tracer.utils.monitor.uses_earlier_rule``) is
judged as the production build judged it. A threshold ``p`` draws a band
around the history's mean, ``mean +/- stddev * (1 + p / 100)``. Count and
token metrics take the mean and sample stddev of calendar buckets (minute,
hour, day or month, by the check interval); every other metric the mean and
population stddev of its rows. The alert message names the band.

The code is that build's, moved here so the alert worker and the graph share
it. Two things differ, both about which window is read, not what a threshold
means:

- Daily and monthly token monitors end their history where the trailing day
  or 30 days begin, as for the current rule (``_get_metric_window_start``).
- The graph reads the band for the history before the window ending now (or
  at the page end, if earlier), so on a page that ends tonight the bars match
  what fires.

Alerts go through the alert worker's ``_handle_alert_trigger``, so one window
alerts once and a monitor muted mid-check stays quiet. The worker's and the
graph's helpers are called through their modules.
"""

import math
from collections import deque
from datetime import datetime as dt_datetime
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Optional

import structlog
from django.utils import timezone

from tracer.models.monitor import (
    AlertTypeChoices,
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    UserAlertMonitor,
)
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_read_budget_error,
)
from tracer.utils import monitor as alert_worker
from tracer.utils import monitor_graphs as graphs

if TYPE_CHECKING:
    from tracer.services.clickhouse.query_builders.monitor_metrics import (
        MonitorMetricsQueryBuilder,
    )
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

logger = structlog.get_logger(__name__)


def get_interval_kind(monitor: UserAlertMonitor) -> str:
    """Calendar bucket kind for the monitor's frequency (Trunc parity)."""
    interval = timedelta(minutes=monitor.alert_frequency)

    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        interval = timedelta(days=1)
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        interval = timedelta(days=30)

    if interval.days >= 30:
        return "month"
    if interval.days >= 1:
        return "day"
    if interval.total_seconds() >= 3600:
        return "hour"
    return "minute"


def historical_band(
    monitor: UserAlertMonitor,
    start_time: dt_datetime,
    end_time: dt_datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
    *,
    analytics: Optional["AnalyticsQueryService"] = None,
    timeout_ms: int = alert_worker.MONITOR_QUERY_TIMEOUT_MS,
) -> tuple[float | None, float | None]:
    """Historical (mean, stddev) for the window, from ClickHouse. Raises on CH errors."""
    analytics = analytics or alert_worker.AnalyticsQueryService()
    builder = builder or alert_worker.build_monitor_ch_builder(monitor)

    query, params = builder.build_earlier_band_query(
        monitor.metric_type,
        start_time,
        end_time,
        interval_kind=get_interval_kind(monitor),
    )
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=timeout_ms,
        settings=alert_worker.MONITOR_CH_SETTINGS,
    )
    if result.data:
        row = result.data[0]
        return row.get("mean"), row.get("stddev")
    return None, None


# --- Alert worker ---------------------------------------------------------------


def check_percentage_band(
    monitor: UserAlertMonitor,
    current_value: float,
    time_window_start: dt_datetime,
    now: dt_datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
) -> None:
    """Checks for alerts based on percentage change from historical mean."""
    historical_start = time_window_start - timedelta(
        minutes=monitor.auto_threshold_time_window
    )

    historical_mean, historical_stddev = historical_band(
        monitor, historical_start, time_window_start, builder
    )

    if historical_mean is None or historical_stddev is None:
        logger.warning(
            f"Could not calculate historical mean/stddev for monitor {monitor.id} "
            f"({monitor.metric_type}). Skipping percentage change check."
        )
        return

    op = monitor.threshold_operator
    sign = 1 if op == ComparisonOperatorChoices.GREATER_THAN else -1

    critical_dev = historical_stddev * (
        1 + (monitor.critical_threshold_value or 0) / 100
    )
    warning_dev = historical_stddev * (1 + (monitor.warning_threshold_value or 0) / 100)

    critical_threshold = (
        (historical_mean + sign * critical_dev)
        if monitor.critical_threshold_value is not None
        else None
    )
    warning_threshold = (
        (historical_mean + sign * warning_dev)
        if monitor.warning_threshold_value is not None
        else None
    )

    alert_type = None
    threshold_val = None

    if critical_threshold is not None and alert_worker._compare(
        current_value, op, critical_threshold
    ):
        alert_type = AlertTypeChoices.CRITICAL
        threshold_val = critical_threshold
    elif warning_threshold is not None and alert_worker._compare(
        current_value, op, warning_threshold
    ):
        alert_type = AlertTypeChoices.WARNING
        threshold_val = warning_threshold

    if alert_type:
        message = (
            f"Metric '{monitor.name}' for project '{monitor.project.name}' "
            f"({current_value:.2f}) breached the {alert_type} threshold "
            f"({monitor.threshold_operator} {threshold_val:.2f}) based on historical data "
            f"(mean: {historical_mean:.2f}, stddev: {historical_stddev:.2f})."
        )
        alert_worker._handle_alert_trigger(
            monitor, message, alert_type, time_window_start, now
        )


# --- Graph ----------------------------------------------------------------------


def _calculate_std_dev(data: list[float]) -> float:
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
    if monitor.critical_threshold_value is not None and alert_worker._compare(
        current_value, monitor.threshold_operator, critical_threshold
    ):
        return "critical"
    if monitor.warning_threshold_value is not None and alert_worker._compare(
        current_value, monitor.threshold_operator, warning_threshold
    ):
        return "warning"
    return "healthy"


def _process_percentage_change_buckets(
    all_buckets: list[dict[str, Any]],
    monitor: UserAlertMonitor,
    time_window_start: dt_datetime | None,
    frequency_delta: timedelta,
    auto_threshold_time_window: timedelta,
    eval_band: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Processes aggregated buckets to generate graph and alert data.

    When ``eval_band`` (the evaluator's own mean/stddev from
    ``historical_band``) is supplied, the alert bars use it so the preview
    matches what the evaluator would actually fire. Otherwise it falls back to
    a rolling per-bucket band (used only when the evaluator stats are
    unavailable, e.g. no history).
    """
    graph_data = []
    alert_bar_data = []
    historical_window: deque[dict[str, Any]] = deque()

    op = monitor.threshold_operator
    sign = 1 if op == ComparisonOperatorChoices.GREATER_THAN else -1

    comparison_time_window_start = graphs._ensure_timezone_aware(time_window_start)

    for bucket in all_buckets:
        current_timestamp = bucket["timestamp"]
        current_value = bucket["value"] if bucket["value"] is not None else 0

        current_timestamp = graphs._ensure_timezone_aware(current_timestamp)

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


def get_percentage_band_graph_data(
    monitor: UserAlertMonitor,
    time_window_start: dt_datetime | None = None,
    time_window_end: dt_datetime | None = None,
    *,
    deadline: ReadDeadline | None = None,
) -> dict[str, Any]:
    """Graph + alert-bar data for a percentage-change monitor. Raises on CH errors."""
    deadline = deadline or graphs.start_monitor_graph_deadline()
    frequency_seconds = alert_worker.get_period_seconds(monitor)
    if not frequency_seconds:
        return {"graph_data": [], "alert_bar_data": []}
    analytics = graphs.AnalyticsQueryService()

    auto_threshold_time_window = timedelta(minutes=monitor.auto_threshold_time_window)

    effective_end = time_window_end or timezone.now()
    extended_start = None
    if time_window_start:
        extended_start = time_window_start - auto_threshold_time_window

    try:
        builder = graphs._build_monitor_graph_ch_builder(monitor, deadline)
        ts_start = extended_start or (effective_end - timedelta(days=30))
        query, params = builder.build_time_series_query(
            monitor.metric_type,
            ts_start,
            effective_end,
            frequency_seconds,
        )
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(
                graphs.MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
            ),
            settings=alert_worker.MONITOR_CH_SETTINGS,
        )
        deadline.remaining_ms(floor_ms=1)

        all_buckets = []
        for row in result.data:
            ts = row.get("timestamp")
            if ts is not None:
                if isinstance(ts, str):
                    ts = dt_datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = graphs._ensure_timezone_aware(ts)
                # NULL values stay None; coercion/filtering happens downstream.
                all_buckets.append(
                    {
                        "timestamp": ts,
                        "value": row.get("value"),
                    }
                )

        if not all_buckets:
            return {"graph_data": [], "alert_bar_data": []}

        frequency_delta = timedelta(seconds=frequency_seconds)
        # Use the evaluator's own band so preview and firing remain identical:
        # the history before the window ending now (or at the page end, if
        # earlier), never time that has not happened yet.
        band_end = min(graphs._ensure_timezone_aware(effective_end), timezone.now())
        eval_band = _evaluator_percentage_band(
            monitor,
            builder,
            analytics,
            hist_end=band_end - frequency_delta,
            auto_threshold_time_window=auto_threshold_time_window,
            deadline=deadline,
        )
        graph_data = _process_percentage_change_buckets(
            all_buckets,
            monitor,
            time_window_start,
            frequency_delta,
            auto_threshold_time_window,
            eval_band=eval_band,
        )
        deadline.remaining_ms(floor_ms=1)
        return graph_data
    except graphs.MonitorGraphUnavailable:
        raise
    except Exception as exc:
        if is_read_budget_error(exc):
            raise graphs.MonitorGraphUnavailable(
                "Monitor graph ClickHouse read exceeded its request budget"
            ) from exc
        try:
            deadline.remaining_ms(floor_ms=1)
        except ReadDeadlineExceeded as deadline_exc:
            raise graphs.MonitorGraphUnavailable(
                "Monitor graph request deadline was exhausted"
            ) from deadline_exc
        raise


def _evaluator_percentage_band(
    monitor: UserAlertMonitor,
    builder: Any,
    analytics: "AnalyticsQueryService",
    hist_end: dt_datetime,
    auto_threshold_time_window: timedelta,
    deadline: ReadDeadline,
) -> tuple[float, float] | None:
    """The (mean, stddev) the evaluator uses for its threshold, for the
    historical window ending at ``hist_end``. Returns None when unavailable
    (no history / non-finite), so the caller falls back to the rolling band.
    """
    hist_start = hist_end - auto_threshold_time_window
    mean, stddev = historical_band(
        monitor,
        hist_start,
        hist_end,
        builder,
        analytics=analytics,
        timeout_ms=deadline.remaining_ms(
            graphs.MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
        ),
    )
    deadline.remaining_ms(floor_ms=1)
    if (
        mean is None
        or stddev is None
        or not math.isfinite(mean)
        or not math.isfinite(stddev)
    ):
        return None
    return mean, stddev
