import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


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
