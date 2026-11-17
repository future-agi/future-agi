"""A failed step of an agent row says so in a sentence chosen from a code.

The step's call raises. Its record in the row (the step checkpoint), its
execution record and every place a person reads it (the step's detail and
the list of steps in the agent run view, its output sinks) say "The agent run
failed at this step.", and the exception's own text reaches none of them. The
row's own sentences (its error, First error, each step column's cell) stay as
they were.

Real create, setup, row preparation, graph recorder and agent run views; only
the step's provider call, the workflow service and the evaluator engine are
stand-ins (the graph row tests' transports).
"""

import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from model_hub.models.choices import CellStatus
from model_hub.services.results_grid import first_failure_message
from model_hub.tests.test_experiment_stop_graph_rows import (  # noqa: F401
    STEP_CALL,
    graph_world,
    node_cells,
    prepare,
    run_control_off,
    run_row,
    stored_run,
    transports,
)
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants  # noqa: F401

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

SINKS = "tfc.temporal.agent_playground.activities._call_sinks_safe"
STEP_FAILED = "The agent run failed at this step."
AGENT_FAILED = "The agent run failed on this row"
# What the step's call raised: it must reach no record a person reads.
RAW = "upstream said 503 for request req-5f2c at https://provider.invalid/v1/chat"
# The internal text a failed step recorded before.
OLD_TEXT = "Native graph node execution failed"


def failed_row(tenants):
    """One agent row whose every step call raises; returns what it recorded."""
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, _, _ = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True, side_effect=RuntimeError(RAW)) as step_call,
        patch(SINKS) as sinks,
    ):
        run_row(world, row_id, prepared)
    assert step_call.call_count >= 1
    item = stored_run(world).items.get(deleted=False)
    assert item.status == "failed"
    return {
        "world": world,
        "row_id": row_id,
        "prepared": prepared,
        "item": item,
        "sinks": sinks,
    }


def step_records(row):
    from agent_playground.models import NodeExecution

    return list(
        NodeExecution.no_workspace_objects.filter(
            graph_execution_id=row["prepared"]["graph_execution_id"],
            deleted=False,
        )
    )


def failed_step_records(row):
    failed = [record for record in step_records(row) if record.status == "failed"]
    assert failed
    return failed


def step_detail(row, record):
    return row["world"]["actor"]["client"].get(
        reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": row["prepared"]["graph_execution_id"],
                "node_execution_id": record.id,
            },
        )
    )


def run_detail(row):
    return row["world"]["actor"]["client"].get(
        reverse(
            "graph-execution-detail",
            kwargs={
                "graph_id": row["world"]["version"].graph_id,
                "execution_id": row["prepared"]["graph_execution_id"],
            },
        )
    )


def test_the_step_code_chooses_the_step_sentence():
    from model_hub.services.row_failures import (
        AGENT_STEP_FAILED,
        AGENT_STEP_FAILED_MESSAGE,
        coded_message,
    )

    assert AGENT_STEP_FAILED == "agent_step_failed"
    assert AGENT_STEP_FAILED_MESSAGE == STEP_FAILED
    assert (
        coded_message({"code": AGENT_STEP_FAILED, "message": "stored text"})
        == STEP_FAILED
    )


def test_a_failed_step_records_the_step_sentence_with_its_code(tenants):
    row = failed_row(tenants)
    steps = row["item"].metadata["native_graph"]["node_results"]
    failed = [step for step in steps.values() if step["status"] == "FAILED"]
    assert failed
    for step in failed:
        assert step["code"] == "agent_step_failed"
        assert step["error"] == STEP_FAILED
    # The step's execution record, which the agent run view reads.
    for record in failed_step_records(row):
        assert record.error_message == STEP_FAILED


def test_the_step_detail_in_the_agent_run_view_shows_the_step_sentence(tenants):
    row = failed_row(tenants)
    for record in failed_step_records(row):
        response = step_detail(row, record)
        assert response.status_code == 200, response.content
        detail = response.data["result"]
        assert detail["status"] == "failed"
        assert detail["error_message"] == STEP_FAILED


def test_the_agent_runs_list_of_steps_carries_the_step_sentence(tenants):
    row = failed_row(tenants)
    response = run_detail(row)
    assert response.status_code == 200, response.content
    steps = [
        node["node_execution"]
        for node in response.data["result"]["nodes"]
        if node["node_execution"]
    ]
    failed = [step for step in steps if step["status"] == "failed"]
    assert failed
    assert {step["error_message"] for step in failed} == {STEP_FAILED}


def test_a_failed_steps_output_sinks_get_the_step_sentence(tenants):
    row = failed_row(tenants)
    calls = [
        call.kwargs
        for call in row["sinks"].call_args_list
        if call.kwargs.get("status") == "FAILED"
    ]
    assert calls
    assert {call["metadata"]["error"] for call in calls} == {STEP_FAILED}


def test_no_raw_text_from_a_failed_step_reaches_a_person(tenants):
    row = failed_row(tenants)
    shown = {
        "row error": json.dumps(row["item"].error),
        "step checkpoint": json.dumps(
            row["item"].metadata["native_graph"]["node_results"]
        ),
        "list of steps": run_detail(row).content.decode(),
    }
    for record in step_records(row):
        shown[f"step record {record.id}"] = record.error_message or ""
        shown[f"step detail {record.id}"] = step_detail(row, record).content.decode()
    for node_id, cell in node_cells(row["world"], row["row_id"]).items():
        shown[f"step cell {node_id}"] = f"{cell.value} {cell.value_infos}"
    for number, call in enumerate(row["sinks"].call_args_list):
        shown[f"sink {number}"] = json.dumps(call.kwargs.get("metadata"))
    for where, text in shown.items():
        assert RAW not in text, where
        assert OLD_TEXT not in text, where


def test_a_failed_step_leaves_the_rows_own_sentences_as_they_were(tenants):
    row = failed_row(tenants)
    item = row["item"]
    assert item.error == {
        "type": "GraphExecutionFailure",
        "code": "agent_failed",
        "message": AGENT_FAILED,
    }
    # First error reads the row's sentence, not the step's.
    assert (
        first_failure_message({(item.example_id, item.trial_number): item})
        == AGENT_FAILED
    )
    failed = [
        cell
        for cell in node_cells(row["world"], row["row_id"]).values()
        if cell.status == CellStatus.ERROR.value
    ]
    assert failed
    for cell in failed:
        assert cell.value == AGENT_FAILED
        assert json.loads(cell.value_infos)["reason"] == AGENT_FAILED
