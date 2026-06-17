import json
from pathlib import Path

import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_prompt_label_error_responses_use_typed_contracts():
    assert (
        _response_ref(_operation("/model-hub/prompt-labels/", "GET"), "400")
        == "ModelHubTextErrorResponse"
    )
    assert (
        _response_ref(
            _operation("/model-hub/prompt-labels/create-system-labels/", "POST"),
            "500",
        )
        == "ModelHubTextErrorResponse"
    )
    assert (
        _response_ref(
            _operation(
                "/model-hub/prompt-labels/{template_id}/{label_id}/assign-label-by-id/",
                "POST",
            ),
            "404",
        )
        == "ModelHubTextErrorResponse"
    )


@pytest.mark.django_db
def test_create_system_prompt_labels_endpoint_is_idempotent(auth_client):
    response = auth_client.post("/model-hub/prompt-labels/create-system-labels/")

    assert response.status_code == http_status.HTTP_200_OK
    result = response.json()["result"]
    assert set(result) == {"created", "count"}


@pytest.mark.django_db
def test_prompt_label_validation_errors_use_general_envelope(auth_client):
    response = auth_client.get("/model-hub/prompt-labels/get-by-name/")

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["status"] is False
    assert data["result"] == "'name' is required"


def _label_rows(response):
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]
    return []


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


