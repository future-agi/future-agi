import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def prompt_config():
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Answer {{input}}"}],
        "output_format": "string",
    }


@pytest.mark.django_db
def test_litellm_run_prompt_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/run-prompt/",
        {
            "dataset_id": str(uuid.uuid4()),
            "model": "gpt-4o",
            "name": "prompt-column",
            "messages": [{"role": "user", "content": "Answer {{input}}"}],
            "modelName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "modelName")


@pytest.mark.django_db
def test_add_run_prompt_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/add_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "datasetId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "datasetId")


@pytest.mark.django_db
def test_preview_run_prompt_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/preview_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "first_n_rows": 1,
            "firstNRows": 1,
        },
        format="json",
    )

    assert_unknown_field(response, "firstNRows")


@pytest.mark.django_db
def test_edit_run_prompt_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/develops/edit_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "column_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "columnId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "columnId")


@pytest.mark.django_db
def test_run_prompt_for_rows_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/run-prompt-for-rows/",
        {
            "run_prompt_ids": [str(uuid.uuid4())],
            "row_ids": [str(uuid.uuid4())],
            "selected_all_rows": False,
            "selectedAllRows": True,
        },
        format="json",
    )

    assert_unknown_field(response, "selectedAllRows")
