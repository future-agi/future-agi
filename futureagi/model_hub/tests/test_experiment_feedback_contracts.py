import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


@pytest.mark.django_db
def test_experiment_feedback_create_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/experiments/v2/{uuid.uuid4()}/feedback/",
        {
            "source_id": str(uuid.uuid4()),
            "source": "experiment",
            "value": "Passed",
            "row_id": str(uuid.uuid4()),
            "sourceId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "sourceId")


@pytest.mark.django_db
def test_experiment_feedback_submit_rejects_unknown_request_fields(auth_client):
    response = auth_client.post(
        f"/model-hub/experiments/v2/{uuid.uuid4()}/feedback/submit-feedback/",
        {
            "action_type": "recalculate_row",
            "feedback_id": str(uuid.uuid4()),
            "user_eval_metric_id": str(uuid.uuid4()),
            "value": "Passed",
            "feedbackId": "legacy camel alias",
        },
        format="json",
    )

    assert_unknown_field(response, "feedbackId")
