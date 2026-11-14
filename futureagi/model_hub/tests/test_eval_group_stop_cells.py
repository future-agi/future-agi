"""Stopping a group evaluation marks its experiment snapshot cells."""

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.eval_group import apply_eval_group_to_experiment


@pytest.mark.django_db
def test_stopping_group_eval_updates_snapshot_cells_only(
    auth_client, organization, workspace, user
):
    dataset = Dataset.objects.create(
        name="Source Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    snapshot = Dataset.objects.create(
        name="Experiment Snapshot",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
    )
    unrelated = Dataset.objects.create(
        name="Unrelated Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    experiment = ExperimentsTable.objects.create(
        name="Group Experiment", dataset=dataset, snapshot_dataset=snapshot
    )
    template = EvalTemplate.objects.create(
        name="Group Template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )
    group = EvalGroup.objects.create(
        name="Group Eval", organization=organization, workspace=workspace
    )
    group.eval_templates.add(template)
    apply_eval_group_to_experiment(
        group, {"experiment_id": experiment.id}, {}, user, [], workspace, {}
    )
    metric = experiment.user_eval_template_ids.get()
    assert metric.dataset_id == dataset.id
    assert str(metric.source_id) == str(experiment.id)

    cells = []
    for target in (snapshot, unrelated):
        eval_column = Column.objects.create(
            name="Evaluation",
            dataset=target,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{experiment.id}-sourceid-{metric.id}",
        )
        reason_column = Column.objects.create(
            name="Reason",
            dataset=target,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_column.id}-sourceid-{metric.id}",
        )
        row = Row.objects.create(dataset=target, order=0)
        cells.append(
            [
                Cell.objects.create(
                    dataset=target,
                    row=row,
                    column=column,
                    status=CellStatus.RUNNING.value,
                )
                for column in (eval_column, reason_column)
            ]
        )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/stop_user_eval/{metric.id}/",
        {"experiment_id": str(experiment.id)},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    metric.refresh_from_db()
    assert metric.status == StatusType.ERROR.value
    for cell in cells[0]:
        cell.refresh_from_db()
        assert cell.status == CellStatus.ERROR.value
        assert cell.value == "Evaluation stopped by user"
    for cell in cells[1]:
        cell.refresh_from_db()
        assert cell.status == CellStatus.RUNNING.value
