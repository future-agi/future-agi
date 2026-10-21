"""Regression tests for scenario-clustered pass-rate intervals."""

from unittest.mock import MagicMock

import pytest

from simulate.services.run_reliability_v3 import (
    build_reliability,
    scenario_clustered_interval,
)


@pytest.mark.parametrize(
    ("clusters", "expected_n"),
    [
        ([(10, 10), (10, 10), (10, 10)], 3),
        ([(0, 10), (0, 10), (0, 10)], 3),
        ([(5, 10)], 1),
        ([(5, 10), (5, 10), (5, 10)], 3),
    ],
)
def test_interval_fallback_uses_scenarios_not_trials(clusters, expected_n):
    interval = scenario_clustered_interval(clusters)
    assert interval is not None
    assert interval["evaluated"] == sum(evaluated for _, evaluated in clusters)
    assert interval["clusters"] == len(clusters)
    assert interval["effective_n"] == expected_n
    assert interval["low"] <= interval["high"]


def test_interval_uses_cluster_variance_when_available():
    interval = scenario_clustered_interval([(5, 10), (8, 10), (2, 10)])
    assert interval is not None
    assert interval["effective_n"] > 3
    assert interval["effective_n"] < 30


def test_interval_with_no_evaluated_calls_is_unavailable():
    assert scenario_clustered_interval([(0, 0)]) is None


def test_reliability_tile_requires_every_trial_but_rows_use_evaluated_calls():
    calls = [
        ("all-pass", "All pass", "passed"),
        ("all-pass", "All pass", "passed"),
        ("pass-error", "Pass plus error", "passed"),
        ("pass-error", "Pass plus error", "error"),
        ("pass-unmeasured", "Pass plus inconclusive", "passed"),
        ("pass-unmeasured", "Pass plus inconclusive", "inconclusive"),
        ("fail-error", "Fail plus error", "failed"),
        ("fail-error", "Fail plus error", "error"),
        ("all-error", "All error", "error"),
        ("all-error", "All error", "error"),
    ]
    queryset = MagicMock()
    queryset.order_by.return_value.values_list.return_value.iterator.return_value = (
        iter(calls)
    )

    result = build_reliability(queryset, trials=2)

    assert result["consistent_pass"] == 1
    rows = {row["scenario_key"]: row for row in result["rows"]}
    assert rows["all-pass"]["verdict"] == "passed"
    assert rows["pass-error"]["verdict"] == "passed"
    assert rows["pass-error"]["evaluated"] == 1
    assert rows["pass-error"]["runs"] == 2
    assert rows["pass-unmeasured"]["verdict"] == "passed"
    assert rows["pass-unmeasured"]["evaluated"] == 1
    assert rows["fail-error"]["verdict"] == "failed"
    assert rows["fail-error"]["evaluated"] == 1
    assert rows["all-error"]["verdict"] == "not_evaluated"
