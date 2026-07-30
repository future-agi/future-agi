import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import SchemaTypeChoices, UserResponseSchema


def _rows(response):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]
    return []


@pytest.mark.django_db
def test_response_schema_crud_scopes_workspace_and_parses_updates(
    auth_client, organization, workspace, user
):
    other_workspace = Workspace.objects.create(
        name="Response Schema Other Workspace",
        organization=organization,
        created_by=user,
    )
    other_workspace_schema = UserResponseSchema.no_workspace_objects.create(
        name="Workspace local schema",
        description="same name in another workspace should be allowed",
        schema={"type": "object", "properties": {"other": {"type": "string"}}},
        schema_type=SchemaTypeChoices.JSON.value,
        organization=organization,
        workspace=workspace,
    )
    hidden_schema = UserResponseSchema.no_workspace_objects.create(
        name="Hidden response schema",
        description="hidden other-workspace schema",
        schema={"type": "object", "properties": {"hidden": {"type": "string"}}},
        schema_type=SchemaTypeChoices.JSON.value,
        organization=organization,
        workspace=workspace,
    )

    auth_client.set_workspace(other_workspace)
    create_response = auth_client.post(
        "/model-hub/response_schema/",
        {
            "name": "Workspace local schema",
            "description": "active workspace response schema",
            "schema_type": SchemaTypeChoices.JSON.value,
            "schema": '{"type":"object","properties":{"answer":{"type":"string"}}}',
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_201_CREATED
    schema_id = create_response.json()["id"]
    created_schema = UserResponseSchema.no_workspace_objects.get(id=schema_id)
    assert created_schema.organization == organization
    assert created_schema.workspace == other_workspace
    assert created_schema.schema == {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
    }

    list_response = auth_client.get("/model-hub/response_schema/")
    assert list_response.status_code == http_status.HTTP_200_OK
    list_ids = {row["id"] for row in _rows(list_response)}
    assert schema_id in list_ids
    assert str(hidden_schema.id) not in list_ids

    detail_response = auth_client.get(f"/model-hub/response_schema/{schema_id}/")
    assert detail_response.status_code == http_status.HTTP_200_OK
    assert detail_response.json()["name"] == "Workspace local schema"

    hidden_detail_response = auth_client.get(
        f"/model-hub/response_schema/{hidden_schema.id}/"
    )
    assert hidden_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    same_workspace_duplicate = auth_client.post(
        "/model-hub/response_schema/",
        {
            "name": "Workspace local schema",
            "description": "duplicate active workspace response schema",
            "schema_type": SchemaTypeChoices.JSON.value,
            "schema": {"type": "object"},
        },
        format="json",
    )
    assert same_workspace_duplicate.status_code == http_status.HTTP_400_BAD_REQUEST

    patch_response = auth_client.patch(
        f"/model-hub/response_schema/{schema_id}/",
        {
            "name": "Workspace local schema patched",
            "schema": "type: object\nproperties:\n  score:\n    type: number\n",
            "schema_type": SchemaTypeChoices.YAML.value,
        },
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    created_schema.refresh_from_db()
    assert created_schema.name == "Workspace local schema patched"
    assert created_schema.workspace == other_workspace
    assert created_schema.schema_type == SchemaTypeChoices.YAML.value
    assert created_schema.schema == {
        "type": "object",
        "properties": {"score": {"type": "number"}},
    }

    replacement_response = auth_client.put(
        f"/model-hub/response_schema/{schema_id}/",
        {
            "name": "Workspace local schema final",
            "description": "final response schema",
            "schema_type": SchemaTypeChoices.JSON.value,
            "schema": '{"type":"object","properties":{"final":{"type":"boolean"}}}',
        },
        format="json",
    )
    assert replacement_response.status_code == http_status.HTTP_200_OK
    created_schema.refresh_from_db()
    assert created_schema.name == "Workspace local schema final"
    assert created_schema.description == "final response schema"
    assert created_schema.workspace == other_workspace
    assert created_schema.schema == {
        "type": "object",
        "properties": {"final": {"type": "boolean"}},
    }

    invalid_json_response = auth_client.post(
        "/model-hub/response_schema/",
        {
            "name": "Invalid response schema",
            "description": "invalid",
            "schema_type": SchemaTypeChoices.JSON.value,
            "schema": "[1,2,3]",
        },
        format="json",
    )
    assert invalid_json_response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert not UserResponseSchema.no_workspace_objects.filter(
        name="Invalid response schema"
    ).exists()

    delete_response = auth_client.delete(f"/model-hub/response_schema/{schema_id}/")
    assert delete_response.status_code == http_status.HTTP_204_NO_CONTENT
    created_schema.refresh_from_db()
    assert created_schema.deleted is True
    assert created_schema.deleted_at is not None

    deleted_detail_response = auth_client.get(
        f"/model-hub/response_schema/{schema_id}/"
    )
    assert deleted_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    other_workspace_schema.refresh_from_db()
    assert other_workspace_schema.deleted is False
    assert other_workspace_schema.workspace == workspace
