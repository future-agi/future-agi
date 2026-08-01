import math
import statistics
from datetime import timedelta

import structlog
from django.db.models import (
    DurationField,
    ExpressionWrapper,
    F,
    Q,
)
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from slack_sdk.errors import SlackApiError
from slack_sdk.webhook import WebhookClient

from tfc.temporal import temporal_activity
from tfc.utils.email import email_helper
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.monitor import (
    AlertTypeChoices,
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
    UserAlertMonitorLog,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService

logger = structlog.get_logger(__name__)

_MONITOR_CH_TIMEOUT_MS = 750
# Monitor values can trigger alerts, so overflow must throw and be caught
# rather than returning a partial aggregate that could generate a false alert.
_MONITOR_CH_SETTINGS = {
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
    "max_memory_usage": 268_435_456,
    "max_bytes_to_read": 1_073_741_824,
    "read_overflow_mode": "throw",
    "max_result_rows": 2000,
    "result_overflow_mode": "throw",
}


def _build_monitor_ch_builder(monitor):
    """Construct a MonitorMetricsQueryBuilder from a monitor instance."""
    project_id = getattr(monitor, "project_id", None)
    if not project_id:
        # Legacy rows can predate the now-required project field. Converting
        # None to the string "None" sends an invalid UUID to ClickHouse every
        # time the monitor scheduler runs. Fail closed before constructing SQL.
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

    # The monitor ClickHouse route reads the CH25 spans table. Selecting the
    # legacy compiler when rollout flags are absent emits non-pruning
    # created_at predicates and legacy attribute columns against that table.
    return MonitorMetricsQueryBuilderV2(
        project_id=str(project_id),
        filters=monitor.filters,
        eval_config_id=eval_config_id,
        eval_output_type=eval_output_type,
        threshold_metric_value=monitor.threshold_metric_value,
    )


def _get_frequency_seconds(monitor, start_time=None, end_time=None):
    """Return a bucket width that keeps historical results below 1,000 rows."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        frequency_seconds = 24 * 60 * 60
    elif monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        frequency_seconds = 30 * 24 * 60 * 60
    else:
        frequency_seconds = max(int(monitor.alert_frequency or 1) * 60, 60)

    if start_time is not None and end_time is not None:
        window_seconds = max((end_time - start_time).total_seconds(), 0)
        minimum_minutes = max(1, math.ceil(window_seconds / (1000 * 60)))
        frequency_seconds = max(frequency_seconds, minimum_minutes * 60)

    return frequency_seconds


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
        raise Exception(f"Error processing monitor {monitor.id}: {e}") from e


def _process_monitor(monitor, now):
    """Processes a single monitor."""
    time_window_start = now - timedelta(minutes=monitor.alert_frequency)

    metric_value = _get_metric_value(monitor, time_window_start, now)
    if metric_value is None:
        return

    _check_thresholds_and_alert(monitor, metric_value, time_window_start, now)


def _get_metric_value(monitor, start_time, end_time):
    """Calculate a monitor metric from the authoritative ClickHouse store."""
    analytics = AnalyticsQueryService()
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
        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_MONITOR_CH_TIMEOUT_MS,
            settings=_MONITOR_CH_SETTINGS,
        )
        if result.data:
            return result.data[0].get("value")
        return None
    except Exception as e:
        logger.warning(
            "CH monitor metric exceeded read budget; skipping monitor evaluation",
            error=str(e),
            monitor_id=str(monitor.id),
        )
        return None


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
    """Calculate historical monitor statistics from ClickHouse only."""
    analytics = AnalyticsQueryService()
    try:
        metric_type = monitor.metric_type
        builder = _build_monitor_ch_builder(monitor)

        if metric_type in (
            MonitorMetricTypeChoices.COUNT_OF_ERRORS,
            MonitorMetricTypeChoices.TOKEN_USAGE,
            MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
            MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
        ):
            query, params = builder.build_time_series_query(
                metric_type,
                start_time,
                end_time,
                _get_frequency_seconds(monitor, start_time, end_time),
            )
        else:
            query, params = builder.build_historical_stats_query(
                metric_type, start_time, end_time
            )

        result = analytics.execute_ch_query(
            query,
            params,
            timeout_ms=_MONITOR_CH_TIMEOUT_MS,
            settings=_MONITOR_CH_SETTINGS,
        )
        if metric_type in (
            MonitorMetricTypeChoices.COUNT_OF_ERRORS,
            MonitorMetricTypeChoices.TOKEN_USAGE,
            MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
            MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
        ):
            values = {
                str(row.get("timestamp")): row.get("value")
                for row in result.data
                if row.get("value") is not None
            }
            if not values:
                return None, None
            return _calculate_stats_from_time_series(values)

        if result.data:
            row = result.data[0]
            return row.get("mean"), row.get("stddev")
        return None, None
    except Exception as e:
        logger.warning(
            "CH historical stats exceeded read budget; skipping percentage check",
            error=str(e),
            monitor_id=str(monitor.id),
        )
        return None, None


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
