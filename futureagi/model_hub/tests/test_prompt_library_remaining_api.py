import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.middleware.workspace_context import clear_workspace_context


def _prompt_config(content="Hello {{name}}"):
    return {
        "messages": [{"role": "user", "content": content}],
        "configuration": {
            "model": "gpt-4o-mini",
            "model_detail": {"type": "chat", "model_name": "gpt-4o-mini"},
        },
    }


def _results(response):
    payload = response.json()
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _create_prompt_template(
    *,
    organization,
    workspace,
    user,
    name,
    folder=None,
    version="v1",
    is_default=True,
    is_draft=False,
):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        description=f"{name} description",
        organization=organization,
        workspace=workspace,
        created_by=user,
        prompt_folder=folder,
        variable_names={"name": ["Ada"]},
    )
    version_obj = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version=version,
        prompt_config_snapshot=_prompt_config(f"{name} {{{{name}}}}"),
        variable_names={"name": ["Ada"]},
        output=[{"text": f"{name} output"}],
        is_default=is_default,
        is_draft=is_draft,
        commit_message="Initial commit" if not is_draft else "",
    )
    return template, version_obj


@pytest.mark.django_db
def test_prompt_history_and_execution_scope_to_active_workspace(
    auth_client, organization, workspace, user
):
    active_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt library active folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    active_template, version_v1 = _create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
        folder=active_folder,
        name="Prompt library active template",
        version="v1",
        is_default=False,
    )
    version_v2 = PromptVersion.no_workspace_objects.create(
        original_template=active_template,
        template_version="v2",
        prompt_config_snapshot=_prompt_config("Prompt library active v2 {{name}}"),
        variable_names={"name": ["Grace"]},
        output=[{"text": "v2 output"}],
        is_default=True,
        commit_message="Promoted v2",
    )

    other_workspace = Workspace.objects.create(
        name="Prompt Library Other Workspace",
        organization=organization,
        created_by=user,
    )
    other_template, other_version = _create_prompt_template(
        organization=organization,
        workspace=other_workspace,
        user=user,
        name="Prompt library other workspace template",
    )

    execution_detail = auth_client.get(
        f"/model-hub/prompt-executions/{active_template.id}/"
    )
    assert execution_detail.status_code == http_status.HTTP_200_OK
    assert execution_detail.json()["name"] == "Prompt library active template"

    other_execution_detail = auth_client.get(
        f"/model-hub/prompt-executions/{other_template.id}/"
    )
    assert other_execution_detail.status_code == http_status.HTTP_404_NOT_FOUND

    history_list = auth_client.get(
        "/model-hub/prompt-history-executions/",
        {"page_size": 25, "page": 1},
    )
    assert history_list.status_code == http_status.HTTP_200_OK
    history_ids = {row["id"] for row in _results(history_list)}
    assert str(version_v1.id) in history_ids
    assert str(version_v2.id) in history_ids
    assert str(other_version.id) not in history_ids

    template_history = auth_client.get(
        "/model-hub/prompt-history-executions/",
        {"template_id": str(active_template.id), "page_size": 25, "page": 1},
    )
    assert template_history.status_code == http_status.HTTP_200_OK
    template_history_ids = {row["id"] for row in _results(template_history)}
    assert template_history_ids == {str(version_v1.id), str(version_v2.id)}

    other_template_history = auth_client.get(
        "/model-hub/prompt-history-executions/",
        {"template_id": str(other_template.id), "page_size": 25, "page": 1},
    )
    assert other_template_history.status_code == http_status.HTTP_400_BAD_REQUEST

    history_detail = auth_client.get(
        f"/model-hub/prompt-history-executions/{version_v2.id}/"
    )
    assert history_detail.status_code == http_status.HTTP_200_OK
    assert history_detail.json()["template_version"] == "v2"

    other_history_detail = auth_client.get(
        f"/model-hub/prompt-history-executions/{other_version.id}/"
    )
    assert other_history_detail.status_code == http_status.HTTP_404_NOT_FOUND

    execution_version_detail = auth_client.get(
        f"/model-hub/prompt-history-executions/execution-details/{version_v2.id}/"
    )
    assert execution_version_detail.status_code == http_status.HTTP_200_OK
    assert _results(execution_version_detail)["template_version"] == "v2"

    other_execution_version_detail = auth_client.get(
        f"/model-hub/prompt-history-executions/execution-details/{other_version.id}/"
    )
    assert other_execution_version_detail.status_code == http_status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_prompt_execution_list_tolerates_empty_prompt_config_snapshot(
    auth_client, organization, workspace, user
):
    template = PromptTemplate.no_workspace_objects.create(
        name="Prompt library empty snapshot",
        description="Prompt execution list should tolerate legacy empty snapshots",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=[],
        is_default=True,
    )

    response = auth_client.get(
        "/model-hub/prompt-executions/",
        {"page_size": 25, "page": 1},
    )

    assert response.status_code == http_status.HTTP_200_OK
    rows = _results(response)
    created_row = next(row for row in rows if row["id"] == str(template.id))
    assert created_row["model"] is None
    assert created_row["model_detail"] is None


