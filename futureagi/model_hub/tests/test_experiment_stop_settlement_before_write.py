"""A stopped agent row is kept even when its workflow's settlement runs while
the Stop is still cancelling.

The Stop cancels the experiment's workflows, and Temporal passes the cancel
to the agent workflow at once. That workflow's row handler then runs the
row's settlement while the Stop is still asking the workflow service about
reruns. The settlement leaves a started row to its worker only when the
experiment already reads Cancelled, and the Stop used to write Cancelled only
after its cancel calls returned. In that order the settlement took the
started row over, and the paid step's result was refused. Now the Stop writes
Cancelled before it cancels anything, and again after.

The parent's handler (ProcessAgentWorkflow._process_agent_for_row) and its
activities are the real ones. The child is the real graph row in a worker
thread. The Stop goes through the real Stop view, and its cancel call (the
workflow service) is a stand-in that delivers the cancel to the parent and
waits for the parent's settlement before it returns, which is the order that
was unsafe. The provider and the evaluator engine and billing are stand-ins.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from django.db import connection

from model_hub.models.choices import CellStatus
from model_hub.tests.test_experiment_stop_graph_rows import (  # noqa: F401
    STEP_CALL,
    STOPPED,
    graph_world,
    node_cells,
    run_control_off,
    stored_run,
    transports,
)
from model_hub.tests.test_experiment_stop_graph_rows_workflow import cancelled_child
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants

pytestmark = pytest.mark.django_db(transaction=True)

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"
CONFIRMED = {
    "main_cancelled": True,
    "rerun_cancelled": 0,
    "main_confirmed": True,
    "reruns_confirmed": True,
}


def test_a_settlement_during_the_stops_cancel_keeps_the_started_row(tenants):
    from model_hub.views.experiment_runner import _run_native_graph_row
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.workflows import ProcessAgentWorkflow

    world = graph_world(tenants["a"])
    experiment = world["experiment"]
    actor = world["actor"]
    row_id = world["rows"][0]
    plan = world["binding"].run.metadata["native_identity"]["graph_plan"]
    in_flight, release = threading.Event(), threading.Event()
    cancel_reached, settled = threading.Event(), threading.Event()
    worker, stop_answer = {}, {}

    def step_call(provider, *args, **kwargs):
        # The step's call is sent; it comes back after the Stop is done.
        in_flight.set()
        assert release.wait(60)
        return "late answer", {}

    def run_worker(graph_input):
        native = graph_input.native_context
        try:
            worker["result"] = _run_native_graph_row(
                native["experiment_id"],
                native["dataset_id"],
                native["row_id"],
                native["dispatch"],
                graph_execution_id=graph_input.graph_execution_id,
                graph_version_id=graph_input.graph_version_id,
                input_payload=graph_input.input_payload,
            )
        except Exception as error:
            worker["error"] = error
        finally:
            connection.close()

    def service_cancel(_experiment_id):
        # Temporal passes the cancel to the agent workflow, whose settlement
        # runs before this call returns to the Stop.
        cancel_reached.set()
        assert settled.wait(60)
        return dict(CONFIRMED)

    def press_stop():
        try:
            with patch(CANCEL, side_effect=service_cancel):
                response = actor["client"].post(
                    f"/model-hub/experiments/v2/{experiment.id}/stop/",
                    {},
                    format="json",
                )
            stop_answer["status"] = response.status_code
        finally:
            connection.close()

    async def execute(name, value, **kwargs):
        result = await getattr(activities, name)(value)
        if name == "create_error_agent_cells_activity":
            settled.set()
        return result

    pending = {}

    async def child(name, graph_input, **kwargs):
        thread = threading.Thread(target=run_worker, args=(graph_input,))
        worker["thread"] = thread
        thread.start()
        assert await asyncio.to_thread(in_flight.wait, 60)
        # Stop is pressed while the step is in flight. Its cancel reaches
        # this workflow while the Stop is still in its cancel call.
        pending["stop"] = asyncio.ensure_future(asyncio.to_thread(press_stop))
        assert await asyncio.to_thread(cancel_reached.wait, 60)
        raise cancelled_child()

    async def drive():
        status = await ProcessAgentWorkflow()._process_agent_for_row(
            row_id=row_id,
            dataset_id=str(experiment.snapshot_dataset_id),
            experiment_id=str(experiment.id),
            graph_version_id=str(world["version"].id),
            node_column_mapping=plan["node_column_mapping"],
            experiment_agent_config_id=str(world["config"].id),
            native_context=world["context"],
        )
        await pending["stop"]
        return status

    @asynccontextmanager
    async def heartbeat():
        yield

    stack, engine, billing = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True, side_effect=step_call) as step,
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_activity",
            side_effect=execute,
        ),
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_child_workflow",
            side_effect=child,
        ),
        patch.object(activities, "Heartbeater", heartbeat),
    ):
        status = asyncio.run(drive())
        assert status == "FAILED" and stop_answer["status"] == 200
        release.set()
        worker["thread"].join(60)

    # The worker finished the row: the settlement did not take it over.
    assert "error" not in worker, worker.get("error")
    item = stored_run(world).items.get(deleted=False)
    nodes = item.metadata["native_graph"]["node_results"]
    sent = [node for node in nodes.values() if node["status"] == "SUCCESS"]
    assert len(sent) == 1 and sent[0]["outputs"]["response"] == "late answer"
    assert item.error["type"] == "ExecutionStopped"
    assert ("late answer", CellStatus.PASS.value) in {
        (cell.value, cell.status) for cell in node_cells(world, row_id).values()
    }
    assert STOPPED in {cell.value for cell in node_cells(world, row_id).values()}
    # Charged once: one step call, no evaluator after the Stop.
    assert step.call_count == 1
    assert engine.run.call_count == 0 and billing.call_count == 0
