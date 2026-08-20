from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from tracer.services.clickhouse.query_builders.monitor_metrics import (
        MonitorMetricsQueryBuilder,
    )

import structlog
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
from tracer.services.clickhouse.query_service import AnalyticsQueryService

# Pruned monitor queries scale with the window, not table size; 30s covers the
# historical window crossing the hot/cold TTL boundary.
MONITOR_QUERY_TIMEOUT_MS = 30_000
MONITOR_CH_SETTINGS = {"max_threads": 4, "max_bytes_in_set": 500_000_000}

# The eval template stores the EvalOutputType value ("score"/"Pass/Fail"/
# "choices"); the CH builder branches on these normalized keys.
_EVAL_OUTPUT_TYPE_MAP = {
    EvalOutputType.SCORE.value: "SCORE",
    EvalOutputType.PASS_FAIL.value: "PASS_FAIL",
    EvalOutputType.CHOICES.value: "CHOICES",
}


class MonitorConfigError(Exception):
    """Monitor misconfiguration (e.g. deleted eval config); not retryable."""


def build_monitor_ch_builder(monitor: UserAlertMonitor) -> "MonitorMetricsQueryBuilder":
    """Construct the routed MONITOR_METRICS builder from a monitor instance."""
    eval_config_id = None
    eval_output_type = None
    if (
        monitor.metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS
        and monitor.metric
    ):
        try:
            custom_eval_config = CustomEvalConfig.objects.get(id=monitor.metric)
        except (CustomEvalConfig.DoesNotExist, DjangoValidationError) as e:
            # ValidationError = non-UUID junk in the free CharField ``metric``
            # — a permanent misconfig, not a transient failure to retry.
            raise MonitorConfigError(f"Eval config {monitor.metric} not found") from e
        raw_output = custom_eval_config.eval_template.config.get("output")
        # Normalize the stored EvalOutputType value to the builder's key.
        eval_output_type = _EVAL_OUTPUT_TYPE_MAP.get(raw_output)
        if eval_output_type is None:
            raise MonitorConfigError(f"Eval config {monitor.metric} has no output type")
        eval_config_id = str(monitor.metric)

    # v1↔v2 dispatch — flips with CH25_QUERY_TYPES_V2_PRIMARY=MONITOR_METRICS
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    BuilderCls = get_query_builder_class("MONITOR_METRICS")
    try:
        return BuilderCls(
            project_id=str(monitor.project_id),
            filters=monitor.filters,
            eval_config_id=eval_config_id,
            eval_output_type=eval_output_type,
            threshold_metric_value=monitor.threshold_metric_value,
        )
    except ValueError as e:
        # Filter translation rejects the stored filters — permanent misconfig.
        raise MonitorConfigError(f"Invalid monitor filters: {e}") from e


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


def _send_alert_email(monitor: UserAlertMonitor, message: str, alert_type: str) -> None:
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


def _send_slack_notification(
    monitor: UserAlertMonitor, message: str, alert_type: str
) -> None:
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
    except Exception as e:
        # Broad on purpose: network errors are not SlackApiError, and an escape
        # here would retry the whole task and double-fire the alert.
        logger.error(
            f"Failed to send {alert_type} Slack notification for monitor {monitor.id}: {e}"
        )


