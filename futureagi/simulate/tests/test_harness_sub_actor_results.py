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
        {
            "name": "crm_deal_lookup",
            "held": True,
            "reason": "",
            "judged": False,
            "verdict": "pass",
        },
        {
            "name": "crm_discount_applied",
            "held": False,
            "reason": "not applied",
            "judged": False,
            "verdict": "fail",
        },
    ],
    "evaluations": [],
}


def _proofs_on_the_run_s_versions(monkeypatch, proofs):
    from types import SimpleNamespace

    from simulate.services import hosted_harness_ingestion as ingestion

    rows = SimpleNamespace(
        values_list=lambda *a, **k: SimpleNamespace(first=lambda: proofs)
    )
    manager = SimpleNamespace(filter=lambda **pair: rows)
    monkeypatch.setattr(
        ingestion.HarnessSuiteValidation, "no_workspace_objects", manager
    )
    return SimpleNamespace(
        suite_version_id="s", environment_version_id="e", agent_version_id="a"
    )


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

    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live_manager_negotiates_discount": _RUN4_PROOF}
    )
    unverified = unverified_checks(job, _RUN4_RECEIPT["scenario_key"])
    assert unverified == ["crm_deal_lookup", "crm_discount_applied"]

    graded = graded_receipt(_RUN4_RECEIPT, unverified)

    assert receipt_outcome_status(graded) == "no_score"
    # Neither check its proof excused counts, the one that held included.
    assert _receipt_evaluations(graded) == []
    assert _receipt_evaluation_coverage(graded)["unmeasured"] == 2
    assert _receipt_evaluation_coverage(graded)["complete"] is True
    # The sealed receipt is never rewritten.
    assert _RUN4_RECEIPT["status"] == "failed"
    metadata = {
        "hosted_harness_receipt": _RUN4_RECEIPT,
        UNVERIFIED_KEY: unverified,
        "harness_outcome_status": receipt_outcome_status(graded),
    }
    applied = next(
        one
        for one in receipt_sub_goals(metadata)
        if one["name"] == "crm_discount_applied"
    )
    assert (applied["passed"], applied["result"]) == (None, "unmeasured")
    assert "never verified" in applied["reason"]
    call = CallExecution(
        status=CallExecution.CallStatus.COMPLETED, call_metadata=metadata
    )
    # The run lists it with no score, outside the pass rate, never Failed.
    assert call_outcome(call, {}) == "no_score"
    # Fix my agent takes neither for a pass or a failure of the agent's.
    assert judged_sub_goals(graded_call_receipt(metadata)) == {
        "crm_deal_lookup": None,
        "crm_discount_applied": None,
    }


def test_a_verified_check_s_failure_still_fails_the_agent(monkeypatch):
    from simulate.services.hosted_harness_ingestion import (
        graded_receipt,
        unverified_checks,
    )

    proof = {**_RUN4_PROOF, "excused": ["crm_deal_lookup"]}
    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live-manager-negotiates-discount": proof}
    )
    # Both failed: the lookup its proof never verified, the discount it did.
    both = {
        **_RUN4_RECEIPT,
        "sub_goals": [
            {**one, "held": False, "verdict": "fail"}
            for one in _RUN4_RECEIPT["sub_goals"]
        ],
    }

    graded = graded_receipt(both, unverified_checks(job, both["scenario_key"]))

    assert receipt_outcome_status(graded) == "failed"
    assert [(one["name"], one["passed"]) for one in _receipt_evaluations(graded)] == [
        ("crm_discount_applied", False)
    ]


def test_a_trial_that_ended_on_its_own_failure_stays_failed_though_its_check_was_unverified(
    monkeypatch,
):
    from simulate.services.hosted_harness_ingestion import (
        graded_receipt,
        unverified_checks,
    )

    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live-manager-negotiates-discount": _RUN4_PROOF}
    )
    # The world applied a write twice: a failure of the agent's, whatever the checks say.
    twice = {
        **_RUN4_RECEIPT,
        "failure": {"domain": "agent", "code": "duplicate_commit"},
    }

    graded = graded_receipt(twice, unverified_checks(job, twice["scenario_key"]))

    assert receipt_outcome_status(graded) == "failed"
    # The unverified checks themselves still read unmeasured.
    assert _receipt_evaluations(graded) == []


def test_without_a_proof_or_with_the_agent_s_own_one_the_checks_read_as_the_receipt_says(
    monkeypatch,
):
    from simulate.services.harness_versioning.versions import OTHER_AGENTS
    from simulate.services.hosted_harness_ingestion import (
        graded_receipt,
        unverified_checks,
    )

    job = _proofs_on_the_run_s_versions(monkeypatch, {})
    assert (
        graded_receipt(
            _RUN4_RECEIPT, unverified_checks(job, _RUN4_RECEIPT["scenario_key"])
        )
        is _RUN4_RECEIPT
    )
    # Proved with the run's own agent version: that proof verified both checks.
    proved = {
        **_RUN4_PROOF,
        OTHER_AGENTS: {"a": {"format": 2, "proved": True, "excused": []}},
    }
    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live-manager-negotiates-discount": proved}
    )
    assert unverified_checks(job, _RUN4_RECEIPT["scenario_key"]) == []


