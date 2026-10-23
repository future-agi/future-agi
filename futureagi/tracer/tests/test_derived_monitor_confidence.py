# ruff: noqa: F811
"""Fixed-window confidence gating; simulations execute the production estimator."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from tracer.models.derived_metric import DerivedMetricMonitorDelivery
from tracer.models.monitor import UserAlertMonitorLog
from tracer.services.derived_metrics.integrations import (
    monitor_comparison_value,
    notify_run_monitors,
)
from tracer.tests.test_derived_metric_api import delivery_case, saved  # noqa: F401
from tracer.tests.test_derived_metric_clickhouse import metric_ch  # noqa: F401
from tracer.tests.test_derived_metric_uncertainty import (
    REPLICATES,
    Batch,
    _scope,
    stage_and_rollup,
)
from tracer.utils.monitor import _check_static_threshold


@pytest.mark.django_db
def test_confident_option_is_saved_and_invalid_modes_are_rejected(delivery_case):
    from tracer.serializers.monitor import UserAlertMonitorSerializer

    _, monitor = delivery_case
    assert monitor.alert_when == "point"
    serializer = UserAlertMonitorSerializer(
        monitor, data={"alert_when": "confident"}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    monitor.refresh_from_db()
    assert monitor.alert_when == "confident"
    for update, field in (
        ({"alert_when": "unknown"}, "alert_when"),
        ({"threshold_operator": "equals"}, "threshold_operator"),
        ({"metric_type": "span_response_time", "metric": None}, "alert_when"),
    ):
        invalid = UserAlertMonitorSerializer(monitor, data=update, partial=True)
        assert not invalid.is_valid()
        assert field in invalid.errors


def row(value=0.8, low=0.7, high=0.9, status="ok"):
    return {
        "value": value,
        "uncertainty": {"status": status, "ci_low": low, "ci_high": high},
    }


@pytest.mark.parametrize(
    "operator,expected", [("less_than", 0.9), ("greater_than", 0.7), ("equals", None)]
)
def test_confidence_uses_the_nearest_bound(operator, expected):
    monitor = SimpleNamespace(alert_when="confident", threshold_operator=operator)
    assert monitor_comparison_value(monitor, row()) == expected
    for invalid in (
        row(status="not_estimable"),
        row(low=None),
        row(low=float("nan")),
        row(low=1, high=0),
        row(value=None),
    ):
        assert monitor_comparison_value(monitor, invalid) is None
    monitor.alert_when = "point"
    assert monitor_comparison_value(monitor, row(status="not_estimable")) == 0.8


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mode,status,high,expected_alerts,expected_deliveries",
    [
        ("point", "not_estimable", None, 1, 1),
        ("confident", "ok", 0.8, 0, 1),
        ("confident", "ok", 0.7, 1, 1),
        ("confident", "not_estimable", None, 0, 0),
    ],
)
def test_confident_delivery_uses_existing_ledger(
    delivery_case, mode, status, high, expected_alerts, expected_deliveries
):
    run, monitor = delivery_case
    monitor.alert_when = mode
    monitor.save(update_fields=["alert_when"])
    # Persisted-value seam only; real PG transactions/log and deduplication.
    with patch(
        "tracer.services.derived_metrics.integrations.read_results",
        return_value={"results": [row(0.6, 0.4, high, status)]},
    ):
        notify_run_monitors(run)
        notify_run_monitors(run)
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == expected_alerts
    assert (
        DerivedMetricMonitorDelivery.objects.filter(monitor=monitor).count()
        == expected_deliveries
    )


def test_seeded_fixed_window_alert_noise_and_shift(metric_ch):
    rng = np.random.default_rng(20261024)
    monitor = SimpleNamespace(
        name="Rate",
        project=SimpleNamespace(name="Synthetic"),
        threshold_operator="less_than",
        critical_threshold_value=0.90,
        warning_threshold_value=None,
    )
    counts = []
    for truth in (0.92, 0.75):
        batch = Batch("mean", "unweighted")
        for rep in range(REPLICATES):
            batch.add(rep, rng.binomial(1, truth, 50), np.ones(50))
        results = stage_and_rollup(metric_ch, _scope(), "mean(y)", batch)
        fired = {"point": [], "confident": []}
        # Only notification delivery is stubbed: estimator, gating and threshold
        # precedence are production code. No real email/Slack is sent.
        with patch("tracer.utils.monitor._handle_alert_trigger"):
            for mode in fired:
                monitor.alert_when = mode
                for result in results:
                    bound = monitor_comparison_value(
                        monitor, row(result[1], result[3], result[4], result[5])
                    )
                    fired[mode].append(
                        bound is not None
                        and bool(
                            _check_static_threshold(
                                monitor, result[1], None, None, comparison_value=bound
                            )
                        )
                    )
        counts.append(fired)
    point_noise = sum(counts[0]["point"])
    confident_noise = sum(counts[0]["confident"])
    detection = float(
        np.any(np.asarray(counts[1]["confident"][:1998]).reshape(-1, 3), axis=1).mean()
    )
    print(
        "alert-simulation",
        {
            "windows_per_state": REPLICATES,
            "point_false_alerts": point_noise,
            "confident_false_alerts": confident_noise,
            "shift_detected_within_three_windows": detection,
        },
    )
    assert point_noise > 0
    assert confident_noise <= point_noise / 10
    assert detection >= 0.90
