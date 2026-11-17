"""The experiment's Stop (run control off): a repeat Stop repairs a stop that
could not end its runs, and nothing queued is sent after a Stop.

- A Stop ends the runs itself only when every workflow is known stopped.
  When the first Stop could not confirm that (the service did not answer, a
  rerun could not be reached, the records could not be read), and the
  cancelled workflow's own cleanup also failed, the runs stayed unfinished:
  edits and reruns were refused for good, and a repeat Stop changed nothing.
  Now a repeat Stop asks again and, under the same rule, ends them.
- An evaluation batch hands up to five rows at a time to its workers. Rows
  still queued when the Stop is applied used to start a charged call; now
  each row checks the Stop before its call: one not yet charged is marked
  stopped and never charged.
- An agent row (graph) of an older experiment is not prepared after the
  Stop: nothing is sent for it.

Real create, setup, Stop view and cleanup; only the provider, the workflow
service and the evaluator call are stand-ins.
"""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from agent_playground.models import GraphExecution
from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_submit import ensure_native_idle
from model_hub.tests import test_experiment_agent_target_admission as agents
from model_hub.tests.test_experiment_run_control import seed
from model_hub.tests.test_experiment_stop_default_path import (
    CANCEL,
    CONFIRMED,
    NOT_REACHED,
    native_world,
    stop,
    stored,
)
from model_hub.tests.test_native_prompt_create_bridge import (  # noqa: F401
    native_fixture_evaluator,
)
from model_hub.tests.test_native_prompt_create_bridge import tenants as tenants
from model_hub.views.eval_runner import EvaluationRunner

# Row execution manages worker connections, as the native bridge tests do.
pytestmark = pytest.mark.django_db(transaction=True)

STOPPED = "Experiment stopped"


@pytest.fixture(autouse=True)
def run_control_off(settings):
    settings.EXPERIMENT_RUN_CONTROL_ENABLED = False


# A repeat Stop ---------------------------------------------------------------


def test_a_repeat_stop_ends_the_runs_a_first_stop_could_not(tenants):
    actor, experiment, context, _ = native_world(tenants)
    # The first Stop could not reach the workflow service: it ends nothing
    # itself, and (here) the workflow's own cleanup never ran.
    stop(actor, experiment, NOT_REACHED)
    assert stored(context["run_id"]).completed_at is None
    with pytest.raises(ValueError):
        ensure_native_idle(experiment)

    result = stop(actor, experiment, CONFIRMED)

    run = stored(context["run_id"])
    assert run.status == "cancelled" and run.completed_at is not None
    assert ensure_native_idle(experiment) is None
    assert result["workflows_cancelled"] == {"main": True, "reruns": 0}


@pytest.mark.parametrize(
    "again",
    [NOT_REACHED, {**CONFIRMED, "reruns_confirmed": False}],
    ids=["service_unreachable", "rerun_not_reached"],
)
def test_a_repeat_stop_ends_nothing_while_a_workflow_is_not_known_stopped(
    tenants, again
):
    actor, experiment, context, _ = native_world(tenants)
    stop(actor, experiment, NOT_REACHED)

    stop(actor, experiment, again)

    assert stored(context["run_id"]).completed_at is None


def test_a_repeat_stop_with_nothing_left_asks_the_service_nothing(tenants):
    actor, experiment, context, _ = native_world(tenants)
    stop(actor, experiment, CONFIRMED)
    assert stored(context["run_id"]).completed_at is not None

    with patch(CANCEL, side_effect=AssertionError("no second cancel")):
        response = actor["client"].post(
            f"/model-hub/experiments/v2/{experiment.id}/stop/", {}, format="json"
        )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["workflows_cancelled"] == {
        "main": False,
        "reruns": 0,
    }


# Queued evaluation rows --------------------------------------------------------


@pytest.fixture
def evaluation(organization, workspace, user):
    experiment, rows, variants = seed(organization, workspace, user)
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )
    _, _, column = variants[0]
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.source_configs = {"experiment_id": str(experiment.id)}
    runner.user_eval_metric_id = uuid4()
    runner.is_only_eval = True
    runner.replace_column_id = None
    runner.column = None
    return experiment, rows[0], column, runner


def evaluate_row(runner, row, column, calls):
    def call(self, row, mappings):
        # The evaluator call: charged when it is prepared.
        calls.append(row.id)
        return {"reason": "Polite."}, CellStatus.PASS.value, "Yes"

    with patch.object(EvaluationRunner, "_process_eval_result", call):
        runner._process_and_create_cell(row, {}, column.dataset, column)
    return Cell.no_workspace_objects.get(column=column, row=row, deleted=False)


def test_a_queued_evaluation_row_after_the_stop_is_never_charged(evaluation):
    experiment, row, column, runner = evaluation
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.CANCELLED.value
    )
    calls = []

    cell = evaluate_row(runner, row, column, calls)

    assert calls == []
    assert (cell.status, cell.value) == (CellStatus.ERROR.value, STOPPED)
    assert json.loads(cell.value_infos) == {"reason": STOPPED}


def test_a_queued_evaluation_row_of_a_running_experiment_runs(evaluation):
    _, row, column, runner = evaluation
    calls = []

    cell = evaluate_row(runner, row, column, calls)

    assert calls == [row.id]
    assert (cell.status, cell.value) == (CellStatus.PASS.value, "Yes")


# Agent rows of older experiments -------------------------------------------------


def test_an_agent_row_is_not_prepared_after_the_stop(tenants):
    from model_hub.tests.test_experiment_target_kind import worker_args
    from tfc.temporal.experiments.activities import (
        _prepare_agent_row_sync,
        _setup_agent_sync,
    )

    agent = agents.stored_agent(tenants["a"])
    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = _setup_agent_sync(
            str(agent.experiment.id),
            str(agent.experiment.snapshot_dataset_id),
            str(agent.eac.id),
        )
        assert setup["status"] == "READY"
        ExperimentsTable.no_workspace_objects.filter(pk=agent.experiment.pk).update(
            status=StatusType.CANCELLED.value
        )
        graphs = GraphExecution._base_manager.count()

        result = _prepare_agent_row_sync(*worker_args(agent))

    assert result["status"] == "FAILED"
    assert result["error"] == STOPPED
    assert result["graph_execution_id"] == "" and result["input_payload"] == {}
    # No graph was started for it.
    assert GraphExecution._base_manager.count() == graphs
