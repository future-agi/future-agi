"""Rerunning experiment outputs also resets the evaluations of those outputs.

``ExperimentRerunCellsV2View.post`` with ``source_ids`` (the experiment tables
whose outputs are rerun) sets the tables' output columns and their evaluation
columns to RUNNING and resets their cells before it starts the rerun workflow.
A table's evaluation columns carry one of two source ids:

* ``<table id>-<output column id>-sourceid-<metric id>``: one column per
  evaluated output column, written by ``EvaluationRunner._get_column_config``
  and ``CompositeEvaluationRunner.build_column_config``;
* ``<table id>-sourceid-<metric id>``: tables from earlier releases and
  evaluation tag columns.
"""

import uuid
from unittest.mock import patch

import pytest

from accounts.models import Organization
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentPromptConfig
from model_hub.tests.test_experiment_dataset_completion_matcher import (
    FORMATS,
    _cell,
    _column,
    _create_run,
    _evaluation,
    _login,
    _seed,
    _snapshot_state,
    _state,
    _table,
    _tenant_b,
    _write_cells,
)
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db

RERUN = "/model-hub/experiments/v2/{}/rerun-cells/"
RESET = (StatusType.RUNNING.value, [CellStatus.RUNNING.value] * 2, ["", ""])


@pytest.fixture
def workflow():
    """The rerun workflow start; everything before it runs for real."""
    with patch(
        "tfc.temporal.experiments.start_rerun_cells_v2_workflow",
        return_value="rerun-test",
    ) as start:
        yield start


@pytest.fixture
def no_model_call():
    """No step of these runs sends a model request."""
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    with patch.object(
        RunPrompt, "litellm_response", side_effect=AssertionError("model call")
    ) as model_call:
        yield model_call


def _prompt(owned):
    return ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=owned.table,
        name=f"Run {owned.table.id}",
        model="gpt-4o-mini",
    )


def _finished(column, rows, status=CellStatus.PASS.value):
    Column.all_objects.filter(id=column.id).update(status=StatusType.COMPLETED.value)
    for row in rows:
        _cell(column, row, status)
    return column


def _seeded_run(organization, workspace):
    seeded = _seed(organization, workspace)
    seeded.prompt = _prompt(seeded.own)
    return seeded


def _after(column):
    """Column status, then the status and value of each cell by row order."""
    clear_workspace_context()
    cells = Cell.all_objects.filter(column_id=column.id).order_by("row__order")
    return (
        Column.all_objects.get(id=column.id).status,
        [cell.status for cell in cells],
        [cell.value for cell in cells],
    )


def _rerun(client, experiment_id, **body):
    clear_workspace_context()
    return client.post(RERUN.format(experiment_id), body, format="json")


