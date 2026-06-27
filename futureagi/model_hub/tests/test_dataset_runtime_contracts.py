import json
import uuid
from unittest.mock import patch

import pytest
from django.http import QueryDict
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from model_hub.constants import (
    MAX_EMPTY_DATASET_ROWS,
    SDK_API_KEY_PLACEHOLDER,
    SDK_SECRET_KEY_PLACEHOLDER,
)
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    CreateEmptyDatasetRequestSerializer,
    DatasetTableQuerySerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DatasetListQuerySerializer,
    DatasetTableResponseSerializer,
)
from model_hub.services.dataset_service import delete_datasets


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def _query_data(values):
    query = QueryDict("", mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


def _post_add_rows_sdk(auth_client, dataset_id):
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        return_value=_SuccessfulResourceCallLog(),
    ):
        return auth_client.post(
            "/model-hub/develops/add_rows_sdk/",
            {"dataset_id": str(dataset_id)},
            format="json",
        )


def _get_knowledge_base_sdk(auth_client, kb_type="create"):
    return auth_client.get(
        "/model-hub/knowledge-base/",
        {"type": kb_type, "name": "SDK Snippet Knowledge Base"},
    )


def test_dataset_list_query_contract_matches_frontend_list_params():
    serializer = DatasetListQuerySerializer(
        data=_query_data(
            {
                "search_text": "eval",
                "page": "0",
                "page_size": "25",
                "sort": '[{"column_id":"number_of_datapoints","type":"descending"}]',
            }
        )
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page"] == 0
    assert serializer.validated_data["page_size"] == 25


def test_dataset_list_query_contract_rejects_unknown_params():
    serializer = DatasetListQuerySerializer(data=_query_data({"pageSize": "25"}))

    assert not serializer.is_valid()
    assert "pageSize" in serializer.errors


def test_dataset_table_query_contract_matches_frontend_grid_params():
    serializer = DatasetTableQuerySerializer(
        data=_query_data(
            {
                "current_page_index": "0",
                "page_size": "100",
                "filters": "[]",
                "sort": '[{"column_id":"score","type":"descending"}]',
                "column_config_only": "true",
            }
        )
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == [
        {"column_id": "score", "type": "descending"}
    ]
    assert serializer.validated_data["column_config_only"] is True


def test_dataset_table_query_contract_rejects_legacy_camel_query_param_keys():
    serializer = DatasetTableQuerySerializer(
        data=_query_data(
            {
                "current_page_index": "0",
                "page_size": "100",
                "filters": "[]",
                "sort": '[{"column_id":"score","type":"descending"}]',
                "columnConfigOnly": "true",
            }
        )
    )

    assert not serializer.is_valid()
    assert "columnConfigOnly" in serializer.errors


def test_dataset_table_query_contract_rejects_legacy_camel_sort_keys():
    serializer = DatasetTableQuerySerializer(
        data=_query_data(
            {
                "current_page_index": "0",
                "page_size": "100",
                "filters": "[]",
                "sort": '[{"columnId":"score","type":"descending"}]',
                "column_config_only": "true",
            }
        )
    )

    assert not serializer.is_valid()
    assert "sort" in serializer.errors


def test_dataset_table_response_contract_accepts_metadata_status_object():
    serializer = DatasetTableResponseSerializer(
        data={
            "status": True,
            "result": {
                "metadata": {
                    "dataset_name": "QA dataset",
                    "total_rows": 1,
                    "total_pages": 1,
                    "error_messages": [],
                    "status": {"dataset_status": "Completed"},
                },
                "column_config": [],
                "table": [],
                "dataset_config": {},
                "synthetic_dataset": False,
                "synthetic_dataset_percentage": 100.0,
                "synthetic_regenerate": False,
                "is_processing_data": False,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_create_empty_dataset_request_validates_row_bounds():
    at_limit = CreateEmptyDatasetRequestSerializer(
        data={
            "new_dataset_name": "Within Limit",
            "model_type": "generative_llm",
            "row": MAX_EMPTY_DATASET_ROWS,
        }
    )
    over_limit = CreateEmptyDatasetRequestSerializer(
        data={
            "new_dataset_name": "Over Limit",
            "model_type": "generative_llm",
            "row": MAX_EMPTY_DATASET_ROWS + 1,
        }
    )

    assert at_limit.is_valid(), at_limit.errors
    assert at_limit.validated_data["model_type"] == "GenerativeLLM"
    assert not over_limit.is_valid()
    assert "row" in over_limit.errors


def test_create_empty_dataset_request_rejects_unknown_model_type():
    serializer = CreateEmptyDatasetRequestSerializer(
        data={
            "new_dataset_name": "Unknown Model Type",
            "model_type": "not-a-model-type",
        }
    )

    assert not serializer.is_valid()
    assert "model_type" in serializer.errors


@pytest.mark.django_db
def test_create_empty_dataset_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "Strict Contract Dataset",
            "model_type": "generative_llm",
            "newDatasetName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "newDatasetName")


@pytest.mark.django_db
def test_add_synthetic_data_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/develops/{uuid.uuid4()}/add_synthetic_data/",
        {
            "num_rows": 10,
            "columns": [
                {
                    "name": "answer",
                    "data_type": "text",
                    "description": "Answer",
                    "skip": False,
                    "is_new": True,
                    "property": "answer",
                }
            ],
            "dataset": {
                "description": "Dataset",
                "objective": "Generate rows",
                "patterns": [],
            },
            "fill_existing_rows": False,
            "fillExistingRows": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "fillExistingRows")


@pytest.mark.django_db
def test_add_rows_from_existing_dataset_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/develops/{uuid.uuid4()}/add_rows_from_existing_dataset/",
        {
            "source_dataset_id": str(uuid.uuid4()),
            "column_mapping": {str(uuid.uuid4()): str(uuid.uuid4())},
            "sourceDatasetId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "sourceDatasetId")


@pytest.mark.django_db
def test_create_dataset_from_experiment_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/develops/{uuid.uuid4()}/create-dataset/",
        {
            "name": "From Experiment",
            "model_type": "generative_llm",
            "modelType": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "modelType")


@pytest.mark.django_db
def test_get_huggingface_config_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/get-huggingface-dataset-config/",
        {
            "dataset_path": "future-agi/example",
            "datasetPath": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "datasetPath")


@pytest.mark.django_db
def test_create_huggingface_dataset_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/create-dataset-from-huggingface/",
        {
            "name": "HF Dataset",
            "model_type": "generative_llm",
            "num_rows": 10,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
            "huggingfaceDatasetName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "huggingfaceDatasetName")


@pytest.mark.django_db
def test_create_synthetic_dataset_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/create-synthetic-dataset/",
        {
            "num_rows": 10,
            "columns": [
                {
                    "name": "answer",
                    "data_type": "text",
                    "description": "Answer",
                    "property": "answer",
                }
            ],
            "dataset": {
                "name": "Synthetic Dataset",
                "description": "Dataset",
                "objective": "Generate rows",
                "patterns": [],
            },
            "numRows": 10,
        },
        format="json",
    )

    assert_unknown_field(response, "numRows")


@pytest.mark.django_db
def test_update_synthetic_dataset_config_rejects_unknown_request_fields(auth_client):
    response = auth_client.put(
        f"/model-hub/develops/{uuid.uuid4()}/update-synthetic-config/",
        {
            "num_rows": 10,
            "columns": [
                {
                    "name": "answer",
                    "data_type": "text",
                    "description": "Answer",
                    "property": "answer",
                }
            ],
            "dataset": {
                "name": "Synthetic Dataset",
                "description": "Dataset",
                "objective": "Generate rows",
                "patterns": [],
            },
            "regenerate": True,
            "numRows": 10,
        },
        format="json",
    )

    assert_unknown_field(response, "numRows")


@pytest.mark.django_db
def test_add_huggingface_rows_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/develops/{uuid.uuid4()}/add_rows_from_huggingface/",
        {
            "num_rows": 10,
            "huggingface_dataset_name": "future-agi/example",
            "huggingface_dataset_config": "default",
            "huggingface_dataset_split": "train",
            "huggingfaceDatasetName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "huggingfaceDatasetName")


@pytest.mark.django_db
def test_add_rows_sdk_uses_placeholders_without_creating_user_key(
    auth_client, user, workspace
):
    dataset = Dataset.objects.create(
        name="SDK Snippet Placeholder Dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).delete()

    response = _post_add_rows_sdk(auth_client, dataset.id)

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    response_text = json.dumps(response.json())
    assert result["api_keys"] == {
        "api_key": SDK_API_KEY_PLACEHOLDER,
        "secret_key": SDK_SECRET_KEY_PLACEHOLDER,
    }
    assert SDK_API_KEY_PLACEHOLDER in response_text
    assert SDK_SECRET_KEY_PLACEHOLDER in response_text
    assert (
        OrgApiKey.no_workspace_objects.filter(
            organization=user.organization,
            type="user",
            user=user,
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_add_rows_sdk_does_not_expose_existing_user_key(auth_client, user, workspace):
    dataset = Dataset.objects.create(
        name="SDK Snippet Existing Key Dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    raw_api_key = "raw-sdk-api-key"
    raw_secret_key = "raw-sdk-secret-key"
    OrgApiKey.no_workspace_objects.create(
        organization=user.organization,
        type="user",
        enabled=True,
        user=user,
        api_key=raw_api_key,
        secret_key=raw_secret_key,
    )

    response = _post_add_rows_sdk(auth_client, dataset.id)

    assert response.status_code == status.HTTP_200_OK
    response_text = json.dumps(response.json())
    assert raw_api_key not in response_text
    assert raw_secret_key not in response_text
    assert SDK_API_KEY_PLACEHOLDER in response_text
    assert SDK_SECRET_KEY_PLACEHOLDER in response_text


@pytest.mark.django_db
def test_add_rows_sdk_rejects_dataset_from_another_organization(auth_client, user):
    other_org = Organization.objects.create(name="Other SDK Snippet Org")
    dataset = Dataset.objects.create(
        name="Other Org SDK Snippet Dataset",
        organization=other_org,
        column_order=[],
        column_config={},
    )

    response = _post_add_rows_sdk(auth_client, dataset.id)

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_knowledge_base_sdk_uses_placeholders_without_creating_user_key(
    auth_client, user
):
    OrgApiKey.no_workspace_objects.filter(
        organization=user.organization,
        type="user",
        user=user,
    ).delete()

    response = _get_knowledge_base_sdk(auth_client)

    assert response.status_code == status.HTTP_200_OK
    code = response.json()["result"]["code"]
    assert SDK_API_KEY_PLACEHOLDER in code
    assert SDK_SECRET_KEY_PLACEHOLDER in code
    assert (
        OrgApiKey.no_workspace_objects.filter(
            organization=user.organization,
            type="user",
            user=user,
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_knowledge_base_sdk_does_not_expose_existing_user_key(auth_client, user):
    raw_api_key = "raw-kb-api-key"
    raw_secret_key = "raw-kb-secret-key"
    OrgApiKey.no_workspace_objects.create(
        organization=user.organization,
        type="user",
        enabled=True,
        user=user,
        api_key=raw_api_key,
        secret_key=raw_secret_key,
    )

    for kb_type in ("create", "update"):
        response = _get_knowledge_base_sdk(auth_client, kb_type=kb_type)

        assert response.status_code == status.HTTP_200_OK
        code = response.json()["result"]["code"]
        assert raw_api_key not in code
        assert raw_secret_key not in code
        assert SDK_API_KEY_PLACEHOLDER in code
        assert SDK_SECRET_KEY_PLACEHOLDER in code


@pytest.mark.django_db
def test_delete_datasets_service_sets_deleted_at():
    organization = Organization.objects.create(name="Delete Dataset Service Org")
    dataset = Dataset.objects.create(
        name="Delete Dataset Service Contract",
        organization=organization,
        column_order=[],
        column_config={},
    )

    result = delete_datasets(dataset_ids=[str(dataset.id)], organization=organization)

    assert result["deleted"] == 1
    dataset.refresh_from_db()
    assert dataset.deleted is True
    assert dataset.deleted_at is not None


@pytest.mark.django_db
def test_delete_dataset_api_sets_deleted_at():
    organization = Organization.objects.create(name="Delete Dataset API Org")
    user = User.objects.create_user(
        email="delete-dataset-api@example.com",
        password="testpassword123",
        name="Delete Dataset API User",
        organization=organization,
    )
    dataset = Dataset.objects.create(
        name="Delete Dataset API Contract",
        organization=organization,
        user=user,
        column_order=[],
        column_config={},
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete(
        "/model-hub/develops/delete_dataset/",
        {"dataset_ids": [str(dataset.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset.refresh_from_db()
    assert dataset.deleted is True
    assert dataset.deleted_at is not None


@pytest.mark.django_db
def test_delete_column_api_sets_deleted_at_on_column_and_cells():
    organization = Organization.objects.create(name="Delete Column API Org")
    user = User.objects.create_user(
        email="delete-column-api@example.com",
        password="testpassword123",
        name="Delete Column API User",
        organization=organization,
    )
    dataset = Dataset.objects.create(
        name="Delete Column API Contract",
        organization=organization,
        user=user,
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        name="temp column",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(dataset=dataset, column=column, row=row, value="value")
    dataset.column_order = [str(column.id)]
    dataset.column_config = {str(column.id): {"is_visible": True}}
    dataset.save(update_fields=["column_order", "column_config"])
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete(
        f"/model-hub/develops/{dataset.id}/delete_column/{column.id}/",
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    column.refresh_from_db()
    cell.refresh_from_db()
    dataset.refresh_from_db()
    assert column.deleted is True
    assert column.deleted_at is not None
    assert cell.deleted is True
    assert cell.deleted_at is not None
    assert str(column.id) not in dataset.column_order
    assert str(column.id) not in dataset.column_config


@pytest.mark.django_db
def test_delete_row_api_sets_deleted_at_on_row_and_cells():
    organization = Organization.objects.create(name="Delete Row API Org")
    user = User.objects.create_user(
        email="delete-row-api@example.com",
        password="testpassword123",
        name="Delete Row API User",
        organization=organization,
    )
    dataset = Dataset.objects.create(
        name="Delete Row API Contract",
        organization=organization,
        user=user,
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        name="temp column",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(dataset=dataset, column=column, row=row, value="value")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete(
        f"/model-hub/develops/{dataset.id}/delete_row/",
        {"row_ids": [str(row.id)], "selected_all_rows": False},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    row.refresh_from_db()
    cell.refresh_from_db()
    assert row.deleted is True
    assert row.deleted_at is not None
    assert cell.deleted is True
    assert cell.deleted_at is not None


@pytest.mark.django_db
def test_update_column_type_falls_back_to_sync_conversion_when_dispatch_fails():
    organization = Organization.objects.create(name="Update Column Type API Org")
    user = User.objects.create_user(
        email="update-column-type-api@example.com",
        password="testpassword123",
        name="Update Column Type API User",
        organization=organization,
    )
    dataset = Dataset.objects.create(
        name="Update Column Type API Contract",
        organization=organization,
        user=user,
        column_order=[],
        column_config={},
    )
    column = Column.objects.create(
        name="numeric text",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(dataset=dataset, column=column, row=row, value="42")
    client = APIClient()
    client.force_authenticate(user=user)

    with patch(
        "model_hub.views.develop_dataset.perform_conversion.apply_async",
        side_effect=RuntimeError("Temporal unavailable"),
    ):
        response = client.put(
            f"/model-hub/develops/{dataset.id}/update_column_type/{column.id}/",
            {
                "new_column_type": DataTypeChoices.INTEGER.value,
                "preview": False,
                "force_update": True,
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["status"] == "Completed"
    assert result["new_data_type"] == DataTypeChoices.INTEGER.value
    column.refresh_from_db()
    cell.refresh_from_db()
    assert column.data_type == DataTypeChoices.INTEGER.value
    assert column.status == "Completed"
    assert cell.status == "pass"
    assert cell.value == "42"
