"""Read-only replay of unsaved monitor thresholds under one interactive budget.

Only complete intervals are considered. The seven-day read boundary also applies
to rolling metrics and percentage-change baselines: incomplete history is never
silently replaced by a shorter baseline or by today's threshold.
"""

import math
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone

from tracer.models.monitor import (
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.read_budget import (
    ReadDeadlineExceeded,
    is_read_budget_error,
)
from tracer.utils.monitor import (
    MONITOR_CH_SETTINGS,
    _compare,
    baseline_periods,
    uses_earlier_rule,
)
from tracer.utils.monitor_earlier_rule import get_interval_kind
from tracer.utils.monitor_graphs import (
    MONITOR_GRAPH_CH_TIMEOUT_CAP_MS,
    MonitorGraphUnavailable,
    _build_monitor_graph_ch_builder,
    start_monitor_graph_deadline,
)

MAX_PREVIEW_DAYS = 7
MAX_PREVIEW_BUCKETS = 168
_PREVIEW_MINUTES = MAX_PREVIEW_DAYS * 24 * 60


def _finite(value):
    return value is not None and math.isfinite(value)


def get_threshold_preview(monitor, *, now=None, deadline=None):
    """Return metric values, effective thresholds and fires without persisting.

    Common static metrics use one grouped query. Rolling daily metrics and
    historical bands use the same aggregate queries as the evaluator, with at
    most two reads per bucket and a single shared request deadline. A timeout
    fails the entire preview rather than returning a misleading partial count.
    """
    deadline = deadline or start_monitor_graph_deadline()
    now = now or timezone.now()
    frequency = monitor.alert_frequency
    if frequency < 5:
        raise ValueError("Alert frequency must be at least five minutes.")
    empty = {
        "series": [],
        "fired_buckets": [],
        "fire_count": 0,
        "bucket_count": 0,
        "evaluated_count": 0,
        "window_start": (now - timedelta(days=MAX_PREVIEW_DAYS)).isoformat(),
        "window_end": now.isoformat(),
        "window_limited": False,
        "interval_minutes": frequency,
    }
    # A 30-day total cannot be reconstructed from a strict seven-day read.
    if (
        frequency > _PREVIEW_MINUTES
        or monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT
    ):
        return {**empty, "reason": "interval_exceeds_window"}

    seconds = frequency * 60
    end = datetime.fromtimestamp(
        int(now.timestamp()) // seconds * seconds, tz=dt_timezone.utc
    )
    read_start = end - timedelta(days=MAX_PREVIEW_DAYS)
    count = min(MAX_PREVIEW_BUCKETS, _PREVIEW_MINUTES // frequency)
    start = end - timedelta(seconds=count * seconds)
    result = {
        **empty,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_limited": count * frequency < _PREVIEW_MINUTES,
    }
    settings = {
        **MONITOR_CH_SETTINGS,
        "max_result_rows": MAX_PREVIEW_BUCKETS,
        "max_result_bytes": 1_000_000,
        "result_overflow_mode": "throw",
    }
    analytics = AnalyticsQueryService()

    def execute(query_and_params):
        query, params = query_and_params
        if not analytics.supports_per_query_read_settings:
            raise ReadDeadlineExceeded(
                "Monitor preview requires a server execution cap"
            )
        remaining_ms = deadline.remaining_ms(
            MONITOR_GRAPH_CH_TIMEOUT_CAP_MS, floor_ms=1
        )
        response = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=remaining_ms,
            server_execution_cap_ms=remaining_ms,
            settings=settings,
        )
        deadline.remaining_ms(floor_ms=1)
        if len(response.data) > MAX_PREVIEW_BUCKETS:
            raise MonitorGraphUnavailable("Monitor preview exceeded its row budget")
        return response.data

    try:
        builder = _build_monitor_graph_ch_builder(monitor, deadline)
        daily = monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT
        if daily:
            rows = []
            for index in range(count):
                timestamp = start + timedelta(seconds=index * seconds)
                bucket_end = timestamp + timedelta(seconds=seconds)
                metric_start = bucket_end - timedelta(days=1)
                if metric_start < read_start:
                    continue
                data = execute(
                    builder.build_metric_value_query(
                        monitor.metric_type, metric_start, bucket_end
                    )
                )
                if data:
                    rows.append(
                        {"timestamp": timestamp, "value": data[0].get("value")}
                    )
        else:
            rows = execute(
                builder.build_time_series_query(
                    monitor.metric_type, start, end, seconds
                )
            )

        by_timestamp = {}
        for row in rows:
            timestamp = row.get("timestamp")
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if timestamp is not None:
                if timezone.is_naive(timestamp):
                    timestamp = timestamp.replace(tzinfo=dt_timezone.utc)
                by_timestamp[timestamp] = row.get("value")

        # COUNT is zero on empty windows in the live evaluator. Other metrics
        # return NULL when no matching data exists, not a synthetic zero.
        if monitor.metric_type == MonitorMetricTypeChoices.COUNT_OF_ERRORS:
            for index in range(count):
                by_timestamp.setdefault(
                    start + timedelta(seconds=index * seconds), 0
                )

        percentage = (
            monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE
        )
        for timestamp, value in sorted(by_timestamp.items()):
            # Never include a partial first/last bucket.
            if timestamp < start or timestamp + timedelta(seconds=seconds) > end:
                continue
            # v2 represents missing token data as zero; the live evaluator
            # deliberately returns NULL instead of firing a low-spend alert.
            if monitor.metric_type == MonitorMetricTypeChoices.TOKEN_USAGE and value == 0:
                value = None
            critical = monitor.critical_threshold_value
            warning = monitor.warning_threshold_value
            ready = _finite(value)
            if percentage:
                critical = warning = None
                lookback = monitor.auto_threshold_time_window
                # History ends before the metric's full evaluation window,
                # including a daily metric's trailing day, as in the worker.
                history_end = (
                    timestamp + timedelta(seconds=seconds) - timedelta(days=1)
                    if daily else timestamp
                )
                history_start = history_end - timedelta(minutes=lookback)
                if (
                    not 0 < lookback <= _PREVIEW_MINUTES
                    or history_start < read_start
                ):
                    ready = False
                elif ready:
                    earlier = uses_earlier_rule(monitor)
                    if earlier:
                        query = builder.build_earlier_band_query(
                            monitor.metric_type, history_start, history_end,
                            interval_kind=get_interval_kind(monitor),
                        )
                    else:
                        query = builder.build_historical_stats_query(
                            monitor.metric_type, history_start, history_end,
                            periods=baseline_periods(monitor, history_end - history_start),
                        )
                    stats = execute(query)
                    mean = stats[0].get("mean") if stats else None
                    stddev = stats[0].get("stddev") if stats else None
                    ready = _finite(mean) and (not earlier or _finite(stddev))
                    if ready:
                        sign = (
                            1 if monitor.threshold_operator == "greater_than" else -1
                        )
                        if monitor.critical_threshold_value is not None:
                            percent = monitor.critical_threshold_value / 100
                            critical = (
                                mean + sign * stddev * (1 + percent)
                                if earlier else mean * (1 + sign * percent)
                            )
                        if monitor.warning_threshold_value is not None:
                            percent = monitor.warning_threshold_value / 100
                            warning = (
                                mean + sign * stddev * (1 + percent)
                                if earlier else mean * (1 + sign * percent)
                            )

            # A non-finite derived band is unknown, not a healthy interval.
            ready = ready and (
                monitor.critical_threshold_value is None or _finite(critical)
            ) and (monitor.warning_threshold_value is None or _finite(warning))
            bucket_status = "insufficient_data"
            if ready:
                bucket_status = "healthy"
                if _finite(critical) and _compare(
                    value, monitor.threshold_operator, critical
                ):
                    bucket_status = "critical"
                elif _finite(warning) and _compare(
                    value, monitor.threshold_operator, warning
                ):
                    bucket_status = "warning"
            point = {
                "timestamp": timestamp.isoformat(),
                "end_timestamp": (timestamp + timedelta(seconds=seconds)).isoformat(),
                "value": value if _finite(value) else None,
                "critical_threshold": critical if _finite(critical) else None,
                "warning_threshold": warning if _finite(warning) else None,
                "status": bucket_status,
            }
            result["series"].append(point)
            if ready:
                result["evaluated_count"] += 1
            if bucket_status in ("critical", "warning"):
                result["fired_buckets"].append(point)
        deadline.remaining_ms(floor_ms=1)
    except Exception as exc:
        if is_read_budget_error(exc) or isinstance(exc, ReadDeadlineExceeded):
            raise MonitorGraphUnavailable(
                "Monitor preview exceeded its request budget"
            ) from exc
        raise

    result["bucket_count"] = len(result["series"])
    result["fire_count"] = len(result["fired_buckets"])
    return result
