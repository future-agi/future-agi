"""The arithmetic rules of a Console agent's learning curves (learning-service P1-09), on run
rows alone: no database, no ClickHouse. The same figures, read from seeded spans and checked
against an independent recompute, are tracer/tests/omega_agent_curves/test_curves_replay.py.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.omega_agents.curves.aggregate import (
    CHARGED_NOTHING,
    MINIMUM_N,
    NO_COMPLETED_RUNS,
    NO_MODEL_CALLS,
    NO_PREVIOUS_VERSION,
    NO_PRICED_RUNS,
    NO_RUNS,
    TOO_FEW_RUNS,
    UNDER_ONE_MILLISECOND,
    AgentRun,
    curves,
    nearest_rank,
    overview,
    points,
    summarize,
)

pytestmark = pytest.mark.unit

DAY = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def run(index=0, **fields) -> AgentRun:
    values = {
        "trace_id": f"trace-{index}",
        "task_id": f"task-{index}",
        "version": 1,
        "started_at": DAY,
        "latency_ms": 1000,
        "approval_wait_ms": 0,
        "status": "OK",
        "llm_spans": 1,
        "unpriced_llm_spans": 0,
        "cost_micros": 1000,
    }
    values.update(fields)
    return AgentRun(**values)


def test_a_failed_run_counts_in_n_cost_and_latency():
    runs = [
        run(0, cost_micros=1000, latency_ms=100),
        run(1, status="ERROR", cost_micros=3000, latency_ms=900),
    ]

    summary = summarize(runs)

    assert (summary["n"], summary["completed"], summary["failed"]) == (2, 1, 1)
    assert summary["totalCostUsd"] == {"value": 0.004, "n": 2, "reason": None}
    assert summary["costPerRunUsd"]["value"] == 0.002
    assert summary["costPer1kRunsUsd"]["value"] == 2.0
    assert summary["p95LatencyMs"] == {"value": 900, "n": 2, "reason": None}
    assert summary["outcomeSuccessRate"] == {"value": 0.5, "n": 2, "reason": None}


def test_a_run_without_a_finishing_event_is_unfinished_never_a_success():
    summary = summarize([run(0), run(1, status="UNSET"), run(2, status="")])

    assert (summary["completed"], summary["unfinished"]) == (1, 2)
    assert summary["outcomeSuccessRate"]["value"] == 1 / 3


def test_approval_wait_is_taken_out_of_latency_and_never_below_zero():
    runs = [
        run(0, latency_ms=10_000, approval_wait_ms=7_000),
        run(1, latency_ms=2_000, approval_wait_ms=5_000),
        run(2, latency_ms=4_000, approval_wait_ms=-50),
    ]

    summary = summarize(runs)

    # Net latencies: 3,000, 0 and 4,000 (a negative wait is no wait).
    assert summary["p50LatencyMs"]["value"] == 3_000
    assert summary["p95LatencyMs"]["value"] == 4_000
    assert summary["approvalWaitExcludedMs"] == 7_000 + 2_000


@pytest.mark.parametrize(
    ("values", "percent", "expected"),
    [
        ([5], 50, 5),
        ([5], 95, 5),
        ([1, 2], 50, 1),
        ([1, 2, 3, 4], 50, 2),
        ([1, 2, 3, 4, 5], 50, 3),
        (list(range(1, 21)), 95, 19),
        (list(range(1, 22)), 95, 20),
        (list(range(1, 101)), 95, 95),
        (list(range(1, 101)), 100, 100),
    ],
)
def test_percentiles_are_nearest_rank(values, percent, expected):
    assert nearest_rank(values, percent) == expected


def test_an_unpriced_run_is_left_out_of_cost_and_counted():
    runs = [
        run(0, cost_micros=2_000),
        run(1, unpriced_llm_spans=1, cost_micros=999_999),
    ]

    summary = summarize(runs)

    assert (summary["pricedRuns"], summary["unpricedRuns"]) == (1, 1)
    assert summary["totalCostUsd"] == {"value": 0.002, "n": 1, "reason": None}
    assert summary["costPerRunUsd"] == {"value": 0.002, "n": 1, "reason": None}
    # Latency and outcome still count the unpriced run.
    assert summary["p50LatencyMs"]["n"] == 2


def test_every_missing_or_zero_figure_says_why():
    nothing = summarize([])
    assert nothing["totalCostUsd"] == {"value": None, "n": 0, "reason": NO_RUNS}
    assert nothing["p50LatencyMs"] == {"value": None, "n": 0, "reason": NO_RUNS}
    assert nothing["outcomeSuccessRate"]["reason"] == NO_RUNS

    unpriced = summarize([run(0, unpriced_llm_spans=2)])
    assert unpriced["costPerRunUsd"] == {
        "value": None,
        "n": 0,
        "reason": NO_PRICED_RUNS,
    }

    no_calls = summarize([run(0, llm_spans=0, cost_micros=0)])
    assert no_calls["totalCostUsd"] == {"value": 0.0, "n": 1, "reason": NO_MODEL_CALLS}

    free = summarize([run(0, llm_spans=3, cost_micros=0)])
    assert free["totalCostUsd"] == {"value": 0.0, "n": 1, "reason": CHARGED_NOTHING}

    failed = summarize([run(0, status="ERROR")])
    assert failed["outcomeSuccessRate"] == {
        "value": 0.0,
        "n": 1,
        "reason": NO_COMPLETED_RUNS,
    }

    instant = summarize([run(0, latency_ms=5, approval_wait_ms=5)])
    assert instant["p50LatencyMs"]["reason"] == UNDER_ONE_MILLISECOND


def test_cost_per_run_is_rounded_to_the_micro_dollar_half_to_even():
    # 1 micro over 4 runs is 0.25 micro: 0. 10 micros over 4 is 2.5: 2. 14 over 4 is 3.5: 4.
    assert (
        summarize([run(i, cost_micros=c) for i, c in enumerate([1, 0, 0, 0])])[
            "costPerRunUsd"
        ]["value"]
        == 0.0
    )
    assert (
        summarize([run(i, cost_micros=c) for i, c in enumerate([10, 0, 0, 0])])[
            "costPerRunUsd"
        ]["value"]
        == 0.000002
    )
    assert (
        summarize([run(i, cost_micros=c) for i, c in enumerate([14, 0, 0, 0])])[
            "costPerRunUsd"
        ]["value"]
        == 0.000004
    )


def test_points_are_per_day_and_version_with_unversioned_last():
    later = DAY + timedelta(days=1)
    rows = points(
        [
            run(0, started_at=later, version=2),
            run(1, started_at=DAY, version=None),
            run(2, started_at=DAY, version=2),
            run(3, started_at=DAY, version=1),
        ]
    )

    assert [(row["day"], row["version"], row["n"]) for row in rows] == [
        ("2026-10-20", 1, 1),
        ("2026-10-20", 2, 1),
        ("2026-10-20", None, 1),
        ("2026-10-21", 2, 1),
    ]


def test_tiles_compare_the_latest_version_with_the_one_before_it():
    older = [run(i, version=1, cost_micros=4_000, latency_ms=800) for i in range(12)]
    newer = [
        run(100 + i, version=3, cost_micros=1_000, latency_ms=500) for i in range(10)
    ]
    newer[0] = run(100, version=3, status="ERROR", cost_micros=1_000, latency_ms=500)

    tiles = overview(older + newer)

    assert (tiles["version"], tiles["previousVersion"]) == (3, 1)
    cost = tiles["tiles"]["costPer1kRunsUsd"]
    assert (cost["value"], cost["previousValue"], cost["delta"]) == (1.0, 4.0, -3.0)
    assert (cost["n"], cost["previousN"], cost["deltaReason"]) == (10, 12, None)
    latency = tiles["tiles"]["p50LatencyMs"]
    assert (latency["value"], latency["previousValue"], latency["delta"]) == (
        500,
        800,
        -300,
    )
    success = tiles["tiles"]["outcomeSuccessRate"]
    assert (success["value"], success["previousValue"]) == (0.9, 1.0)
    assert success["delta"] == 0.9 - 1.0


def test_a_tile_below_the_minimum_n_is_withheld_and_so_is_its_delta():
    older = [run(i, version=1) for i in range(MINIMUM_N)]
    newer = [run(100 + i, version=2) for i in range(MINIMUM_N - 1)]

    tile = overview(older + newer)["tiles"]["p50LatencyMs"]

    assert tile["value"] is None and tile["reason"] == TOO_FEW_RUNS
    assert tile["n"] == MINIMUM_N - 1
    assert tile["previousValue"] == 1000 and tile["previousN"] == MINIMUM_N
    assert tile["delta"] is None and tile["deltaReason"] == TOO_FEW_RUNS


def test_the_first_version_has_no_delta():
    tile = overview([run(i, version=4) for i in range(MINIMUM_N)])["tiles"][
        "costPer1kRunsUsd"
    ]

    assert tile["value"] == 1.0
    assert tile["delta"] is None and tile["deltaReason"] == NO_PREVIOUS_VERSION


def test_the_answer_states_its_minimum_n_and_has_every_part():
    answer = curves([run(0)])

    assert answer["minimumN"] == MINIMUM_N
    assert set(answer) == {"minimumN", "totals", "versions", "points", "overview"}
    assert answer["versions"][0]["firstDay"] == "2026-10-20"
