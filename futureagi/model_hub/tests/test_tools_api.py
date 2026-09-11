import json

import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.openai_tools import Tools


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


def _rows(response):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]
    return []


def _tool_config(property_type="string"):
    return {
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": property_type}},
            "required": ["value"],
        }
    }


@pytest.mark.django_db
def test_tool_lifecycle_scopes_workspace_parses_configs_and_soft_deletes(
    auth_client, organization, workspace, user
):
    hidden_tool = Tools.no_workspace_objects.create(
        name="Workspace local tool",
        description="same name in another workspace should be allowed",
        config=_tool_config(),
        config_type="json",
        organization=organization,
        workspace=workspace,
    )
    other_workspace = Workspace.objects.create(
        name="Tools Other Workspace",
        organization=organization,
        created_by=user,
    )

    auth_client.set_workspace(other_workspace)
    create_response = auth_client.post(
        "/model-hub/tools/",
        {
            "name": "Workspace local tool",
            "description": "active workspace tool",
            "config": json.dumps(_tool_config()),
            "config_type": "json",
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_201_CREATED
    created = _result(create_response)
    tool_id = created["id"]
    assert created["organization"] == str(organization.id)
    assert created["config"] == _tool_config()

    tool = Tools.no_workspace_objects.get(id=tool_id)
    assert tool.organization == organization
    assert tool.workspace == other_workspace
    assert tool.config == _tool_config()

    list_response = auth_client.get("/model-hub/tools/")
    assert list_response.status_code == http_status.HTTP_200_OK
    list_ids = {row["id"] for row in _rows(list_response)}
    assert tool_id in list_ids
    assert str(hidden_tool.id) not in list_ids

    detail_response = auth_client.get(f"/model-hub/tools/{tool_id}/")
    assert detail_response.status_code == http_status.HTTP_200_OK
    assert detail_response.json()["id"] == tool_id

    hidden_detail_response = auth_client.get(f"/model-hub/tools/{hidden_tool.id}/")
    assert hidden_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    duplicate_response = auth_client.post(
        "/model-hub/tools/",
        {
            "name": "Workspace local tool",
            "description": "duplicate active workspace tool",
            "config": _tool_config(),
            "config_type": "json",
        },
        format="json",
    )
    assert duplicate_response.status_code == http_status.HTTP_400_BAD_REQUEST

    patch_response = auth_client.patch(
        f"/model-hub/tools/{tool_id}/",
        {"description": "metadata-only patch"},
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    patched = _result(patch_response)
    assert patched["description"] == "metadata-only patch"
    tool.refresh_from_db()
    assert tool.description == "metadata-only patch"
    assert tool.workspace == other_workspace
    assert tool.config == _tool_config()

    yaml_response = auth_client.patch(
        f"/model-hub/tools/{tool_id}/",
        {
            "config_type": "yaml",
            "config": """
parameters:
  type: object
  properties:
    value:
      type: number
  required:
    - value
""",
        },
        format="json",
    )
    assert yaml_response.status_code == http_status.HTTP_200_OK
    yaml_patched = _result(yaml_response)
    assert yaml_patched["config"] == _tool_config(property_type="number")
    tool.refresh_from_db()
    assert tool.config == _tool_config(property_type="number")
    assert tool.config_type == "yaml"
    assert tool.workspace == other_workspace

    replace_response = auth_client.put(
        f"/model-hub/tools/{tool_id}/",
        {
            "name": "Workspace local tool final",
            "description": "final tool",
            "config": _tool_config(property_type="boolean"),
            "config_type": "json",
        },
        format="json",
    )
    assert replace_response.status_code == http_status.HTTP_200_OK
    replaced = _result(replace_response)
    assert replaced["name"] == "Workspace local tool final"
    assert replaced["config"] == _tool_config(property_type="boolean")
    tool.refresh_from_db()
    assert tool.name == "Workspace local tool final"
    assert tool.config_type == "json"
    assert tool.workspace == other_workspace

    delete_response = auth_client.delete(f"/model-hub/tools/{tool_id}/")
    assert delete_response.status_code == http_status.HTTP_204_NO_CONTENT
    tool.refresh_from_db()
    assert tool.deleted is True
    assert tool.deleted_at is not None

    deleted_detail_response = auth_client.get(f"/model-hub/tools/{tool_id}/")
    assert deleted_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    hidden_tool.refresh_from_db()
    assert hidden_tool.deleted is False
    assert hidden_tool.workspace == workspace
