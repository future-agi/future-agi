"""The experiment's Stop and agent (graph) rows (run control off).

A graph row runs its steps one after another, then its evaluators. Before,
a Stop that landed while one of its calls was in flight ended the row as
skipped, and the call's result was refused when it came back: the step's
provider call (paid on the customer's own key) and an evaluator call
(charged in Future AGI credits when it is prepared) were paid for and then
dropped.

Now, as for prompt rows:
- a call sent before the Stop finishes and its result is saved: the step's
  output in its cell, the evaluator's score in the row's outcome. The row is
  sealed and counted, each call charged once;
- nothing new is sent after the Stop: no next step, no evaluator. A step not
  sent says "Experiment stopped"; an evaluator not called says
  execution_stopped;
- a row whose graph had not started is never sent; the Stop ends it as
  before;
- another tenant's run is untouched.

Real create, setup, row preparation, graph recorder, evaluator runner and
Stop view; only the provider call, the workflow service and the evaluator
engine and billing are stand-ins (the bridge tests' transports).
"""

from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.experiments import ExperimentRun
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.native_run_projection import project_run
from model_hub.tests.test_experiment_stop_default_path import running, stop
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_eval_transport,
    native_fixture_evaluator,
    native_preflight,
    r8_create_graph,
    r8_graph_context,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.tests.test_native_prompt_transport import r11_llm_graph
from model_hub.views.experiment_runner import _run_native_graph_row
from tfc.temporal.experiments import activities

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

STEP_CALL = (
    "agent_playground.services.engine.runners.llm_prompt.RunPrompt.litellm_response"
)
STOPPED = "Experiment stopped"


@pytest.fixture(autouse=True)
def run_control_off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


def graph_world(actor, rows=1):
    """A graph experiment of two prompt steps, set up and running."""
    source = actor["exp"]["dataset"]
    source_column = Column.no_workspace_objects.get(dataset=source, deleted=False)
    for order in range(1, rows):
        extra = Row.no_workspace_objects.create(dataset=source, order=order)
        Cell.no_workspace_objects.create(
            dataset=source, column=source_column, row=extra, value=f"row {order}"
        )
    world = r8_create_graph(actor, graph_factory=r11_llm_graph)
    context, binding, target = r8_graph_context(world)
    assert len(target["snapshot_row_ids"]) == rows
    experiment = world["experiment"]
    activities._setup_agent_sync(
        str(experiment.id),
        str(experiment.snapshot_dataset_id),
        str(world["config"].id),
        native_context=context,
    )
    running(experiment)
    world.update(context=context, binding=binding, rows=target["snapshot_row_ids"])
    return world


def prepare(world, row_id):
    experiment = world["experiment"]
    prepared = activities._prepare_agent_row_sync(
        row_id,
        str(experiment.snapshot_dataset_id),
        str(experiment.id),
        str(world["version"].id),
        str(world["config"].id),
        native_context=world["context"],
    )
    assert prepared["status"] == "READY", prepared
    return prepared


def run_row(world, row_id, prepared):
    experiment = world["experiment"]
    return _run_native_graph_row(
        str(experiment.id),
        str(experiment.snapshot_dataset_id),
        row_id,
        world["context"],
        graph_execution_id=prepared["graph_execution_id"],
        graph_version_id=str(world["version"].id),
        input_payload=prepared["input_payload"],
    )


def stored_run(world):
    return ExperimentRun._base_manager.get(pk=world["binding"].run_id)


def node_cells(world, row_id):
    plan = world["binding"].run.metadata["native_identity"]["graph_plan"]
    return {
        node_id: Cell.no_workspace_objects.get(
            column_id=column_id, row_id=row_id, deleted=False
        )
        for node_id, column_id in plan["node_column_mapping"].items()
    }


