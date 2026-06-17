import pytest
from rest_framework import status


def _assert_field_error(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.mark.django_db
class TestAIEvalWriterContracts:
    def test_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "prompt",
                "outputFormat": "legacy camel alias",
            },
            format="json",
        )

        _assert_field_error(response, "outputFormat")

    def test_rejects_invalid_output_format_before_model_call(self, auth_client):
        response = auth_client.post(
            "/model-hub/ai-eval-writer/",
            {
                "description": "Check whether the response is helpful",
                "output_format": "json",
            },
            format="json",
        )

        _assert_field_error(response, "output_format")