@pytest.mark.parametrize("fmt", FORMATS)
def test_rerun_resets_the_evaluations_of_the_rerun_outputs(
    user, organization, workspace, fmt, workflow
):
    seeded = _seeded_run(organization, workspace)
    Column.all_objects.filter(id=seeded.own.output.id).update(
        status=StatusType.COMPLETED.value
    )
    column = _finished(_evaluation(seeded, seeded.own, fmt), seeded.rows)
    client = _login(user, organization, workspace)

    response = _rerun(
        client, seeded.experiment.id, source_ids=[str(seeded.own.table.id)]
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    started = workflow.call_args.kwargs
    assert started["prompt_config_ids"] == [str(seeded.prompt.id)]
    assert started["dataset_id"] == str(seeded.data.id)
    assert _after(seeded.own.output) == RESET
    assert _after(column) == RESET


def test_rerun_of_failed_rows_resets_their_failed_evaluation_cells(
    user, organization, workspace, workflow
):
    seeded = _seeded_run(organization, workspace)
    column = _evaluation(seeded, seeded.own, "evaluation")
    Column.all_objects.filter(id=column.id).update(status=StatusType.COMPLETED.value)
    _cell(column, seeded.rows[0], CellStatus.ERROR.value)
    _cell(column, seeded.rows[1], CellStatus.PASS.value)
    client = _login(user, organization, workspace)

    response = _rerun(
        client,
        seeded.experiment.id,
        source_ids=[str(seeded.own.table.id)],
        failed_only=True,
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    assert workflow.call_args.kwargs["failed_only"] is True
    # The output cells did not fail and are kept.
    assert _after(seeded.own.output)[1:] == (
        [CellStatus.PASS.value] * 2,
        ["Original", "Original"],
    )
    assert _after(column) == (
        StatusType.RUNNING.value,
        [CellStatus.RUNNING.value, CellStatus.PASS.value],
        ["", "Original"],
    )


def test_rerun_resets_the_evaluation_tags_of_the_rerun_outputs(
    user, organization, workspace, workflow
):
    seeded = _seeded_run(organization, workspace)
    tags = _column(
        seeded.data,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        f"{seeded.own.table.id}-sourceid-{seeded.metric.id}",
    )
    _finished(tags, seeded.rows)
    client = _login(user, organization, workspace)

    response = _rerun(
        client, seeded.experiment.id, source_ids=[str(seeded.own.table.id)]
    )

    assert response.status_code == 200, response.content
    assert _after(tags) == RESET


def test_rerun_leaves_other_tables_and_datasets_alone(
    user, organization, workspace, workflow
):
    seeded = _seeded_run(organization, workspace)
    own = _finished(_evaluation(seeded, seeded.own, "evaluation"), seeded.rows)
    # Another table of the experiment, not rerun.
    other = _table(seeded, "f")
    assert str(other.table.id)[0] != str(seeded.own.table.id)[0]
    _prompt(other)
    kept = [
        other.output,
        _finished(_evaluation(seeded, other, "evaluation"), seeded.rows),
        _finished(_evaluation(seeded, other, "earlier"), seeded.rows),
    ]
    # A removed evaluation column of the rerun table.
    removed = _finished(
        _column(
            seeded.data,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            f"{seeded.own.table.id}-{uuid.uuid4()}-sourceid-{seeded.metric.id}",
        ),
        seeded.rows,
    )
    Column.all_objects.filter(id=removed.id).update(deleted=True)
    kept.append(removed)
    # Columns with the rerun table's evaluation id in other datasets.
    tenant_b = Organization.objects.create(name="Rerun tenant B")
    for org, ws in ((organization, workspace), (tenant_b, None)):
        dataset = Dataset.no_workspace_objects.create(
            name="Unrelated", organization=org, workspace=ws
        )
        row = Row.objects.create(dataset=dataset, order=1)
        for source, source_id in (
            (SourceChoices.EXPERIMENT_EVALUATION.value, own.source_id),
            (
                SourceChoices.EXPERIMENT_EVALUATION.value,
                f"{seeded.own.table.id}-sourceid-{seeded.metric.id}",
            ),
            (SourceChoices.EXPERIMENT.value, str(seeded.own.table.id)),
        ):
            stray = _column(dataset, source, source_id)
            _finished(stray, [row])
            kept.append(stray)
    before = _state(kept)
    client = _login(user, organization, workspace)

    response = _rerun(
        client, seeded.experiment.id, source_ids=[str(seeded.own.table.id)]
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    assert workflow.call_args.kwargs["prompt_config_ids"] == [str(seeded.prompt.id)]
    assert _state(kept) == before


def test_other_tenant_cannot_rerun_the_experiment(organization, workspace, workflow):
    seeded = _seeded_run(organization, workspace)
    column = _finished(_evaluation(seeded, seeded.own, "evaluation"), seeded.rows)
    before = _state([seeded.own.output, column])
    tenant_b = _tenant_b()
    client = _login(tenant_b.user, tenant_b.organization, tenant_b.workspace)

    response = _rerun(
        client, seeded.experiment.id, source_ids=[str(seeded.own.table.id)]
    )

    assert response.status_code == 400, response.content
    workflow.assert_not_called()
    assert _state([seeded.own.output, column]) == before


# Two tenants, real create API, the rerun request the experiment page sends.


@pytest.mark.django_db(transaction=True)
def test_created_run_rerun_resets_its_evaluation(
    user, organization, workspace, workflow, no_model_call
):
    tenant_b = _tenant_b()
    run_a = _create_run(user, organization, workspace)
    run_b = _create_run(tenant_b.user, tenant_b.organization, tenant_b.workspace)
    # Tenant B's snapshot carries a finished column with A's evaluation id.
    stray = Column.no_workspace_objects.create(
        name="Copied evaluation",
        data_type="text",
        dataset_id=run_b.snapshot_id,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=run_a.evaluation.source_id,
        status=StatusType.COMPLETED.value,
    )
    _write_cells(run_b, stray, CellStatus.PASS.value, "Copied")
    # A's run finished.
    _write_cells(run_a, run_a.output, CellStatus.PASS.value, "Sunny")
    _write_cells(run_a, run_a.evaluation, CellStatus.PASS.value, "Passed")
    clear_workspace_context()
    Column.no_workspace_objects.filter(
        id__in=[run_a.output.id, run_a.evaluation.id]
    ).update(status=StatusType.COMPLETED.value)
    b_before = _snapshot_state(run_b)
    a_before = _snapshot_state(run_a)

    refused = _rerun(
        run_b.client, run_a.experiment_id, source_ids=[str(run_a.table.id)]
    )

    assert refused.status_code == 400, refused.content
    workflow.assert_not_called()
    assert _snapshot_state(run_a) == a_before

    response = _rerun(
        run_a.client, run_a.experiment_id, source_ids=[str(run_a.table.id)]
    )

    assert response.status_code == 200, response.content
    workflow.assert_called_once()
    started = workflow.call_args.kwargs
    clear_workspace_context()
    record = ExperimentPromptConfig.no_workspace_objects.get(
        experiment_dataset=run_a.table
    )
    assert started["prompt_config_ids"] == [str(record.id)]
    assert started["dataset_id"] == str(run_a.snapshot_id)
    reset = (StatusType.RUNNING.value, [CellStatus.RUNNING.value], [""])
    assert _after(run_a.output) == reset
    assert _after(run_a.evaluation) == reset
    assert _snapshot_state(run_b) == b_before
    no_model_call.assert_not_called()