def test_an_unverified_check_that_held_never_passes_the_trial(monkeypatch):
    from simulate.services.hosted_harness_ingestion import graded_receipt

    held = {
        **_RUN4_RECEIPT,
        "status": "passed",
        "sub_goals": [
            {**one, "held": True, "verdict": "pass"}
            for one in _RUN4_RECEIPT["sub_goals"]
        ],
    }

    graded = graded_receipt(held, ["crm_discount_applied"])

    assert receipt_outcome_status(graded) == "no_score"
    assert [one["name"] for one in _receipt_evaluations(graded)] == ["crm_deal_lookup"]


def test_a_failed_evaluation_keeps_the_trial_failed_beside_an_unverified_check():
    from simulate.services.hosted_harness_ingestion import graded_receipt

    judged = {
        **_RUN4_RECEIPT,
        "evaluations": [{"name": "tone", "kind": "checkpoint", "passed": False}],
    }

    assert (
        receipt_outcome_status(graded_receipt(judged, ["crm_discount_applied"]))
        == "failed"
    )
    # One whose grading failed decides nothing.
    errored = {
        **judged,
        "evaluations": [{"name": "tone", "passed": False, "grading_error": "timeout"}],
    }
    assert (
        receipt_outcome_status(graded_receipt(errored, ["crm_discount_applied"]))
        == "no_score"
    )


def test_a_candidate_agent_version_without_its_own_proof_is_graded_by_the_case_s_proof(
    monkeypatch,
):
    from simulate.services.harness_versioning.versions import OTHER_AGENTS
    from simulate.services.hosted_harness_ingestion import unverified_checks

    # Fix my agent's check run: a candidate version that was never validated. The case's proof plays the reference,
    # not the agent, so its excused list still says which checks were never verified; the rest grade the candidate.
    proved = {
        **_RUN4_PROOF,
        "excused": ["crm_deal_lookup"],
        "agent_version_id": "default",
    }
    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live-manager-negotiates-discount": proved}
    )
    assert unverified_checks(job, _RUN4_RECEIPT["scenario_key"]) == ["crm_deal_lookup"]
    # The run's own version's entry wins where it has one.
    own = {**proved, OTHER_AGENTS: {"a": {"format": 2, "excused": []}}}
    job = _proofs_on_the_run_s_versions(
        monkeypatch, {"live-manager-negotiates-discount": own}
    )
    assert unverified_checks(job, _RUN4_RECEIPT["scenario_key"]) == []


def test_compare_and_fix_my_agent_read_an_unverified_check_as_undecided_never_a_pass():
    from simulate.selectors.agent_fix import judged_sub_goals
    from simulate.services.run_compare import trial_passed

    goals = [
        {"name": "greeted", "held": True, "verdict": "pass"},
        {"name": "applied", "held": None, "verdict": "unmeasured"},
    ]

    assert trial_passed(goals, "passed") is None
    # Even where the agent broke the conversation, an unverified check is not the agent's failure.
    broke = {"sub_goals": goals, "failure": {"domain": "agent"}}
    assert judged_sub_goals(broke) == {"greeted": True, "applied": None}


@pytest.mark.parametrize("domain", ["environment", "infrastructure", "simulator"])
def test_a_trial_that_ended_on_a_failure_of_ours_is_never_the_agent_s_failure(domain):
    from simulate.services.hosted_harness_ingestion import graded_receipt

    ours = {**_RUN4_RECEIPT, "failure": {"domain": domain, "code": "seed_failed"}}

    graded = graded_receipt(ours, ["crm_discount_applied"])

    assert receipt_outcome_status(graded) == "errored"


def test_a_double_commit_keeps_the_trial_failed_beside_an_unverified_check():
    from simulate.serializers.hosted_harness import HarnessCallSerializer
    from simulate.services.hosted_harness_ingestion import graded_receipt

    call = {
        "started_at": "2026-01-01T00:00:00.000Z",
        "ended_at": "2026-01-01T00:00:01.000Z",
        "duration_ms": 1000,
        "turns": 1,
        "duplicate_commits": 1,
    }
    # The platform keeps the field when it reads the receipt.
    read = HarnessCallSerializer(data=call)
    assert read.is_valid(), read.errors
    assert read.validated_data["duplicate_commits"] == 1
    twice = {
        **_RUN4_RECEIPT,
        "sub_goals": [
            {**one, "held": True, "verdict": "pass"}
            for one in _RUN4_RECEIPT["sub_goals"]
        ],
        "call": call,
    }

    assert (
        receipt_outcome_status(graded_receipt(twice, ["crm_discount_applied"]))
        == "failed"
    )


