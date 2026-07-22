import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric


def _create_dataset_fixture(organization, workspace, user, *, name, value):
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    column = Column.no_workspace_objects.create(
        name=f"{name} column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value=value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order"])
    return dataset, column


def _create_metric(organization, workspace, dataset, column, *, name):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"{name} template",
        organization=organization,
        workspace=workspace,
        config={
            "output": "Pass/Fail",
            "required_keys": ["output"],
            "eval_type_id": "CustomCodeEval",
        },
        criteria="Return pass when the answer is useful.",
        eval_type="code",
    )
    return UserEvalMetric.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        config={"mapping": {"output": str(column.id)}},
        status=StatusType.COMPLETED.value,
        show_in_sidebar=True,
    )


def _same_org_other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        name=f"Other Workspace {uuid.uuid4()}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_column_values_hides_same_org_other_workspace_dataset(
    auth_client,
    organization,
    workspace,
    user,
):
    active_dataset, active_column = _create_dataset_fixture(
        organization,
        workspace,
        user,
        name="active column values dataset",
        value="active workspace value",
    )
    other_workspace = _same_org_other_workspace(organization, user)
    other_dataset, other_column = _create_dataset_fixture(
        organization,
        other_workspace,
        user,
        name="other column values dataset",
        value="other workspace value",
    )

    active_response = auth_client.post(
        "/model-hub/get-column-values/",
        {
            "dataset_id": str(active_dataset.id),
            "column_placeholders": {"input": str(active_column.id)},
        },
        format="json",
    )

    assert active_response.status_code == status.HTTP_200_OK
    values = active_response.json()["result"]["result"]["input"]["values"]
    assert values == ["active workspace value"]

    other_response = auth_client.post(
        "/model-hub/get-column-values/",
        {
            "dataset_id": str(other_dataset.id),
            "column_placeholders": {"input": str(other_column.id)},
        },
        format="json",
    )

    assert other_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_metrics_by_column_hides_same_org_other_workspace_metrics(
    auth_client,
    organization,
    workspace,
    user,
):
    active_dataset, active_column = _create_dataset_fixture(
        organization,
        workspace,
        user,
        name="active metrics column dataset",
        value="active metric value",
    )
    active_metric = _create_metric(
        organization,
        workspace,
        active_dataset,
        active_column,
        name="active metrics by column",
    )

    other_workspace = _same_org_other_workspace(organization, user)
    other_dataset, other_column = _create_dataset_fixture(
        organization,
        other_workspace,
        user,
        name="other metrics column dataset",
        value="other metric value",
    )
    other_metric = _create_metric(
        organization,
        other_workspace,
        other_dataset,
        other_column,
        name="other metrics by column",
    )

    active_response = auth_client.get(
        "/model-hub/metrics/by-column/",
        {"column_id": str(active_column.id)},
    )
    assert active_response.status_code == status.HTTP_200_OK
    active_ids = {row["id"] for row in active_response.json()["result"]}
    assert str(active_metric.id) in active_ids
    assert str(other_metric.id) not in active_ids

    other_response = auth_client.get(
        "/model-hub/metrics/by-column/",
        {"column_id": str(other_column.id)},
    )
    assert other_response.status_code == status.HTTP_200_OK
    assert other_response.json()["result"] == []
