import uuid

import pytest
from rest_framework import status


def _assert_field_error(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


@pytest.mark.django_db
class TestDerivedVariableContracts:
    def test_preview_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            "/model-hub/prompt-templates/derived-variables/preview/",
            {
                "content": {"answer": "yes"},
                "column_name": "output",
                "columnName": "legacy camel alias",
            },
            format="json",
        )

        _assert_field_error(response, "columnName")

    def test_preview_rejects_missing_content(self, auth_client):
        response = auth_client.post(
            "/model-hub/prompt-templates/derived-variables/preview/",
            {"column_name": "output"},
            format="json",
        )

        _assert_field_error(response, "content")

    def test_extract_rejects_unknown_request_fields(self, auth_client):
        response = auth_client.post(
            f"/model-hub/prompt-templates/{uuid.uuid4()}/derived-variables/extract/",
            {
                "version": "1",
                "column_name": "output",
                "columnName": "legacy camel alias",
            },
            format="json",
        )

        _assert_field_error(response, "columnName")

    def test_extract_rejects_missing_version(self, auth_client):
        response = auth_client.post(
            f"/model-hub/prompt-templates/{uuid.uuid4()}/derived-variables/extract/",
            {"column_name": "output"},
            format="json",
        )

        _assert_field_error(response, "version")
