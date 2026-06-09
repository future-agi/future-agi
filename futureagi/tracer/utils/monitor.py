import statistics
from datetime import timedelta

import pandas as pd
import structlog
from django.db.models import (
    Avg,
    Case,
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Q,
    StdDev,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Now, Trunc
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from slack_sdk.errors import SlackApiError

# from prophet import Prophet
from slack_sdk.webhook import WebhookClient

logger = structlog.get_logger(__name__)
from tfc.temporal import temporal_activity
from tfc.utils.email import email_helper
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.monitor import (
    AlertTypeChoices,
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
    UserAlertMonitorLog,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService, QueryType
from tracer.utils.eval_tasks import parsing_evaltask_filters


def _build_monitor_ch_builder(monitor):
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


def _mute_monitor(monitor):
    """Mutes a monitor."""
    monitor.is_mute = True
    monitor.save(update_fields=["is_mute"])


def _get_interval_kind(monitor):
    """Gets the interval kind for the monitor."""
    interval = timedelta(minutes=monitor.alert_frequency)

    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        interval = timedelta(days=1)
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        interval = timedelta(days=30)

    # Django's Trunc function requires a string literal. We convert the
    # timedelta to the most appropriate 'kind' string.
    if interval.days >= 30:
        interval_kind = "month"
    elif interval.days >= 1:
        interval_kind = "day"
    elif interval.total_seconds() >= 3600:
        interval_kind = "hour"
    else:
        interval_kind = "minute"

    return interval_kind


def _send_alert_email(monitor, message, alert_type):
    """Sends an email notification for an alert."""
    if not monitor.notification_emails:
        return
    try:
        email_helper(
            mail_subject=f"[{alert_type.upper()}] Alert Triggered: {monitor.name}",
            template_name="alert_user.html",
            template_data={  # TODO: add link to the alert and change the template data
                "alert_name": monitor.name,
                "alert_message": message,
                "alert_type": alert_type,
            },
            to_email_list=list(monitor.notification_emails),
        )
        logger.info(f"Sent {alert_type} alert email for monitor {monitor.id}")
    except Exception as e:
        logger.error(
            f"Failed to send {alert_type} alert email for monitor {monitor.id}: {e}"
        )


def _send_slack_notification(monitor, message, alert_type):
    """Sends a Slack notification for an alert."""
    if not monitor.slack_webhook_url:
        return

    webhook = WebhookClient(monitor.slack_webhook_url)

    title = f"[{alert_type.upper()}] Alert Triggered: {monitor.name}"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":bell: {title}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
    ]

    if monitor.slack_notes:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Notes:*\n{monitor.slack_notes}",
                },
            }
        )

    try:
        webhook.send(blocks=blocks)
        logger.info(f"Sent {alert_type} Slack notification for monitor {monitor.id}")
    except SlackApiError as e:
        logger.error(
            f"Failed to send {alert_type} Slack notification for monitor {monitor.id}: {e}"
        )


def _handle_alert_trigger(
    monitor, message, alert_type, time_window_start=None, now=None
):
    """Handles the actions when an alert is triggered."""
    UserAlertMonitorLog.objects.create(
        alert=monitor,
        type=alert_type,
        message=message,
        time_window_start=time_window_start,
        time_window_end=now,
    )
    _send_alert_email(monitor, message, alert_type)
    _send_slack_notification(monitor, message, alert_type)


@temporal_activity(
    max_retries=0,
    time_limit=3600,
    queue="tasks_l",
)
def check_alerts():
    """
    Periodically checks all active monitors for alert conditions.
    """
    now = timezone.now()
    logger.info(f"Starting alert check job at {now}")

    monitors_to_check = UserAlertMonitor.objects.filter(is_mute=False).filter(
        Q(last_checked_at__isnull=True)
        | Q(
            last_checked_at__lte=Now()
            - ExpressionWrapper(
                F("alert_frequency") * timedelta(minutes=1),
                output_field=DurationField(),
            )
        )
    )

    monitor_ids = list(monitors_to_check.values_list("id", flat=True))
    monitors_to_check.update(last_checked_at=now)

    for monitor_id in monitor_ids:
        process_monitor_task.delay(monitor_id, now.isoformat())

    logger.info("Alert check job finished.")


