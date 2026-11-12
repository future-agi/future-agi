import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tracer.services.clickhouse.query_builders.monitor_metrics import (
        MonitorMetricsQueryBuilder,
    )

import structlog
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import Now
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from slack_sdk.webhook import WebhookClient
from tfc.temporal import temporal_activity
from tfc.utils.email import email_helper
from tfc.utils.error_codes import get_error_message

from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.monitor import (
    AlertTypeChoices,
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    ThresholdRuleChoices,
    UserAlertMonitor,
    UserAlertMonitorLog,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService

logger = structlog.get_logger(__name__)

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


def uses_choice_threshold(eval_template) -> bool:
    """True when the eval's alert metric is the share of one chosen label.

    A scoring eval may also carry labels, but its metric is the mean score, so
    a stored choice is neither validated, displayed, nor copied for it.
    """
    if eval_template is None:
        return False
    output_type = (eval_template.config or {}).get("output")
    return output_type in (
        EvalOutputType.PASS_FAIL.value,
        EvalOutputType.CHOICES.value,
    ) and bool(eval_template.choices)


class MonitorConfigError(Exception):
    """Monitor misconfiguration (e.g. deleted eval config); not retryable."""


def uses_earlier_rule(monitor: UserAlertMonitor) -> bool:
    """True for a percentage-change monitor saved before the threshold update
    and not edited since: it is still judged against a band around the
    history's mean (tracer.utils.monitor_earlier_rule)."""
    return (
        monitor.threshold_type == ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE
        and monitor.threshold_rule == ThresholdRuleChoices.EARLIER
    )


def counts_error_free_providers(monitor: UserAlertMonitor) -> bool:
    """True for a service-provider error-rate monitor whose numbers are about
    the share of providers with no errors: saved before the threshold update
    and neither converted nor edited since (a percentage-change monitor, a
    static one the conversion could not convert exactly, or one an older
    server saved while the update rolled out)."""
    return (
        monitor.metric_type == MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES
        and monitor.threshold_rule == ThresholdRuleChoices.EARLIER
    )


def build_monitor_ch_builder(monitor: UserAlertMonitor) -> "MonitorMetricsQueryBuilder":
    """Construct the routed MONITOR_METRICS builder from a monitor instance."""
    # Monitors are project-scoped (the REST serializer and UI require one); a
    # NULL project would otherwise bind as ``project_id = 'None'`` and fail in
    # ClickHouse (Code 376) on every run. Checked first so a project-less eval
    # monitor reports this cause, not a missing eval config.
    if monitor.project_id is None:
        raise MonitorConfigError(get_error_message("MONITOR_PROJECT_REQUIRED"))

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
            provider_error_free=counts_error_free_providers(monitor),
        )
    except ValueError as e:
        # Filter translation rejects the stored filters — permanent misconfig.
        raise MonitorConfigError(f"Invalid monitor filters: {e}") from e


# Metrics whose value is a total over the period: their previous-period
# baseline is the history window's total divided by the periods in it.
TOKEN_METRICS = (
    MonitorMetricTypeChoices.TOKEN_USAGE,
    MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
    MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
)
PER_PERIOD_TOTAL_METRICS = (MonitorMetricTypeChoices.COUNT_OF_ERRORS, *TOKEN_METRICS)


