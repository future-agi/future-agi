"""Alert evaluator behavior with ClickHouse mocked: value pass-through,
None-suppression, threshold firing, CH settings.
DB-backed (monitor rows), no real ClickHouse."""

from __future__ import annotations

from datetime import UTC, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from structlog.testing import capture_logs
from tfc.utils.error_codes import get_error_message

from tracer.models.monitor import MonitorMetricTypeChoices, UserAlertMonitorLog
from tracer.utils import monitor as monitor_mod
from tracer.utils.monitor import (
    MONITOR_CH_SETTINGS,
    MONITOR_QUERY_TIMEOUT_MS,
    MonitorConfigError,
    _get_metric_value,
    _process_monitor,
    _send_alert_email,
    build_monitor_ch_builder,
    process_monitor_task,
)

pytestmark = pytest.mark.django_db


def _fake_eval_config(output_value: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        eval_template=SimpleNamespace(config={"output": output_value})
    )


@pytest.mark.parametrize(
    "stored,expected",
    [("score", "SCORE"), ("Pass/Fail", "PASS_FAIL"), ("choices", "CHOICES")],
)
def test_eval_output_type_normalized_to_builder_key(
    user_alert_monitor, stored: str, expected: str
) -> None:
    # The template stores the EvalOutputType value; the builder branches on the
    # normalized key. A mismatch silently disables every eval monitor.
    user_alert_monitor.metric_type = "evaluation_metrics"
    user_alert_monitor.metric = "22222222-2222-2222-2222-222222222222"
    with mock.patch.object(
        monitor_mod.CustomEvalConfig.objects,
        "get",
        return_value=_fake_eval_config(stored),
    ):
        builder = build_monitor_ch_builder(user_alert_monitor)
    assert builder.eval_output_type == expected


def test_missing_eval_config_raises_config_error_at_builder(
    user_alert_monitor,
) -> None:
    # The raise lives in build_monitor_ch_builder so evaluator, graph and
    # preview all fail loud — not just the evaluator path.
    user_alert_monitor.metric_type = "evaluation_metrics"
    user_alert_monitor.metric = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(MonitorConfigError):
        build_monitor_ch_builder(user_alert_monitor)


def test_unknown_eval_output_type_raises_config_error(user_alert_monitor) -> None:
    user_alert_monitor.metric_type = "evaluation_metrics"
    user_alert_monitor.metric = "22222222-2222-2222-2222-222222222222"
    user_alert_monitor.save()
    with mock.patch.object(
        monitor_mod.CustomEvalConfig.objects,
        "get",
        return_value=_fake_eval_config("bogus"),
    ):
        with pytest.raises(MonitorConfigError):
            _get_metric_value(
                user_alert_monitor,
                timezone.now() - timedelta(hours=1),
                timezone.now(),
            )