@pytest.mark.django_db
def test_prompt_label_crud_persists_workspace_and_scopes_custom_labels(
    auth_client, organization, workspace, user
):
    other_workspace = Workspace.objects.create(
        name="Prompt Label Other Workspace",
        organization=organization,
        created_by=user,
    )
    other_label = PromptLabel.no_workspace_objects.create(
        name="Other workspace label",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=other_workspace,
    )

    create_response = auth_client.post(
        "/model-hub/prompt-labels/",
        {
            "name": "Journey label",
            "type": LabelTypeChoices.CUSTOM.value,
            "metadata": {"color": "blue"},
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_201_CREATED
    label_id = create_response.json()["id"]
    created_label = PromptLabel.no_workspace_objects.get(id=label_id)
    assert created_label.organization == organization
    assert created_label.workspace == workspace
    assert created_label.type == LabelTypeChoices.CUSTOM.value

    list_response = auth_client.get("/model-hub/prompt-labels/")
    assert list_response.status_code == http_status.HTTP_200_OK
    listed_ids = {row["id"] for row in _label_rows(list_response)}
    assert label_id in listed_ids
    assert str(other_label.id) not in listed_ids

    detail_response = auth_client.get(f"/model-hub/prompt-labels/{label_id}/")
    assert detail_response.status_code == http_status.HTTP_200_OK
    assert detail_response.json()["name"] == "Journey label"

    other_detail_response = auth_client.get(
        f"/model-hub/prompt-labels/{other_label.id}/"
    )
    assert other_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    patch_response = auth_client.patch(
        f"/model-hub/prompt-labels/{label_id}/",
        {"name": "Journey label patched"},
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    created_label.refresh_from_db()
    assert created_label.name == "Journey label patched"
    assert created_label.workspace == workspace

    put_response = auth_client.put(
        f"/model-hub/prompt-labels/{label_id}/",
        {
            "name": "Journey label replaced",
            "type": LabelTypeChoices.CUSTOM.value,
            "metadata": {"color": "green"},
        },
        format="json",
    )
    assert put_response.status_code == http_status.HTTP_200_OK
    created_label.refresh_from_db()
    assert created_label.name == "Journey label replaced"
    assert created_label.workspace == workspace
    assert created_label.metadata == {"color": "green"}

    system_response = auth_client.post("/model-hub/prompt-labels/create-system-labels/")
    assert system_response.status_code == http_status.HTTP_200_OK

    duplicate_system_response = auth_client.post(
        "/model-hub/prompt-labels/",
        {"name": "Production", "type": LabelTypeChoices.CUSTOM.value},
        format="json",
    )
    assert duplicate_system_response.status_code == http_status.HTTP_400_BAD_REQUEST

    delete_response = auth_client.delete(f"/model-hub/prompt-labels/{label_id}/")
    assert delete_response.status_code == http_status.HTTP_204_NO_CONTENT
    created_label.refresh_from_db()
    assert created_label.deleted is True
    assert created_label.deleted_at is not None


@pytest.mark.django_db
def test_prompt_label_assignment_default_and_lookup_lifecycle(
    auth_client, organization, workspace, user
):
    template = PromptTemplate.objects.create(
        name="Prompt Label Lifecycle",
        description="Prompt label lifecycle coverage",
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=["customer"],
    )
    version_one = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "v1"}]},
        variable_names={"customer": ["Ada"]},
        output=[],
        is_default=True,
    )
    version_two = PromptVersion.objects.create(
        original_template=template,
        template_version="v2",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "v2"}]},
        variable_names={"customer": ["Grace"]},
        output=[],
        is_default=False,
    )
    other_template = PromptTemplate.objects.create(
        name="Prompt Label Other Template",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    other_version = PromptVersion.objects.create(
        original_template=other_template,
        template_version="v2",
        is_default=False,
    )

    system_response = auth_client.post("/model-hub/prompt-labels/create-system-labels/")
    assert system_response.status_code == http_status.HTTP_200_OK
    production_label = PromptLabel.no_workspace_objects.get(
        name="Production",
        organization__isnull=True,
        type=LabelTypeChoices.SYSTEM.value,
    )

    custom_response = auth_client.post(
        "/model-hub/prompt-labels/",
        {
            "name": "Lifecycle label",
            "type": LabelTypeChoices.CUSTOM.value,
            "metadata": {"kind": "lifecycle"},
        },
        format="json",
    )
    assert custom_response.status_code == http_status.HTTP_201_CREATED
    custom_label_id = custom_response.json()["id"]

    assign_multiple_response = auth_client.post(
        "/model-hub/prompt-labels/assign-multiple-labels/",
        {
            "template_version_id": str(version_one.id),
            "label_ids": [custom_label_id, str(production_label.id)],
        },
        format="json",
    )
    assert assign_multiple_response.status_code == http_status.HTTP_200_OK
    version_one.refresh_from_db()
    assert set(version_one.labels.values_list("name", flat=True)) == {
        "Lifecycle label",
        "Production",
    }

    template_labels_response = auth_client.get(
        "/model-hub/prompt-labels/template-labels/",
        {"template_id": str(template.id)},
    )
    assert template_labels_response.status_code == http_status.HTTP_200_OK
    template_label_rows = _result(template_labels_response)
    assert any(
        row["version"] == "v1"
        and "Lifecycle label" in row["labels"]
        and "Production" in row["labels"]
        for row in template_label_rows
    )

    by_label_response = auth_client.get(
        "/model-hub/prompt-labels/get-by-name/",
        {"name": template.name, "label": "Lifecycle label"},
    )
    assert by_label_response.status_code == http_status.HTTP_200_OK
    assert _result(by_label_response)["version"] == "v1"

    by_version_response = auth_client.get(
        "/model-hub/prompt-labels/get-by-name/",
        {"name": template.name, "version": "v2"},
    )
    assert by_version_response.status_code == http_status.HTTP_200_OK
    assert _result(by_version_response)["version"] == "v2"

    assign_by_id_response = auth_client.post(
        f"/model-hub/prompt-labels/{template.id}/{custom_label_id}/assign-label-by-id/",
        {"version": "v2"},
        format="json",
    )
    assert assign_by_id_response.status_code == http_status.HTTP_200_OK
    assign_by_id_result = _result(assign_by_id_response)
    assert assign_by_id_result["version"] == "v2"
    assert assign_by_id_result["moved_from_versions"] == ["v1"]
    version_one.refresh_from_db()
    version_two.refresh_from_db()
    assert not version_one.labels.filter(id=custom_label_id).exists()
    assert version_two.labels.filter(id=custom_label_id).exists()

    set_default_response = auth_client.post(
        "/model-hub/prompt-labels/set-default/",
        {"template_name": template.name, "version": "v2"},
        format="json",
    )
    assert set_default_response.status_code == http_status.HTTP_200_OK
    version_one.refresh_from_db()
    version_two.refresh_from_db()
    other_version.refresh_from_db()
    assert version_one.is_default is False
    assert version_two.is_default is True
    assert other_version.is_default is False

    remove_response = auth_client.post(
        "/model-hub/prompt-labels/remove/",
        {"label_id": custom_label_id, "version_id": str(version_two.id)},
        format="json",
    )
    assert remove_response.status_code == http_status.HTTP_200_OK
    assert not version_two.labels.filter(id=custom_label_id).exists()
