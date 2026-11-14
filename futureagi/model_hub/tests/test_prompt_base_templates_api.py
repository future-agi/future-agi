import pytest
from rest_framework import status as http_status

from accounts.models.workspace import Workspace
from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from tfc.middleware.workspace_context import clear_workspace_context


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


def _create_prompt_version(
    *,
    organization,
    workspace,
    user,
    name,
    version="v1",
    is_draft=False,
):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        description=f"{name} description",
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=["topic"],
    )
    prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version=version,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": f"{name} {{{{topic}}}}"}]
        },
        variable_names={"topic": ["coverage"]},
        output=[],
        is_draft=is_draft,
        is_default=True,
    )
    return template, prompt_version


@pytest.mark.django_db
def test_prompt_base_template_crud_scopes_workspace_and_samples(
    auth_client, organization, workspace, user
):
    _, prompt_version = _create_prompt_version(
        organization=organization,
        workspace=workspace,
        user=user,
        name="Base template source",
    )

    other_workspace = Workspace.objects.create(
        name="Prompt Base Other Workspace",
        organization=organization,
        created_by=user,
    )
    _, other_prompt_version = _create_prompt_version(
        organization=organization,
        workspace=other_workspace,
        user=user,
        name="Other base template source",
    )
    other_base_template = PromptBaseTemplate.no_workspace_objects.create(
        name="Other workspace base template",
        organization=organization,
        workspace=other_workspace,
        prompt_version=other_prompt_version,
        category="Other Category",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "other"}]},
        created_by=user,
    )

    clear_workspace_context()
    sample_template = PromptBaseTemplate.no_workspace_objects.create(
        name="Global sample base template",
        organization=None,
        workspace=None,
        is_sample=True,
        category="Sample Category",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "sample"}]},
        created_by=None,
    )

    create_response = auth_client.post(
        "/model-hub/prompt-base-templates/",
        {
            "name": "Journey base template",
            "is_sample": True,
            "prompt_version": str(prompt_version.id),
            "category": "Journey Category",
        },
        format="json",
    )

    assert create_response.status_code == http_status.HTTP_201_CREATED
    created = _result(create_response)
    base_template_id = created["id"]
    base_template = PromptBaseTemplate.no_workspace_objects.get(id=base_template_id)
    assert base_template.organization == organization
    assert base_template.workspace == workspace
    assert base_template.created_by == user
    assert base_template.is_sample is False
    assert base_template.prompt_config_snapshot == prompt_version.prompt_config_snapshot

    list_response = auth_client.get(
        "/model-hub/prompt-base-templates/",
        {"page_size": 25, "page_number": 0},
    )
    assert list_response.status_code == http_status.HTTP_200_OK
    list_result = _result(list_response)
    list_ids = {row["id"] for row in list_result["data"]}
    assert base_template_id in list_ids
    assert str(sample_template.id) in list_ids
    assert str(other_base_template.id) not in list_ids

    category_response = auth_client.get(
        "/model-hub/prompt-base-templates/",
        {"category": "Journey Category", "page_size": 25, "page_number": 0},
    )
    assert category_response.status_code == http_status.HTTP_200_OK
    category_ids = {row["id"] for row in _result(category_response)["data"]}
    assert base_template_id in category_ids
    assert str(other_base_template.id) not in category_ids

    categories_response = auth_client.get(
        "/model-hub/prompt-base-templates/get-all-categories/"
    )
    assert categories_response.status_code == http_status.HTTP_200_OK
    categories = set(_result(categories_response))
    assert {"Journey Category", "Sample Category"}.issubset(categories)
    assert "Other Category" not in categories

    detail_response = auth_client.get(
        f"/model-hub/prompt-base-templates/{base_template_id}/"
    )
    assert detail_response.status_code == http_status.HTTP_200_OK
    assert _result(detail_response)["name"] == "Journey base template"

    sample_detail_response = auth_client.get(
        f"/model-hub/prompt-base-templates/{sample_template.id}/"
    )
    assert sample_detail_response.status_code == http_status.HTTP_200_OK
    assert _result(sample_detail_response)["is_sample"] is True

    other_detail_response = auth_client.get(
        f"/model-hub/prompt-base-templates/{other_base_template.id}/"
    )
    assert other_detail_response.status_code == http_status.HTTP_404_NOT_FOUND

    duplicate_response = auth_client.post(
        "/model-hub/prompt-base-templates/",
        {
            "name": "Journey base template",
            "prompt_version": str(prompt_version.id),
            "category": "Journey Category",
        },
        format="json",
    )
    assert duplicate_response.status_code == http_status.HTTP_400_BAD_REQUEST

    other_version_response = auth_client.post(
        "/model-hub/prompt-base-templates/",
        {
            "name": "Blocked other prompt version",
            "prompt_version": str(other_prompt_version.id),
            "category": "Journey Category",
        },
        format="json",
    )
    assert other_version_response.status_code == http_status.HTTP_400_BAD_REQUEST
    assert not PromptBaseTemplate.no_workspace_objects.filter(
        name="Blocked other prompt version"
    ).exists()

    patch_response = auth_client.patch(
        f"/model-hub/prompt-base-templates/{base_template_id}/",
        {"name": "Journey base template patched"},
        format="json",
    )
    assert patch_response.status_code == http_status.HTTP_200_OK
    base_template.refresh_from_db()
    assert base_template.name == "Journey base template patched"
    assert base_template.workspace == workspace
    assert base_template.is_sample is False

    put_response = auth_client.put(
        f"/model-hub/prompt-base-templates/{base_template_id}/",
        {
            "name": "Journey base template replaced",
            "is_sample": True,
            "prompt_version": str(prompt_version.id),
            "category": "Journey Category Updated",
            "prompt_config_snapshot": {
                "messages": [{"role": "user", "content": "updated"}]
            },
        },
        format="json",
    )
    assert put_response.status_code == http_status.HTTP_200_OK
    base_template.refresh_from_db()
    assert base_template.name == "Journey base template replaced"
    assert base_template.workspace == workspace
    assert base_template.is_sample is False
    assert base_template.category == "Journey Category Updated"

    sample_patch_response = auth_client.patch(
        f"/model-hub/prompt-base-templates/{sample_template.id}/",
        {"name": "Mutated sample"},
        format="json",
    )
    assert sample_patch_response.status_code == http_status.HTTP_400_BAD_REQUEST
    sample_template.refresh_from_db()
    assert sample_template.name == "Global sample base template"

    sample_delete_response = auth_client.delete(
        f"/model-hub/prompt-base-templates/{sample_template.id}/"
    )
    assert sample_delete_response.status_code == http_status.HTTP_400_BAD_REQUEST
    sample_template.refresh_from_db()
    assert sample_template.deleted is False

    delete_response = auth_client.delete(
        f"/model-hub/prompt-base-templates/{base_template_id}/"
    )
    assert delete_response.status_code == http_status.HTTP_200_OK
    base_template.refresh_from_db()
    assert base_template.deleted is True
    assert base_template.deleted_at is not None
