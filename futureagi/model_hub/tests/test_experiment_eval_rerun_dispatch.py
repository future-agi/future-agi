"""Eval-only mutations queue evaluations without restarting prompt generation."""

import json
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus, OwnerChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def experiment(organization, workspace, user, prompt_template, prompt_version):
    dataset = Dataset.objects.create(
        name="Source", organization=organization, workspace=workspace, user=user
    )
    snapshot = Dataset.objects.create(
        name="Snapshot",
        organization=organization,
        workspace=workspace,
        source="experiment_snapshot",
    )
    column = Column.objects.create(
        dataset=snapshot,
        name="query",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    for order in range(2):
        Row.objects.create(dataset=snapshot, order=order)
    experiment = ExperimentsTable.objects.create(
        name="Dispatch experiment",
        dataset=dataset,
        snapshot_dataset=snapshot,
        column=column,
        user=user,
        experiment_type="llm",
        status=StatusType.COMPLETED.value,
    )
    edt = ExperimentDatasetTable.objects.create(name="Prompt", experiment=experiment)
    ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        name="Prompt",
        model="fast-model",
        model_config={},
        model_params={},
        configuration={},
    )
    output = Column.objects.create(
        dataset=snapshot,
        name="Prompt output",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(edt.id),
        data_type="text",
    )
    edt.columns.add(output)
    return experiment


@pytest.fixture
def eval_template():
    return EvalTemplate.no_workspace_objects.create(
        name="Dispatch template",
        organization=None,
        owner=OwnerChoices.SYSTEM.value,
        config={
            "required_keys": ["output"],
            "output": "Pass/Fail",
            "eval_type_id": "AgentEvaluator",
            "config": {},
        },
        choices=["Passed", "Failed"],
        output_type_normalized="pass_fail",
        visible_ui=True,
    )


def _eval_entry(template):
    return {
        "name": "Added eval",
        "template_id": str(template.id),
        "model": "",
        "config": {"mapping": {"output": "output"}, "config": {}},
    }


@pytest.fixture
def workflow_starts():
    with (
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as main,
        patch(
            "tfc.temporal.experiments.start_experiment_eval_rerun_workflow",
            create=True,
            return_value="queued-eval-workflow",
        ) as eval_rerun,
    ):
        yield main, eval_rerun


def _assert_eval_dispatch(experiment, metric_id, workflow_starts):
    main, eval_rerun = workflow_starts
    eval_rerun.assert_called_once_with(
        experiment_id=str(experiment.id),
        dataset_id=str(experiment.snapshot_dataset_id),
        eval_template_ids=[str(metric_id)],
    )
    main.assert_not_called()


def test_put_only_adds_eval(auth_client, experiment, eval_template, workflow_starts):
    response = auth_client.put(
        f"/model-hub/experiments/v2/{experiment.id}/",
        {"user_eval_metrics": [_eval_entry(eval_template)]},
        format="json",
    )
    assert response.status_code == 200, response.data
    metric = experiment.user_eval_template_ids.get()
    _assert_eval_dispatch(experiment, metric.id, workflow_starts)


def test_put_prompt_change_still_restarts(auth_client, experiment, workflow_starts):
    epc = ExperimentPromptConfig.objects.get(experiment_dataset__experiment=experiment)
    response = auth_client.put(
        f"/model-hub/experiments/v2/{experiment.id}/",
        {
            "prompt_config": [
                {
                    "id": str(epc.id),
                    "prompt_id": str(epc.prompt_template_id),
                    "prompt_version": str(epc.prompt_version_id),
                    "model": epc.model,
                    "configuration": {"temperature": 0.7},
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    main, eval_rerun = workflow_starts
    main.assert_called_once_with(
        experiment_id=str(experiment.id),
        rerun_prompt_config_ids=[str(epc.id)],
        rerun_agent_config_ids=[],
        rerun_eval_template_ids=[],
        column_changed=False,
    )
    eval_rerun.assert_not_called()


def test_add_eval_run_queues_eval(
    auth_client, experiment, eval_template, workflow_starts
):
    response = auth_client.post(
        f"/model-hub/experiments/{experiment.id}/add-eval/",
        {**_eval_entry(eval_template), "run": True},
        format="json",
    )
    assert response.status_code == 200, response.data
    metric = experiment.user_eval_template_ids.get()
    _assert_eval_dispatch(experiment, metric.id, workflow_starts)


def test_run_additional_evaluations_queues_eval(
    auth_client,
    experiment,
    eval_template,
    workflow_starts,
    organization,
    workspace,
    user,
):
    metric = UserEvalMetric.objects.create(
        name="Existing eval",
        dataset=experiment.dataset,
        template=eval_template,
        organization=organization,
        workspace=workspace,
        user=user,
        config=_eval_entry(eval_template)["config"],
    )
    response = auth_client.post(
        f"/model-hub/experiments/{experiment.id}/run-evaluations/",
        {"eval_template_ids": [str(metric.id)]},
        format="json",
    )
    assert response.status_code == 200, response.data
    _assert_eval_dispatch(experiment, metric.id, workflow_starts)


def test_queue_eval_only_rerun_marks_snapshot_cells_running(
    experiment, eval_template, workflow_starts, organization, workspace, user
):
    from model_hub.services import experiment_utils

    queue = getattr(experiment_utils, "queue_eval_only_rerun", None)
    assert queue is not None, "queue_eval_only_rerun is missing"
    snapshot = experiment.snapshot_dataset
    metric = UserEvalMetric.objects.create(
        name="Placeholder eval",
        dataset=snapshot,
        template=eval_template,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    per_edt = Column.objects.create(
        dataset=snapshot,
        name="Per-prompt eval",
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"edt-sourceid-{metric.id}",
    )
    base = Column.objects.create(
        dataset=snapshot,
        name="Base eval",
        data_type="text",
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    deleted_column = Column.objects.create(
        dataset=snapshot,
        name="Deleted eval",
        data_type="text",
        deleted=True,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    deleted_row = Row.objects.create(dataset=snapshot, order=2, deleted=True)
    rows = list(Row.objects.filter(dataset=snapshot, deleted=False))
    Cell.objects.create(
        dataset=snapshot,
        column=per_edt,
        row=rows[0],
        value="old result",
        status=CellStatus.PASS.value,
        value_infos=json.dumps({"reason": "old reason"}),
    )
    workflow_id = queue(experiment, [metric.id])
    assert workflow_id == "queued-eval-workflow"
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value
    cells = list(Cell.objects.filter(dataset=snapshot, column__in=[per_edt, base]))
    assert {(cell.row_id, cell.column_id) for cell in cells} == {
        (row.id, column.id) for row in rows for column in [per_edt, base]
    }
    for cell in cells:
        assert cell.status == CellStatus.RUNNING.value
        assert cell.value == ""
        assert json.loads(cell.value_infos) == {}
    assert not Cell.objects.filter(column=deleted_column).exists()
    assert not Cell.objects.filter(row=deleted_row).exists()
    _assert_eval_dispatch(experiment, metric.id, workflow_starts)