def get_period_seconds(monitor: UserAlertMonitor) -> int:
    """Length of one evaluation period: the window one metric value covers."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        return 24 * 60 * 60
    if monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        return 30 * 24 * 60 * 60
    return monitor.alert_frequency * 60


def baseline_periods(monitor: UserAlertMonitor, history: timedelta) -> float | None:
    """Evaluation periods in a history window: window / period, with no floor.

    A history shorter than one period is a fraction of it (15 minutes of a
    1-hour period is 0.25), so a total over it is scaled up to one period, a
    rate comparison. None when the period or the window is not positive (a bad
    row): there is nothing to scale by, so no baseline.
    """
    period_seconds = get_period_seconds(monitor)
    history_seconds = history.total_seconds()
    if period_seconds <= 0 or history_seconds <= 0:
        return None
    return history_seconds / period_seconds


def _send_alert_email(monitor: UserAlertMonitor, message: str, alert_type: str) -> None:
    """Sends an email notification for an alert."""
    if not monitor.notification_emails:
        return
    try:
        email_helper(
            mail_subject=f"[{alert_type.upper()}] Alert Triggered: {monitor.name}",
            template_name="alert_user.html",
            template_data={
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
    time_window_start: datetime | None = None,
    now: datetime | None = None,
) -> None:
    """Handles the actions when an alert is triggered."""
    with transaction.atomic():
        # Metric and historical queries can outlive a mute; recheck before
        # firing. The row lock makes a duplicate check of this window wait here.
        live = UserAlertMonitor.objects.select_for_update(of=("self",)).filter(
            pk=monitor.pk, is_mute=False
        )
        # Select a column: exists() selects none, which drops the OF clause.
        if live.values_list("pk", flat=True).first() is None:
            return
        # One alert per window: a retried or duplicated check of the same
        # window neither writes nor sends it again. A deleted row was still
        # delivered, so it counts too.
        if (
            now is not None
            and UserAlertMonitorLog.all_objects.filter(
                alert=monitor,
                time_window_start=time_window_start,
                time_window_end=now,
            ).exists()
        ):
            logger.info(
                "monitor_alert_window_already_fired", monitor_id=str(monitor.id)
            )
            return

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

    if monitor.is_mute:
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


def _get_metric_window_start(
    monitor: UserAlertMonitor, start_time: datetime, end_time: datetime
) -> datetime:
    """Use the same trailing window for metric queries and alert evidence."""
    if monitor.metric_type == MonitorMetricTypeChoices.DAILY_TOKENS_SPENT:
        return end_time - timedelta(days=1)
    if monitor.metric_type == MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT:
        return end_time - timedelta(days=30)
    return start_time


def _process_monitor(monitor: UserAlertMonitor, now: datetime) -> None:
    """Processes a single monitor."""
    time_window_start = _get_metric_window_start(
        monitor, now - timedelta(minutes=monitor.alert_frequency), now
    )

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
) -> float | None:
    """Metric value for the time window, from ClickHouse. Raises on CH errors."""
    analytics = AnalyticsQueryService()
    builder = builder or build_monitor_ch_builder(monitor)
    metric_type = monitor.metric_type

    # Preserve trailing-window semantics for direct metric-query callers too.
    ch_start = _get_metric_window_start(monitor, start_time, end_time)

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


def get_previous_period_baseline(
    monitor: UserAlertMonitor,
    start_time: datetime,
    end_time: datetime,
    builder: Optional["MonitorMetricsQueryBuilder"] = None,
    *,
    analytics: AnalyticsQueryService | None = None,
    timeout_ms: int = MONITOR_QUERY_TIMEOUT_MS,
) -> float | None:
    """What a percentage-change monitor compares with: the metric's value for
    one evaluation period, from the history window ``[start_time, end_time)``.

    The alert worker and the monitor graph both call this, so the graph's
    current bucket is judged exactly as the worker fires. Count and token
    metrics: the window's total over the periods in it (empty periods count as
    0; a window shorter than one period is scaled up to one, so 15 minutes of
    a 1-hour period is its total times 4). Rate, latency and eval metrics: the
    metric pooled over the window, as the value query computes it. None when
    the window has no data: not enough history, so there is no percentage
    alert. A real zero (data, no errors) is 0.0. None also for a period or
    window that is not positive. Raises on CH errors.
    """
    analytics = analytics or AnalyticsQueryService()
    builder = builder or build_monitor_ch_builder(monitor)

    periods = baseline_periods(monitor, end_time - start_time)
    if periods is None:
        return None
    query, params = builder.build_historical_stats_query(
        monitor.metric_type,
        start_time,
        end_time,
        periods=periods,
    )
    result = analytics.execute_ch_query(
        query,
        params,
        timeout_ms=timeout_ms,
        settings=MONITOR_CH_SETTINGS,
    )
    baseline = result.data[0].get("mean") if result.data else None
    if baseline is None or not math.isfinite(baseline):
        return None
    return float(baseline)


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

    elif uses_earlier_rule(monitor):
        # Imported here: the earlier rule builds on this module.
        from tracer.utils.monitor_earlier_rule import check_percentage_band

        check_percentage_band(monitor, current_value, time_window_start, now, builder)

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
    """Checks for alerts based on percentage change from the previous period."""
    historical_start = time_window_start - timedelta(
        minutes=monitor.auto_threshold_time_window
    )

    baseline = get_previous_period_baseline(
        monitor, historical_start, time_window_start, builder
    )

    if baseline is None:
        logger.warning(
            "monitor_percentage_change_no_history",
            monitor_id=str(monitor.id),
            metric_type=monitor.metric_type,
        )
        return

    breach = percentage_change_breach(monitor, current_value, baseline)
    if breach:
        alert_type, threshold_val = breach
        message = (
            f"Metric '{monitor.name}' for project '{monitor.project.name}' "
            f"({current_value:.2f}) breached the {alert_type} threshold "
            f"({monitor.threshold_operator} {threshold_val:.2f}): "
            f"{_previous_period_phrase(monitor, baseline)}."
        )
        _handle_alert_trigger(monitor, message, alert_type, time_window_start, now)


def percentage_change_breach(
    monitor: UserAlertMonitor, current_value: float, baseline: float
) -> tuple[str, float] | None:
    """(alert type, threshold) when ``current_value`` breaches the monitor's
    percentage of ``baseline``; None when it does not.

    The comparison is ``current > baseline * (1 + p)`` (``<`` and ``1 - p`` for
    less-than), so nothing divides: against a zero baseline any increase fires
    a greater-than monitor, and an unchanged zero never alerts.
    """
    op = monitor.threshold_operator
    sign = 1 if op == ComparisonOperatorChoices.GREATER_THAN else -1
    for alert_type, percent in (
        (AlertTypeChoices.CRITICAL, monitor.critical_threshold_value),
        (AlertTypeChoices.WARNING, monitor.warning_threshold_value),
    ):
        if percent is None:
            continue
        threshold = baseline * (1 + sign * percent / 100)
        if _compare(current_value, op, threshold):
            return alert_type, threshold
    return None


def _plain_number(value: float) -> str:
    """4.0 -> "4", 2.5 -> "2.5", 0.004 -> "0.004" (a small rate never reads 0)."""
    if value != 0 and abs(value) < 0.01:
        return f"{value:.2g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _plain_duration(minutes: int) -> str:
    """60 -> "hour", 180 -> "3 hours", 10080 -> "7 days", 90 -> "90 minutes"."""
    if minutes % (24 * 60) == 0:
        count, unit = minutes // (24 * 60), "day"
    elif minutes % 60 == 0:
        count, unit = minutes // 60, "hour"
    else:
        count, unit = minutes, "minute"
    return unit if count == 1 else f"{count} {unit}s"


def _previous_period_phrase(monitor: UserAlertMonitor, baseline: float) -> str:
    """The baseline in words: "up from 4 in the previous period"; for a longer
    history "up from an average of 4 per period over the previous 7 days"; for
    a shorter one "up from a rate of 4 per period over the previous 15 minutes".
    """
    direction = (
        "up"
        if monitor.threshold_operator == ComparisonOperatorChoices.GREATER_THAN
        else "down"
    )
    value = _plain_number(baseline)
    history_minutes = monitor.auto_threshold_time_window
    # Exactly one period: the baseline is that period's value.
    periods = baseline_periods(monitor, timedelta(minutes=history_minutes))
    if periods is None or periods == 1:
        return f"{direction} from {value} in the previous period"
    if monitor.metric_type in PER_PERIOD_TOTAL_METRICS:
        # A longer history's total is averaged per period; a shorter one's is
        # scaled up to one period, a rate. Neither is one period's count.
        kind = "an average" if periods > 1 else "a rate"
        value = f"{kind} of {value} per period"
    history = _plain_duration(history_minutes)
    return f"{direction} from {value} over the previous {history}"


def _compare(value1: float, operator: str, value2: float) -> bool:
    """Compares two values based on the operator."""
    if operator == ComparisonOperatorChoices.GREATER_THAN:
        return value1 > value2
    elif operator == ComparisonOperatorChoices.LESS_THAN:
        return value1 < value2
    return False
