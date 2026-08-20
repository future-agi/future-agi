"""Alert evaluator behavior with ClickHouse mocked: value pass-through,
None-suppression, threshold firing, CH settings.
DB-backed (monitor rows), no real ClickHouse."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest import mock

import pytest
from django.utils import timezone

from tracer.models.monitor import UserAlertMonitorLog
from tracer.utils import monitor as monitor_mod
from tracer.utils.monitor import (
    MONITOR_CH_SETTINGS,
    MONITOR_QUERY_TIMEOUT_MS,
    MonitorConfigError,
    _get_metric_value,
    _process_monitor,
    build_monitor_ch_builder,
    get_interval_kind,
    process_monitor_task,
)

pytestmark = pytest.mark.django_db


def _fake_eval_config(output_value: Optional[str]) -> SimpleNamespace:
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


def _ch_result(rows: List[dict]) -> SimpleNamespace:
    return SimpleNamespace(data=rows)


def _patch_ch(results: List[Any]) -> mock._patch:
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
    with mock.patch.object(monitor_mod, "_send_alert_email"), mock.patch.object(
        monitor_mod, "_send_slack_notification"
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


def test_percentage_change_fires_on_deviation(user_alert_monitor) -> None:
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.critical_threshold_value = 50
    user_alert_monitor.save()
    # current 20 vs mean 10, stddev 2 -> critical threshold 10 + 2*1.5 = 13
    with _patch_ch(
        [
            _ch_result([{"value": 20.0}]),
            _ch_result([{"mean": 10.0, "stddev": 2.0}]),
        ]
    ):
        _process_monitor(user_alert_monitor, timezone.now())
    assert UserAlertMonitorLog.objects.get().type == "critical"


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


@pytest.mark.parametrize(
    "metric_type,alert_frequency,expected",
    [
        ("count_of_errors", 30, "minute"),
        ("count_of_errors", 60, "hour"),
        ("count_of_errors", 60 * 25, "day"),
        ("daily_tokens_spent", 60, "day"),
        ("monthly_tokens_spent", 60, "month"),
    ],
)
def test_interval_kind_mapping(
    user_alert_monitor, metric_type: str, alert_frequency: int, expected: str
) -> None:
    user_alert_monitor.metric_type = metric_type
    user_alert_monitor.alert_frequency = alert_frequency
    assert get_interval_kind(user_alert_monitor) == expected


def test_graph_static_formats_ch_series(user_alert_monitor) -> None:
    from tracer.utils import monitor_graphs as graphs_mod

    instance = mock.MagicMock()
    ts = timezone.now()
    instance.execute_ch_query.return_value = _ch_result(
        [{"timestamp": ts, "value": 3}, {"timestamp": ts, "value": None}]
    )
    with mock.patch.object(
        graphs_mod, "AnalyticsQueryService", return_value=instance
    ):
        data = graphs_mod.get_static_metric_graph_data(user_alert_monitor)
    assert data == [
        {"timestamp": ts.isoformat(), "value": 3},
        {"timestamp": ts.isoformat(), "value": 0},
    ]


def test_percentage_graph_alert_bars_use_evaluator_band(user_alert_monitor) -> None:
    # The alert bars must be coloured by the evaluator's own historical stats
    # (build_historical_stats_query), not a divergent per-bucket rolling stddev,
    # so the preview reflects real firing.
    from tracer.utils import monitor_graphs as graphs_mod

    user_alert_monitor.metric_type = "span_response_time"
    user_alert_monitor.threshold_type = "percentage_change"
    user_alert_monitor.threshold_operator = "greater_than"
    user_alert_monitor.critical_threshold_value = 0
    user_alert_monitor.warning_threshold_value = None
    user_alert_monitor.save()

    ts = timezone.now()
    instance = mock.MagicMock()
    # First CH call = time series (one high bucket); second = evaluator stats.
    instance.execute_ch_query.side_effect = [
        _ch_result([{"timestamp": ts, "value": 1000.0}]),
        _ch_result([{"mean": 100.0, "stddev": 10.0}]),  # per-row band
    ]
    with mock.patch.object(
        graphs_mod, "AnalyticsQueryService", return_value=instance
    ):
        out = graphs_mod.get_percentage_change_metric_graph_data(user_alert_monitor)

    # A second CH query (the evaluator's historical stats) was issued.
    assert instance.execute_ch_query.call_count == 2
    # 1000 > 100 + 10*(1+0/100) = 110 -> critical, using the evaluator band.
    assert out["alert_bar_data"][-1]["status"] == "critical"


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