def _ch_result(rows: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(data=rows)


def _patch_ch(results: list[Any]) -> mock._patch:
    """Patch AnalyticsQueryService in the evaluator; each call pops a result.

    A result that is an Exception instance is raised instead of returned.
    """
    instance = mock.MagicMock()

    def _side_effect(*args: Any, **kwargs: Any) -> SimpleNamespace:
        if not results:
            raise AssertionError("unexpected extra CH call (stub list exhausted)")
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    instance.execute_ch_query.side_effect = _side_effect
    patcher = mock.patch.object(
        monitor_mod, "AnalyticsQueryService", return_value=instance
    )
    patcher.ch_instance = instance  # type: ignore[attr-defined]
    return patcher


@pytest.fixture(autouse=True)
def _mute_notifications():
    with (
        mock.patch.object(monitor_mod, "_send_alert_email"),
        mock.patch.object(monitor_mod, "_send_slack_notification"),
    ):
        yield


def test_metric_value_passthrough_and_ch_settings(user_alert_monitor) -> None:
    patcher = _patch_ch([_ch_result([{"value": 42.0}])])
    with patcher:
        now = timezone.now()
        value = _get_metric_value(user_alert_monitor, now - timedelta(hours=1), now)
    assert value == 42.0
    _, kwargs = patcher.ch_instance.execute_ch_query.call_args
    assert kwargs["timeout_ms"] == MONITOR_QUERY_TIMEOUT_MS
    assert kwargs["settings"] == MONITOR_CH_SETTINGS


def test_none_value_suppresses_thresholds(user_alert_monitor) -> None:
    with _patch_ch([_ch_result([{"value": None}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.count() == 0


def test_static_critical_fires(user_alert_monitor) -> None:
    # fixture: count_of_errors, static, greater_than, critical 0.1
    with _patch_ch([_ch_result([{"value": 5.0}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    log = UserAlertMonitorLog.objects.get()
    assert log.type == "critical"


def test_static_below_threshold_no_fire(user_alert_monitor) -> None:
    with _patch_ch([_ch_result([{"value": 0.05}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.count() == 0


def test_percentage_change_fires_on_baseline_increase(user_alert_monitor) -> None:
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.critical_threshold_value = 50
    user_alert_monitor.save()
    # Current 20 vs baseline mean 10: a 50% increase sets the boundary at 15.
    with _patch_ch(
        [
            _ch_result([{"value": 20.0}]),
            _ch_result([{"mean": 10.0, "stddev": 2.0}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())
    log = UserAlertMonitorLog.objects.get()
    assert log.type == "critical"
    assert "(greater_than 15.00)" in log.message


@pytest.mark.parametrize(
    "operator,critical,warning,current,expected_type,expected_boundary",
    [
        ("greater_than", 50, None, 101, None, None),
        ("greater_than", 50, None, 150, None, None),
        ("greater_than", 50, None, 151, "critical", 150),
        ("greater_than", 50, 20, 120, None, None),
        ("greater_than", 50, 20, 121, "warning", 120),
        ("greater_than", 50, 20, 150, "warning", 120),
        ("greater_than", 50, 20, 151, "critical", 150),
        ("greater_than", None, 20, 121, "warning", 120),
        ("less_than", 50, None, 99, None, None),
        ("less_than", 50, None, 50, None, None),
        ("less_than", 50, None, 49, "critical", 50),
        ("less_than", 50, 20, 80, None, None),
        ("less_than", 50, 20, 79, "warning", 80),
        ("less_than", 50, 20, 50, "warning", 80),
        ("less_than", 50, 20, 49, "critical", 50),
        ("less_than", None, 20, 79, "warning", 80),
    ],
)
def test_percentage_change_uses_stable_baseline(
    user_alert_monitor,
    operator,
    critical,
    warning,
    current,
    expected_type,
    expected_boundary,
) -> None:
    """Percentages apply to the mean, with strict tier boundaries."""
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.threshold_operator = operator
    user_alert_monitor.critical_threshold_value = critical
    user_alert_monitor.warning_threshold_value = warning
    with _patch_ch(
        [
            _ch_result([{"value": current}]),
            _ch_result([{"mean": 100.0, "stddev": 0.0}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())

    if expected_type is None:
        assert not UserAlertMonitorLog.objects.exists()
    else:
        log = UserAlertMonitorLog.objects.get()
        assert log.type == expected_type
        assert f"({operator} {expected_boundary:.2f})" in log.message


@pytest.mark.parametrize("stddev", [200.0, None])
@pytest.mark.parametrize(
    "operator,current,boundary",
    [("greater_than", 151, 150), ("less_than", 49, 50)],
)
def test_percentage_change_does_not_depend_on_stddev(
    user_alert_monitor, stddev, operator, current, boundary
) -> None:
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.threshold_operator = operator
    user_alert_monitor.critical_threshold_value = 50
    with _patch_ch(
        [
            _ch_result([{"value": current}]),
            _ch_result([{"mean": 100.0, "stddev": stddev}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())

    log = UserAlertMonitorLog.objects.get()
    assert log.type == "critical"
    assert f"({operator} {boundary:.2f})" in log.message


@pytest.mark.parametrize(
    "operator,current,expected_type",
    [
        ("greater_than", 1, "critical"),
        ("greater_than", 0, None),
        ("greater_than", -1, None),
        ("less_than", 1, None),
        ("less_than", 0, None),
        ("less_than", -1, "critical"),
    ],
)
def test_percentage_change_zero_baseline(
    user_alert_monitor, operator, current, expected_type
) -> None:
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.threshold_operator = operator
    user_alert_monitor.critical_threshold_value = 50
    user_alert_monitor.warning_threshold_value = 20
    with _patch_ch(
        [
            _ch_result([{"value": current}]),
            _ch_result([{"mean": 0.0, "stddev": 0.0}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())

    if expected_type is None:
        assert not UserAlertMonitorLog.objects.exists()
    else:
        log = UserAlertMonitorLog.objects.get()
        assert log.type == expected_type
        assert f"({operator} 0.00)" in log.message


def test_percentage_change_uses_selected_historical_window(user_alert_monitor) -> None:
    user_alert_monitor.auto_threshold_time_window = 180
    user_alert_monitor.critical_threshold_value = 50
    now = timezone.now()
    window_start = now - timedelta(minutes=user_alert_monitor.alert_frequency)
    builder = mock.sentinel.monitor_builder
    with mock.patch.object(
        monitor_mod, "get_previous_period_baseline", return_value=100.0
    ) as previous_period_baseline:
        monitor_mod._check_percentage_change_threshold(
            user_alert_monitor, 151, window_start, now, builder
        )

    previous_period_baseline.assert_called_once_with(
        user_alert_monitor, window_start - timedelta(minutes=180), window_start, builder
    )
    log = UserAlertMonitorLog.objects.get()
    assert log.type == "critical"
    assert "(greater_than 150.00)" in log.message
    assert log.time_window_start == window_start
    assert log.time_window_end == now


def test_percentage_change_skips_without_history(user_alert_monitor) -> None:
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.save()
    with _patch_ch(
        [
            _ch_result([{"value": 20.0}]),
            _ch_result([{"mean": None, "stddev": None}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.count() == 0


def test_daily_tokens_uses_trailing_day_window(user_alert_monitor) -> None:
    user_alert_monitor.metric_type = "daily_tokens_spent"
    user_alert_monitor.save()
    patcher = _patch_ch([_ch_result([{"value": 100}])])
    with patcher:
        now = timezone.now()
        _get_metric_value(user_alert_monitor, now - timedelta(hours=1), now)
    args, _ = patcher.ch_instance.execute_ch_query.call_args
    params = args[1]
    window = params["end_time"] - params["start_time"]
    assert timedelta(hours=23) < window <= timedelta(days=1)


def test_failure_raises_for_temporal_retry(user_alert_monitor) -> None:
    # _original_func skips the activity wrapper's close_old_connections(),
    # which would break the test transaction.
    task_fn = process_monitor_task._original_func
    with _patch_ch([RuntimeError("CH timeout")]):
        with pytest.raises(RuntimeError):
            task_fn(str(user_alert_monitor.id), timezone.now().isoformat())


def test_missing_eval_config_returns_without_retry(
    user_alert_monitor,
) -> None:
    user_alert_monitor.metric_type = "evaluation_metrics"
    user_alert_monitor.metric = "99999999-9999-9999-9999-999999999999"
    user_alert_monitor.save()
    task_fn = process_monitor_task._original_func
    with _patch_ch([]):
        # Must return (no raise): misconfiguration is permanent, retrying is noise.
        task_fn(str(user_alert_monitor.id), timezone.now().isoformat())


def test_invalid_filters_are_non_retryable(user_alert_monitor) -> None:
    # A stored filter the builder rejects must become a non-retryable
    # MonitorConfigError (build_monitor_ch_builder wraps the ValueError), so the
    # task returns instead of raising and Temporal-retrying forever.
    user_alert_monitor.filters = {
        "span_attributes_filters": [
            {
                "column_id": "x",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "nonsense_op",
                    "filter_value": "v",
                },
            }
        ]
    }
    user_alert_monitor.save()
    task_fn = process_monitor_task._original_func
    with _patch_ch([]):  # must fail before any CH call
        task_fn(str(user_alert_monitor.id), timezone.now().isoformat())
    assert UserAlertMonitorLog.objects.count() == 0


def test_invalid_observation_type_filter_raises_config_error(
    user_alert_monitor,
) -> None:
    # Non-list/non-str observation_type is a permanent misconfig: the builder's
    # ValueError must surface as MonitorConfigError, not silently drop the filter.
    user_alert_monitor.filters = {"observation_type": 123}
    with pytest.raises(MonitorConfigError):
        build_monitor_ch_builder(user_alert_monitor)


@pytest.mark.parametrize("metric_type", MonitorMetricTypeChoices.values)
def test_projectless_monitor_raises_config_error_at_builder(
    user_alert_monitor, metric_type: str
) -> None:
    # Production: monitors with a NULL project bound ``project_id = 'None'``
    # and failed in ClickHouse (Code 376) on every run. The guard runs before
    # the eval-config lookup, so every metric type reports the missing project.
    user_alert_monitor.project = None
    user_alert_monitor.metric_type = metric_type
    if metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS:
        user_alert_monitor.metric = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(MonitorConfigError) as exc_info:
        build_monitor_ch_builder(user_alert_monitor)
    assert str(exc_info.value) == get_error_message("MONITOR_PROJECT_REQUIRED")


@pytest.mark.parametrize(
    "metric_type,filters",
    [
        ("count_of_errors", {}),
        ("error_rates_for_function_calling", {"observation_type": "tool"}),
        ("daily_tokens_spent", {}),
    ],
)
def test_projectless_monitor_is_skipped_without_ch_or_retry(
    user_alert_monitor, metric_type: str, filters: dict
) -> None:
    # The three production metric types that failed hourly with Code 376.
    user_alert_monitor.project = None
    user_alert_monitor.metric_type = metric_type
    user_alert_monitor.filters = filters
    user_alert_monitor.save()
    task_fn = process_monitor_task._original_func
    patcher = _patch_ch([])  # any CH call raises
    with patcher, capture_logs() as records:
        # Returns (no raise, so no Temporal retry) before any CH statement.
        task_fn(str(user_alert_monitor.id), timezone.now().isoformat())
    patcher.ch_instance.execute_ch_query.assert_not_called()
    skipped = [r for r in records if r["event"] == "monitor_misconfigured"]
    assert [r["monitor_id"] for r in skipped] == [str(user_alert_monitor.id)]
    assert UserAlertMonitorLog.objects.count() == 0


def test_deleted_monitor_returns_quietly() -> None:
    task_fn = process_monitor_task._original_func
    with _patch_ch([]):  # no CH call may happen for a missing monitor
        task_fn("99999999-9999-9999-9999-999999999999", timezone.now().isoformat())
    assert UserAlertMonitorLog.objects.count() == 0


def test_notification_helpers_receive_alert_args(user_alert_monitor) -> None:
    with mock.patch.object(monitor_mod, "_send_alert_email") as email_mock:
        with _patch_ch([_ch_result([{"value": 5.0}])]):
            _process_monitor(user_alert_monitor, timezone.now())
    args, _ = email_mock.call_args
    assert args[0] == user_alert_monitor
    assert "breached the critical threshold" in args[1]
    assert args[2] == "critical"


@override_settings(APP_BASE_URL="https://app.eu.futureagi.com")
def test_alert_email_button_links_to_this_deployment(user_alert_monitor) -> None:
    user_alert_monitor.notification_emails = ["alerts@example.com"]
    user_alert_monitor.save(update_fields=["notification_emails"])
    mail.outbox.clear()

    _send_alert_email(user_alert_monitor, "breached the critical threshold", "critical")

    assert len(mail.outbox) == 1
    alternative = mail.outbox[0].alternatives[0]
    html_body = (
        alternative.content if hasattr(alternative, "content") else alternative[0]
    )
    assert 'href="https://app.eu.futureagi.com/dashboard/alerts"' in html_body
    assert 'href="/dashboard/alerts"' not in html_body
    assert "app.futureagi.com" not in html_body.replace("app.eu.futureagi.com", "")


@pytest.mark.parametrize(
    "metric_type,alert_frequency,history_minutes,expected",
    [
        ("count_of_errors", 60, 60, 1.0),
        ("count_of_errors", 15, 60 * 24 * 7, 672.0),
        ("count_of_errors", 60, 90, 1.5),
        # A history shorter than one period is that fraction of a period, so
        # its total is scaled up to one period (no floor at 1).
        pytest.param("count_of_errors", 60, 30, 0.5, id="30m-of-an-hour"),
        pytest.param("count_of_errors", 60, 15, 0.25, id="15m-of-an-hour"),
        pytest.param("daily_tokens_spent", 60, 60, 1 / 24, id="1h-of-a-day"),
        ("daily_tokens_spent", 60, 60 * 24 * 7, 7.0),
        ("monthly_tokens_spent", 60, 60 * 24 * 60, 2.0),
    ],
)
def test_baseline_periods_count_evaluation_periods_in_the_history(
    user_alert_monitor,
    metric_type: str,
    alert_frequency: int,
    history_minutes: int,
    expected: float,
) -> None:
    user_alert_monitor.metric_type = metric_type
    user_alert_monitor.alert_frequency = alert_frequency
    assert (
        monitor_mod.baseline_periods(
            user_alert_monitor, timedelta(minutes=history_minutes)
        )
        == expected
    )


def test_previous_period_baseline_passes_periods_and_reads_mean(
    user_alert_monitor,
) -> None:
    # Three hours of history for an hourly monitor: three periods.
    now = timezone.now()
    patcher = _patch_ch([_ch_result([{"mean": 1.0, "stddev": None}])])
    builder = mock.MagicMock()
    builder.build_historical_stats_query.return_value = ("stats", {})
    with patcher:
        baseline = monitor_mod.get_previous_period_baseline(
            user_alert_monitor, now - timedelta(hours=3), now, builder
        )

    assert baseline == 1.0
    builder.build_historical_stats_query.assert_called_once_with(
        "count_of_errors", now - timedelta(hours=3), now, periods=3.0
    )
    _, kwargs = patcher.ch_instance.execute_ch_query.call_args
    assert kwargs["timeout_ms"] == MONITOR_QUERY_TIMEOUT_MS
    assert kwargs["settings"] == MONITOR_CH_SETTINGS


@pytest.mark.parametrize("row", [{"mean": None}, {"mean": float("nan")}, None])
def test_previous_period_baseline_without_history_is_none(
    user_alert_monitor, row
) -> None:
    now = timezone.now()
    builder = mock.MagicMock()
    builder.build_historical_stats_query.return_value = ("stats", {})
    with _patch_ch([_ch_result([row] if row else [])]):
        baseline = monitor_mod.get_previous_period_baseline(
            user_alert_monitor, now - timedelta(hours=1), now, builder
        )
    assert baseline is None


def test_graph_static_formats_ch_series(user_alert_monitor) -> None:
    from tracer.utils import monitor_graphs as graphs_mod

    instance = mock.MagicMock()
    ts = timezone.now()
    instance.execute_ch_query.return_value = _ch_result(
        [{"timestamp": ts, "value": 3}, {"timestamp": ts, "value": None}]
    )
    with mock.patch.object(graphs_mod, "AnalyticsQueryService", return_value=instance):
        data = graphs_mod.get_static_metric_graph_data(user_alert_monitor)
    assert data == [
        {"timestamp": ts.isoformat(), "value": 3},
        {"timestamp": ts.isoformat(), "value": 0},
    ]


def test_percentage_graph_bar_holding_now_uses_worker_baseline(
    user_alert_monitor,
) -> None:
    # The bar holding now is coloured by the worker's own baseline query
    # (get_previous_period_baseline), so the preview reflects real firing.
    from tracer.utils import monitor_graphs as graphs_mod

    user_alert_monitor.metric_type = "span_response_time"
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.threshold_operator = "greater_than"
    user_alert_monitor.critical_threshold_value = 0
    user_alert_monitor.warning_threshold_value = None
    user_alert_monitor.save()

    now = timezone.now().replace(minute=30, second=0, microsecond=0)
    instance = mock.MagicMock()
    # First CH call = time series (the hour holding now); second = baseline.
    instance.execute_ch_query.side_effect = [
        _ch_result([{"timestamp": now.replace(minute=0), "value": 1000.0}]),
        _ch_result([{"mean": 100.0, "stddev": 10.0}]),  # pooled latency
    ]
    with (
        mock.patch.object(graphs_mod, "AnalyticsQueryService", return_value=instance),
        mock.patch.object(graphs_mod.timezone, "now", return_value=now),
    ):
        out = graphs_mod.get_percentage_change_metric_graph_data(user_alert_monitor)

    # A second CH query (the worker's previous-period baseline) was issued.
    assert instance.execute_ch_query.call_count == 2
    # 1000 > 100 * (1 + 0/100) -> critical, against the worker's baseline.
    assert out["alert_bar_data"][-1]["status"] == "critical"


# --- Monitors saved before the threshold update keep the earlier rule --------
#
# Production judged a percentage threshold p against a band around the
# history's mean, mean +/- stddev * (1 + p / 100). Rows
# saved before the update and not edited since keep that rule; rows saved
# after it compare with one previous period, baseline * (1 +/- p / 100).


def _fired(monitor, value, band, now) -> UserAlertMonitorLog | None:
    """Check ``monitor`` at ``now`` with ClickHouse answering ``value`` and the
    history ``band``; the alert it wrote for that window, if any."""
    with _patch_ch([_ch_result([{"value": value}]), _ch_result([band])]):
        _process_monitor(monitor, now)
    return UserAlertMonitorLog.objects.filter(
        alert=monitor, time_window_end=now
    ).first()


def test_same_saved_percentages_keep_the_rule_they_were_saved_with(
    production_alert_monitor, user_alert_monitor
) -> None:
    # 400% / 300% (the form's numbers) on history with mean 100, stddev 10.
    # Earlier rule: critical above 100 + 10 * 5 = 150, warning above
    # 100 + 10 * 4 = 140. Current rule: critical above 100 * 5 = 500, warning
    # above 100 * 4 = 400.
    saved = {"critical_threshold_value": 400, "warning_threshold_value": 300}
    earlier = production_alert_monitor(**saved)
    current = user_alert_monitor
    current.threshold_type = "percentage_change"
    current.critical_threshold_value = 400
    current.warning_threshold_value = 300
    current.save()
    band = {"mean": 100.0, "stddev": 10.0}
    start = timezone.now()

    results = {}
    for step, value in enumerate((151, 141, 130, 401, 501)):
        now = start + timedelta(hours=step)
        results[value] = tuple(
            log.type if log else None
            for log in (_fired(m, value, band, now) for m in (earlier, current))
        )

    assert results == {
        151: ("critical", None),
        141: ("warning", None),
        130: (None, None),
        401: ("critical", "warning"),
        501: ("critical", "critical"),
    }
    earlier_message = UserAlertMonitorLog.objects.get(
        alert=earlier, time_window_end=start
    ).message
    assert "(151.00) breached the critical threshold (greater_than 150.00)" in (
        earlier_message
    )
    assert earlier_message.endswith(
        "based on historical data (mean: 100.00, stddev: 10.00)."
    )
    current_message = UserAlertMonitorLog.objects.get(
        alert=current, type="critical"
    ).message
    assert "(greater_than 500.00)" in current_message


def test_less_than_saved_before_the_update_keeps_the_band_below_the_mean(
    production_alert_monitor,
) -> None:
    # Below by 50%: the earlier rule's line is 100 - 10 * 1.5 = 85, so a 16%
    # drop is critical; the current rule's line is 100 * 0.5 = 50.
    monitor = production_alert_monitor(
        threshold_operator="less_than", critical_threshold_value=50
    )

    log = _fired(monitor, 84, {"mean": 100.0, "stddev": 10.0}, timezone.now())

    assert log is not None and log.type == "critical"
    assert "(less_than 85.00) based on historical data" in log.message


def test_errors_spike_saved_before_the_update_fires_on_every_flat_check(
    production_alert_monitor, user_alert_monitor
) -> None:
    # Omega's "Errors spike" as production created it: 200% / 100%, every 15
    # minutes, 7 days of history. Production compared each 15-minute total
    # with per-minute statistics: a flat 100 errors per
    # 15 minutes (noise sd 10) is 6.667 a minute with sd 10 / sqrt(15) =
    # 2.582, so its critical line is 6.667 + 2.582 * 3 = 14.41 and every
    # check is critical. The current rule compares with one period of the
    # history, 100, and stays quiet.
    spec = {
        "critical_threshold_value": 200,
        "warning_threshold_value": 100,
        "alert_frequency": 15,
        "auto_threshold_time_window": 60 * 24 * 7,
    }
    earlier = production_alert_monitor(name="Errors spike (production)", **spec)
    current = user_alert_monitor
    current.threshold_type = "percentage_change"
    for field, value in spec.items():
        setattr(current, field, value)
    current.save()
    per_minute = {"mean": 100 / 15, "stddev": 10 / 15**0.5}
    start = timezone.now()

    band_sql = []
    for check in range(24):
        now = start + timedelta(minutes=15 * check)
        patcher = _patch_ch([_ch_result([{"value": 100.0}]), _ch_result([per_minute])])
        with patcher:
            _process_monitor(earlier, now)
        band_sql.append(patcher.ch_instance.execute_ch_query.call_args_list[1].args[0])
        _fired(current, 100.0, {"mean": 100.0, "stddev": None}, now)

    logs = UserAlertMonitorLog.objects.filter(alert=earlier)
    assert [log.type for log in logs] == ["critical"] * 24
    assert all(
        "(100.00) breached the critical threshold (greater_than 14.41) based on "
        "historical data (mean: 6.67, stddev: 2.58)." in log.message
        for log in logs
    )
    assert all("toStartOfMinute(start_time)" in sql for sql in band_sql)
    assert not UserAlertMonitorLog.objects.filter(alert=current).exists()


@pytest.mark.parametrize(
    "metric_type,alert_frequency,bucket,anchor",
    [
        ("count_of_errors", 15, "toStartOfMinute", timedelta(minutes=15)),
        ("count_of_errors", 60, "toStartOfHour", timedelta(hours=1)),
        ("token_usage", 240, "toStartOfHour", timedelta(hours=4)),
        # Daily and monthly tokens keep the current history anchor: the
        # history ends where the trailing day or 30 days begin.
        ("daily_tokens_spent", 60, "toStartOfDay", timedelta(days=1)),
        ("monthly_tokens_spent", 60, "toStartOfMonth", timedelta(days=30)),
    ],
)
def test_earlier_band_reads_production_calendar_buckets(
    production_alert_monitor, metric_type, alert_frequency, bucket, anchor
) -> None:
    monitor = production_alert_monitor(
        metric_type=metric_type,
        alert_frequency=alert_frequency,
        critical_threshold_value=400,
        auto_threshold_time_window=60 * 24 * 7,
    )
    now = timezone.now()
    patcher = _patch_ch(
        [_ch_result([{"value": 1.0}]), _ch_result([{"mean": 1.0, "stddev": 0.0}])]
    )
    with patcher:
        _process_monitor(monitor, now)

    band_sql, params = patcher.ch_instance.execute_ch_query.call_args_list[1].args
    assert f"{bucket}(start_time)" in band_sql
    assert "stddevSamp(bucket_value)" in band_sql
    history_end = (now - anchor).astimezone(UTC).replace(tzinfo=None)
    assert params["end_time"] == history_end
    assert params["start_time"] == history_end - timedelta(days=7)


@pytest.mark.parametrize("threshold_type", ["static", "percentage_change"])
def test_provider_monitors_read_the_share_their_numbers_were_saved_in(
    production_alert_monitor, user_alert_monitor, threshold_type
) -> None:
    # Saved before the update, the numbers are about the share of providers
    # with no errors; saved after it, about the share with errors.
    fields = {
        "metric_type": "service_provider_error_rates",
        "threshold_type": threshold_type,
        "threshold_operator": "less_than",
        "critical_threshold_value": 95.0,
    }
    earlier = production_alert_monitor(**fields)
    current = user_alert_monitor
    for field, value in fields.items():
        setattr(current, field, value)
    current.save()
    now = timezone.now()

    def provider_share(monitor) -> str:
        sql, _ = build_monitor_ch_builder(monitor).build_metric_value_query(
            monitor.metric_type, now - timedelta(hours=1), now
        )
        return " ".join(sql.split())

    assert "uniqIf(provider, error_count = 0) / uniq(provider)" in (
        provider_share(earlier)
    )
    assert "uniqIf(provider, error_count > 0) / uniq(provider)" in (
        provider_share(current)
    )


# --- Static threshold operators & tiers ---------------------------------------


def test_static_less_than_fires(user_alert_monitor) -> None:
    # less_than spend-style monitor: value below threshold fires.
    user_alert_monitor.threshold_operator = "less_than"
    user_alert_monitor.critical_threshold_value = 100
    user_alert_monitor.save()
    with _patch_ch([_ch_result([{"value": 40.0}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.get().type == "critical"


def test_static_less_than_above_threshold_no_fire(user_alert_monitor) -> None:
    user_alert_monitor.threshold_operator = "less_than"
    user_alert_monitor.critical_threshold_value = 100
    user_alert_monitor.save()
    with _patch_ch([_ch_result([{"value": 500.0}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.count() == 0


def test_static_warning_tier_fires_when_critical_not_breached(
    user_alert_monitor,
) -> None:
    user_alert_monitor.critical_threshold_value = 100
    user_alert_monitor.warning_threshold_value = 10
    user_alert_monitor.save()
    with _patch_ch([_ch_result([{"value": 50.0}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.get().type == "warning"


def test_static_critical_wins_over_warning(user_alert_monitor) -> None:
    user_alert_monitor.critical_threshold_value = 100
    user_alert_monitor.warning_threshold_value = 10
    user_alert_monitor.save()
    with _patch_ch([_ch_result([{"value": 500.0}])]):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.get().type == "critical"


def test_monthly_tokens_uses_trailing_month_window(user_alert_monitor) -> None:
    user_alert_monitor.metric_type = "monthly_tokens_spent"
    user_alert_monitor.save()
    patcher = _patch_ch([_ch_result([{"value": 100}])])
    with patcher:
        now = timezone.now()
        _get_metric_value(user_alert_monitor, now - timedelta(hours=1), now)
    args, _ = patcher.ch_instance.execute_ch_query.call_args
    params = args[1]
    window = params["end_time"] - params["start_time"]
    assert timedelta(days=29) < window <= timedelta(days=30)


# --- check_alerts scheduler ---------------------------------------------------


def _make_monitor(user_alert_monitor, **kw):
    from tracer.models.monitor import UserAlertMonitor

    m = UserAlertMonitor.objects.create(
        organization=user_alert_monitor.organization,
        workspace=user_alert_monitor.workspace,
        project=user_alert_monitor.project,
        name=kw.pop("name", "m2"),
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0.1,
        alert_frequency=60,
        is_mute=kw.pop("is_mute", False),
        **kw,
    )
    return m


def test_check_alerts_dispatches_due_and_stamps(user_alert_monitor) -> None:
    # last_checked_at is NULL -> due; must be stamped and dispatched.
    assert user_alert_monitor.last_checked_at is None
    with mock.patch.object(monitor_mod.process_monitor_task, "delay") as delay:
        monitor_mod.check_alerts._original_func()
    assert delay.call_count == 1
    assert delay.call_args[0][0] == user_alert_monitor.id
    user_alert_monitor.refresh_from_db()
    assert user_alert_monitor.last_checked_at is not None


def test_check_alerts_skips_recently_checked(user_alert_monitor) -> None:
    user_alert_monitor.last_checked_at = timezone.now()
    user_alert_monitor.save()
    with mock.patch.object(monitor_mod.process_monitor_task, "delay") as delay:
        monitor_mod.check_alerts._original_func()
    assert delay.call_count == 0


def test_check_alerts_redispatches_stale(user_alert_monitor) -> None:
    # Checked longer than alert_frequency (60 min) ago -> due again.
    user_alert_monitor.last_checked_at = timezone.now() - timedelta(minutes=61)
    user_alert_monitor.save()
    with mock.patch.object(monitor_mod.process_monitor_task, "delay") as delay:
        monitor_mod.check_alerts._original_func()
    assert delay.call_count == 1


def test_check_alerts_skips_muted(user_alert_monitor) -> None:
    user_alert_monitor.is_mute = True
    user_alert_monitor.save()
    with mock.patch.object(monitor_mod.process_monitor_task, "delay") as delay:
        monitor_mod.check_alerts._original_func()
    assert delay.call_count == 0


def test_check_alerts_one_bad_dispatch_does_not_drop_rest(
    user_alert_monitor,
) -> None:
    m2 = _make_monitor(user_alert_monitor)
    calls = []

    def _delay(monitor_id, now_iso):
        calls.append(monitor_id)
        if len(calls) == 1:
            raise RuntimeError("queue down")

    with mock.patch.object(
        monitor_mod.process_monitor_task, "delay", side_effect=_delay
    ):
        monitor_mod.check_alerts._original_func()  # must not raise
    assert set(calls) == {user_alert_monitor.id, m2.id}
