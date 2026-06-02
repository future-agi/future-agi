import uuid

import pytest
from rest_framework import status


def _assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.mark.django_db
class TestMetricContracts:
    def test_create_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/create/",
            {
                "model_id": str(uuid.uuid4()),
                "name": "Quality metric",
                "prompt": "Check answer quality",
                "metric_type": "boolean",
                "evaluation_type": "llm",
                "datasets": [],
                "modelId": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "modelId")

    def test_edit_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/update/",
            {
                "id": str(uuid.uuid4()),
                "name": "Quality metric",
                "prompt": "Check answer quality",
                "metric_type": "boolean",
                "evaluation_type": "llm",
                "datasets": [],
                "metricType": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "metricType")

    def test_test_metric_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/custom-metric/test/",
            {
                "prompt": "Check answer quality",
                "promptText": "legacy camel alias",
            },
            format="json",
        )

        _assert_unknown_field(response, "promptText")