def transports():
    """Evaluator engine/billing and the usage preflight, as the bridge tests do."""
    from contextlib import ExitStack

    stack = ExitStack()
    engine, billing = stack.enter_context(native_eval_transport(score=0))
    stack.enter_context(
        native_preflight(MagicMock(return_value=SimpleNamespace(allowed=True)))
    )
    return stack, engine, billing


def answers_with_a_stop_on_call(world, call_number, answers):
    calls = []

    def respond(provider, *args, **kwargs):
        calls.append(provider)
        if len(calls) == call_number:
            # Stop is pressed while this step's call is in flight.
            answers.append(stop(world["actor"], world["experiment"]))
        return f"answer {len(calls)}", {}

    return respond


# A step in flight at the Stop ---------------------------------------------------


def test_a_step_in_flight_at_the_stop_is_saved_and_no_new_step_is_sent(tenants):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    answers = []
    stack, engine, billing = transports()
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=answers_with_a_stop_on_call(world, 1, answers),
        ) as step_call,
    ):
        result = run_row(world, row_id, prepared)

    # The stop answered while the step was in flight, and ended the run.
    assert answers[0]["rows_in_flight"] == 1
    run = stored_run(world)
    assert run.status == "cancelled" and run.completed_at is not None
    # No new step and no evaluator after the stop.
    assert step_call.call_count == 1
    assert engine.run.call_count == 0 and billing.call_count == 0

    item = run.items.get(deleted=False)
    assert item.status == "failed"
    assert item.error["type"] == "ExecutionStopped"
    assert result["status"] == "FAILED"
    nodes = item.metadata["native_graph"]["node_results"]
    sent = [node for node in nodes.values() if node["status"] == "SUCCESS"]
    not_sent = [node for node in nodes.values() if node["status"] == "SKIPPED"]
    assert len(sent) == 1 and sent[0]["outputs"]["response"] == "answer 1"
    assert len(not_sent) == 1 and not_sent[0]["error"] == STOPPED
    # Saved where the page reads it.
    cells = {(cell.value, cell.status) for cell in node_cells(world, row_id).values()}
    assert cells == {
        ("answer 1", CellStatus.PASS.value),
        (STOPPED, CellStatus.ERROR.value),
    }
    # Its evaluators were not called after the stop; the row says why.
    outcomes = item.score_refs[0]["terminal_results"]
    assert {(ref["status"], ref["reason"]) for ref in outcomes} == {
        ("not_assessed", "execution_stopped")
    }
    # Counted.
    assert (
        project_run(run, dataset=world["experiment"].dataset)["counts"]["failed"] == 1
    )


def test_a_graph_whose_last_step_is_in_flight_completes_and_is_counted(tenants):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    answers = []
    stack, engine, billing = transports()
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=answers_with_a_stop_on_call(world, 2, answers),
        ) as step_call,
    ):
        result = run_row(world, row_id, prepared)

    # Each step's call was sent once, both before the stop.
    assert step_call.call_count == 2
    assert engine.run.call_count == 0 and billing.call_count == 0
    run = stored_run(world)
    item = run.items.get(deleted=False)
    assert result["status"] == "SUCCESS"
    assert item.status == "completed"
    cells = {(cell.value, cell.status) for cell in node_cells(world, row_id).values()}
    assert cells == {
        ("answer 1", CellStatus.PASS.value),
        ("answer 2", CellStatus.PASS.value),
    }
    outcomes = item.score_refs[0]["terminal_results"]
    assert {ref["reason"] for ref in outcomes} == {"execution_stopped"}
    assert (
        project_run(run, dataset=world["experiment"].dataset)["counts"]["completed"]
        == 1
    )


# An evaluation in flight at the Stop --------------------------------------------


