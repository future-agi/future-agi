"""A failed or cancelled agent row says so in words chosen from a code.

Real create, setup, row preparation, graph recorder and Stop view; only the
step's provider call, the workflow service and the evaluator engine are
stand-ins (the graph row tests' transports).
"""

import json
from contextlib import suppress
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.results_grid import result_cell, trial_record
from model_hub.tests.test_experiment_stop_default_path import stop
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

AGENT_FAILED = "The agent run failed on this row"
AGENT_CANCELLED = "The agent run was cancelled"


def test_a_failed_agent_row_says_the_agent_run_failed(tenants):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    stack, _, _ = transports()
    with (
        stack,
        patch(
            STEP_CALL, autospec=True, side_effect=RuntimeError("provider unavailable")
        ),
    ):
        run_row(world, row_id, prepared)
    run = stored_run(world)
    item = run.items.get(deleted=False)
    assert item.status == "failed"
    # The row drawer reads this; the graph's own text stays on its execution record.
    assert item.error == {
        "type": "GraphExecutionFailure",
        "code": "agent_failed",
        "message": AGENT_FAILED,
    }
    failed = [
        cell
        for cell in node_cells(world, row_id).values()
        if cell.status == CellStatus.ERROR.value
    ]
    assert failed
    for cell in failed:
        assert cell.value == AGENT_FAILED
        assert json.loads(cell.value_infos)["reason"] == AGENT_FAILED
    # The results grid's cell for the row carries the same sentence.
    grid = result_cell([trial_record(item, item.trial_number, run, _bindings(run))])
    assert grid["status"] == "error" and grid["error"] == AGENT_FAILED


def test_an_agent_row_ended_by_a_stop_says_the_agent_run_was_cancelled(tenants):
    world = graph_world(tenants["a"], rows=2)
    first, _ = world["rows"]
    prepared = prepare(world, first)
    stack, _, _ = transports()
    with stack, patch(STEP_CALL, autospec=True) as step_call:
        stop(world["actor"], world["experiment"])
        with suppress(RunWriteError):
            run_row(world, first, prepared)
    assert step_call.call_count == 0
    items = list(stored_run(world).items.filter(deleted=False))
    assert [item.status for item in items] == ["skipped", "skipped"]
    for item in items:
        assert item.error == {
            "type": "ExecutionCancelled",
            "code": "agent_cancelled",
            "message": AGENT_CANCELLED,
        }
