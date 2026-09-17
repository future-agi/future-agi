import math
from collections import deque
from contextlib import contextmanager, nullcontext
from datetime import datetime as dt_datetime
from datetime import timedelta
from typing import Any

import structlog
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
    is_read_budget_error,
)
from tracer.utils.monitor import (
    MONITOR_CH_SETTINGS,
    build_monitor_ch_builder,
    get_interval_kind,
)

logger = structlog.get_logger(__name__)

MONITOR_GRAPH_WALL_MS = settings.INTERACTIVE_READ_DEFAULT_WALL_MS
MONITOR_GRAPH_CH_TIMEOUT_CAP_MS = settings.MONITOR_GRAPH_CH_TIMEOUT_CAP_MS
MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS = (
    settings.MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS
)


class MonitorGraphUnavailable(RuntimeError):
    """A monitor graph could not be read exactly inside its request wall."""


def start_monitor_graph_deadline() -> ReadDeadline:
    """Create the single wall clock shared by one monitor-graph request."""

    return ReadDeadline.start(MONITOR_GRAPH_WALL_MS)


def _execute_monitor_graph_pg_query_with_deadline(
    deadline: ReadDeadline,
    timeout_cap_ms: int | None,
    execute,
    sql,
    params,
    many,
    context,
):
    """Shrink PostgreSQL's per-statement timeout against the request wall."""

    timeout_ms = deadline.remaining_ms(timeout_cap_ms, floor_ms=1)
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(timeout_ms),),
    )
    result = execute(sql, params, many, context)
    deadline.remaining_ms(floor_ms=1)
    return result


@contextmanager
def monitor_graph_postgres_budget(
    deadline: ReadDeadline, timeout_cap_ms: int | None = None
):
    """Bound PostgreSQL metadata reads by the remaining graph deadline."""

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        return _execute_monitor_graph_pg_query_with_deadline(
            deadline,
            timeout_cap_ms,
            execute,
            sql,
            params,
            many,
            context,
        )

    try:
        transaction_context = (
            transaction.atomic() if connection.vendor == "postgresql" else nullcontext()
        )
        with transaction_context:
            if connection.vendor == "postgresql":
                with connection.execute_wrapper(execute_with_remaining_timeout):
                    yield
            else:
                yield
        deadline.remaining_ms(floor_ms=1)
    except MonitorGraphUnavailable:
        raise
    except (DatabaseError, ReadDeadlineExceeded) as exc:
        raise MonitorGraphUnavailable(
            "Monitor graph PostgreSQL metadata read exceeded its request budget"
        ) from exc


def _build_monitor_graph_ch_builder(monitor, deadline: ReadDeadline):
    """Build the shared monitor query builder within the request deadline."""

    with monitor_graph_postgres_budget(
        deadline,
        timeout_cap_ms=MONITOR_GRAPH_METADATA_PG_TIMEOUT_CAP_MS,
    ):
        return build_monitor_ch_builder(monitor)


