"""Regression tests for pass^1 / pass^k and the scenario-clustered interval."""

import random
from unittest.mock import MagicMock

import pytest

from simulate.services.run_reliability_v3 import (
    build_reliability,
    scenario_interval,
)


def _reliability(calls, trials):
    queryset = MagicMock()
    queryset.order_by.return_value.values_list.return_value.iterator.return_value = (
        iter(
            (f"call-{index}", key, label, outcome)
            for index, (key, label, outcome) in enumerate(calls)
        )
    )
    return build_reliability(queryset, trials=trials)


def test_interval_is_withheld_below_ten_scenarios():
    assert scenario_interval([(1, 3)] * 9) is None
    assert scenario_interval([(1, 3)] * 9 + [(0, 0)]) is None
    assert scenario_interval([(1, 3)] * 10) is not None


def test_interval_counts_scenarios_not_trials():
    # Same 60 verdicts and pooled rate, split across 20 vs 60 scenarios. Fewer,
    # all-or-nothing scenarios carry less information, so their range is wider.
    clustered = scenario_interval([(3, 3)] * 12 + [(0, 3)] * 8)
    spread = scenario_interval([(1, 1)] * 36 + [(0, 1)] * 24)
    assert clustered["low"] < 60.0 < clustered["high"]
    assert spread["low"] < 60.0 < spread["high"]
    assert clustered["high"] - clustered["low"] > spread["high"] - spread["low"]
    assert clustered["scenarios"] == 20
    assert clustered["evaluated"] == 60


@pytest.mark.parametrize(
    "clusters",
    [[(3, 3)] * 12, [(2, 3)] * 10, [(0, 3)] * 10],
    ids=["all-pass", "all-two-of-three", "all-fail"],
)
def test_interval_keeps_width_when_every_scenario_agrees(clusters):
    interval = scenario_interval(clusters)
    assert interval["high"] - interval["low"] > 10
    # No spread between scenarios: one verdict per scenario, not per trial.
    assert interval["effective_n"] == len(clusters)


@pytest.mark.parametrize("scenarios", [10, 20, 50])
def test_interval_covers_a_near_perfect_agent(scenarios):
    # Near-perfect agents are where people read this number hardest, and where a
    # range built only from the observed scenario rates collapses.
    rng = random.Random(scenarios)
    true_rate, k, runs = 0.99, 3, 400
    covered = 0
    for _ in range(runs):
        clusters = [
            (sum(rng.random() < true_rate for _ in range(k)), k)
            for _ in range(scenarios)
        ]
        interval = scenario_interval(clusters)
        covered += interval["low"] <= true_rate * 100 <= interval["high"]
    assert covered / runs >= 0.9


def test_pass_hat_metrics_leave_inconclusive_trials_out():
    calls = [
        ("all-pass", "All pass", "passed"),
        ("all-pass", "All pass", "passed"),
        ("all-pass", "All pass", "passed"),
        ("two-of-three", "Two of three", "passed"),
        ("two-of-three", "Two of three", "passed"),
        ("two-of-three", "Two of three", "failed"),
        ("pass-error", "Pass plus error", "passed"),
        ("pass-error", "Pass plus error", "passed"),
        ("pass-error", "Pass plus error", "error"),
        ("all-error", "All error", "error"),
        ("all-error", "All error", "inconclusive"),
        ("all-error", "All error", "error"),
    ]

    result = _reliability(calls, trials=3)

    assert result["k"] == 3
    assert result["inconclusive"] == 4
    # (1 + 2/3 + 1) / 3 conclusive scenarios; all-error has no evaluated trial.
    assert result["pass_hat_1"] == 88.89
    # Only scenarios whose 3 trials were all conclusive can show 3 of 3 passing.
    assert result["pass_hat_k_scenarios"] == 2
    assert result["pass_hat_k_passed"] == 1
    assert result["pass_hat_k"] == 50.0
    # pass^k drops pass-error and all-error, so the header must say so.
    assert result["pass_hat_k_excluded"] == 2
    # Below the interval floor the header shows pooled counts, sent as such.
    assert (result["trials_passed"], result["trials_evaluated"]) == (7, 8)
    assert result["pass_rate_interval"] is None
    rows = {row["scenario_key"]: row for row in result["rows"]}
    assert (rows["pass-error"]["passed"], rows["pass-error"]["evaluated"]) == (2, 2)
    assert rows["pass-error"]["verdict"] == "passed"
    assert rows["all-error"]["verdict"] == "not_evaluated"
    assert sorted(rows["two-of-three"]["call_ids"]) == ["call-3", "call-4", "call-5"]


@pytest.mark.parametrize(
    ("calls", "trials"),
    [
        # k = 1: pass^k would only repeat pass^1.
        ([("a", "A", "passed"), ("b", "B", "failed")], 1),
        # Uneven trial counts: no single k to report.
        (
            [
                ("a", "A", "passed"),
                ("a", "A", "passed"),
                ("b", "B", "passed"),
            ],
            2,
        ),
    ],
)
def test_pass_hat_k_needs_one_shared_k_above_one(calls, trials):
    result = _reliability(calls, trials=trials)
    assert result["pass_hat_k"] is None
    assert result["pass_hat_1"] is not None


def test_fifty_scenarios_at_k_three_report_an_interval():
    calls = [
        (f"s{index}", f"S{index}", "passed" if index % 4 or trial else "failed")
        for index in range(50)
        for trial in range(3)
    ]
    result = _reliability(calls, trials=3)
    assert result["k"] == 3
    interval = result["pass_rate_interval"]
    assert interval["scenarios"] == 50
    assert interval["low"] < result["pass_hat_1"] < interval["high"]
