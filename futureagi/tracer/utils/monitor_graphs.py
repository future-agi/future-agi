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
    PER_PERIOD_TOTAL_METRICS,
    TOKEN_METRICS,
    build_monitor_ch_builder,
    get_period_seconds,
    get_previous_period_baseline,
    percentage_change_breach,
    uses_earlier_rule,
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
    elif uses_earlier_rule(monitor):
        # Imported here: the earlier rule builds on this module.
        from tracer.utils.monitor_earlier_rule import get_percentage_band_graph_data

        return get_percentage_band_graph_data(
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
    frequency_seconds = get_period_seconds(monitor)
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


def _bucket_status(
    monitor: UserAlertMonitor, current_value: float, baseline: float | None
) -> str:
    """critical/warning/healthy for a bucket, judged by the alert worker's own
    rule (tracer.utils.monitor.percentage_change_breach); insufficient_data
    when there is no previous-period baseline."""
    if baseline is None:
        return "insufficient_data"
    breach = percentage_change_breach(monitor, current_value, baseline)
    return str(breach[0]) if breach else "healthy"


class _PreviousPeriods:
    """Rolling previous-period baseline over the graph's own buckets.

    The series form of ``get_previous_period_baseline``: for a bucket at ``t``
    it reads the buckets in ``[t - history, t)``. Count and token metrics: their
    total over the whole buckets that window holds (empty buckets count as 0;
    a 15-minute history on a 10-minute interval holds one). Rate, latency and
    eval metrics: the mean of the buckets that have a value. No data in the
    window (for tokens, no tokens) is no baseline.

    A history shorter than one bucket holds no whole bucket, so earlier bars
    have no baseline and read insufficient_data: the series cannot scale part
    of a bucket exactly. The bar holding now still takes the worker's own
    baseline, scaled up to one period.
    """

    def __init__(
        self, monitor: UserAlertMonitor, history: timedelta, frequency: timedelta
    ) -> None:
        self.history = history
        # For a bucket at t, earlier buckets sit at t - k * frequency (k >= 1);
        # when history < frequency every one is before t - history, so the
        # window is always empty and the floor at 1 only guards the division.
        self.periods = max(1, history // frequency)
        self.per_period_total = monitor.metric_type in PER_PERIOD_TOTAL_METRICS
        self.tokens = monitor.metric_type in TOKEN_METRICS
        self.window: deque[tuple[dt_datetime, float]] = deque()
        self.total: float = 0
        self.nonzero = 0

    def baseline_before(self, timestamp: dt_datetime) -> float | None:
        while self.window and self.window[0][0] < timestamp - self.history:
            _, value = self.window.popleft()
            self.total -= value
            self.nonzero -= value != 0
        if not self.window:
            self.total = 0  # no float drift across an empty window
            return None
        if self.tokens and not self.nonzero:
            return None
        if self.per_period_total:
            return self.total / self.periods
        return self.total / len(self.window)

    def add(self, timestamp: dt_datetime, value: float | None) -> None:
        if value is not None:
            self.window.append((timestamp, value))
            self.total += value
            self.nonzero += value != 0


def _process_percentage_change_buckets(
    all_buckets: list[dict[str, Any]],
    monitor: UserAlertMonitor,
    time_window_start: dt_datetime | None,
    frequency_delta: timedelta,
    history: timedelta,
    series_start: dt_datetime,
    live_bucket: tuple[dt_datetime, float | None] | None = None,
) -> dict[str, Any]:
    """Graph and alert-bar data, each bucket judged against its own previous
    period.

    ``live_bucket`` is ``(timestamp, baseline)`` for the bucket holding now:
    that baseline is the worker's own (``get_previous_period_baseline`` over the
    window the worker judges against), so its bar matches what fires. Every
    other bucket uses the series form of the same rule. A bucket whose history
    starts before ``series_start`` (data the graph did not read) has no
    baseline.
    """
    graph_data = []
    alert_bar_data = []
    previous = _PreviousPeriods(monitor, history, frequency_delta)
    comparison_time_window_start = _ensure_timezone_aware(time_window_start)

    for bucket in all_buckets:
        current_timestamp = _ensure_timezone_aware(bucket["timestamp"])
        current_value = bucket["value"] if bucket["value"] is not None else 0

        baseline = previous.baseline_before(current_timestamp)
        if live_bucket is not None and current_timestamp == live_bucket[0]:
            baseline = live_bucket[1]
        elif current_timestamp - history < series_start:
            baseline = None

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
                    "status": _bucket_status(monitor, current_value, baseline),
                }
            )

        previous.add(current_timestamp, bucket["value"])

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
    frequency_seconds = get_period_seconds(monitor)
    if not frequency_seconds:
        return {"graph_data": [], "alert_bar_data": []}
    analytics = AnalyticsQueryService()

    history = timedelta(minutes=monitor.auto_threshold_time_window)

    effective_end = time_window_end or timezone.now()
    extended_start = None
    if time_window_start:
        extended_start = time_window_start - history

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
        # The bucket holding now (or the page end, if earlier) takes the
        # worker's own baseline, so its bar matches what fires. The worker
        # judges against the window before now; a page that ends in the
        # future (end of today) must not put that baseline in time that has
        # not happened yet.
        baseline_end = min(_ensure_timezone_aware(effective_end), timezone.now())
        live_bucket = None
        live_timestamp = next(
            (
                bucket["timestamp"]
                for bucket in all_buckets
                if bucket["timestamp"]
                < baseline_end
                <= bucket["timestamp"] + frequency_delta
            ),
            None,
        )
        if live_timestamp is not None:
            hist_end = baseline_end - frequency_delta
            live_bucket = (
                live_timestamp,
                get_previous_period_baseline(
                    monitor,
                    hist_end - history,
                    hist_end,
                    builder,
                    analytics=analytics,
                    timeout_ms=deadline.remaining_ms(
                        MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
                    ),
                ),
            )
            deadline.remaining_ms(floor_ms=1)
        graph_data = _process_percentage_change_buckets(
            all_buckets,
            monitor,
            time_window_start,
            frequency_delta,
            history,
            series_start=_ensure_timezone_aware(ts_start),
            live_bucket=live_bucket,
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


def _ensure_timezone_aware(dt: dt_datetime | None) -> dt_datetime | None:
    """Make a datetime timezone-aware if naive."""
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
