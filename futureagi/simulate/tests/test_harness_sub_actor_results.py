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
        "unmeasured": 0,
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
    # An evaluation that decided the call is a pass or fail of its own.
    assert (
        receipt_outcome_status(
            {
                "status": "passed",
                "sub_goals": [_GOALS[1]],
                "evaluations": [{"kind": "code", "name": "e", "passed": True}],
            }
        )
        == "passed"
    )


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
        # A receipt from before verdicts whose only sub-goal was undecided keeps its status.
        ("passed", [{"name": "x", "held": None, "judged": True}], "passed"),
    ],
)
def test_a_call_with_no_pass_or_fail_has_no_score_and_an_error_stays_an_error(
    status, goals, outcome
):
    assert receipt_outcome_status({"status": status, "sub_goals": goals}) == outcome


def test_an_agent_fix_check_leaves_out_a_sub_goal_that_did_not_apply():
    from simulate.selectors.agent_fix import judged_sub_goals

    receipt = {
        "sub_goals": [
            {"name": "skipped", "held": True, "verdict": "not_applicable"},
            {"name": "unsure", "held": None, "verdict": "inconclusive"},
            {"name": "kept", "held": True, "verdict": "pass"},
        ]
    }
    assert judged_sub_goals(receipt) == {"kept": True}
    assert judged_sub_goals({**receipt, "failure": {"domain": "agent"}}) == {
        "kept": True
    }


# Run 4's shape: a manager approves through the world, the agent never applies the discount. The case's proof could
# only assume its steps, so it excused both checks; the trial's receipt failed one of them.
_RUN4_PROOF = {
    "format": 2,
    "kept_as_written": True,
    "excused": ["crm_deal_lookup", "crm_discount_applied"],
}
_RUN4_RECEIPT = {
    "scenario_key": "live-manager-negotiates-discount",
    "status": "failed",
    "sub_goals": [
        {"name": "crm_deal_lookup", "held": True, "reason": "", "judged": False, "verdict": "pass"},
        {"name": "crm_discount_applied", "held": False, "reason": "not applied", "judged": False, "verdict": "fail"},
    ],
    "evaluations": [],
}


def _proofs_on_the_run_s_versions(monkeypatch, proofs):
    from types import SimpleNamespace

    from simulate.services import hosted_harness_ingestion as ingestion

    rows = SimpleNamespace(values_list=lambda *a, **k: SimpleNamespace(first=lambda: proofs))
    manager = SimpleNamespace(filter=lambda **pair: rows)
    monkeypatch.setattr(ingestion.HarnessSuiteValidation, "no_workspace_objects", manager)
    return SimpleNamespace(suite_version_id="s", environment_version_id="e", agent_version_id="a")


def test_a_check_the_case_s_proof_never_verified_does_not_fail_the_agent(monkeypatch):
    from simulate.models import CallExecution
    from simulate.selectors.agent_fix import judged_sub_goals
    from simulate.services.hosted_harness_ingestion import (
        UNVERIFIED_KEY,
        graded_call_receipt,
        graded_receipt,
        unverified_checks,
    )
    from simulate.services.run_results_v3 import call_outcome, receipt_sub_goals

    job = _proofs_on_the_run_s_versions(monkeypatch, {"live_manager_negotiates_discount": _RUN4_PROOF})
    unverified = unverified_checks(job, _RUN4_RECEIPT["scenario_key"])
    assert unverified == ["crm_deal_lookup", "crm_discount_applied"]

    graded = graded_receipt(_RUN4_RECEIPT, unverified)

    assert receipt_outcome_status(graded) == "inconclusive"
    assert [one["name"] for one in _receipt_evaluations(graded)] == ["crm_deal_lookup"]
    assert _receipt_evaluation_coverage(graded)["unmeasured"] == 1
    assert _receipt_evaluation_coverage(graded)["complete"] is True
    # The sealed receipt is never rewritten.
    assert _RUN4_RECEIPT["status"] == "failed"
    metadata = {
        "hosted_harness_receipt": _RUN4_RECEIPT,
        UNVERIFIED_KEY: unverified,
        "harness_outcome_status": receipt_outcome_status(graded),
    }
    applied = next(one for one in receipt_sub_goals(metadata) if one["name"] == "crm_discount_applied")
    assert (applied["passed"], applied["result"]) == (None, "unmeasured")
    assert "never verified" in applied["reason"]
    call = CallExecution(status=CallExecution.CallStatus.COMPLETED, call_metadata=metadata)
    # The run lists it Not measured (the inconclusive outcome), never Failed.
    assert call_outcome(call, {}) == "inconclusive"
    # Fix my agent never takes it for a sub-goal the agent failed.
    assert judged_sub_goals(graded_call_receipt(metadata)) == {"crm_deal_lookup": True}


def test_a_verified_check_s_failure_still_fails_the_agent(monkeypatch):
    from simulate.services.hosted_harness_ingestion import (
        graded_receipt,
        unverified_checks,
    )

    proof = {**_RUN4_PROOF, "excused": ["crm_deal_lookup"]}
    job = _proofs_on_the_run_s_versions(monkeypatch, {"live-manager-negotiates-discount": proof})
    # Both failed: the lookup its proof never verified, the discount it did.
    both = {
        **_RUN4_RECEIPT,
        "sub_goals": [{**one, "held": False, "verdict": "fail"} for one in _RUN4_RECEIPT["sub_goals"]],
    }

    graded = graded_receipt(both, unverified_checks(job, both["scenario_key"]))

    assert receipt_outcome_status(graded) == "failed"
    assert [(one["name"], one["passed"]) for one in _receipt_evaluations(graded)] == [("crm_discount_applied", False)]


def test_without_a_proof_or_with_the_agent_s_own_one_the_checks_read_as_the_receipt_says(monkeypatch):
    from simulate.services.harness_versioning.versions import OTHER_AGENTS
    from simulate.services.hosted_harness_ingestion import (
        graded_receipt,
        unverified_checks,
    )

    job = _proofs_on_the_run_s_versions(monkeypatch, {})
    assert graded_receipt(_RUN4_RECEIPT, unverified_checks(job, _RUN4_RECEIPT["scenario_key"])) is _RUN4_RECEIPT
    # Proved with the run's own agent version: that proof verified both checks.
    proved = {**_RUN4_PROOF, OTHER_AGENTS: {"a": {"format": 2, "proved": True, "excused": []}}}
    job = _proofs_on_the_run_s_versions(monkeypatch, {"live-manager-negotiates-discount": proved})
    assert unverified_checks(job, _RUN4_RECEIPT["scenario_key"]) == []