def test_a_trial_the_world_committed_twice_is_failed_in_compare_and_fix_my_agent_though_every_check_held():
    from simulate.selectors.agent_fix import REPEATED_WRITE, judged_sub_goals
    from simulate.services.run_compare import trial_passed

    held = [
        {"name": "looked_up", "held": True, "verdict": "pass"},
        {"name": "applied", "held": True, "verdict": "pass"},
    ]
    twice = {"status": "failed", "sub_goals": held, "call": {"duplicate_commits": 1}}

    assert trial_passed(held, "failed") is False
    assert judged_sub_goals(twice) == {
        "looked_up": True,
        "applied": True,
        REPEATED_WRITE: False,
    }
    # The same trial without the double commit passed.
    assert trial_passed(held, "passed") is True
    assert judged_sub_goals({"status": "passed", "sub_goals": held}) == {
        "looked_up": True,
        "applied": True,
    }


def test_a_verified_failure_fails_the_trial_beside_an_unverified_check():
    from simulate.services.run_compare import trial_passed

    goals = [
        {"name": "applied", "held": False, "verdict": "fail"},
        {"name": "looked_up", "held": None, "verdict": "unmeasured"},
    ]

    assert trial_passed(goals, "failed") is False
    # Nothing verified failed: undecided, never a pass.
    assert trial_passed(goals[1:], "no_score") is None


def test_a_passed_receipt_with_no_score_is_never_counted_passed_in_a_run_s_totals():
    from simulate.services.run_outcome import outcome_fields

    receipts = {
        "by_status": {"passed": 3, "failed": 1},
        "no_score_by_status": {"passed": 2},
    }

    fields = outcome_fields(status="completed", receipts=receipts, calls=None)

    assert (
        fields["outcome_passed"],
        fields["outcome_failed"],
        fields["outcome_no_score"],
    ) == (1, 1, 2)


def test_a_trial_that_ended_on_our_side_never_blames_the_agent_in_verify_or_fix_my_agent():
    from simulate.selectors.agent_fix import _record, judged_sub_goals
    from simulate.services.run_compare import trial_passed

    # Our simulated caller went off script; the checks ran on a conversation that was never the scenario's.
    off_script = {
        "status": "errored",
        "failure": {"domain": "simulator", "code": "caller_off_script"},
        "sub_goals": [
            {"name": "looked_up", "held": False, "verdict": "fail"},
            {"name": "applied", "held": False, "verdict": "fail"},
        ],
    }

    assert (
        trial_passed(off_script["sub_goals"], "errored", off_script["failure"]) is None
    )
    assert judged_sub_goals(off_script) == {}
    record = _record("case", 1, off_script, "")
    assert (record.status, record.sub_goals) == ("error", {})
    # The agent broke the conversation itself: that is its failure.
    agent = {**off_script, "failure": {"domain": "agent", "code": "agent_hung_up"}}
    assert trial_passed(agent["sub_goals"], "errored", agent["failure"]) is False


def test_compare_counts_an_agent_that_stalled_as_a_failed_trial_and_our_failure_as_undecided():
    from simulate.services.run_compare import ScenarioTrials

    goals = [{"name": "replied", "held": False, "verdict": "fail"}]

    stalled = ScenarioTrials("case")
    stalled.add(
        goals,
        "errored",
        "error",
        None,
        {"domain": "agent", "code": "target_agent_stalled"},
    )
    assert (stalled.side()["passed"], stalled.side()["trials"]) == (0, 1)

    ours = ScenarioTrials("case")
    ours.add(
        goals,
        "errored",
        "error",
        None,
        {"domain": "simulator", "code": "caller_off_script"},
    )
    assert (ours.side()["trials"], ours.side()["undecided"]) == (0, 1)


def test_a_trial_the_agent_itself_errored_reads_failed_in_the_row_the_scan_and_the_pass_rate():
    from simulate.models import CallExecution
    from simulate.services.run_reliability_v3 import trial_pass_from
    from simulate.services.run_results_v3 import call_outcome
    from simulate.services.run_results_v3_scan import _outcome

    def call(domain):
        return CallExecution(
            status=CallExecution.CallStatus.COMPLETED,
            call_metadata={
                "harness_outcome_status": "errored",
                "hosted_harness_receipt": {
                    "status": "errored",
                    "failure": {"domain": domain},
                },
            },
        )

    assert call_outcome(call("agent"), {}) == "failed"
    assert call_outcome(call("simulator"), {}) == "error"
    assert (
        _outcome("completed", "errored", None, None, None, agent_failed=True)
        == "failed"
    )
    assert _outcome("completed", "errored", None, None, None) == "error"
    # One pass and three stalls: 25%, never 100%.
    assert trial_pass_from({"passed": 1, "failed": 3}, 0)["trials_evaluated"] == 4
