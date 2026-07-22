import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.views.dynamic_columns import ExecutePythonCodeView


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.mark.django_db
def test_vector_db_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/add_vector_db_column/",
        {
            "column_id": str(uuid.uuid4()),
            "sub_type": "pinecone",
            "api_key": str(uuid.uuid4()),
            "columnId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "columnId")


@pytest.mark.django_db
def test_extract_json_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/develops/{uuid.uuid4()}/extract-json-column/",
        {
            "column_id": str(uuid.uuid4()),
            "json_key": "payload.answer",
            "jsonKey": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "jsonKey")


@pytest.mark.django_db
def test_classify_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/classify-column/",
        {
            "column_id": str(uuid.uuid4()),
            "labels": ["good", "bad"],
            "language_model_id": "gpt-4o",
            "languageModelId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "languageModelId")


@pytest.mark.django_db
def test_extract_entities_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/extract-entities/",
        {
            "column_id": str(uuid.uuid4()),
            "instruction": "Extract companies",
            "columnId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "columnId")


@pytest.mark.django_db
def test_add_api_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/add-api-column/",
        {
            "column_name": "api_result",
            "config": {
                "url": "https://example.com",
                "method": "GET",
                "output_type": "string",
            },
            "columnName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "columnName")


@pytest.mark.django_db
def test_execute_python_code_rejects_unknown_request_fields(user):
    factory = APIRequestFactory()
    request = factory.post(
        f"/model-hub/datasets/{uuid.uuid4()}/execute-code/",
        {
            "code": "def main(**kwargs): return 'ok'",
            "new_column_name": "python_result",
            "newColumnName": "legacy camel alias",
        },
        format="json",
    )
    force_authenticate(request, user=user)

    response = ExecutePythonCodeView.as_view()(request, dataset_id=uuid.uuid4())

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["details"]["newColumnName"] == ["Unknown field."]


@pytest.mark.django_db
def test_conditional_column_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/conditional-column/",
        {
            "config": [{"type": "else", "value": "fallback"}],
            "new_column_name": "condition_result",
            "newColumnName": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "newColumnName")


@pytest.mark.django_db
def test_rerun_operation_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/columns/{uuid.uuid4()}/rerun-operation/",
        {
            "operation_type": "classify",
            "config": {"labels": ["good", "bad"]},
            "operationType": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "operationType")


@pytest.mark.django_db
def test_preview_dataset_operation_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/datasets/{uuid.uuid4()}/preview/extract_json/",
        {
            "column_id": str(uuid.uuid4()),
            "json_key": "payload.answer",
            "jsonKey": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "jsonKey")
