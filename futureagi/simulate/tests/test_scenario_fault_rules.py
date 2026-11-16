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
TARGETS = door_targets(READING, RULES, [])


@pytest.mark.parametrize(
    ("fault", "field"),
    [
        ({"action": "orders.refund", "mode": "delay"}, "mode"),
        ({"action": "", "mode": "unavailable"}, "action"),
        ({"action": "orders.refund", "mode": "unavailable", "on_call": 0}, "on_call"),
        (
            {
                "action": "orders.refund",
                "mode": "timeout_before_commit",
                "delay_seconds": 31,
            },
            "delay_seconds",
        ),
        ({"action": "orders.refund", "mode": "error", "status": 200}, "status"),
        ({"action": "orders.refund", "mode": "error", "message": "x" * 501}, "message"),
        ({"action": "orders.refund", "mode": "unavailable", "when": "now"}, None),
        ("orders.refund", None),
        ({"action": "orders.cancel", "mode": "unavailable"}, "action"),
        ({"action": "lookup_order", "mode": "unavailable"}, "action"),
        ({"action": "orders.find", "mode": "timeout_after_commit"}, "mode"),
    ],
)
def test_a_fault_the_world_cannot_apply_is_refused_under_its_field(fault, field):
    from simulate.services.scenario_faults import faults_problems

    [problem] = faults_problems([fault], TARGETS)
    assert (problem["index"], problem["field"]) == (0, field)


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
    assert door_targets({"tools": ["x"]}, RULES, []) is None
    assert (
        fault_problem({"action": "anything", "mode": "timeout_after_commit"}, None)
        is None
    )
    assert fault_problem({"action": "anything", "mode": "delay"}, None) is not None


def test_each_target_offers_only_the_modes_the_world_can_apply():
    targets = {one["tool"]: one for one in fault_targets(READING, RULES, [])}

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
    targets = {one["tool"]: one["writes"] for one in fault_targets(contract, RULES, [])}

    assert targets == {"post": True, "get": False}


def test_a_scenario_row_s_faults_read_as_the_harness_applies_them():
    applied = (
        "action",
        "tool",
        "mode",
        "on_call",
        "delay_seconds",
        "status",
        "message",
    )
    resolved = resolved_faults(
        [
            {"action": "orders.refund", "mode": "timeout_after_commit", "on_call": "2"},
            {"action": "orders.refund", "mode": "error", "delay_seconds": 4},
            {"action": "gone", "mode": "explode", "on_call": 3},
        ],
        fault_targets(READING, RULES, []),
    )

    # Text read as the harness reads it, defaults applied, and a value the mode never reads left out.
    assert [tuple(one[key] for key in applied) for one in resolved] == [
        ("orders.refund", "issue_refund", "timeout_after_commit", 2, 30.0, None, ""),
        ("orders.refund", "issue_refund", "error", 1, None, 500, ""),
        ("gone", None, "explode", None, None, None, ""),
    ]
    assert resolved_faults(None, []) == []


def _scenario(name, faults):
    return {"scenario_key": name, "name": name, "faults": faults}


AFTER_COMMIT_ON_A_READ = [{"action": "orders.find", "mode": "timeout_after_commit"}]


def test_a_write_that_gives_a_scenario_a_fault_the_world_cannot_apply_is_refused_naming_it():
    with pytest.raises(HostedHarnessError) as raised:
        require_valid_faults(
            [_scenario("late-refund", AFTER_COMMIT_ON_A_READ)], targets=TARGETS, cast=[]
        )

    assert raised.value.code == "fault_invalid" and raised.value.status_code == 422
    assert raised.value.details["problems"][0]["scenario"] == "late-refund"


def test_a_write_checks_only_the_scenarios_whose_faults_it_changes():
    before = [_scenario("old", AFTER_COMMIT_ON_A_READ), _scenario("other", [])]
    unchanged = [_scenario("old", AFTER_COMMIT_ON_A_READ), _scenario("other", [])]

    # An old scenario saved under older rules does not fail an edit that leaves its faults alone.
    require_valid_faults(unchanged, targets=TARGETS, cast=[], before=before)
    with pytest.raises(HostedHarnessError):
        require_valid_faults(
            [_scenario("old", []), _scenario("other", AFTER_COMMIT_ON_A_READ)],
            targets=TARGETS,
            cast=[],
            before=before,
        )
    with pytest.raises(HostedHarnessError):
        require_valid_faults(
            [*unchanged, _scenario("new", AFTER_COMMIT_ON_A_READ)],
            targets=TARGETS,
            cast=[],
            before=before,
        )


