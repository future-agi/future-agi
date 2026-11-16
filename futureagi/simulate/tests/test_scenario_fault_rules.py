"""The server refuses a scenario fault the test world cannot apply, in the editor's words, by one rule: a fault that
would not load, one naming no tool of the agent, one on a tool the world's door cannot hold, and a mode the tool
cannot take. It reads faults as the harness does, and a draft write checks only the scenarios it changes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simulate.services.harness_actor_rules import actor_rules
from simulate.services.harness_tool_reach import door_targets, fault_targets
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.scenario_faults import (
    FAULT_BOUNDS,
    FAULT_DEFAULTS,
    fault_problem,
    require_valid_faults,
    resolved_faults,
)
from simulate.tests.test_harness_tool_status import CONTRACT

RULES = actor_rules({})
# issue_refund writes behind the door (action orders.refund); lookup_order runs inside the agent; a read tool behind
# the door is added for the mode rule.
READING = {
    **CONTRACT,
    "tools": [*CONTRACT["tools"], {"name": "find_order"}],
    "tool_entrypoints": [
        *CONTRACT["tool_entrypoints"],
        {
            "tool": "find_order",
            "mode": "service",
            "service": "orders",
            "endpoint": "/orders/find/",
            "effect": "read",
        },
    ],
}
TARGETS = door_targets(READING, RULES)


@pytest.mark.parametrize(
    ("fault", "problem"),
    [
        (
            {"action": "orders.refund", "mode": "delay"},
            "choose what happens to the call",
        ),
        ({"action": "", "mode": "unavailable"}, "choose the tool it applies to"),
        (
            {"action": "orders.refund", "mode": "unavailable", "on_call": 0},
            "the call it applies to must be a whole number from 1",
        ),
        (
            {
                "action": "orders.refund",
                "mode": "timeout_before_commit",
                "delay_seconds": 31,
            },
            "the delay must be between 0 and 30 seconds",
        ),
        (
            {"action": "orders.refund", "mode": "error", "status": 200},
            "the status must be a whole number from 400 to 599",
        ),
        (
            {"action": "orders.refund", "mode": "error", "message": "x" * 501},
            "the message must be text of at most 500 characters",
        ),
        (
            {"action": "orders.refund", "mode": "unavailable", "when": "now"},
            "when is not part of a test condition",
        ),
        ("orders.refund", "a test condition could not be read"),
        (
            {"action": "orders.cancel", "mode": "unavailable"},
            "orders.cancel is not one of this agent's tools",
        ),
        (
            {"action": "lookup_order", "mode": "unavailable"},
            "lookup_order runs inside the agent, so the test world cannot hold its calls",
        ),
        (
            {"action": "orders.find", "mode": "timeout_after_commit"},
            "find_order changes no records, so its answer cannot be lost after a change",
        ),
    ],
)
def test_a_fault_the_world_cannot_apply_says_why_in_the_editor_s_words(fault, problem):
    assert fault_problem(fault, TARGETS) == problem


@pytest.mark.parametrize(
    "fault",
    [
        {"action": "orders.refund", "mode": "timeout_after_commit"},
        # The harness reads numbers written as text, so a suite it runs is never refused for them.
        {
            "action": "orders.refund",
            "mode": "timeout_before_commit",
            "on_call": "2",
            "delay_seconds": "5",
        },
        {
            "action": "issue_refund",
            "mode": "error",
            "status": 429,
            "message": "slow down",
        },
        {"action": "orders.find", "mode": "unavailable"},
    ],
)
def test_a_fault_the_world_can_apply_passes(fault):
    assert fault_problem(fault, TARGETS) is None


def test_a_contract_that_says_nothing_of_the_door_checks_the_fault_alone():
    assert door_targets({"tools": ["x"]}, RULES) is None
    assert (
        fault_problem({"action": "anything", "mode": "timeout_after_commit"}, None)
        is None
    )
    assert (
        fault_problem({"action": "anything", "mode": "delay"}, None)
        == "choose what happens to the call"
    )


def test_each_target_offers_only_the_modes_the_world_can_apply_with_defaults_and_bounds():
    targets = {one["tool"]: one for one in fault_targets(READING, RULES)}

    assert [one["mode"] for one in targets["issue_refund"]["modes"]] == [
        "timeout_before_commit",
        "timeout_after_commit",
        "unavailable",
        "error",
    ]
    assert [one["mode"] for one in targets["find_order"]["modes"]] == [
        "timeout_before_commit",
        "unavailable",
        "error",
    ]
    assert targets["lookup_order"]["modes"] == []
    assert (
        targets["issue_refund"]["modes"][1]["label"] == "Runs, then the answer is lost"
    )
    assert targets["find_order"]["defaults"] == {
        "on_call": 1,
        "delay_seconds": 30.0,
        "status": 500,
    }
    assert targets["find_order"]["bounds"] == {
        "delay_seconds": {"min": 0, "max": 30},
        "status": {"min": 400, "max": 599},
    }
    assert (targets["find_order"]["defaults"], targets["find_order"]["bounds"]) == (
        FAULT_DEFAULTS,
        FAULT_BOUNDS,
    )


def test_a_tool_that_declares_no_effect_writes_unless_its_method_only_reads():
    contract = {
        "tools": ["post", "get"],
        "tool_entrypoints": [
            {"tool": "post", "mode": "service", "service": "s", "endpoint": "/p"},
            {
                "tool": "get",
                "mode": "service",
                "service": "s",
                "endpoint": "/g",
                "method": "get",
            },
        ],
    }
    targets = {one["tool"]: one["writes"] for one in fault_targets(contract, RULES)}

    assert targets == {"post": True, "get": False}


def test_a_scenario_row_s_faults_read_as_the_harness_applies_them():
    targets = fault_targets(READING, RULES)

    assert resolved_faults(
        [
            {"action": "orders.refund", "mode": "timeout_after_commit", "on_call": "2"},
            {"action": "orders.refund", "mode": "error", "delay_seconds": 4},
            {"action": "gone", "mode": "explode", "on_call": 3},
        ],
        targets,
    ) == [
        {
            "action": "orders.refund",
            "tool": "issue_refund",
            "mode": "timeout_after_commit",
            "mode_label": "Runs, then the answer is lost",
            "on_call": 2,
            "delay_seconds": 30.0,
            "status": None,
            "message": "",
        },
        {
            "action": "orders.refund",
            "tool": "issue_refund",
            "mode": "error",
            "mode_label": "Answers with an error",
            "on_call": 1,
            "delay_seconds": None,
            "status": 500,
            "message": "",
        },
        {
            "action": "gone",
            "tool": None,
            "mode": "explode",
            "mode_label": "explode",
            "on_call": None,
            "delay_seconds": None,
            "status": None,
            "message": "",
        },
    ]
    assert resolved_faults(None, targets) == []


def _scenario(name, faults):
    return {"scenario_key": name, "name": name, "faults": faults}


AFTER_COMMIT_ON_A_READ = [{"action": "orders.find", "mode": "timeout_after_commit"}]


def test_a_write_that_gives_a_scenario_a_fault_the_world_cannot_apply_is_refused_naming_it():
    with pytest.raises(HostedHarnessError) as raised:
        require_valid_faults(
            [_scenario("late-refund", AFTER_COMMIT_ON_A_READ)], targets=TARGETS
        )

    assert raised.value.code == "fault_invalid" and raised.value.status_code == 422
    assert "late-refund" in raised.value.message
    assert "find_order changes no records" in raised.value.message


def test_a_write_checks_only_the_scenarios_whose_faults_it_changes():
    before = [_scenario("old", AFTER_COMMIT_ON_A_READ), _scenario("other", [])]
    unchanged = [_scenario("old", AFTER_COMMIT_ON_A_READ), _scenario("other", [])]

    # An old scenario saved under older rules does not fail an edit that leaves its faults alone.
    require_valid_faults(unchanged, targets=TARGETS, before=before)
    with pytest.raises(HostedHarnessError):
        require_valid_faults(
            [_scenario("old", []), _scenario("other", AFTER_COMMIT_ON_A_READ)],
            targets=TARGETS,
            before=before,
        )
    with pytest.raises(HostedHarnessError):
        require_valid_faults(
            [*unchanged, _scenario("new", AFTER_COMMIT_ON_A_READ)],
            targets=TARGETS,
            before=before,
        )


@pytest.mark.django_db
def test_an_amend_refuses_a_mode_the_tool_cannot_take_on_faults(user, workspace):
    from simulate.tests.test_harness_amend_receipt_codes import _receipts, _set

    with patch(
        "simulate.services.harness_environment.edit_fault_targets", return_value=TARGETS
    ):
        refused = _receipts(user, workspace, [_set("faults", AFTER_COMMIT_ON_A_READ)])
        applied = _receipts(
            user,
            workspace,
            [
                _set(
                    "faults",
                    [{"action": "orders.refund", "mode": "timeout_after_commit"}],
                )
            ],
        )

    assert refused == [("refused", "fault_invalid", "faults")]
    assert applied == [("applied", "applied", "faults")]


@pytest.fixture
def storage(monkeypatch):
    from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _tree(scenarios):
    import json

    return {
        "scenarios.json": json.dumps(scenarios),
        **{
            f"scenarios/{one['name']}/scenario.json": json.dumps(one)
            for one in scenarios
        },
    }


@pytest.mark.django_db
def test_a_draft_edit_that_leaves_an_old_fault_alone_is_written(
    user, workspace, storage, settings
):
    from simulate.services.harness_versioning import drafts
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests import test_harness_versioning_record as record_tests
    from simulate.tests.test_harness_amend_archive import NAMES
    from simulate.tests.test_harness_versioning_record import _authoring, _scenario

    settings.HARNESS_VERSIONING_ENABLED = True
    # Saved before faults were checked: a mode the harness does not know.
    old = [
        _scenario(NAMES[0], faults=[{"action": "orders.get", "mode": "delay"}]),
        _scenario(NAMES[1]),
    ]
    environment = record_tests._environment(user, workspace, "old-fault-kept")
    record_authoring(environment, _authoring(**_tree(old)), origin="authoring")
    environment.refresh_from_db()

    reworded = [old[0], dict(old[1], instruction="call about it again")]
    write = drafts.record_draft(
        environment, _authoring(**_tree(reworded)), origin="chat", user=user
    )
    assert write is not None and "suite" in write.written

    broken = [old[0], dict(old[1], faults=[{"action": "orders.get", "mode": "delay"}])]
    with pytest.raises(HostedHarnessError) as raised:
        drafts.record_draft(
            environment, _authoring(**_tree(broken)), origin="chat", user=user
        )
    assert raised.value.code == "fault_invalid" and NAMES[1] in raised.value.message
