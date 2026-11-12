"""An experiment's Stop also stops its reference-column evaluations.

The experiment's Stop (the empty-body whole stop) cancels the experiment.
Its base evaluations (the evaluations of the reference column, run by
activities._run_base_evaluation_sync) used to keep sending batches; every
row was charged and its usage event emitted, and then the result was thrown
away because the experiment was cancelled. Now no new batch is sent once the
experiment is cancelled, and a result whose call was already sent is saved.
Base evaluations of a running experiment are unchanged.

The loop is the real one and the stop goes through the real Stop view (run
control off, the default). The evaluation runner is a stand-in that records
what a row costs where the real runner pays for it: the charge when the call
is prepared (EvaluationRunner._handle_api_call ->
log_and_deduct_cost_for_api_request) and the usage event after the result
(EvaluationRunner._emit_cell_usage), both inside _process_eval_result. The
saved result is checked on the real runner's cell writer.
"""

import json
from types import SimpleNamespace

import pytest

from model_hub.models.choices import (
    CellStatus,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.experiments import ExperimentStopV2View
from tfc.temporal.experiments import activities

pytestmark = pytest.mark.django_db

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"


@pytest.fixture
def world(organization, workspace, user, monkeypatch):
    experiment, _, _ = seed(organization, workspace, user)
    dataset = experiment.snapshot_dataset
    experiment.column = Column.objects.create(
        name="reference",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["column", "status"])
    template = EvalTemplate.no_workspace_objects.create(
        name="tone-check",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "choices"},
        choices=["Yes", "No"],
    )
    metric = UserEvalMetric.objects.create(
        name="tone",
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}},
        user=user,
    )
    state = SimpleNamespace(
        experiment=experiment,
        dataset=dataset,
        rows=[row.id for row in Row.objects.filter(dataset=dataset).order_by("order")],
        metric=metric,
        user=user,
        workspace=workspace,
        charges=[],
        usage_events=[],
        on_send=lambda row_id: None,
        monkeypatch=monkeypatch,
    )

    class StandInRunner:
        """Records each row's charge and usage event, as the real runner pays."""

        def __init__(self, **kwargs):
            self.dataset = None
            self.base_column = None

        def load_user_eval_metric(self):
            return None

        def _get_column_config(self, dataset):
            return {}

        def _create_or_update_column(self, dataset, config, new_column=False):
            return None

        def run_prompt(self, row_ids=None):
            for row_id in row_ids:
                state.charges.append(row_id)
                state.usage_events.append(row_id)
                state.on_send(row_id)

    monkeypatch.setattr("model_hub.views.eval_runner.EvaluationRunner", StandInRunner)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    from tfc.utils.distributed_state import evaluation_tracker

    monkeypatch.setattr(evaluation_tracker, "should_cancel", lambda *_: False)
    return state


def whole_stop(world):
    world.monkeypatch.setattr(
        CANCEL, lambda _id: {"main_cancelled": True, "rerun_cancelled": 0}
    )
    response = call(
        ExperimentStopV2View, "post", world.experiment.id, world.user, world.workspace
    )
    assert response.status_code == 200, response.data
    assert ExperimentsTable.objects.get(id=world.experiment.id).status == (
        StatusType.CANCELLED.value
    )


def run(world, batch_size):
    return activities._run_base_evaluation_sync(
        str(world.experiment.id), str(world.metric.id), batch_size=batch_size
    )


def test_after_the_stop_no_new_batch_is_sent_nor_charged(world):
    def stop_during_the_first_batch(row_id):
        if len(world.charges) == 1:
            whole_stop(world)

    world.on_send = stop_during_the_first_batch

    result = run(world, batch_size=1)

    assert result["status"] == "CANCELLED"
    # The first batch was already sent; nothing after the stop.
    assert world.charges == world.rows[:1]
    assert world.usage_events == world.rows[:1]


def test_a_batch_already_sent_finishes_and_then_nothing_more(world):
    def stop_during_the_first_batch(row_id):
        if len(world.charges) == 1:
            whole_stop(world)

    world.on_send = stop_during_the_first_batch

    result = run(world, batch_size=2)

    assert result["status"] == "CANCELLED"
    assert world.charges == world.rows[:2]
    assert world.usage_events == world.rows[:2]


def test_an_experiment_stopped_before_its_base_evaluations_sends_nothing(world):
    whole_stop(world)

    result = run(world, batch_size=1)

    assert result["status"] == "SKIPPED"
    assert world.charges == []
    assert world.usage_events == []


def test_a_running_experiments_base_evaluations_are_unchanged(world):
    result = run(world, batch_size=1)

    assert result["status"] == "COMPLETED"
    assert world.charges == world.rows
    assert world.usage_events == world.rows


def cell_writer(world, keep_results_after_stop):
    """The real runner's cell writer, as the base evaluation runner has it."""
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.source_configs = {"experiment_id": str(world.experiment.id)}
    runner.user_eval_metric_id = world.metric.id
    runner.is_only_eval = True
    runner.replace_column_id = None
    runner.column = None
    if keep_results_after_stop:
        runner.keep_results_after_stop = True
    return runner


def write_result(world, runner):
    column = Column.objects.create(
        name="tone",
        dataset=world.dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.get(id=world.rows[0])
    runner._create_cell(
        world.dataset, column, row, {"reason": "Polite."}, "Yes", CellStatus.PASS.value
    )
    return Cell.objects.filter(column=column, row=row, deleted=False).first()


def test_a_base_evaluation_result_already_paid_for_is_saved_after_the_stop(world):
    whole_stop(world)

    cell = write_result(world, cell_writer(world, keep_results_after_stop=True))

    assert cell is not None
    assert cell.value == "Yes"
    assert cell.status == CellStatus.PASS.value
    assert json.loads(cell.value_infos) == {"reason": "Polite."}


def test_other_experiment_evaluations_still_discard_after_the_stop(world):
    whole_stop(world)

    cell = write_result(world, cell_writer(world, keep_results_after_stop=False))

    assert cell is None


def test_the_base_evaluation_runner_keeps_results(world):
    seen = {}

    class Recording:
        def __init__(self, **kwargs):
            self.dataset = None
            self.base_column = None

        def load_user_eval_metric(self):
            return None

        def _get_column_config(self, dataset):
            return {}

        def _create_or_update_column(self, dataset, config, new_column=False):
            return None

        def run_prompt(self, row_ids=None):
            seen["keep"] = getattr(self, "keep_results_after_stop", False)

    world.monkeypatch.setattr("model_hub.views.eval_runner.EvaluationRunner", Recording)

    run(world, batch_size=3)

    assert seen["keep"] is True