def _handle_alert_trigger(
    monitor: UserAlertMonitor,
    message: str,
    alert_type: str,
    time_window_start: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> None:
    """Handles the actions when an alert is triggered."""
    UserAlertMonitorLog.objects.create(
        alert=monitor,
        type=alert_type,
        message=message,
        time_window_start=time_window_start,
        time_window_end=now,
    )
    logger.info(
        "monitor_alert_fired",
        monitor_id=str(monitor.id),
        metric_type=monitor.metric_type,
        alert_type=str(alert_type),
    )
    _send_alert_email(monitor, message, alert_type)
    _send_slack_notification(monitor, message, alert_type)


@temporal_activity(
    max_retries=0,
    time_limit=3600,
    queue="tasks_l",
)
def check_alerts() -> None:
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
    UserAlertMonitor.objects.filter(id__in=monitor_ids).update(last_checked_at=now)

    for monitor_id in monitor_ids:
        # Isolate dispatch failures so one bad delay() doesn't drop the rest of
        # the batch (they're already stamped and won't re-dispatch this cycle).
        try:
            process_monitor_task.delay(monitor_id, now.isoformat())
        except Exception as e:
            logger.error(
                "monitor_dispatch_failed", monitor_id=str(monitor_id), error=str(e)
            )

    logger.info("Alert check job finished.")


@temporal_activity(
    max_retries=3,
    time_limit=3600,
    queue="tasks_l",
)
def process_monitor_task(monitor_id: str, now_iso: str) -> None:
    """Processes a single monitor; logs failures and re-raises for Temporal retry."""
    now = parse_datetime(now_iso)
    if now is None:
        raise ValueError(f"Invalid now_iso timestamp: {now_iso!r}")
    try:
        monitor = UserAlertMonitor.objects.get(id=monitor_id)
    except UserAlertMonitor.DoesNotExist:
        logger.info("monitor_gone_before_check", monitor_id=str(monitor_id))
        return

    logger.info(f"Checking monitor: {monitor.name} ({monitor.id}) at {now}")
    try:
        _process_monitor(monitor, now)
    except MonitorConfigError as e:
        # Permanent misconfiguration — log and don't retry.
        logger.warning(
            "monitor_misconfigured", monitor_id=str(monitor.id), error=str(e)
        )
        return
    except Exception as e:
        logger.error(
            "monitor_check_failed",
            monitor_id=str(monitor.id),
            metric_type=monitor.metric_type,
            error=str(e),
        )
        raise


def _process_monitor(monitor: UserAlertMonitor, now: datetime) -> None:
    """Processes a single monitor."""
    time_window_start = now - timedelta(minutes=monitor.alert_frequency)

    # Build once: value + historical share the builder (avoids a second eval
    # config read and filter translation per percentage-change cycle).
    builder = build_monitor_ch_builder(monitor)

    metric_value = _get_metric_value(monitor, time_window_start, now, builder)
    if metric_value is None:
        logger.warning(
            "monitor_no_data",
            monitor_id=str(monitor.id),
            metric_type=monitor.metric_type,
        )
        return

    _check_thresholds_and_alert(monitor, metric_value, time_window_start, now, builder)


def _get_metric_value(
    monitor: UserAlertMonitor,
    start_time: datetime,
    end_time: datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
) -> Optional[float]:
    """Metric value for the time window, from ClickHouse. Raises on CH errors."""
    analytics = AnalyticsQueryService()
    builder = builder or build_monitor_ch_builder(monitor)
    metric_type = monitor.metric_type

    # DAILY/MONTHLY are trailing windows regardless of alert_frequency.
    ch_start = start_time
    if metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        ch_start = end_time - timedelta(days=1)
    elif metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        ch_start = end_time - timedelta(days=30)

    query, params = builder.build_metric_value_query(metric_type, ch_start, end_time)
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=MONITOR_QUERY_TIMEOUT_MS,
        settings=MONITOR_CH_SETTINGS,
    )
    if result.data:
        return result.data[0].get("value")
    return None


def _get_historical_stats(
    monitor: UserAlertMonitor,
    start_time: datetime,
    end_time: datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Historical (mean, stddev) for the window, from ClickHouse. Raises on CH errors."""
    analytics = AnalyticsQueryService()
    builder = builder or build_monitor_ch_builder(monitor)

    query, params = builder.build_historical_stats_query(
        monitor.metric_type,
        start_time,
        end_time,
        interval_kind=get_interval_kind(monitor),
    )
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=MONITOR_QUERY_TIMEOUT_MS,
        settings=MONITOR_CH_SETTINGS,
    )
    if result.data:
        row = result.data[0]
        return row.get("mean"), row.get("stddev")
    return None, None


def _check_thresholds_and_alert(
    monitor: UserAlertMonitor,
    current_value: float,
    time_window_start: datetime,
    now: datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
) -> None:
    """Checks the metric value against the monitor's thresholds and alerts if needed."""

    if monitor.threshold_type == ThresholdCalculationMethodChoices.STATIC:
        _check_static_threshold(monitor, current_value, time_window_start, now)

    elif monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE:
        _check_percentage_change_threshold(
            monitor, current_value, time_window_start, now, builder
        )


def _check_static_threshold(
    monitor: UserAlertMonitor,
    current_value: float,
    time_window_start: datetime,
    now: datetime,
) -> None:
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


def _check_percentage_change_threshold(
    monitor: UserAlertMonitor,
    current_value: float,
    time_window_start: datetime,
    now: datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
) -> None:
    """Checks for alerts based on percentage change from historical mean."""
    historical_start = time_window_start - timedelta(
        minutes=monitor.auto_threshold_time_window
    )

    historical_mean, historical_stddev = _get_historical_stats(
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


def _compare(value1: float, operator: str, value2: float) -> bool:
    """Compares two values based on the operator."""
    if operator == ComparisonOperatorChoices.GREATER_THAN:
        return value1 > value2
    elif operator == ComparisonOperatorChoices.LESS_THAN:
        return value1 < value2
    return False