@temporal_activity(
    max_retries=0,
    time_limit=3600,
    queue="tasks_l",
)
def process_monitor_task(monitor_id, now_iso):
    """Processes a single monitor."""
    now = parse_datetime(now_iso)
    monitor = UserAlertMonitor.objects.get(id=monitor_id)

    logger.info(f"Checking monitor: {monitor.name} ({monitor.id})")
    try:
        _process_monitor(monitor, now)
    except Exception as e:
        # _mute_monitor(monitor)
        raise Exception(f"Error processing monitor {monitor.id}: {e}")


def _process_monitor(monitor, now):
    """Processes a single monitor."""
    time_window_start = now - timedelta(minutes=monitor.alert_frequency)

    metric_value = _get_metric_value(monitor, time_window_start, now)
    if metric_value is None:
        return

    _check_thresholds_and_alert(monitor, metric_value, time_window_start, now)


def _get_metric_value(monitor, start_time, end_time):
    """Calculates the value of the metric for the given time window."""

    # --- ClickHouse dispatch ---
    analytics = AnalyticsQueryService()
    if analytics.should_use_clickhouse(QueryType.MONITOR_METRICS):
        try:
            builder = _build_monitor_ch_builder(monitor)
            metric_type = monitor.metric_type

            # For DAILY/MONTHLY tokens, override start_time
            ch_start = start_time
            if metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
                ch_start = end_time - timedelta(days=1)
            elif metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
                ch_start = end_time - timedelta(days=30)

            query, params = builder.build_metric_value_query(
                metric_type, ch_start, end_time
            )
            result = analytics.execute_ch_query(query, params, timeout_ms=5000)
            if result.data:
                return result.data[0].get("value")
            return None
        except Exception as e:
            logger.warning(
                "CH monitor metric failed, falling back to PG",
                error=str(e),
                monitor_id=str(monitor.id),
            )

    # --- PostgreSQL fallback ---
    filters = parsing_evaltask_filters(monitor.filters)
    base_queryset = ObservationSpan.objects.filter(
        project=monitor.project, created_at__range=(start_time, end_time)
    )

    base_queryset = base_queryset.filter(filters)

    metric_type = monitor.metric_type
    value = None

    match metric_type:
        case MonitorMetricTypeChoices.COUNT_OF_ERRORS:
            value = base_queryset.filter(status="ERROR").count()

        case MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING:
            result = base_queryset.filter(observation_type="tool").aggregate(
                total_calls=Count("id"),
                error_calls=Count("id", filter=Q(status="ERROR")),
            )
            if result["total_calls"] == 0:
                value = None
            else:
                value = result["error_calls"] / result["total_calls"]

        case MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES:
            result = (
                base_queryset.exclude(trace__session__isnull=True)
                .values("trace__session")
                .annotate(error_count=Count("id", filter=Q(status="ERROR")))
                .aggregate(
                    total_sessions=Count("trace__session"),
                    error_free_sessions=Count(
                        "trace__session", filter=Q(error_count=0)
                    ),
                )
            )
            total_sessions = result["total_sessions"]
            error_free_sessions = result["error_free_sessions"]

            if not total_sessions:
                value = None
            else:
                value = error_free_sessions / total_sessions

        case (
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES
        ):  # here are we doing error free rate or error rate ?
            result = (
                base_queryset.exclude(provider__isnull=True)
                .values("provider")
                .annotate(error_count=Count("id", filter=Q(status="ERROR")))
                .aggregate(
                    total_providers=Count("provider"),
                    error_free_providers=Count("provider", filter=Q(error_count=0)),
                )
            )
            total_providers = result["total_providers"]
            error_free_providers = result["error_free_providers"]

            if not total_providers:
                value = None
            else:
                value = error_free_providers / total_providers

        case MonitorMetricTypeChoices.LLM_API_FAILURE_RATES:
            result = base_queryset.filter(observation_type="llm").aggregate(
                total_calls=Count("id"),
                error_calls=Count("id", filter=Q(status="ERROR")),
            )
            if result["total_calls"] == 0:
                value = None
            else:
                value = result["error_calls"] / result["total_calls"]

        case MonitorMetricTypeChoices.SPAN_RESPONSE_TIME:
            value = base_queryset.aggregate(avg_latency=Avg("latency_ms"))[
                "avg_latency"
            ]

        case MonitorMetricTypeChoices.LLM_RESPONSE_TIME:
            value = base_queryset.filter(observation_type="llm").aggregate(
                avg_latency=Avg("latency_ms")
            )["avg_latency"]

        case MonitorMetricTypeChoices.TOKEN_USAGE:
            value = base_queryset.aggregate(total=Sum("total_tokens"))["total"]

        case MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
            daily_start_time = end_time - timedelta(days=1)
            value = (
                ObservationSpan.objects.filter(
                    project=monitor.project, created_at__gte=daily_start_time
                )
                .filter(filters)
                .aggregate(total=Sum("total_tokens"))["total"]
            )

        case MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
            monthly_start_time = end_time - timedelta(days=30)
            value = (
                ObservationSpan.objects.filter(
                    project=monitor.project, created_at__gte=monthly_start_time
                )
                .filter(filters)
                .aggregate(total=Sum("total_tokens"))["total"]
            )

        case MonitorMetricTypeChoices.EVALUATION_METRICS:
            value, _ = _get_evaluation_metric_stats(monitor, start_time, end_time)

        case _:
            value = None

    return value


