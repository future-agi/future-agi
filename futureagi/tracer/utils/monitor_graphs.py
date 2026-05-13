import math
from collections import deque
from datetime import datetime as dt_datetime
from datetime import timedelta

import structlog
from django.db.models import (
    Avg,
    Case,
    Count,
    DateTimeField,
    ExpressionWrapper,
    F,
    FloatField,
    Func,
    IntegerField,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Extract
from django.utils import timezone

logger = structlog.get_logger(__name__)
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService, QueryType
from tracer.utils.eval_tasks import parsing_evaltask_filters


def _build_monitor_graph_ch_builder(monitor):
    """Construct a MonitorMetricsQueryBuilder from a monitor instance."""
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

    return MonitorMetricsQueryBuilder(
        project_id=str(monitor.project_id),
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


# Helper class to use PostgreSQL's to_timestamp function in the ORM.
class ToTimestamp(Func):
    function = "TO_TIMESTAMP"
    output_field = DateTimeField()


def _apply_time_window_filter(queryset, start_time=None, end_time=None):
    """Applies time window filters to a queryset based on the created_at field."""
    if start_time:
        queryset = queryset.filter(created_at__gte=start_time)
    if end_time:
        queryset = queryset.filter(created_at__lte=end_time)
    return queryset


def _get_frequency_seconds(monitor):
    """Returns the frequency in seconds for a given monitor."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        frequency_seconds = 24 * 60 * 60  # 1 day
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        frequency_seconds = 30 * 24 * 60 * 60  # 30 days
    else:
        frequency_seconds = monitor.alert_frequency * 60
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
    if analytics.should_use_clickhouse(QueryType.MONITOR_METRICS):
        try:
            frequency_seconds = _get_frequency_seconds(monitor)
            if not frequency_seconds:
                return []

            effective_end = time_window_end or timezone.now()
            effective_start = time_window_start or (effective_end - timedelta(days=7))

            builder = _build_monitor_graph_ch_builder(monitor)
            query, params = builder.build_time_series_query(
                monitor.metric_type,
                effective_start,
                effective_end,
                frequency_seconds,
            )
            result = analytics.execute_ch_query(query, params, timeout_ms=10000)
            return _format_ch_time_series(result.data)
        except Exception as e:
            logger.warning(
                "CH static graph data failed, falling back to PG",
                error=str(e),
                monitor_id=str(monitor.id),
            )

    # --- PostgreSQL fallback ---
    try:
        metric_type = monitor.metric_type

        frequency_seconds = _get_frequency_seconds(monitor)

        if not frequency_seconds:
            logger.warning(
                f"Monitor {monitor.id} has no alert_frequency. Cannot generate graph."
            )
            return []

        # Handle evaluation metrics separately as they use a different model
        if metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS:
            return _get_eval_metric_graph_data(
                monitor, time_window_start, time_window_end, frequency_seconds
            )

        # Handle group-based error-free rate metrics separately
        if metric_type in [
            MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES,
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
        ]:
            return _get_group_error_free_rate_data(
                monitor, time_window_start, time_window_end, frequency_seconds
            )

        filters = parsing_evaltask_filters(monitor.filters)

        base_queryset = ObservationSpan.objects.filter(project=monitor.project)

        base_queryset = _apply_time_window_filter(
            base_queryset, time_window_start, time_window_end
        )
        base_queryset = base_queryset.filter(filters)

        bucket_annotation = ToTimestamp(F("epoch") - (F("epoch") % frequency_seconds))
        queryset = base_queryset.annotate(
            epoch=Extract("created_at", "epoch")
        ).annotate(timestamp=bucket_annotation)

        # Get the correct aggregation expression for the metric type
        aggregation, queryset = _get_aggregation_expression(metric_type, queryset)

        if aggregation is None:
            logger.warning(f"Graphing for metric type {metric_type} is not supported.")
            return []

        # Group by the calculated timestamp bucket and apply the aggregation
        graph_queryset = (
            queryset.values("timestamp")
            .annotate(value=aggregation)
            .values("timestamp", "value")
            .order_by("timestamp")
        )

        result = [
            {
                "timestamp": item["timestamp"].isoformat(),
                "value": item["value"] if item["value"] is not None else 0,
            }
            for item in graph_queryset
        ]

        return result

    except Exception as e:
        logger.error(f"Error generating graph data: {e}", exc_info=True)
        return []


def _get_aggregation_expression(metric_type, queryset):
    """Returns the correct Django ORM aggregation expression for a given metric type."""
    if metric_type in [
        MonitorMetricTypeChoices.TOKEN_USAGE,
        MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
        MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
    ]:
        return Sum("total_tokens"), queryset

    if metric_type == MonitorMetricTypeChoices.COUNT_OF_ERRORS:
        return Count("id", filter=Q(status="ERROR")), queryset

    if metric_type in [
        MonitorMetricTypeChoices.SPAN_RESPONSE_TIME,
        MonitorMetricTypeChoices.LLM_RESPONSE_TIME,
    ]:
        if metric_type == MonitorMetricTypeChoices.LLM_RESPONSE_TIME:
            queryset = queryset.filter(observation_type="llm")
        return Avg("latency_ms"), queryset

    rate_aggregation = Avg(
        Case(
            When(status="ERROR", then=Value(1.0)),
            default=Value(0.0),
            output_field=FloatField(),
        )
    )

    if metric_type in [
        MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING,
        MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
    ]:
        obs_type = (
            "tool"
            if metric_type == MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING
            else "llm"
        )
        queryset = queryset.filter(observation_type=obs_type)
        return rate_aggregation, queryset

    return None, queryset


def _get_eval_metric_graph_data(
    monitor, time_window_start=None, time_window_end=None, frequency_seconds=None
):
    """Handles graph data generation for evaluation metrics."""
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
        eval_output_type = custom_eval_config.eval_template.config.get("output")
    except CustomEvalConfig.DoesNotExist:
        logger.error(
            f"CustomEvalConfig {monitor.metric} not found for monitor {monitor.id}"
        )
        return []

    filters = parsing_evaltask_filters(monitor.filters)

    base_queryset = EvalLogger.objects.filter(
        custom_eval_config=custom_eval_config,
        target_type="span",
        observation_span__in=ObservationSpan.objects.filter(filters),
    )

    base_queryset = _apply_time_window_filter(
        base_queryset, time_window_start, time_window_end
    )

    aggregation = None
    if eval_output_type == EvalOutputType.SCORE:
        aggregation = Avg("output_float")
    elif eval_output_type == EvalOutputType.PASS_FAIL:
        output_bool = monitor.threshold_metric_value == "Passed"
        aggregation = Avg(
            Case(
                When(output_bool=output_bool, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
    elif eval_output_type == EvalOutputType.CHOICES:
        choice = monitor.threshold_metric_value
        if not choice:
            return []
        aggregation = Avg(
            Case(
                When(output_str_list__contains=[choice], then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )

    if not aggregation:
        return []

    bucket_annotation = ToTimestamp(F("epoch") - (F("epoch") % frequency_seconds))
    queryset = base_queryset.annotate(epoch=Extract("created_at", "epoch")).annotate(
        timestamp=bucket_annotation
    )

    graph_queryset = (
        queryset.values("timestamp")
        .annotate(value=aggregation)
        .values("timestamp", "value")
        .order_by("timestamp")
    )

    result = [
        {
            "timestamp": item["timestamp"].isoformat(),
            "value": item["value"] if item["value"] is not None else 0,
        }
        for item in graph_queryset
    ]

    return result


def _get_group_error_free_rate_data(
    monitor, time_window_start=None, time_window_end=None, frequency_seconds=None
):
    """Handles graph data generation for group-based error-free rate metrics."""
    metric_type = monitor.metric_type
    filters = parsing_evaltask_filters(monitor.filters)

    # Build the base queryset with optional time filtering
    base_queryset = ObservationSpan.objects.filter(project=monitor.project)

    base_queryset = _apply_time_window_filter(
        base_queryset, time_window_start, time_window_end
    )

    base_queryset = base_queryset.filter(filters)

    # Filter based on metric type
    if metric_type == MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES:
        base_queryset = base_queryset.exclude(trace__session__isnull=True)
        group_field = "trace__session"
    elif metric_type == MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES:
        base_queryset = base_queryset.exclude(provider__isnull=True)
        group_field = "provider"
    else:
        return []

    # Add timestamp buckets
    bucket_annotation = ToTimestamp(F("epoch") - (F("epoch") % frequency_seconds))
    queryset = base_queryset.annotate(epoch=Extract("created_at", "epoch")).annotate(
        timestamp=bucket_annotation
    )

    # Single query to calculate error-free rate per timestamp
    timestamp_stats = (
        queryset.values("timestamp", group_field)
        .annotate(
            has_error=Case(
                When(status="ERROR", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .values("timestamp")
        .annotate(
            total_groups=Count(group_field, distinct=True),
            error_free_groups=Count(group_field, distinct=True, filter=Q(has_error=0)),
        )
        .annotate(
            error_free_rate=Case(
                When(total_groups=0, then=Value(0.0)),
                default=ExpressionWrapper(
                    F("error_free_groups") * 1.0 / F("total_groups"),
                    output_field=FloatField(),
                ),
            )
        )
        .values("timestamp", "error_free_rate")
        .order_by("timestamp")
    )

    # Format the result to match expected structure
    result = [
        {
            "timestamp": item["timestamp"].isoformat(),
            "value": (
                item["error_free_rate"] if item["error_free_rate"] is not None else 0
            ),
        }
        for item in timestamp_stats
    ]

    return result


def _calculate_std_dev(data):
    """Helper to calculate standard deviation."""
    n = len(data)
    if n < 2:
        return 0.0
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / (n - 1)
    return math.sqrt(variance)


def _get_eval_metric_buckets(
    monitor, extended_start, time_window_end, bucket_annotation, filters
):
    """Handles bucket creation for EVALUATION_METRICS."""
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
        eval_output_type = custom_eval_config.eval_template.config.get("output")
    except CustomEvalConfig.DoesNotExist:
        logger.error(
            f"CustomEvalConfig {monitor.metric} not found for monitor {monitor.id}"
        )
        return None

    base_queryset = EvalLogger.objects.filter(
        custom_eval_config=custom_eval_config,
        target_type="span",
        observation_span__in=ObservationSpan.objects.filter(filters),
    )
    base_queryset = _apply_time_window_filter(
        base_queryset, extended_start, time_window_end
    )

    aggregation = None
    if eval_output_type == EvalOutputType.SCORE:
        aggregation = Avg("output_float")
    elif eval_output_type == EvalOutputType.PASS_FAIL:
        output_bool = monitor.threshold_metric_value == "Passed"
        aggregation = Avg(
            Case(
                When(output_bool=output_bool, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
    elif eval_output_type == EvalOutputType.CHOICES:
        choice = monitor.threshold_metric_value
        if not choice:
            return None
        aggregation = Avg(
            Case(
                When(output_str_list__contains=[choice], then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )

    if not aggregation:
        return None

    bucket_queryset = (
        base_queryset.annotate(epoch=Extract("created_at", "epoch"))
        .annotate(timestamp=bucket_annotation)
        .values("timestamp")
        .annotate(value=aggregation)
        .order_by("timestamp")
    )
    return list(bucket_queryset)


def _get_group_error_rate_buckets(
    monitor, extended_start, time_window_end, bucket_annotation, filters
):
    """Handles bucket creation for group-based error rate metrics."""
    metric_type = monitor.metric_type
    base_queryset = ObservationSpan.objects.filter(project=monitor.project)
    base_queryset = _apply_time_window_filter(
        base_queryset, extended_start, time_window_end
    )
    base_queryset = base_queryset.filter(filters)

    if metric_type == MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES:
        base_queryset = base_queryset.exclude(trace__session__isnull=True)
        group_field = "trace__session"
    elif metric_type == MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES:
        base_queryset = base_queryset.exclude(provider__isnull=True)
        group_field = "provider"
    else:
        return None

    queryset = base_queryset.annotate(epoch=Extract("created_at", "epoch")).annotate(
        timestamp=bucket_annotation
    )

    timestamp_stats = (
        queryset.values("timestamp", group_field)
        .annotate(
            has_error=Case(
                When(status="ERROR", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .values("timestamp")
        .annotate(
            total_groups=Count(group_field, distinct=True),
            error_free_groups=Count(group_field, distinct=True, filter=Q(has_error=0)),
        )
        .annotate(
            value=Case(
                When(total_groups=0, then=Value(0.0)),
                default=ExpressionWrapper(
                    F("error_free_groups") * 1.0 / F("total_groups"),
                    output_field=FloatField(),
                ),
            )
        )
        .values("timestamp", "value")
        .order_by("timestamp")
    )

    return list(timestamp_stats)


def _get_default_observation_span_buckets(
    monitor, extended_start, time_window_end, bucket_annotation, filters
):
    """Handles bucket creation for default ObservationSpan metrics."""
    metric_type = monitor.metric_type
    base_queryset = ObservationSpan.objects.filter(project=monitor.project)
    base_queryset = _apply_time_window_filter(
        base_queryset, extended_start, time_window_end
    )
    base_queryset = base_queryset.filter(filters)

    aggregation, queryset = _get_aggregation_expression(metric_type, base_queryset)

    if aggregation is None:
        logger.warning(f"Graphing for metric type {metric_type} is not supported.")
        return None

    bucket_queryset = (
        queryset.annotate(epoch=Extract("created_at", "epoch"))
        .annotate(timestamp=bucket_annotation)
        .values("timestamp")
        .annotate(value=aggregation)
        .order_by("timestamp")
    )
    return list(bucket_queryset)


def _get_buckets_for_percentage_change(
    monitor, extended_start, time_window_end, frequency_seconds, filters
):
    """Fetches and aggregates data into time buckets for percentage change analysis."""
    metric_type = monitor.metric_type
    bucket_annotation = ToTimestamp(F("epoch") - (F("epoch") % frequency_seconds))

    if metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS:
        return _get_eval_metric_buckets(
            monitor, extended_start, time_window_end, bucket_annotation, filters
        )

    elif metric_type in [
        MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES,
        MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
    ]:
        return _get_group_error_rate_buckets(
            monitor, extended_start, time_window_end, bucket_annotation, filters
        )

    else:
        return _get_default_observation_span_buckets(
            monitor, extended_start, time_window_end, bucket_annotation, filters
        )


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
    if analytics.should_use_clickhouse(QueryType.MONITOR_METRICS):
        try:
            frequency_seconds = _get_frequency_seconds(monitor)
            if not frequency_seconds:
                return {"graph_data": [], "alert_bar_data": []}

            auto_threshold_time_window = timedelta(
                minutes=monitor.auto_threshold_time_window
            )

            effective_end = time_window_end or timezone.now()
            extended_start = None
            if time_window_start:
                extended_start = time_window_start - auto_threshold_time_window

            builder = _build_monitor_graph_ch_builder(monitor)
            query, params = builder.build_time_series_query(
                monitor.metric_type,
                extended_start or (effective_end - timedelta(days=30)),
                effective_end,
                frequency_seconds,
            )
            result = analytics.execute_ch_query(query, params, timeout_ms=10000)

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
                "CH percentage change graph data failed, falling back to PG",
                error=str(e),
                monitor_id=str(monitor.id),
            )

    # --- PostgreSQL fallback ---
    try:
        frequency_seconds = _get_frequency_seconds(monitor)
        if not frequency_seconds:
            logger.warning(
                f"Monitor {monitor.id} has no alert_frequency. Cannot generate graph."
            )
            return {"graph_data": [], "alert_bar_data": []}

        auto_threshold_time_window = timedelta(
            minutes=monitor.auto_threshold_time_window
        )
        filters = parsing_evaltask_filters(monitor.filters)

        # Extend time window backwards for historical data
        extended_start = None
        if time_window_start:
            extended_start = time_window_start - auto_threshold_time_window

        all_buckets = _get_buckets_for_percentage_change(
            monitor, extended_start, time_window_end, frequency_seconds, filters
        )

        if all_buckets is None:
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
        logger.error(f"Error generating graph data: {e}", exc_info=True)
        return {"graph_data": [], "alert_bar_data": []}


def _compare(value, op, threshold):
    """Helper to perform comparison based on operator."""
    if op == ComparisonOperatorChoices.GREATER_THAN:
        return value > threshold
    if op == ComparisonOperatorChoices.LESS_THAN:
        return value < threshold
    return False


class Coalesce(Func):
    """Helper to use COALESCE function in Django ORM."""

    function = "COALESCE"

    def __init__(self, *expressions, **extra):
        if len(expressions) < 2:
            raise ValueError("Coalesce must take at least two expressions")
        super().__init__(*expressions, **extra)


def _ensure_timezone_aware(dt):
    """
    Ensures a datetime object is timezone-aware.
    Returns the datetime as-is if already timezone-aware,
    or converts it using Django's default timezone if naive.
    """
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
