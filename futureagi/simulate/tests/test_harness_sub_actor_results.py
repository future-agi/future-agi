"""Sub-goal results that are neither a pass nor a fail: how they are received, stored and counted."""

import pytest

from simulate.serializers.hosted_harness import HarnessSubGoalSerializer
from simulate.services.hosted_harness_ingestion import (
    _receipt_evaluation_coverage,
    _receipt_evaluations,
    receipt_outcome_status,
    sub_goal_result,
)
from simulate.utils.eval_summary import _calculate_pass_fail_stats

_GOALS = [
    {
        "name": "done",
        "held": True,
        "reason": "done",
        "judged": False,
        "verdict": "pass",
    },
    {
        "name": "noticed",
        "held": True,
        "reason": "Not applicable: x",
        "judged": True,
        "verdict": "not_applicable",
    },
    {
        "name": "heard",
        "held": None,
        "reason": "Inconclusive: y",
        "judged": True,
        "verdict": "inconclusive",
    },
    {"name": "pending", "held": None, "reason": None, "judged": True},
]


def test_a_sub_goal_s_verdict_is_accepted_and_an_unknown_one_refused():
    assert HarnessSubGoalSerializer(data=_GOALS[1]).is_valid()
    assert (
        HarnessSubGoalSerializer(data={**_GOALS[1], "verdict": "maybe"}).is_valid()
        is False
    )
    assert HarnessSubGoalSerializer(data=_GOALS[3]).is_valid()


@pytest.mark.parametrize(
    "goal, result",
    [
        ({"held": True}, "pass"),
        ({"held": False}, "fail"),
        ({"held": None}, None),
        ({"held": "yes"}, None),
        ({"held": True, "verdict": "not_applicable"}, "not_applicable"),
        ({"held": None, "verdict": "inconclusive"}, "inconclusive"),
    ],
)
def test_a_receipt_from_before_verdicts_still_reads_from_held(goal, result):
    assert sub_goal_result(goal) == result


def test_not_applicable_and_inconclusive_are_published_with_their_reason_and_counted_as_decided():
    body = {"sub_goals": _GOALS, "evaluations": []}
    assert [
        (one["name"], one.get("result"), one["passed"])
        for one in _receipt_evaluations(body)
    ] == [
        ("done", None, True),
        ("noticed", "not_applicable", None),
        ("heard", "inconclusive", None),
    ]
    assert _receipt_evaluation_coverage(body) == {
        "expected": 4,
        "executed": 3,
        "failed": 1,
        "complete": False,
    }


def test_a_call_where_every_sub_goal_was_not_applicable_has_no_score():
    assert (
        receipt_outcome_status(
            {"status": "passed", "sub_goals": [_GOALS[1], _GOALS[1]]}
        )
        == "no_score"
    )
    assert (
        receipt_outcome_status({"status": "passed", "sub_goals": _GOALS[:2]})
        == "passed"
    )
    assert receipt_outcome_status({"status": "skipped", "sub_goals": []}) == "skipped"


def test_a_pass_rate_leaves_out_what_was_neither_a_pass_nor_a_fail():
    config = type("Config", (), {"name": "noticed", "id": "c1"})()
    outputs = [
        {"output": value}
        for value in ("Passed", "Failed", "Passed", "Not applicable", "Inconclusive")
    ]
    stats = _calculate_pass_fail_stats(config, outputs)
    assert stats["total_cells"] == 3
    assert stats["output"] == {
        "pass": 66.67,
        "fail": 33.33,
        "pass_count": 2,
        "fail_count": 1,
        "not_applicable_count": 1,
        "inconclusive_count": 1,
    }


@pytest.mark.parametrize(
    "status, goals, outcome",
    [
        # No pass or fail at all: every zero-denominator mix reads no score.
        ("passed", [_GOALS[1]], "no_score"),
        ("passed", [_GOALS[2], _GOALS[2]], "no_score"),
        ("passed", [_GOALS[1], _GOALS[2]], "no_score"),
        # A call with a pass keeps its own status.
        ("passed", [_GOALS[0], _GOALS[2]], "passed"),
        ("failed", [{**_GOALS[0], "held": False, "verdict": "fail"}], "failed"),
        # An execution error stays an error, never no score.
        ("errored", [_GOALS[1]], "errored"),
    ],
)
def test_a_call_with_no_pass_or_fail_has_no_score_and_an_error_stays_an_error(
    status, goals, outcome
):
    assert receipt_outcome_status({"status": status, "sub_goals": goals}) == outcome
