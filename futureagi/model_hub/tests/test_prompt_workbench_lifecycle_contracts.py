import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)


def _create_prompt_template(organization, workspace, user, name, folder=None):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
        prompt_folder=folder,
        variable_names={"name": ["Ada"]},
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "Be concise."}],
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello {{name}}"}],
                },
            ],
            "configuration": {
                "model": "gpt-4o-mini",
                "model_detail": {"type": "chat"},
                "template_format": "mustache",
            },
        },
        variable_names={"name": ["Ada"]},
        is_draft=True,
    )
    return template, version


def _create_eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="prompt_eval_config_contract",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        output_type_normalized="pass_fail",
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["text"],
            "custom_eval": True,
        },
    )


@pytest.mark.django_db
def test_prompt_bulk_delete_stamps_deleted_at_on_template_and_versions(
    auth_client, organization, workspace, user
):
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt bulk delete contract"
    )

    response = auth_client.post(
        "/model-hub/prompt-templates/bulk-delete/",
        {"ids": [str(template.id)]},
        format="json",
    )

    assert response.status_code == 200
    template.refresh_from_db()
    version.refresh_from_db()
    assert template.deleted is True
    assert template.deleted_at is not None
    assert version.deleted is True
    assert version.deleted_at is not None


@pytest.mark.django_db
def test_prompt_folder_delete_stamps_deleted_at_and_cascades_prompt_versions(
    auth_client, organization, workspace, user
):
    folder = PromptFolder.no_workspace_objects.create(
        name="Prompt folder delete contract",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt folder cascade contract", folder
    )

    response = auth_client.delete(f"/model-hub/prompt-folders/{folder.id}/")

    assert response.status_code == 200
    folder.refresh_from_db()
    template.refresh_from_db()
    version.refresh_from_db()
    assert folder.deleted is True
    assert folder.deleted_at is not None
    assert template.deleted is True
    assert template.deleted_at is not None
    assert version.deleted is True
    assert version.deleted_at is not None


@pytest.mark.django_db
def test_run_evals_rejects_eval_config_from_another_prompt(
    auth_client, organization, workspace, user
):
    target_template, target_version = _create_prompt_template(
        organization, workspace, user, "Prompt eval run target"
    )
    other_template, _ = _create_prompt_template(
        organization, workspace, user, "Prompt eval run other"
    )
    eval_template = _create_eval_template(organization, workspace)
    other_config = PromptEvalConfig.no_workspace_objects.create(
        name="Other prompt eval config",
        prompt_template=other_template,
        eval_template=eval_template,
        user=user,
        mapping={"text": "text"},
    )

    url = (
        f"/model-hub/prompt-templates/{target_template.id}/"
        "run-evals-on-multiple-versions/"
    )
    response = auth_client.post(
        url,
        {
            "version_to_run": ["v1"],
            "prompt_eval_config_ids": [str(other_config.id)],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "do not exist for this prompt template" in str(response.data)
    target_version.refresh_from_db()
    assert target_version.evaluation_results in ({}, None)
