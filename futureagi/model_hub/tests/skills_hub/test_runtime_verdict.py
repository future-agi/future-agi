"""Deterministic move-state boundaries. Run on the cloud stack only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import (
    EVAL_PASS_POINTS,
    FAILED_REQUESTS_POINTS,
    FAILED_REQUESTS_RATIO,
    LATENCY_RATIO,
    MIN_EVALUATED,
    MIN_TRACES,
    move_windows,
    regression_verdict,
)

AGENT = ("project", "service", "agent")


def metrics(*, traces=30, failed="0.02", latency=100, evaluated=30, passed="0.9"):
    return {
        "traces": traces,
        "failed_requests": {"rate": failed},
        "latency_ms": {"p95": latency},
        "evals": [{"config_id": "check", "evaluated": evaluated, "pass_rate": passed}],
    }


def verdict(before, after, *, complete=True):
    return regression_verdict({AGENT: before}, {AGENT: after}, complete=complete)


def test_constants_are_the_one_default_rule():
    assert MIN_TRACES == MIN_EVALUATED == 30
    assert FAILED_REQUESTS_POINTS == Decimal("0.01")
    assert FAILED_REQUESTS_RATIO == Decimal("1.5")
    assert LATENCY_RATIO == Decimal("1.25")
    assert EVAL_PASS_POINTS == Decimal("0.05")


@pytest.mark.parametrize(
    "after,expected",
    [("0.029999", "no_change"), ("0.03", "worse"), ("0.030001", "worse")],
)
def test_failed_requests_one_point_boundary(after, expected):
    assert verdict(metrics(failed="0.02"), metrics(failed=after))["verdict"] == expected


@pytest.mark.parametrize(
    "after,expected",
    [("0.059999", "no_change"), ("0.06", "worse"), ("0.060001", "worse")],
)
def test_failed_requests_ratio_boundary(after, expected):
    assert verdict(metrics(failed="0.04"), metrics(failed=after))["verdict"] == expected


@pytest.mark.parametrize(
    "after,expected",
    [("0.009999", "no_change"), ("0.01", "worse"), ("0.010001", "worse")],
)
def test_failed_requests_zero_baseline_still_requires_a_point(after, expected):
    assert verdict(metrics(failed="0"), metrics(failed=after))["verdict"] == expected


@pytest.mark.parametrize(
    "after,expected", [(124.999, "no_change"), (125, "worse"), (125.001, "worse")]
)
def test_latency_boundary(after, expected):
    assert verdict(metrics(), metrics(latency=after))["verdict"] == expected


@pytest.mark.parametrize(
    "after,expected",
    [("0.850001", "no_change"), ("0.85", "worse"), ("0.849999", "worse")],
)
def test_eval_pass_boundary(after, expected):
    assert verdict(metrics(), metrics(passed=after))["verdict"] == expected


@pytest.mark.parametrize(
    "count,expected", [(29, "not_enough_data"), (30, "worse"), (31, "worse")]
)
@pytest.mark.parametrize("side", ["before", "after"])
def test_trace_sample_boundary_on_both_sides(count, expected, side):
    before, after = metrics(), metrics(failed="0.03")
    (before if side == "before" else after)["traces"] = count
    assert verdict(before, after)["verdict"] == expected


@pytest.mark.parametrize(
    "count,expected", [(29, "no_change"), (30, "worse"), (31, "worse")]
)
@pytest.mark.parametrize("side", ["before", "after"])
def test_evaluated_sample_boundary_on_both_sides(count, expected, side):
    before, after = metrics(), metrics(passed="0.85")
    (before if side == "before" else after)["evals"][0]["evaluated"] = count
    assert verdict(before, after)["verdict"] == expected


@pytest.mark.parametrize(
    "worse", [metrics(failed="0.03"), metrics(latency=125), metrics(passed="0.85")]
)
def test_improvement_is_the_mirror_rule(worse):
    result = verdict(worse, metrics())
    assert result["verdict"] == "better"
    assert result["primary_action"] is None
    assert verdict(worse, metrics(), complete=False)["verdict"] == "watching"


def test_one_worse_agent_dominates_other_agent_improvement():
    other = ("project", "service", "other")
    result = regression_verdict(
        {AGENT: metrics(), other: metrics(latency=125)},
        {AGENT: metrics(failed="0.03"), other: metrics()},
        complete=True,
    )
    assert result["verdict"] == "worse"
    assert result["trigger"]["metric"] == "failed_requests"
    assert result["trigger"]["agents"] == 1
    assert result["trigger"]["before"] == [{"agent_key": AGENT, "value": "0.02"}]


def test_same_agents_means_the_whole_project_service_name_tuple():
    before = {AGENT: metrics(), ("other-project", "service", "agent"): metrics()}
    after = {
        AGENT: metrics(),
        ("project", "other-service", "agent"): metrics(failed="0.9"),
    }
    result = regression_verdict(before, after, complete=True)
    assert result["verdict"] == "no_change"
    assert [row["agent_key"] for row in result["by_agent"]] == [AGENT]


def test_disjoint_agents_are_not_enough_data():
    assert (
        regression_verdict({AGENT: metrics()}, {}, complete=True)["verdict"]
        == "not_enough_data"
    )


def test_watching_collecting_so_far_and_final_states():
    collecting = verdict(metrics(traces=29), metrics(failed="0.9"), complete=False)
    assert collecting["verdict"] == "watching"
    assert collecting["so_far"] is False
    live = verdict(metrics(), metrics(failed="0.03"), complete=False)
    assert live["verdict"] == "worse_so_far" and live["so_far"] is True
    assert live["primary_action"] == "review_failed"
    assert verdict(metrics(), metrics(failed="0.03"))["verdict"] == "worse"


def test_missing_roots_and_evals_do_not_become_success():
    empty = metrics(failed=None, latency=None, evaluated=0, passed=None)
    assert verdict(empty, empty)["verdict"] == "not_enough_data"
    assert verdict(metrics(latency=0), metrics(latency=0))["verdict"] == "no_change"


MOVED = datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_move_windows_cut_before_at_previous_move_and_keep_open_after():
    previous = MOVED - timedelta(hours=2)
    windows = move_windows(
        moved_at=MOVED,
        now=MOVED + timedelta(hours=3),
        window=timedelta(days=1),
        previous_move_at=previous,
    )
    assert windows["before"] == {"from": previous, "to": MOVED}
    assert windows["after"] == {"from": MOVED, "to": MOVED + timedelta(hours=3)}
    assert windows["before_cut_at"] == previous
    assert windows["complete"] is False
    assert windows["hours_elapsed"] == 3 and windows["hours_total"] == 24


def test_exact_end_closes_window_and_previous_outside_window_does_not_cut():
    windows = move_windows(
        moved_at=MOVED,
        now=MOVED + timedelta(days=1),
        window=timedelta(days=1),
        previous_move_at=MOVED - timedelta(days=2),
    )
    assert windows["complete"] is True
    assert windows["before_cut_at"] is None
    assert windows["after"]["to"] == windows["ends_at"]


@pytest.mark.parametrize(
    "window", [timedelta(minutes=59), timedelta(days=7, seconds=1)]
)
def test_window_range_rejects_invalid_values(window):
    with pytest.raises(SkillsHubError):
        move_windows(moved_at=MOVED, now=MOVED, window=window)


@pytest.mark.parametrize("window", [timedelta(hours=1), timedelta(days=7)])
def test_window_range_includes_boundaries(window):
    assert move_windows(moved_at=MOVED, now=MOVED, window=window)["complete"] is False


def test_future_events_and_naive_timestamps_are_rejected():
    with pytest.raises(SkillsHubError):
        move_windows(
            moved_at=MOVED, now=MOVED - timedelta(seconds=1), window=timedelta(days=1)
        )
    with pytest.raises(SkillsHubError):
        move_windows(
            moved_at=MOVED.replace(tzinfo=None), now=MOVED, window=timedelta(days=1)
        )
