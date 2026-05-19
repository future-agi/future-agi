import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.mark.django_db
def test_optimisation_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        "/model-hub/optimisation/create/",
        {
            "name": "Optimise prompt",
            "dataset_id": str(uuid.uuid4()),
            "messages": [{"role": "user", "content": "Answer"}],
            "user_eval_template_ids": [],
            "model_config": {},
            "optimize_type": "prompt",
            "datasetId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "datasetId")


@pytest.mark.django_db
def test_optimisation_update_rejects_unknown_request_fields(auth_client):
    response = auth_client.put(
        f"/model-hub/optimisation/update/{uuid.uuid4()}/",
        {"datasetId": "legacy camel alias"},
        format="json",
    )

    assert_unknown_field(response, "datasetId")
