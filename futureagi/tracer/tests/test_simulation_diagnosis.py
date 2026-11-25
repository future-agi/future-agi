from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.simulation_diagnosis import build_diagnosis


@pytest.mark.parametrize("output", ["Passed", "Failed"])
def test_diagnosis_accepts_harness_evaluations(output):
    call = SimpleNamespace(
        id="call-1",
        status="completed",
        call_metadata={},
        eval_outputs={
            "harness-check": {
                "source": "harness",
                "status": "completed",
                "output": output,
            }
        },
    )
    with (
        patch(
            "tracer.services.simulation_diagnosis.CallExecution.no_workspace_objects.filter"
        ) as calls,
        patch(
            "tracer.services.simulation_diagnosis.sub_goal_catalogue", return_value={}
        ),
    ):
        calls.return_value.order_by.return_value = [call]
        result = build_diagnosis(SimpleNamespace(run_test_id="run-1"), [], {}, {})

    assert result["summary"]["measured_call_count"] == 1
    assert result["summary"]["excluded_call_ids"] == []


def test_a_sub_goal_that_did_not_apply_to_a_call_is_not_counted_as_tested():
    def call(key, goal):
        return SimpleNamespace(
            id=key,
            status="completed",
            call_metadata={"hosted_harness_receipt": {"sub_goals": [goal]}},
            eval_outputs={},
        )

    calls_run = [
        call("broke", {"name": "refund", "held": False}),
        call("skipped", {"name": "refund", "held": True, "verdict": "not_applicable"}),
    ]
    with (
        patch(
            "tracer.services.simulation_diagnosis.CallExecution.no_workspace_objects.filter"
        ) as calls,
        patch(
            "tracer.services.simulation_diagnosis.sub_goal_catalogue", return_value={}
        ),
    ):
        calls.return_value.order_by.return_value = calls_run
        result = build_diagnosis(SimpleNamespace(run_test_id="run-1"), [], {}, {})

    [goal] = result["goals"]
    assert (goal["broken_call_ids"], goal["tested_call_count"]) == (["broke"], 1)