def test_an_evaluation_in_flight_at_the_stop_is_saved_and_charged_once(tenants):
    world = graph_world(tenants["a"])
    row_id = world["rows"][0]
    prepared = prepare(world, row_id)
    answers = []
    stack, engine, billing = transports()
    scored = engine.run.return_value

    def evaluate(*args, **kwargs):
        # Stop is pressed while the first evaluator call is in flight; it
        # was charged when it was prepared.
        if engine.run.call_count == 1:
            answers.append(stop(world["actor"], world["experiment"]))
        return scored

    engine.run.side_effect = evaluate
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=lambda provider, *a, **k: ("answer", {}),
        ),
    ):
        result = run_row(world, row_id, prepared)

    assert len(answers) == 1
    # The call in flight was charged once and its score kept; the second
    # terminal's evaluator was never called nor charged.
    assert engine.run.call_count == 1 and billing.call_count == 1
    run = stored_run(world)
    item = run.items.get(deleted=False)
    assert result["status"] == "SUCCESS" and item.status == "completed"
    outcomes = item.score_refs[0]["terminal_results"]
    assert sorted((ref["status"], ref.get("reason")) for ref in outcomes) == [
        ("assessed", None),
        ("not_assessed", "execution_stopped"),
    ]
    assessed = next(ref for ref in outcomes if ref["status"] == "assessed")
    assert assessed["value"] == 0
    assert (
        project_run(run, dataset=world["experiment"].dataset)["counts"]["completed"]
        == 1
    )


# A row not yet sent ---------------------------------------------------------------


def test_a_row_not_started_at_the_stop_is_never_sent(tenants):
    from model_hub.views.experiment_runner import _prepare_native_graph_row

    world = graph_world(tenants["a"], rows=2)
    first, second = world["rows"]
    experiment = world["experiment"]
    prepared = prepare(world, first)
    stack, engine, billing = transports()
    with stack, patch(STEP_CALL, autospec=True) as step_call:
        stop(world["actor"], experiment)
        # Its graph never started: nothing is sent for it now (its worker is
        # refused, or finds it ended).
        with suppress(RunWriteError):
            run_row(world, first, prepared)
        # A row not prepared by the stop cannot be prepared after it.
        with pytest.raises(RunWriteError):
            _prepare_native_graph_row(
                str(experiment.id),
                str(experiment.snapshot_dataset_id),
                str(world["config"].id),
                second,
                world["context"],
            )
    assert step_call.call_count == 0
    assert engine.run.call_count == 0 and billing.call_count == 0
    # The stop ended both rows as never run, as before.
    run = stored_run(world)
    items = list(run.items.filter(deleted=False))
    assert [(item.status, item.error["type"]) for item in items] == [
        ("skipped", "ExecutionCancelled"),
        ("skipped", "ExecutionCancelled"),
    ]


# Other runs ----------------------------------------------------------------------


def test_another_tenants_graph_run_is_untouched(tenants):
    mine = graph_world(tenants["a"])
    theirs = graph_world(tenants["b"])
    theirs_row = theirs["rows"][0]
    prepare(theirs, theirs_row)
    their_run = stored_run(theirs)
    their_items = list(their_run.items.order_by("id").values())
    their_cells = list(
        Cell.no_workspace_objects.filter(dataset=theirs["experiment"].snapshot_dataset)
        .order_by("id")
        .values()
    )

    row_id = mine["rows"][0]
    prepared = prepare(mine, row_id)
    answers = []
    stack, _, _ = transports()
    with (
        stack,
        patch(
            STEP_CALL,
            autospec=True,
            side_effect=answers_with_a_stop_on_call(mine, 1, answers),
        ),
        suppress(RunWriteError),
    ):
        run_row(mine, row_id, prepared)

    assert len(answers) == 1
    assert stored_run(theirs).status == their_run.status
    assert stored_run(theirs).completed_at is None
    assert list(stored_run(theirs).items.order_by("id").values()) == their_items
    assert (
        list(
            Cell.no_workspace_objects.filter(
                dataset=theirs["experiment"].snapshot_dataset
            )
            .order_by("id")
            .values()
        )
        == their_cells
    )
    theirs["experiment"].refresh_from_db()
    assert theirs["experiment"].status == StatusType.RUNNING.value
