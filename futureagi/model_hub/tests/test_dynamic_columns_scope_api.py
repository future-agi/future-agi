import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row


def _create_dataset_fixture(user, workspace, name="Hidden dynamic dataset"):
    dataset = Dataset.no_workspace_objects.create(
        name=name,
        organization=user.organization,
        workspace=workspace,
        user=user,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
    )
    source_column = Column.objects.create(
        name="payload",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    dataset.column_order = [str(source_column.id)]
    dataset.column_config = {
        str(source_column.id): {"is_visible": True, "is_frozen": None}
    }
    dataset.save(update_fields=["column_order", "column_config"])
    row = Row.objects.create(dataset=dataset, order=1)
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=source_column,
        value="{'name': 'Hidden User', 'group': 'secret'}",
    )
    return dataset, source_column


def _other_workspace(user):
    return Workspace.objects.create(
        name=f"Hidden Workspace {uuid.uuid4()}",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _mutation_cases(dataset, column):
    return [
        (
            f"/model-hub/develops/{dataset.id}/extract-json-column/",
            {
                "column_id": str(column.id),
                "json_key": "name",
                "new_column_name": "json_result",
            },
            "json_result",
        ),
        (
            f"/model-hub/datasets/{dataset.id}/add-api-column/",
            {
                "column_name": "api_result",
                "config": {
                    "url": "https://example.com",
                    "method": "GET",
                    "output_type": "string",
                },
            },
            "api_result",
        ),
        (
            f"/model-hub/datasets/{dataset.id}/conditional-column/",
            {
                "new_column_name": "conditional_result",
                "config": [
                    {
                        "branch_type": "else",
                        "condition": "",
                        "branch_node_config": {
                            "type": "static_value",
                            "config": {"value": "fallback"},
                        },
                    }
                ],
            },
            "conditional_result",
        ),
        (
            f"/model-hub/datasets/{dataset.id}/classify-column/",
            {
                "column_id": str(column.id),
                "labels": ["alpha", "beta"],
                "new_column_name": "classification_result",
            },
            "classification_result",
        ),
        (
            f"/model-hub/datasets/{dataset.id}/extract-entities/",
            {
                "column_id": str(column.id),
                "instruction": "Extract names",
                "new_column_name": "entities_result",
            },
            "entities_result",
        ),
        (
            f"/model-hub/datasets/{dataset.id}/add_vector_db_column/",
            {
                "column_id": str(column.id),
                "sub_type": "pinecone",
                "api_key": str(uuid.uuid4()),
                "new_column_name": "vector_result",
            },
            "vector_result",
        ),
    ]


@pytest.mark.django_db
def test_dynamic_column_mutations_reject_other_workspace_before_creating_columns(
    auth_client, user
):
    dataset, column = _create_dataset_fixture(user, _other_workspace(user))

    with (
        patch("model_hub.views.dynamic_columns.extract_json_async.delay") as json_task,
        patch("model_hub.views.dynamic_columns.add_api_column_async.delay") as api_task,
        patch(
            "model_hub.views.dynamic_columns.conditional_column_async.delay"
        ) as conditional_task,
        patch("model_hub.views.dynamic_columns.classify_column_async.delay") as classify_task,
        patch("model_hub.views.dynamic_columns.extract_async.delay") as extract_task,
        patch(
            "model_hub.views.dynamic_columns.add_vector_db_column_async.delay"
        ) as vector_task,
    ):
        for url, payload, new_column_name in _mutation_cases(dataset, column):
            response = auth_client.post(url, payload, format="json")

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert not Column.all_objects.filter(
                dataset=dataset, name=new_column_name
            ).exists()

        for task in (
            json_task,
            api_task,
            conditional_task,
            classify_task,
            extract_task,
            vector_task,
        ):
            task.assert_not_called()


@pytest.mark.django_db
def test_dynamic_preview_rejects_other_workspace_dataset(auth_client, user):
    dataset, column = _create_dataset_fixture(user, _other_workspace(user))

    response = auth_client.post(
        f"/model-hub/datasets/{dataset.id}/preview/extract_json/",
        {"column_id": str(column.id), "json_key": "name"},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_operation_config_and_rerun_reject_other_workspace_column_before_mutation(
    auth_client, user
):
    dataset, column = _create_dataset_fixture(user, _other_workspace(user))
    column.metadata = {
        "labels": ["alpha", "beta"],
        "language_model_id": "gpt-4o",
        "column_id": str(column.id),
        "concurrency": 2,
    }
    column.status = StatusType.COMPLETED.value
    column.save(update_fields=["metadata", "status"])
    Cell.objects.filter(dataset=dataset, column=column).update(
        value="hidden",
        value_infos={"source": "other-workspace"},
        status=CellStatus.PASS.value,
    )

    config_response = auth_client.get(
        f"/model-hub/columns/{column.id}/operation-config/"
    )
    assert config_response.status_code == status.HTTP_400_BAD_REQUEST

    with patch("model_hub.views.dynamic_columns.classify_column_async.delay") as task:
        rerun_response = auth_client.post(
            f"/model-hub/columns/{column.id}/rerun-operation/",
            {"operation_type": "classify"},
            format="json",
        )

    assert rerun_response.status_code == status.HTTP_404_NOT_FOUND
    task.assert_not_called()
    column.refresh_from_db()
    assert column.status == StatusType.COMPLETED.value
    assert list(
        Cell.objects.filter(dataset=dataset, column=column).values_list(
            "value", "value_infos", "status"
        )
    ) == [("hidden", {"source": "other-workspace"}, CellStatus.PASS.value)]
