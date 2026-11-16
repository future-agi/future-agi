"""A fault that holds the agent's write until an actor records an approval: offered only on a write when some actor
can record one, refused on a read or where no actor the scenario plays can, resolved with the action that records it,
and its refusals shown in World state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.selectors.call_detail import trial_state
from simulate.services.harness_actor_rules import actor_rules
from simulate.services.harness_tool_reach import door_targets, fault_targets
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.services.scenario_faults import (
    fault_problem,
    require_valid_faults,
    resolved_faults,
    scenario_approvers,
)
from simulate.tests.test_scenario_fault_rules import READING
from simulate.views.run_results_v3 import TrialStateSerializer

RULES = actor_rules({})
MANAGER = {"id": "manager", "name": "Priya", "permitted_actions": ["approvals.change"]}
CLERK = {"id": "clerk", "name": "Sam", "permitted_actions": ["orders.find"]}
CAST = [MANAGER, CLERK]
NEEDS_APPROVAL = {
    "action": "orders.refund",
    "mode": "approval_required",
    "approval": "approvals.change",
}


def _modes(cast):
    return {
        one["tool"]: [mode["mode"] for mode in one["modes"]]
        for one in fault_targets(READING, RULES, cast)
    }


def test_the_mode_is_offered_only_on_a_write_and_only_when_an_actor_can_record_an_approval():
    with_approver, without = _modes(CAST), _modes([])

    assert "approval_required" in with_approver["issue_refund"]
    assert "approval_required" not in with_approver["find_order"]
    assert "approval_required" not in without["issue_refund"]
    targets = {one["tool"]: one for one in fault_targets(READING, RULES, CAST)}
    [mode] = [
        one
        for one in targets["issue_refund"]["modes"]
        if one["mode"] == "approval_required"
    ]
    assert [one["value"] for one in mode["approval_options"]] == [
        "approvals.change",
        "orders.find",
    ]
    # Only the approval mode carries choices.
    assert all(
        "approval_options" not in one
        for one in targets["issue_refund"]["modes"]
        if one is not mode
    )


@pytest.mark.parametrize(
    ("fault", "approvers", "field"),
    [
        (
            {
                "action": "orders.find",
                "mode": "approval_required",
                "approval": "approvals.change",
            },
            {"approvals.change"},
            "mode",
        ),
        (NEEDS_APPROVAL, {"orders.find"}, "approval"),
        (
            {"action": "orders.refund", "mode": "approval_required"},
            {"approvals.change"},
            "approval",
        ),
        (
            {**NEEDS_APPROVAL, "approved_when": "yes"},
            {"approvals.change"},
            "approved_when",
        ),
    ],
)
def test_an_approval_the_world_cannot_wait_for_is_refused_under_its_field(
    fault, approvers, field
):
    from simulate.services.scenario_faults import faults_problems

    [problem] = faults_problems(
        [fault], door_targets(READING, RULES, CAST), approvers=approvers
    )
    assert problem["field"] == field


def test_an_approval_an_actor_in_the_scenario_can_record_passes():
    fault = {**NEEDS_APPROVAL, "approved_when": {"status": "approved"}, "on_call": 3}

    assert (
        fault_problem(
            fault, door_targets(READING, RULES, CAST), approvers={"approvals.change"}
        )
        is None
    )


@pytest.mark.parametrize(
    "scenario",
    [
        {"actors": [{"id": "manager"}]},
        {"pre_run": [{"actors": ["manager"]}]},
        {"starts_with": {"actor": "manager", "text": "Go"}},
    ],
)
def test_an_actor_the_scenario_plays_anywhere_can_approve(scenario):
    assert "approvals.change" in scenario_approvers(scenario, CAST)
    assert "approvals.change" not in scenario_approvers(
        {"actors": [{"id": "clerk"}]}, CAST
    )


def test_a_write_refuses_an_approval_no_actor_of_the_scenario_can_record():
    unplayed = {
        "name": "refund",
        "scenario_key": "refund",
        "actors": [{"id": "clerk"}],
        "faults": [NEEDS_APPROVAL],
    }
    played = {**unplayed, "actors": [{"id": "manager"}]}

    with pytest.raises(HostedHarnessError) as raised:
        require_valid_faults(
            [unplayed], targets=door_targets(READING, RULES, CAST), cast=CAST
        )
    assert raised.value.code == "fault_invalid"
    require_valid_faults(
        [played], targets=door_targets(READING, RULES, CAST), cast=CAST
    )


def test_the_resolved_fault_names_the_action_that_records_the_approval():
    [resolved] = resolved_faults(
        [{**NEEDS_APPROVAL, "approved_when": {"status": "approved"}, "on_call": 2}],
        fault_targets(READING, RULES, CAST),
    )

    assert (resolved["approval"], resolved["approved_when"], resolved["on_call"]) == (
        "approvals.change",
        {"status": "approved"},
        None,
    )


@pytest.mark.django_db
def test_an_amend_refuses_an_approval_no_actor_of_the_scenario_can_record(
    user, workspace
):
    from simulate.tests.test_harness_amend_receipt_codes import _receipts, _set

    side = (door_targets(READING, RULES, CAST), CAST)
    with patch(
        "simulate.services.harness_environment.edit_fault_side", return_value=side
    ):
        refused = _receipts(user, workspace, [_set("faults", [NEEDS_APPROVAL])])

    assert refused == [("refused", "fault_invalid", "faults")]


def test_writes_refused_for_want_of_an_approval_are_a_notice_in_world_state():
    calls = [
        {
            "name": "issue_refund",
            "refused": True,
            "refusal_code": "approval_missing",
            "kind": "agent_call",
        },
        {
            "name": "issue_refund",
            "refused": True,
            "refusal_code": "approval_missing",
            "kind": "agent_call",
        },
        {
            "name": "cancel_order",
            "refused": True,
            "refusal_code": "approval_missing",
            "kind": "agent_call",
        },
        {
            "name": "issue_refund",
            "refused": True,
            "refusal_code": "egress_blocked",
            "kind": "agent_call",
        },
        {
            "name": "approvals.change",
            "actor_id": "manager",
            "refusal_code": "approval_missing",
        },
    ]
    shown = trial_state(
        SimpleNamespace(id="call", call_metadata={TRIAL_STATE_KEY: {"decisions": []}}),
        calls=calls,
    )

    assert [(one["code"], one["action"], one["count"]) for one in shown["notices"]] == [
        ("approval_missing", "issue_refund", 2),
        ("approval_missing", "cancel_order", 1),
    ]
    assert TrialStateSerializer(data=shown).is_valid(), TrialStateSerializer(
        data=shown
    ).errors