def _get_evaluation_metric_stats(monitor, start_time, end_time):
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
        eval_output_type = custom_eval_config.eval_template.config.get("output")
    except CustomEvalConfig.DoesNotExist:
        logger.error(
            f"CustomEvalConfig {monitor.metric} not found for monitor {monitor.id}"
        )
        _mute_monitor(monitor)
        return None, None

    filters = parsing_evaltask_filters(monitor.filters)

    eval_results = EvalLogger.objects.filter(
        custom_eval_config=custom_eval_config,
        target_type="span",
        created_at__range=(start_time, end_time),
        observation_span__in=ObservationSpan.objects.filter(filters),
    )
    if not eval_results.exists():
        return None, None

    stats = None
    if eval_output_type == EvalOutputType.SCORE:
        stats = eval_results.aggregate(
            mean=Avg("output_float"), stddev=StdDev("output_float")
        )
    elif eval_output_type == EvalOutputType.PASS_FAIL:
        output_bool = True if monitor.threshold_metric_value == "Passed" else False
        annotated_results = eval_results.annotate(
            pass_value=Case(
                When(output_bool=output_bool, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
        stats = annotated_results.aggregate(
            mean=Avg("pass_value"), stddev=StdDev("pass_value")
        )
    elif eval_output_type == EvalOutputType.CHOICES:
        choice = monitor.threshold_metric_value
        if not choice:
            logger.error(f"Choice is not set for monitor {monitor.id}")
            return None, None
        annotated_results = eval_results.annotate(
            choice_value=Case(
                When(output_str_list__contains=[choice], then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
        stats = annotated_results.aggregate(
            mean=Avg("choice_value"), stddev=StdDev("choice_value")
        )

    if stats:
        return stats.get("mean"), stats.get("stddev")

    return None, None


def _get_stats_for_time_aggregated_metrics(monitor, start_time, end_time):
    """
    Orchestrates statistics calculation for time-aggregated metrics.
    It determines the interval, fetches data, and calculates stats.
    """
    interval_kind = _get_interval_kind(monitor)

    time_series_data = _get_time_series_data_for_time_aggregated_metrics(
        monitor, interval_kind, start_time, end_time
    )
    return _calculate_stats_from_time_series(time_series_data)


def _get_time_series_data_for_time_aggregated_metrics(
    monitor, interval_kind, start_time=None, end_time=None
):  # TODO: do we need to make the buckets dynamic (refer to the code in monitor_graphs.py)
    """
    Groups spans in certain intervals and returns a dictionary of {timestamp: value}.
    `interval_kind` must be one of 'minute', 'hour', 'day', 'week', 'month', 'year'.
    """
    filters = parsing_evaltask_filters(monitor.filters)
    base_queryset = ObservationSpan.objects.filter(project=monitor.project).filter(
        filters
    )
    if start_time:
        base_queryset = base_queryset.filter(created_at__gte=start_time)
    if end_time:
        base_queryset = base_queryset.filter(created_at__lte=end_time)

    metric_type = monitor.metric_type

    queryset = base_queryset.annotate(
        timestamp=Trunc("created_at", interval_kind, output_field=DateTimeField())
    ).values("timestamp")

    annotation = None
    if metric_type in [
        MonitorMetricTypeChoices.TOKEN_USAGE,
        MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
        MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
    ]:
        annotation = Sum("total_tokens")
    elif metric_type == MonitorMetricTypeChoices.COUNT_OF_ERRORS:
        annotation = Count("id", filter=Q(status="ERROR"))

    if not annotation:
        return {}

    queryset = queryset.annotate(value=annotation).values("timestamp", "value")

    result = {
        item["timestamp"].strftime("%Y-%m-%d %H:%M:%S"): item["value"]
        for item in queryset
        if item["value"] is not None
    }
    return result


def _calculate_stats_from_time_series(time_series_data: dict):
    """Takes time series data as a dictionary and returns mean and standard deviation."""
    if not time_series_data:
        return 0, 0

    values = list(time_series_data.values())

    if len(values) < 2:
        return statistics.mean(values) if values else 0, 0

    mean = statistics.mean(values)
    stddev = statistics.stdev(values)

    return mean, stddev


def _get_historical_stats(monitor, start_time, end_time):
    """Calculates the historical stats for the given time window."""

    # --- ClickHouse dispatch ---
    analytics = AnalyticsQueryService()
    if analytics.should_use_clickhouse(QueryType.MONITOR_METRICS):
        try:
            metric_type = monitor.metric_type

            # For time-aggregated metrics, compute stats from time-series buckets
            if metric_type in (
                MonitorMetricTypeChoices.COUNT_OF_ERRORS,
                MonitorMetricTypeChoices.TOKEN_USAGE,
                MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
                MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
            ):
                return _get_stats_for_time_aggregated_metrics(
                    monitor, start_time, end_time
                )

            builder = _build_monitor_ch_builder(monitor)
            query, params = builder.build_historical_stats_query(
                metric_type, start_time, end_time
            )
            result = analytics.execute_ch_query(query, params, timeout_ms=5000)
            if result.data:
                row = result.data[0]
                return row.get("mean"), row.get("stddev")
            return None, None
        except Exception as e:
            logger.warning(
                "CH historical stats failed, falling back to PG",
                error=str(e),
                monitor_id=str(monitor.id),
            )

    # --- PostgreSQL fallback ---
    filters = parsing_evaltask_filters(monitor.filters)
    base_queryset = ObservationSpan.objects.filter(
        project=monitor.project, created_at__range=(start_time, end_time)
    ).filter(filters)

    metric_type = monitor.metric_type
    historical_mean = None
    historical_stddev = None

    stats = None

    match metric_type:
        case (
            MonitorMetricTypeChoices.COUNT_OF_ERRORS
            | MonitorMetricTypeChoices.TOKEN_USAGE
            | MonitorMetricTypeChoices.DAILY_TOKENS_SPENT
            | MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT
        ):
            historical_mean, historical_stddev = _get_stats_for_time_aggregated_metrics(
                monitor, start_time, end_time
            )

        case MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING:
            stats = (
                base_queryset.filter(observation_type="tool")
                .annotate(
                    is_error=Case(
                        When(status="ERROR", then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                )
                .aggregate(mean=Avg("is_error"), stddev=StdDev("is_error"))
            )

        case MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES:
            session_values = (
                base_queryset.exclude(trace__session__isnull=True)
                .values("trace__session")
                .annotate(error_count=Count("id", filter=Q(status="ERROR")))
                .annotate(
                    is_error_free=Case(
                        When(error_count__gt=0, then=Value(0.0)),
                        default=Value(1.0),
                        output_field=FloatField(),
                    )
                )
            )
            stats = session_values.aggregate(
                mean=Avg("is_error_free"), stddev=StdDev("is_error_free")
            )

        case (
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES
        ):  # here are we doing error free rate or error rate ?
            provider_values = (
                base_queryset.exclude(provider__isnull=True)
                .values("provider")
                .annotate(error_count=Count("id", filter=Q(status="ERROR")))
                .annotate(
                    is_error_free=Case(
                        When(error_count__gt=0, then=Value(0.0)),
                        default=Value(1.0),
                        output_field=FloatField(),
                    )
                )
            )
            stats = provider_values.aggregate(
                mean=Avg("is_error_free"), stddev=StdDev("is_error_free")
            )

        case MonitorMetricTypeChoices.LLM_API_FAILURE_RATES:
            stats = (
                base_queryset.filter(observation_type="llm")
                .annotate(
                    is_error=Case(
                        When(status="ERROR", then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                )
                .aggregate(mean=Avg("is_error"), stddev=StdDev("is_error"))
            )

        case MonitorMetricTypeChoices.SPAN_RESPONSE_TIME:
            stats = base_queryset.aggregate(
                mean=Avg("latency_ms"), stddev=StdDev("latency_ms")
            )

        case MonitorMetricTypeChoices.LLM_RESPONSE_TIME:
            stats = base_queryset.filter(observation_type="llm").aggregate(
                mean=Avg("latency_ms"), stddev=StdDev("latency_ms")
            )

        case MonitorMetricTypeChoices.EVALUATION_METRICS:
            historical_mean, historical_stddev = _get_evaluation_metric_stats(
                monitor, start_time, end_time
            )

    if stats:
        historical_mean = stats.get("mean")
        historical_stddev = stats.get("stddev")

    return historical_mean, historical_stddev


def _check_thresholds_and_alert(monitor, current_value, time_window_start, now):
    """Checks the metric value against the monitor's thresholds and alerts if needed."""

    if monitor.threshold_type == ThresholdCalculationMethodChoices.STATIC:
        _check_static_threshold(monitor, current_value, time_window_start, now)

    elif monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE:
        _check_percentage_change_threshold(
            monitor, current_value, time_window_start, now
        )

    # elif monitor.threshold_type == ThresholdCalculationMethodChoices.ANOMALY_DETECTION:
    #     _check_anomaly_detection_threshold(monitor, current_value, now)


def _check_static_threshold(monitor, current_value, time_window_start, now):
    """Checks for alerts based on static thresholds."""
    op = monitor.threshold_operator
    critical_val = monitor.critical_threshold_value
    warning_val = monitor.warning_threshold_value

    alert_type = None
    threshold_val = None

    if critical_val is not None and _compare(current_value, op, critical_val):
        alert_type = AlertTypeChoices.CRITICAL
        threshold_val = critical_val
    elif warning_val is not None and _compare(current_value, op, warning_val):
        alert_type = AlertTypeChoices.WARNING
        threshold_val = warning_val

    if alert_type:
        message = (
            f"Metric '{monitor.name}' for Project '{monitor.project.name}'"
            f"({current_value:.2f}) breached the {alert_type} threshold "
            f"({monitor.threshold_operator} {threshold_val})."
        )
        _handle_alert_trigger(monitor, message, alert_type, time_window_start, now)


def _get_time_series_df_for_aggregated_metrics(monitor, now):
    """
    For aggregated metrics, gets time series data and returns it as a Prophet-ready DataFrame.
    """
    interval_kind = _get_interval_kind(monitor)

    time_series_dict = _get_time_series_data_for_time_aggregated_metrics(
        monitor, interval_kind, None, now
    )
    if not time_series_dict:
        return None

    df = pd.DataFrame(list(time_series_dict.items()), columns=["ds", "y"])
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def _get_time_series_df_for_other_metrics(monitor, now):
    """
    For non-aggregated metrics, fetches individual data points and returns a
    Prophet-ready DataFrame, averaging values for duplicate timestamps.
    """
    filters = parsing_evaltask_filters(monitor.filters)
    base_queryset = ObservationSpan.objects.filter(
        project=monitor.project, created_at__lte=now
    ).filter(filters)
    metric_type = monitor.metric_type
    queryset = None

    match metric_type:
        case (
            MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING
            | MonitorMetricTypeChoices.LLM_API_FAILURE_RATES
        ):
            observation_type = (
                "tool"
                if metric_type
                == MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING
                else "llm"
            )
            queryset = (
                base_queryset.filter(observation_type=observation_type)
                .annotate(
                    y=Case(
                        When(status="ERROR", then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                )
                .values("created_at", "y")
            )

        case (
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES
        ):  # here are we doing error free rate or error rate ? this query itself might be incorrect check this
            queryset = (
                base_queryset.exclude(provider__isnull=True)
                .annotate(
                    y=Case(
                        When(status="ERROR", then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                )
                .values("created_at", "y")
            )

        case MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES:
            queryset = (
                base_queryset.exclude(trace__session__isnull=True)
                .values("trace__session")
                .annotate(error_count=Count("id", filter=Q(status="ERROR")))
                .annotate(
                    y=Case(
                        When(error_count__gt=0, then=Value(0.0)),
                        default=Value(1.0),
                        output_field=FloatField(),
                    ),
                    created_at=Max("created_at"),
                )
                .values("created_at", "y")
            )

        case (
            MonitorMetricTypeChoices.SPAN_RESPONSE_TIME
            | MonitorMetricTypeChoices.LLM_RESPONSE_TIME
        ):
            if metric_type == MonitorMetricTypeChoices.LLM_RESPONSE_TIME:
                base_queryset = base_queryset.filter(observation_type="llm")
            queryset = base_queryset.values("created_at").annotate(y=F("latency_ms"))

        case MonitorMetricTypeChoices.EVALUATION_METRICS:
            try:
                custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
                eval_output_type = custom_eval_config.eval_template.config.get("output")
            except CustomEvalConfig.DoesNotExist:
                return None

            eval_results = EvalLogger.objects.filter(
                custom_eval_config=custom_eval_config,
                target_type="span",
                created_at__lte=now,
                observation_span__in=ObservationSpan.objects.filter(filters),
            )

            if eval_output_type == EvalOutputType.SCORE:
                queryset = eval_results.values("created_at").annotate(
                    y=F("output_float")
                )
            elif eval_output_type == EvalOutputType.PASS_FAIL:
                output_bool = (
                    True if monitor.threshold_metric_value == "Passed" else False
                )
                queryset = eval_results.annotate(
                    y=Case(
                        When(output_bool=output_bool, then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ).values("created_at", "y")
            elif eval_output_type == EvalOutputType.CHOICES:
                choice = monitor.threshold_metric_value
                if not choice:
                    return None
                queryset = eval_results.annotate(
                    y=Case(
                        When(output_str_list__contains=[choice], then=Value(1.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ).values("created_at", "y")

    if queryset is None:
        return None

    df = pd.DataFrame(list(queryset))
    if df.empty:
        return None

    df = df.rename(columns={"created_at": "ds"})
    df["ds"] = pd.to_datetime(df["ds"])

    # Average values for duplicate timestamps
    df = df.groupby("ds")["y"].mean().reset_index()

    return df


# def _check_anomaly_detection_threshold(monitor, current_value, now):
#     """Checks for alerts based on anomaly detection using Prophet."""

#     metric_type = monitor.metric_type
#     df = None

#     if metric_type in [
#         MonitorMetricTypeChoices.COUNT_OF_ERRORS,
#         MonitorMetricTypeChoices.TOKEN_USAGE,
#         MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
#         MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
#     ]:
#         df = _get_time_series_df_for_aggregated_metrics(
#             monitor, now
#         )
#     else:
#         df = _get_time_series_df_for_other_metrics(
#             monitor, now
#         )
#     # df = pd.read_csv('https://raw.githubusercontent.com/facebook/prophet/main/examples/example_wp_log_peyton_manning.csv')

#     if df is None or len(df) < 2:
#         logger.warning(
#             f"Not enough historical data to perform anomaly detection for monitor {monitor.id}."
#         )
#         return

#     # Use a standard 95% confidence interval for anomaly detection.
#     m = Prophet(interval_width=0.95)
#     m.fit(df)
#     future = m.make_future_dataframe(periods=1, freq="min")  # Predict for 'now'
#     forecast = m.predict(future)

#     yhat_lower = forecast["yhat_lower"].iloc[-1]
#     yhat_upper = forecast["yhat_upper"].iloc[-1]

#     op = monitor.threshold_operator
#     alert_type = None
#     threshold_bound = None

#     if op == ComparisonOperatorChoices.GREATER_THAN and current_value > yhat_upper:
#         alert_type = AlertTypeChoices.WARNING
#         threshold_bound = yhat_upper
#     elif op == ComparisonOperatorChoices.LESS_THAN and current_value < yhat_lower:
#         alert_type = AlertTypeChoices.WARNING
#         threshold_bound = yhat_lower

#     if alert_type:
#         message = (
#             f"Metric '{monitor.name}' for project '{monitor.project.name}' "
#             f"({current_value:.2f}) was detected as an anomaly. It breached the expected "
#             f"bound of {threshold_bound:.2f} (operator: {op})."
#         )
#         _handle_alert_trigger(monitor, message, alert_type)


def _check_percentage_change_threshold(monitor, current_value, time_window_start, now):
    """Checks for alerts based on percentage change from historical mean."""
    time_window_start = now - timedelta(minutes=monitor.alert_frequency)
    historical_start = time_window_start - timedelta(
        minutes=monitor.auto_threshold_time_window
    )

    historical_mean, historical_stddev = _get_historical_stats(
        monitor, historical_start, time_window_start
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

    if critical_threshold is not None and _compare(
        current_value, op, critical_threshold
    ):
        alert_type = AlertTypeChoices.CRITICAL
        threshold_val = critical_threshold
    elif warning_threshold is not None and _compare(
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
        _handle_alert_trigger(monitor, message, alert_type, time_window_start, now)


def _compare(value1, operator, value2):
    """Compares two values based on the operator."""
    if operator == ComparisonOperatorChoices.GREATER_THAN:
        return value1 > value2
    elif operator == ComparisonOperatorChoices.LESS_THAN:
        return value1 < value2
    return False