@pytest.mark.django_db
def test_prompt_folder_put_scopes_workspace_and_blocks_samples(
    auth_client, organization, workspace, user
):
    other_workspace = Workspace.objects.create(
        name="Prompt Folder Other Workspace",
        organization=organization,
        created_by=user,
    )
    other_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt folder other workspace",
        organization=organization,
        workspace=other_workspace,
        created_by=user,
    )

    clear_workspace_context()
    sample_folder = PromptFolder.no_workspace_objects.create(
        name="Global sample prompt folder",
        organization=None,
        workspace=None,
        is_sample=True,
        created_by=None,
    )

    create_response = auth_client.post(
        "/model-hub/prompt-folders/",
        {"name": "Prompt folder create ignores sample", "is_sample": True},
        format="json",
    )
    assert create_response.status_code == http_status.HTTP_201_CREATED
    created_id = create_response.json()["result"]["id"]
    created_folder = PromptFolder.no_workspace_objects.get(id=created_id)
    assert created_folder.organization == organization
    assert created_folder.workspace == workspace
    assert created_folder.created_by == user
    assert created_folder.is_sample is False

    duplicate_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt folder duplicate target",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )

    put_response = auth_client.put(
        f"/model-hub/prompt-folders/{created_id}/",
        {"name": "Prompt folder renamed with put", "is_sample": True},
        format="json",
    )
    assert put_response.status_code == http_status.HTTP_200_OK
    created_folder.refresh_from_db()
    assert created_folder.name == "Prompt folder renamed with put"
    assert created_folder.workspace == workspace
    assert created_folder.is_sample is False

    duplicate_response = auth_client.put(
        f"/model-hub/prompt-folders/{duplicate_folder.id}/",
        {"name": "Prompt folder renamed with put"},
        format="json",
    )
    assert duplicate_response.status_code == http_status.HTTP_400_BAD_REQUEST
    duplicate_folder.refresh_from_db()
    assert duplicate_folder.name == "Prompt folder duplicate target"

    sample_update = auth_client.put(
        f"/model-hub/prompt-folders/{sample_folder.id}/",
        {"name": "Mutated sample prompt folder"},
        format="json",
    )
    assert sample_update.status_code == http_status.HTTP_400_BAD_REQUEST
    sample_folder.refresh_from_db()
    assert sample_folder.name == "Global sample prompt folder"
    assert sample_folder.deleted is False

    other_update = auth_client.put(
        f"/model-hub/prompt-folders/{other_folder.id}/",
        {"name": "Mutated other prompt folder"},
        format="json",
    )
    assert other_update.status_code == http_status.HTTP_404_NOT_FOUND
    other_folder.refresh_from_db()
    assert other_folder.name == "Prompt folder other workspace"

    folders_response = auth_client.get("/model-hub/prompt-folders/")
    assert folders_response.status_code == http_status.HTTP_200_OK
    folder_ids = {row["id"] for row in _results(folders_response)}
    assert created_id in folder_ids
    assert str(sample_folder.id) in folder_ids
    assert str(other_folder.id) not in folder_ids
