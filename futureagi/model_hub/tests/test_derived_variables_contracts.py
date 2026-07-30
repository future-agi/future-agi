import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion


def _assert_field_error(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field_name in response.json()["details"]


def _create_prompt_template(organization, workspace, user, name):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        output=[
            {
                "customer": {"name": "Ada", "tier": "enterprise"},
                "score": 98,
            }
        ],
        metadata={
            "derived_variables": {
                "answer": {
                    "paths": ["customer.name", "score"],
                    "schema": {
                        "customer.name": {"type": "string", "sample": "Ada"},
                        "score": {"type": "number", "sample": 98},
                    },
                    "full_variables": ["answer.customer.name", "answer.score"],
                    "raw_sample": {"customer": {"name": "Ada"}, "score": 98},
                    "is_json": True,
                },
                "plain_text": {
                    "paths": [],
                    "schema": {},
                    "full_variables": [],
                    "is_json": False,
                },
            }
        },
    )
    return template, version


@pytest.mark.django_db
class TestDerivedVariableContracts:
    def test_preview_extracts_nested_json_without_saving(self, auth_client):
        response = auth_client.post(
            "/model-hub/prompt-templates/derived-variables/preview/",
            {
                "content": {
                    "customer": {"name": "Ada", "active": True},
                    "score": 98,
                },
                "column_name": "preview",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "preview.customer.name" in result["full_variables"]
        assert result["schema"]["customer.active"]["type"] == "boolean"
        assert result["schema"]["score"]["type"] == "number"

    def test_prompt_derived_variable_routes_scope_prompt_versions_to_workspace(
        self, auth_client, organization, workspace, user
    ):
        active_template, active_version = _create_prompt_template(
            organization,
            workspace,
            user,
            "Derived variables active prompt",
        )
        other_workspace = Workspace.objects.create(
            name="Derived variables other workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_template, hidden_version = _create_prompt_template(
            organization,
            other_workspace,
            user,
            "Derived variables hidden prompt",
        )
        hidden_original_metadata = hidden_version.metadata

        list_response = auth_client.get(
            f"/model-hub/prompt-templates/{active_template.id}/derived-variables/",
            {"version": "v1", "column_name": "answer"},
        )
        assert list_response.status_code == status.HTTP_200_OK
        list_result = list_response.json()["result"]
        assert list_result["version"] == "v1"
        assert list_result["derived_variables"] == {
            "answer": ["answer.customer.name", "answer.score"]
        }

        schema_response = auth_client.get(
            f"/model-hub/prompt-templates/{active_template.id}/"
            "derived-variables/answer/schema/",
            {"version": "v1"},
        )
        assert schema_response.status_code == status.HTTP_200_OK
        schema_result = schema_response.json()["result"]
        assert schema_result["paths"] == ["customer.name", "score"]
        assert schema_result["schema"]["customer.name"]["sample"] == "Ada"

        extract_response = auth_client.post(
            f"/model-hub/prompt-templates/{active_template.id}/"
            "derived-variables/extract/",
            {
                "version": "v1",
                "column_name": "extracted",
                "output_index": 0,
                "response_format_type": "json_object",
            },
            format="json",
        )
        assert extract_response.status_code == status.HTTP_200_OK
        extract_result = extract_response.json()["result"]
        assert "extracted.customer.name" in extract_result["full_variables"]

        active_version.refresh_from_db()
        assert (
            active_version.metadata["derived_variables"]["extracted"]["schema"][
                "score"
            ]["type"]
            == "number"
        )

        hidden_list_response = auth_client.get(
            f"/model-hub/prompt-templates/{hidden_template.id}/derived-variables/"
        )
        assert hidden_list_response.status_code == status.HTTP_404_NOT_FOUND

        hidden_schema_response = auth_client.get(
            f"/model-hub/prompt-templates/{hidden_template.id}/"
            "derived-variables/answer/schema/"
        )
        assert hidden_schema_response.status_code == status.HTTP_404_NOT_FOUND

        hidden_extract_response = auth_client.post(
            f"/model-hub/prompt-templates/{hidden_template.id}/"
            "derived-variables/extract/",
            {
                "version": "v1",
                "column_name": "blocked",
                "output_index": 0,
                "response_format_type": "json_object",
            },
            format="json",
        )
        assert hidden_extract_response.status_code == status.HTTP_404_NOT_FOUND
        hidden_version.refresh_from_db()
        assert hidden_version.metadata == hidden_original_metadata

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
