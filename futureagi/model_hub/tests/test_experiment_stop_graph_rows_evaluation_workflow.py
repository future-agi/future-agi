"""An agent row's evaluation in flight at a Stop is kept and billed once,
through the agent workflow's own handling of its cancelled child.

The child (the graph execution) finished its steps and is evaluating the
row when the Stop lands. Temporal reports the cancelled child to the parent,
whose row handler runs the row's settlement while the evaluator call is still
in flight. The settlement leaves the row to its worker, so the evaluator's
score is saved, charged once (the call's charge and the cell's usage event),
and the other terminal's evaluator is never called.

Also: a step stopped at the runner's last hook stays "stopped", not failed,
when the row has per-row limits.

The parent's handler and activities are the real ones; the child is the real
graph row in a worker thread. The provider, the workflow service and the
evaluator engine and billing are stand-ins.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection

from model_hub.tests.test_experiment_stop_default_path import stop
from model_hub.tests.test_experiment_stop_graph_rows import (  # noqa: F401
    STEP_CALL,
    graph_world,
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


def test_an_evaluation_in_flight_at_the_stop_is_kept_and_billed_once(tenants):
    from model_hub.views.eval_runner import EvaluationRunner
    from model_hub.views.experiment_runner import _run_native_graph_row
    from tfc.temporal.experiments import activities
    from tfc.temporal.experiments.workflows import ProcessAgentWorkflow

    world = graph_world(tenants["a"])
    experiment = world["experiment"]
    row_id = world["rows"][0]
    plan = world["binding"].run.metadata["native_identity"]["graph_plan"]
    in_flight, release = threading.Event(), threading.Event()
    worker, settled = {}, {}

    stack, engine, billing = transports()
    scored = engine.run.return_value

    def evaluate(*args, **kwargs):
        # The evaluator call is sent (and was charged when it was prepared);
        # it comes back once the parent has settled.
        in_flight.set()
        assert release.wait(60)
        return scored

    engine.run.side_effect = evaluate

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

    async def execute(name, value, **kwargs):
        result = await getattr(activities, name)(value)
        if name == "create_error_agent_cells_activity":
            settled["result"] = result
        return result

    async def child(name, graph_input, **kwargs):
        thread = threading.Thread(target=run_worker, args=(graph_input,))
        worker["thread"] = thread
        thread.start()
        assert await asyncio.to_thread(in_flight.wait, 60)
        await asyncio.to_thread(stop, world["actor"], experiment)
        raise cancelled_child()

    @asynccontextmanager
    async def heartbeat():
        yield

    original_usage = EvaluationRunner._emit_cell_usage
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=lambda provider, *a, **k: ("answer", {}),
        ),
        patch.object(
            EvaluationRunner,
            "_emit_cell_usage",
            autospec=True,
            side_effect=original_usage,
        ) as usage,
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
        assert "result" in settled and status == "FAILED"
        release.set()
        worker["thread"].join(60)

    assert "error" not in worker, worker.get("error")
    item = stored_run(world).items.get(deleted=False)
    assert item.status == "completed"
    outcomes = item.score_refs[0]["terminal_results"]
    assert sorted((ref["status"], ref.get("reason")) for ref in outcomes) == [
        ("assessed", None),
        ("not_assessed", "execution_stopped"),
    ]
    # Charged once, and its usage event emitted once: the kept score's.
    assert engine.run.call_count == 1
    assert billing.call_count == 1
    assert usage.call_count == 1


def test_a_step_stopped_at_the_last_hook_stays_stopped_under_row_limits():
    from model_hub.views.experiment_runner import _GraphStopped
    from tfc.temporal.experiments import provider_policy

    budget = SimpleNamespace(
        _policy_error="per-row limit reached", refresh_from_db=lambda: None
    )

    class Runner:
        def run(self, config, inputs, execution_context):
            raise _GraphStopped()

    with (
        patch(
            "model_hub.services.experiment_row_budget.budget_for_graph",
            return_value=budget,
        ),
        patch(
            "model_hub.services.experiment_row_budget.check_deadline",
            return_value=None,
        ),
        patch(
            "agentic_eval.core_evals.run_prompt.litellm_response.USE_NEW_RUNPROMPT_HANDLERS",
            True,
        ),
    ):
        with pytest.raises(_GraphStopped):
            provider_policy.run_graph_node(Runner(), {}, {}, {}, "graph-execution")