@pytest.mark.django_db
def test_an_amend_refuses_a_mode_the_tool_cannot_take_on_faults(user, workspace):
    from simulate.tests.test_harness_amend_receipt_codes import _receipts, _set

    with patch(
        "simulate.services.harness_environment.edit_fault_side",
        return_value=(TARGETS, []),
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


def test_an_emptied_optional_value_is_stored_absent_and_takes_the_harness_default():
    from simulate.services.scenario_faults import faults_as_stored

    sent = [
        {
            "action": "orders.refund",
            "mode": "timeout_before_commit",
            "on_call": None,
            "delay_seconds": None,
        }
    ]

    assert faults_as_stored(sent) == [
        {"action": "orders.refund", "mode": "timeout_before_commit"}
    ]
    assert fault_problem(faults_as_stored(sent)[0], TARGETS) is None
    assert faults_as_stored("junk") == "junk"


@pytest.mark.django_db
def test_an_amend_sending_emptied_values_as_null_is_applied(user, workspace):
    from simulate.tests.test_harness_amend_receipt_codes import _receipts, _set

    sent = [
        {
            "action": "orders.refund",
            "mode": "error",
            "on_call": None,
            "status": None,
            "message": None,
        }
    ]
    with patch(
        "simulate.services.harness_environment.edit_fault_side",
        return_value=(TARGETS, []),
    ):
        assert _receipts(user, workspace, [_set("faults", sent)]) == [
            ("applied", "applied", "faults")
        ]


def test_every_fault_the_world_cannot_apply_is_named_by_its_place():
    from simulate.services.scenario_faults import faults_problems

    faults = [
        {
            "action": "orders.refund",
            "mode": "timeout_before_commit",
            "delay_seconds": 45,
        },
        {"action": "orders.refund", "mode": "unavailable"},
        {"action": "orders.refund", "mode": "unavailable", "on_call": 1.5},
        {"action": "orders.find", "mode": "timeout_after_commit"},
    ]

    assert [
        (one["index"], one["field"]) for one in faults_problems(faults, TARGETS)
    ] == [
        (0, "delay_seconds"),
        (2, "on_call"),
        (3, "mode"),
    ]


def test_a_draft_write_refuses_with_every_problem_of_every_changed_scenario():
    bad = [
        {"action": "orders.refund", "mode": "unavailable", "on_call": 0},
        AFTER_COMMIT_ON_A_READ[0],
    ]

    with pytest.raises(HostedHarnessError) as raised:
        require_valid_faults(
            [_scenario("one", bad), _scenario("two", AFTER_COMMIT_ON_A_READ)],
            targets=TARGETS,
            cast=[],
        )

    assert [
        (one["scenario"], one["index"], one["field"])
        for one in raised.value.details["problems"]
    ] == [
        ("one", 0, "on_call"),
        ("one", 1, "mode"),
        ("two", 0, "mode"),
    ]


@pytest.mark.django_db
def test_an_amend_receipt_carries_every_problem_under_its_fault(user, workspace):
    from simulate.tests.test_harness_scenario_amend import SUITE, _job, _post

    sent = [
        {"action": "orders.refund", "mode": "error", "status": 700},
        {"action": "orders.refund", "mode": "unavailable"},
        {"action": "orders.find", "mode": "timeout_after_commit"},
    ]
    job = _job(user, workspace, SUITE)
    with (
        patch(
            "simulate.services.harness_environment.edit_fault_side",
            return_value=(TARGETS, []),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
            return_value=False,
        ),
        patch(
            "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
            return_value=None,
        ),
    ):
        response = _post(
            user,
            job,
            [{"op": "set_field", "scenario": "one", "field": "faults", "value": sent}],
            rework=True,
        )

    [receipt] = response.json()["receipts"]
    assert receipt["code"] == "fault_invalid"
    assert [(one["index"], one["field"]) for one in receipt["problems"]] == [
        (0, "status"),
        (2, "mode"),
    ]


@pytest.mark.django_db
def test_every_amend_receipt_lists_its_fault_problems_empty_unless_it_refused_faults(
    user, workspace
):
    from simulate.tests.test_harness_scenario_amend import SUITE, _job, _post

    job = _job(user, workspace, SUITE)
    with (
        patch(
            "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
            return_value=False,
        ),
        patch(
            "simulate.services.hosted_harness_gateway.rewrite_authoring_scenarios",
            return_value=None,
        ),
    ):
        response = _post(
            user,
            job,
            [
                {
                    "op": "set_field",
                    "scenario": "one",
                    "field": "tests",
                    "value": "reworded",
                },
                {
                    "op": "set_field",
                    "scenario": "nobody",
                    "field": "tests",
                    "value": "x",
                },
            ],
            rework=True,
        )

    assert [one["problems"] for one in response.json()["receipts"]] == [[], []]


# The modes the harness's `Fault` model takes.
HARNESS_FAULT_MODES = {
    "timeout_before_commit",
    "timeout_after_commit",
    "unavailable",
    "error",
    "approval_required",
}


def test_the_mirror_matches_the_harness_s_own_model_where_it_is_installed():
    from typing import get_args

    from simulate.services.scenario_faults import ScenarioFault

    harness = pytest.importorskip("fi.alk.harness.actors")
    fault = harness.Fault

    assert set(fault.model_fields) == set(ScenarioFault.model_fields)
    assert set(get_args(fault.model_fields["mode"].annotation)) == HARNESS_FAULT_MODES
