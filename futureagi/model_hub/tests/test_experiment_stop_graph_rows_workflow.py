"""An agent row's step in flight at a Stop is kept, through the agent
workflow's own handling of its cancelled child.

On a Stop, the agent workflow's child (the graph execution) is cancelled. The
parent receives that as a ChildWorkflowError whose cause is a cancellation.
Its row handler (ProcessAgentWorkflow._process_agent_for_row) catches it and
runs the row's settlement activity (create_error_agent_cells_activity), while
the child's activity is still finishing the step it had sent. The settlement
used to take that started row over and seal it as refused, so the step's
paid output was then refused as "superseded or sealed". Now a settlement never
takes over a row that started before the Stop: the worker finishes it, keeps
the step's output and sends nothing new.

The parent workflow's handler is the real one; its activities are the real
registered ones. Only Temporal's scheduling is replaced: the child is the
real graph row run in a worker thread, cancelled the way Temporal reports it.
The provider call, the workflow service and the evaluator engine and billing
are stand-ins.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from django.db import connection

from model_hub.models.choices import CellStatus
from model_hub.tests.test_experiment_stop_default_path import stop
from model_hub.tests.test_experiment_stop_graph_rows import (  # noqa: F401
    STEP_CALL,
    STOPPED,
    graph_world,
    node_cells,
    run_control_off,
    stored_run,
    transports,
)
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants

pytestmark = pytest.mark.django_db(transaction=True)


def cancelled_child():
    """What Temporal raises in the parent when its child was cancelled."""
    from temporalio.exceptions import CancelledError, ChildWorkflowError

    error = ChildWorkflowError(
        "Child Workflow execution cancelled",
        namespace="default",
        workflow_id="native-graph-stopped",
        run_id="child-run",
        workflow_type="GraphExecutionWorkflow",
        initiated_event_id=1,
        started_event_id=2,
        retry_state=None,
    )
    error.__cause__ = CancelledError("Workflow cancelled")
    return error


def test_a_stopped_rows_settlement_keeps_the_step_its_worker_sent(tenants):
    from temporalio.exceptions import is_cancelled_exception

    from model_hub.views.experiment_runner import _run_native_graph_row
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.workflows import ProcessAgentWorkflow

    world = graph_world(tenants["a"])
    experiment = world["experiment"]
    row_id = world["rows"][0]
    plan = world["binding"].run.metadata["native_identity"]["graph_plan"]
    in_flight, release = threading.Event(), threading.Event()
    worker, settled = {}, {}

    def step_call(provider, *args, **kwargs):
        # The step's call is sent; it comes back once the parent has settled.
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
        except Exception as error:  # surfaced by the assertions below
            worker["error"] = error
        finally:
            connection.close()

    async def execute(name, value, **kwargs):
        # The real registered activities.
        result = await getattr(activities, name)(value)
        if name == "create_error_agent_cells_activity":
            settled["result"] = result
        return result

    async def child(name, graph_input, **kwargs):
        assert name == "GraphExecutionWorkflow"
        thread = threading.Thread(target=run_worker, args=(graph_input,))
        worker["thread"] = thread
        thread.start()
        assert await asyncio.to_thread(in_flight.wait, 60)
        # Stop is pressed while the step is in flight; Temporal cancels the
        # child and reports it to the parent.
        await asyncio.to_thread(stop, world["actor"], experiment)
        error = cancelled_child()
        assert is_cancelled_exception(error)
        raise error

    @asynccontextmanager
    async def heartbeat():
        yield

    stack, engine, billing = transports()
    with (
        stack,
        patch(STEP_CALL, autospec=True, side_effect=step_call),
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
        status = asyncio.run(
            ProcessAgentWorkflow()._process_agent_for_row(
                row_id=row_id,
                dataset_id=str(experiment.snapshot_dataset_id),
                experiment_id=str(experiment.id),
                graph_version_id=str(world["version"].id),
                node_column_mapping=plan["node_column_mapping"],
                experiment_agent_config_id=str(world["config"].id),
                native_context=world["context"],
            )
        )
        # The settlement ran while the step was still in flight.
        assert "result" in settled and status == "FAILED"
        release.set()
        worker["thread"].join(60)

    # The worker finished the row: no "superseded or sealed" refusal.
    assert "error" not in worker, worker.get("error")
    run = stored_run(world)
    item = run.items.get(deleted=False)
    nodes = item.metadata["native_graph"]["node_results"]
    sent = [node for node in nodes.values() if node["status"] == "SUCCESS"]
    assert len(sent) == 1 and sent[0]["outputs"]["response"] == "late answer"
    assert item.error["type"] == "ExecutionStopped"
    # The step's output is saved where the page reads it.
    assert ("late answer", CellStatus.PASS.value) in {
        (cell.value, cell.status) for cell in node_cells(world, row_id).values()
    }
    assert STOPPED in {cell.value for cell in node_cells(world, row_id).values()}
    # Nothing new was sent or charged after the Stop.
    assert engine.run.call_count == 0 and billing.call_count == 0


def test_an_older_agent_row_after_the_stop_starts_no_graph_on_the_workflow_path(
    tenants,
):
    """Older (non-native) agent rows: the workflow prepares each row before
    it starts that row's graph. After the Stop, preparation refuses, so the
    workflow's own handler settles the row and no graph is started."""
    from agent_playground.models import GraphExecution
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.experiments import ExperimentsTable
    from model_hub.tests import test_experiment_agent_target_admission as agents
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.workflows import ProcessAgentWorkflow

    agent = agents.stored_agent(tenants["a"])
    experiment = agent.experiment
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = activities._setup_agent_sync(
            str(experiment.id), str(experiment.snapshot_dataset_id), str(agent.eac.id)
        )
    assert setup["status"] == "READY"
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.CANCELLED.value
    )
    graphs = GraphExecution._base_manager.count()
    cells = list(
        Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
        .order_by("id")
        .values("id", "value", "status")
    )
    children, ran = [], []

    async def execute(name, value, **kwargs):
        ran.append(name)
        return await getattr(activities, name)(value)

    async def child(name, graph_input, **kwargs):
        children.append(graph_input)
        raise AssertionError("no graph is started after the Stop")

    with (
        patch("tfc.temporal.experiments.activities.activity.logger"),
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_activity",
            side_effect=execute,
        ),
        patch(
            "tfc.temporal.experiments.workflows.workflow.execute_child_workflow",
            side_effect=child,
        ),
    ):
        status = asyncio.run(
            ProcessAgentWorkflow()._process_agent_for_row(
                row_id=str(agent.row.id),
                dataset_id=str(experiment.snapshot_dataset_id),
                experiment_id=str(experiment.id),
                graph_version_id=str(agent.version.id),
                node_column_mapping=setup["node_column_mapping"],
                experiment_agent_config_id=str(agent.eac.id),
            )
        )

    assert status == "FAILED"
    assert children == []
    assert ran == ["prepare_agent_row_activity", "create_error_agent_cells_activity"]
    assert GraphExecution._base_manager.count() == graphs
    # The settlement leaves the Stop's marks: no cell is rewritten.
    assert (
        list(
            Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
            .order_by("id")
            .values("id", "value", "status")
        )
        == cells
    )
