"""Queued-monitor mute and evidence-window regressions; ClickHouse is mocked."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.utils import monitor as monitor_mod

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


@pytest.fixture
def alert_io():
    with (
        mock.patch.object(monitor_mod, "AnalyticsQueryService") as analytics,
        mock.patch.object(monitor_mod, "_send_alert_email") as email,
        mock.patch.object(monitor_mod, "_send_slack_notification") as slack,
    ):
        execute = analytics.return_value.execute_ch_query
        execute.return_value = SimpleNamespace(data=[{"value": 2000.0}])
        yield execute, email, slack


def test_queued_check_skips_monitor_muted_before_start(user_alert_monitor, alert_io):
    execute, email, slack = alert_io
    UserAlertMonitor.objects.filter(pk=user_alert_monitor.pk).update(is_mute=True)

    result = monitor_mod.process_monitor_task._original_func(
        str(user_alert_monitor.pk), NOW.isoformat()
    )

    assert result is None
    execute.assert_not_called()
    assert not UserAlertMonitorLog.objects.filter(alert=user_alert_monitor).exists()
    email.assert_not_called()
    slack.assert_not_called()


def test_retry_skips_monitor_muted_after_query_failure(user_alert_monitor, alert_io):
    execute, email, slack = alert_io
    execute.side_effect = RuntimeError("temporary metric query failure")
    task = monitor_mod.process_monitor_task._original_func
    args = (str(user_alert_monitor.pk), NOW.isoformat())
    with pytest.raises(RuntimeError, match="temporary metric query failure"):
        task(*args)
    execute.assert_called_once()
    UserAlertMonitor.objects.filter(pk=user_alert_monitor.pk).update(is_mute=True)
    execute.side_effect = None

    assert task(*args) is None

    execute.assert_called_once()  # The retry must not query again.
    assert not UserAlertMonitorLog.objects.filter(alert=user_alert_monitor).exists()
    email.assert_not_called()
    slack.assert_not_called()


@pytest.mark.parametrize(
    "threshold_type,mute_on_query",
    [
        pytest.param("static", 1, id="static-current"),
        pytest.param("percentage_change", 1, id="percentage-current"),
        pytest.param("percentage_change", 2, id="percentage-history"),
    ],
)
def test_mute_during_calculation_suppresses_issue_and_delivery(
    user_alert_monitor, alert_io, threshold_type, mute_on_query
):
    execute, email, slack = alert_io
    user_alert_monitor.threshold_type = threshold_type
    user_alert_monitor.save(update_fields=["threshold_type"])
    calls = []

    def query_result(*args, **kwargs):
        calls.append(args)
        if len(calls) == mute_on_query:
            UserAlertMonitor.objects.filter(pk=user_alert_monitor.pk).update(
                is_mute=True
            )
        row = {"value": 2000.0} if len(calls) == 1 else {"mean": 10.0, "stddev": 2.0}
        return SimpleNamespace(data=[row])

    execute.side_effect = query_result
    monitor_mod._process_monitor(user_alert_monitor, NOW)

    # The loaded instance is stale: suppression must consult persisted state.
    assert user_alert_monitor.is_mute is False
    assert len(calls) >= mute_on_query
    assert UserAlertMonitor.objects.get(pk=user_alert_monitor.pk).is_mute is True
    assert not UserAlertMonitorLog.objects.filter(alert=user_alert_monitor).exists()
    email.assert_not_called()
    slack.assert_not_called()


@pytest.mark.parametrize("threshold_type", ["static", "percentage_change"])
@pytest.mark.parametrize(
    "metric_type,frequency,window",
    [
        pytest.param("daily_tokens_spent", 60, timedelta(days=1), id="daily-hourly"),
        pytest.param(
            "daily_tokens_spent", 60 * 48, timedelta(days=1), id="daily-two-day"
        ),
        pytest.param(
            "monthly_tokens_spent", 60, timedelta(days=30), id="monthly-hourly"
        ),
        pytest.param(
            "monthly_tokens_spent",
            60 * 24 * 60,
            timedelta(days=30),
            id="monthly-sixty-day",
        ),
        pytest.param("count_of_errors", 5, timedelta(minutes=5), id="errors-five-min"),
        pytest.param(
            "count_of_errors", 90, timedelta(minutes=90), id="errors-ninety-min"
        ),
    ],
)
def test_alert_evidence_matches_effective_metric_window(
    user_alert_monitor, alert_io, threshold_type, metric_type, frequency, window
):
    execute, email, slack = alert_io
    user_alert_monitor.metric_type = metric_type
    user_alert_monitor.alert_frequency = frequency
    user_alert_monitor.threshold_type = threshold_type
    user_alert_monitor.critical_threshold_value = 1000
    user_alert_monitor.warning_threshold_value = None
    user_alert_monitor.save()
    execute.side_effect = [
        SimpleNamespace(data=[{"value": 2000.0}]),
        SimpleNamespace(data=[{"mean": 10.0, "stddev": 2.0}]),
    ]

    monitor_mod.process_monitor_task._original_func(
        str(user_alert_monitor.pk), NOW.isoformat()
    )

    expected_start = NOW - window
    # ClickHouse binds naive UTC; Django persists timezone-aware timestamps.
    expected_query_start = expected_start.replace(tzinfo=None)
    metric_params = execute.call_args_list[0].args[1]
    log = UserAlertMonitorLog.objects.get(alert=user_alert_monitor)
    assert metric_params["start_time"] == expected_query_start
    assert metric_params["end_time"] == NOW.replace(tzinfo=None)
    assert log.time_window_start == expected_start
    assert log.time_window_end == NOW
    assert log.type == "critical"
    email.assert_called_once_with(user_alert_monitor, log.message, "critical")
    slack.assert_called_once_with(user_alert_monitor, log.message, "critical")
    if threshold_type == "percentage_change":
        assert execute.call_count == 2
        history_params = execute.call_args_list[1].args[1]
        assert history_params["end_time"] == expected_query_start
        assert history_params["start_time"] == expected_query_start - timedelta(
            minutes=user_alert_monitor.auto_threshold_time_window
        )
    else:
        execute.assert_called_once()


def test_same_window_processed_twice_alerts_once(user_alert_monitor, alert_io):
    # A retried or duplicated check of one window (the same now) must not
    # write a second alert or send it again; the next window alerts anew.
    execute, email, slack = alert_io
    task = monitor_mod.process_monitor_task._original_func
    monitor_id = str(user_alert_monitor.pk)
    later = NOW + timedelta(hours=1)
    logs = UserAlertMonitorLog.objects.filter(alert=user_alert_monitor).order_by(
        "time_window_end"
    )

    task(monitor_id, NOW.isoformat())
    task(monitor_id, NOW.isoformat())

    assert list(logs.values_list("time_window_start", "time_window_end")) == [
        (NOW - timedelta(hours=1), NOW)
    ]
    assert email.call_count == slack.call_count == 1

    task(monitor_id, later.isoformat())

    assert list(logs.values_list("time_window_start", "time_window_end")) == [
        (NOW - timedelta(hours=1), NOW),
        (NOW, later),
    ]
    assert email.call_count == slack.call_count == 2
