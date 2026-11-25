"""Each scenario amend receipt says what became of the change by a code, and which scenario field it is about."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simulate.tests.test_harness_scenario_amend import SUITE, _job, _post

pytestmark = pytest.mark.django_db


def _receipts(user, workspace, changes, rework=True):
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
        response = _post(user, job, changes, rework=rework)
    assert response.status_code == 200, response.content
    return [
        (one["outcome"], one["code"], one["field"])
        for one in response.json()["receipts"]
    ]


def _set(field, value, scenario="one"):
    return {"op": "set_field", "scenario": scenario, "field": field, "value": value}


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ([{"action": "orders.refund", "mode": "error", "status": 700}], "status"),
        (
            [
                {
                    "action": "orders.refund",
                    "mode": "timeout_before_commit",
                    "delay_seconds": 31,
                }
            ],
            "delay",
        ),
        ([{"action": "orders.refund", "mode": "unavailable", "on_call": 0}], "on_call"),
    ],
)
def test_a_test_condition_the_world_cannot_apply_is_refused_on_faults(
    user, workspace, value, why
):
    assert _receipts(user, workspace, [_set("faults", value)]) == [
        ("refused", "fault_invalid", "faults")
    ], why


def test_an_applied_field_names_itself(user, workspace):
    assert _receipts(user, workspace, [_set("tests", "reworded")]) == [
        ("applied", "applied", "tests")
    ]


def test_a_valid_test_condition_is_applied_on_faults(user, workspace):
    value = [{"action": "orders.refund", "mode": "unavailable"}]

    assert _receipts(user, workspace, [_set("faults", value)]) == [
        ("applied", "applied", "faults")
    ]


def test_refusals_carry_their_kind(user, workspace):
    assert _receipts(
        user,
        workspace,
        [
            _set("instruction", "x"),
            _set("faults", [], scenario="nobody"),
            {"op": "drop", "scenario": "two"},
        ],
        rework=False,
    ) == [
        ("refused", "scenario_unknown", None),
        ("refused", "not_editable", "instruction"),
        ("refused", "needs_rework", None),
    ]


def test_a_persona_edit_names_the_persona(user, workspace):
    assert _receipts(
        user,
        workspace,
        [{"op": "set_persona", "scenario": "two", "persona": {"shoe_size": "9"}}],
    ) == [("refused", "not_editable", "persona")]
