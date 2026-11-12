"""An experiment's Stop also stops its rerun evaluations, and touches only its
own cells.

"Rerun evaluations" with no prompt change hands every row of an output
column to one batch evaluation activity
(activities._process_batch_template_eval_sync), from the experiment run's
eval-only step and from the cell rerun workflow. It checked for a cancelled
experiment only at its start, so after a Stop it sent every row: each row was
charged and its usage event emitted, and the result was thrown away. Now it
sends the rows in batches, sends no new batch once the experiment is
cancelled, marks the rows never sent stopped, and saves a result whose call
was already sent.

The stop marker also stays inside the stopped experiment: when one
evaluation is shared by two runs (two experiments), stopping the first leaves
the second run's running cells running.

The activities are the real ones and the stop goes through the real Stop view
(run control off, the default). The evaluation runner is a stand-in that
records what a row costs where the real runner pays for it (the charge when
the call is prepared and the usage event after the result, both inside
EvaluationRunner._process_eval_result).
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from model_hub.models.choices import (
    CellStatus,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.tests.test_experiment_run_control import call, seed
from model_hub.views.experiments import ExperimentStopV2View
from tfc.temporal.experiments import activities
from tfc.temporal.experiments.types import ProcessBatchEvalInput

pytestmark = pytest.mark.django_db

CANCEL = "tfc.temporal.experiments.cancel_all_experiment_workflows"
WORKFLOWS = Path(activities.__file__).with_name("workflows.py")


def running_cells(dataset, column, rows):
    for row in rows:
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=column,
            value="",
            status=CellStatus.RUNNING.value,
        )


@pytest.fixture
def world(organization, workspace, user, monkeypatch):
    experiment, rows, variants = seed(organization, workspace, user)
    dataset = experiment.snapshot_dataset
    variant, _, output = variants[0]
    output.source = SourceChoices.EXPERIMENT.value
    output.source_id = str(variant.id)
    output.save(update_fields=["source", "source_id"])
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
    # The rerun's result cells, running until each row's result is written.
    rerun_column = Column.objects.create(
        name="tone",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{output.id}-sourceid-{metric.id}",
    )
    running_cells(dataset, rerun_column, rows)
    # A second experiment's run that uses the same evaluation, still running.
    other, other_rows, other_variants = seed(organization, workspace, user)
    other.status = StatusType.RUNNING.value
    other.save(update_fields=["status"])
    _, _, other_output = other_variants[0]
    other_column = Column.objects.create(
        name="tone",
        dataset=other.snapshot_dataset,
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{other_output.id}-sourceid-{metric.id}",
    )
    running_cells(other.snapshot_dataset, other_column, other_rows)

    state = SimpleNamespace(
        experiment=experiment,
        dataset=dataset,
        rows=[row.id for row in rows],
        output=output,
        metric=metric,
        rerun_column=rerun_column,
        other=other,
        other_column=other_column,
        user=user,
        workspace=workspace,
        charges=[],
        usage_events=[],
        keeps=[],
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
            state.keeps.append(getattr(self, "keep_results_after_stop", False))
            for row_id in row_ids:
                state.charges.append(str(row_id))
                state.usage_events.append(str(row_id))
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


def stop_after_first_send(world):
    def stop(row_id):
        if len(world.charges) == 1:
            whole_stop(world)

    world.on_send = stop


def rerun_evaluations(world, rows_per_batch):
    batching = {} if rows_per_batch is None else {"rows_per_batch": rows_per_batch}
    return activities._process_batch_template_eval_sync(
        [str(row_id) for row_id in world.rows],
        str(world.output.id),
        str(world.dataset.id),
        str(world.experiment.id),
        str(world.metric.id),
        **batching,
    )


def statuses(column):
    return {
        str(cell.row_id): cell.status
        for cell in Cell.objects.filter(column=column, deleted=False)
    }


def test_after_the_stop_a_rerun_evaluation_sends_no_new_batch(world):
    stop_after_first_send(world)

    result = rerun_evaluations(world, rows_per_batch=1)

    assert result["status"] == "CANCELLED"
    first = str(world.rows[0])
    assert world.charges == [first]
    assert world.usage_events == [first]
    # Rows never sent are stopped, not left running.
    unsent = statuses(world.rerun_column)
    for row_id in world.rows[1:]:
        assert unsent[str(row_id)] == CellStatus.ERROR.value


def test_a_rerun_batch_already_sent_finishes_and_keeps_its_results(world):
    stop_after_first_send(world)

    result = rerun_evaluations(world, rows_per_batch=2)

    assert result["status"] == "CANCELLED"
    assert world.charges == [str(row_id) for row_id in world.rows[:2]]
    # The runner saves a result whose call was already sent.
    assert world.keeps == [True]


def test_a_rerun_evaluation_of_a_running_experiment_is_unchanged(world):
    result = rerun_evaluations(world, rows_per_batch=2)

    assert result["status"] == "COMPLETED"
    assert world.charges == [str(row_id) for row_id in world.rows]
    assert statuses(world.rerun_column) == {
        str(row_id): CellStatus.RUNNING.value for row_id in world.rows
    }


def test_the_other_callers_still_send_their_rows_in_one_call(world):
    # Per-batch and per-row callers pass no batch size: one runner call, and
    # the runner discards after a stop as before.
    result = rerun_evaluations(world, rows_per_batch=None)

    assert result["status"] == "COMPLETED"
    assert world.keeps == [False]
    assert world.charges == [str(row_id) for row_id in world.rows]


def other_run_is_untouched(world):
    assert set(statuses(world.other_column).values()) == {CellStatus.RUNNING.value}
    world.other.refresh_from_db()
    assert world.other.status == StatusType.RUNNING.value


def test_a_rerun_stop_leaves_the_other_run_of_a_shared_evaluation_running(world):
    stop_after_first_send(world)

    rerun_evaluations(world, rows_per_batch=1)

    # This run's unsent rows are stopped; the other run's are untouched.
    stopped = statuses(world.rerun_column)
    assert stopped[str(world.rows[-1])] == CellStatus.ERROR.value
    other_run_is_untouched(world)


def test_a_base_evaluation_stop_leaves_the_other_run_of_a_shared_evaluation_running(
    world,
):
    stop_after_first_send(world)

    result = activities._run_base_evaluation_sync(
        str(world.experiment.id), str(world.metric.id), batch_size=1
    )

    assert result["status"] == "CANCELLED"
    # This run's own unsent rows are stopped, so a scope that missed this
    # experiment's dataset cannot pass; the other run's are untouched.
    stopped = statuses(world.rerun_column)
    assert stopped[str(world.rows[-1])] == CellStatus.ERROR.value
    other_run_is_untouched(world)


def literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def rerun_eval_inputs():
    """ProcessBatchEvalInput calls in the workflows that pass every row."""
    tree = ast.parse(WORKFLOWS.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", None))
        if name != "ProcessBatchEvalInput":
            continue
        rows = next((k.value for k in node.keywords if k.arg == "row_ids"), None)
        if isinstance(rows, ast.Name) and rows.id in {"edt_row_ids", "row_ids"}:
            found.append({k.arg: literal(k.value) for k in node.keywords})
    return found


def test_both_rerun_evaluation_steps_send_in_batches():
    inputs = rerun_eval_inputs()

    assert len(inputs) == 2
    # The size itself, not only the keyword: it bounds the rows a Stop lets
    # through for each column.
    assert [keywords.get("rows_per_batch") for keywords in inputs] == [10, 10]


def test_the_rerun_activity_passes_its_batch_size_on(monkeypatch):
    # The tests above call the batch function directly. This one runs the
    # activity the workflows schedule: if it stopped passing the batch size
    # on, a rerun would send every row in one call again.
    received = {}

    def batch_function(
        row_ids, column_id, dataset_id, experiment_id, template_id, **kwargs
    ):
        received.update(kwargs)
        return {
            "row_ids": row_ids,
            "column_id": column_id,
            "eval_template_id": template_id,
            "status": "COMPLETED",
        }

    monkeypatch.setattr(activities, "_process_batch_template_eval_sync", batch_function)

    result = asyncio.run(
        ActivityEnvironment().run(
            activities.process_batch_template_eval_activity,
            ProcessBatchEvalInput(
                row_ids=["row-1", "row-2"],
                column_id="column",
                dataset_id="dataset",
                experiment_id="experiment",
                eval_template_id="template",
                rows_per_batch=10,
            ),
        )
    )

    assert result.status == "COMPLETED"
    assert received.get("rows_per_batch") == 10