def _format_ch_time_series(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    time_window_start: dt_datetime | None = None,
    time_window_end: dt_datetime | None = None,
    *,
    deadline: ReadDeadline | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Time-series graph data for a monitor, from ClickHouse."""
    deadline = deadline or start_monitor_graph_deadline()
    try:
        deadline.remaining_ms(floor_ms=1)
    except ReadDeadlineExceeded as exc:
        raise MonitorGraphUnavailable(
            "Monitor graph request deadline was exhausted"
        ) from exc

    if monitor.threshold_type == ThresholdCalculationMethodChoices.STATIC:
        return get_static_metric_graph_data(
            monitor,
            time_window_start,
            time_window_end,
            deadline=deadline,
        )
    elif monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE:
        return get_percentage_change_metric_graph_data(
            monitor,
            time_window_start,
            time_window_end,
            deadline=deadline,
        )
    else:
        raise ValueError(f"Unsupported threshold type: {monitor.threshold_type}")


def get_static_metric_graph_data(
    monitor: UserAlertMonitor,
    time_window_start: dt_datetime | None = None,
    time_window_end: dt_datetime | None = None,
    *,
    deadline: ReadDeadline | None = None,
) -> list[dict[str, Any]]:
    """Bucketed time-series for a static-threshold monitor. Raises on CH errors."""
    deadline = deadline or start_monitor_graph_deadline()
    frequency_seconds = _get_frequency_seconds(monitor)
    if not frequency_seconds:
        return []
    analytics = AnalyticsQueryService()

    effective_end = time_window_end or timezone.now()
    effective_start = time_window_start or (effective_end - timedelta(days=7))

    try:
        builder = _build_monitor_graph_ch_builder(monitor, deadline)
        query, params = builder.build_time_series_query(
            monitor.metric_type,
            effective_start,
            effective_end,
            frequency_seconds,
        )
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(
                MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
            ),
            settings=MONITOR_CH_SETTINGS,
        )
        graph_data = _format_ch_time_series(result.data)
        deadline.remaining_ms(floor_ms=1)
        return graph_data
    except MonitorGraphUnavailable:
        raise
    except Exception as exc:
        if is_read_budget_error(exc):
            raise MonitorGraphUnavailable(
                "Monitor graph ClickHouse read exceeded its request budget"
            ) from exc
        try:
            deadline.remaining_ms(floor_ms=1)
        except ReadDeadlineExceeded as deadline_exc:
            raise MonitorGraphUnavailable(
                "Monitor graph request deadline was exhausted"
            ) from deadline_exc
        raise


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
    all_buckets: list[dict[str, Any]],
    monitor: UserAlertMonitor,
    time_window_start: dt_datetime | None,
    frequency_delta: timedelta,
    auto_threshold_time_window: timedelta,
    eval_band: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Processes aggregated buckets to generate graph and alert data.

    When ``eval_band`` (the evaluator's own mean/stddev from
    ``build_historical_stats_query``) is supplied, the alert bars use it so the
    preview matches what the evaluator would actually fire. Otherwise it falls
    back to a rolling per-bucket band (used only when the evaluator stats are
    unavailable, e.g. no history).
    """
    graph_data = []
    alert_bar_data = []
    historical_window: deque[dict[str, Any]] = deque()

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
    time_window_start: dt_datetime | None = None,
    time_window_end: dt_datetime | None = None,
    *,
    deadline: ReadDeadline | None = None,
) -> dict[str, Any]:
    """Graph + alert-bar data for a percentage-change monitor. Raises on CH errors."""
    deadline = deadline or start_monitor_graph_deadline()
    frequency_seconds = _get_frequency_seconds(monitor)
    if not frequency_seconds:
        return {"graph_data": [], "alert_bar_data": []}
    analytics = AnalyticsQueryService()

    auto_threshold_time_window = timedelta(minutes=monitor.auto_threshold_time_window)

    effective_end = time_window_end or timezone.now()
    extended_start = None
    if time_window_start:
        extended_start = time_window_start - auto_threshold_time_window

    try:
        builder = _build_monitor_graph_ch_builder(monitor, deadline)
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
                MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
            ),
            settings=MONITOR_CH_SETTINGS,
        )
        deadline.remaining_ms(floor_ms=1)

        all_buckets = []
        for row in result.data:
            ts = row.get("timestamp")
            if ts is not None:
                if isinstance(ts, str):
                    ts = dt_datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = _ensure_timezone_aware(ts)
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
        # Use the evaluator's own band so preview and firing remain identical.
        eval_band = _evaluator_percentage_band(
            monitor,
            builder,
            analytics,
            hist_end=effective_end - frequency_delta,
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
    except MonitorGraphUnavailable:
        raise
    except Exception as exc:
        if is_read_budget_error(exc):
            raise MonitorGraphUnavailable(
                "Monitor graph ClickHouse read exceeded its request budget"
            ) from exc
        try:
            deadline.remaining_ms(floor_ms=1)
        except ReadDeadlineExceeded as deadline_exc:
            raise MonitorGraphUnavailable(
                "Monitor graph request deadline was exhausted"
            ) from deadline_exc
        raise


def _evaluator_percentage_band(
    monitor: UserAlertMonitor,
    builder: Any,
    analytics: AnalyticsQueryService,
    hist_end: dt_datetime,
    auto_threshold_time_window: timedelta,
    deadline: ReadDeadline,
) -> tuple[float, float] | None:
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
        query,
        params,
        timeout_ms=deadline.remaining_ms(
            MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
        ),
        settings=MONITOR_CH_SETTINGS,
    )
    deadline.remaining_ms(floor_ms=1)
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


def _ensure_timezone_aware(dt: dt_datetime | None) -> dt_datetime | None:
    """Make a datetime timezone-aware if naive."""
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
