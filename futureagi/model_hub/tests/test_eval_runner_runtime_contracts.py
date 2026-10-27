import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.mark.django_db
def test_custom_eval_template_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/create_custom_evals/",
        {
            "name": "custom-eval-contract",
            "criteria": "Grade {{response}}",
            "required_keys": ["response"],
            "config": {"model": "turing_large"},
            "requiredKeys": ["legacy"],
        },
        format="json",
    )

    assert_unknown_field(response, "requiredKeys")


@pytest.mark.django_db
def test_eval_template_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/eval-template/create/",
        {
            "name": "system-eval",
            "owner": "system",
            "config": {"output": "Pass/Fail"},
            "eval_tags": ["quality"],
            "evalTags": ["legacy"],
        },
        format="json",
    )

    assert_unknown_field(response, "evalTags")


@pytest.mark.django_db
def test_eval_user_template_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/eval-user-template/create/",
        {
            "name": "user-eval",
            "template_id": str(uuid.uuid4()),
            "dataset_id": str(uuid.uuid4()),
            "config": {"mapping": {}},
            "templateId": "legacy",
        },
        format="json",
    )

    assert_unknown_field(response, "templateId")
