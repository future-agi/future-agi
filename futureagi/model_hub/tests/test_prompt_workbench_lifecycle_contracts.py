import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)


def _prompt_config(text="Hello {{name}}"):
    return {
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "Be concise."}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        ],
        "configuration": {
            "model": "gpt-4o-mini",
            "model_detail": {"type": "chat"},
            "template_format": "mustache",
        },
    }


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
        prompt_config_snapshot=_prompt_config(),
        variable_names={"name": ["Ada"]},
        is_draft=True,
    )
    return template, version


def _create_prompt_version(
    template,
    template_version,
    *,
    text=None,
    is_default=False,
    is_draft=True,
):
    return PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version=template_version,
        prompt_config_snapshot=_prompt_config(text or f"Hello from {template_version}"),
        variable_names={"name": ["Ada"]},
        is_default=is_default,
        is_draft=is_draft,
    )


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


@pytest.mark.django_db
def test_prompt_versions_endpoint_returns_prompt_version_rows(
    auth_client, organization, workspace, user
):
    template, version_v1 = _create_prompt_template(
        organization, workspace, user, "Prompt versions contract"
    )
    version_v2 = _create_prompt_version(
        template,
        "v2",
        text="Second version {{name}}",
    )

    response = auth_client.get(f"/model-hub/prompt-templates/{template.id}/versions/")

    assert response.status_code == 200
    rows = response.json()["results"]
    versions = [row["template_version"] for row in rows]
    assert versions[:2] == ["v2", "v1"]
    row_ids = {row["id"] for row in rows}
    assert str(version_v1.id) in row_ids
    assert str(version_v2.id) in row_ids


@pytest.mark.django_db
def test_prompt_sdk_code_accepts_dict_prompt_config_snapshot(
    auth_client, organization, workspace, user
):
    template, _ = _create_prompt_template(
        organization, workspace, user, "Prompt SDK code snapshot contract"
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/get-sdk-code/python/"
    )

    assert response.status_code == 200
    code = response.json()["result"]["python"]
    assert f"/model-hub/prompt-templates/{template.id}/run_template/" in code
    assert "YOUR_API_KEY" in code
    assert "gpt-4o-mini" in code


@pytest.mark.django_db
def test_run_template_prompt_run_submits_organization_id(
    auth_client, organization, workspace, user, monkeypatch
):
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt run organization id contract"
    )
    submitted = {}

    def fake_submit_with_retry(_executor, _func, *args, **_kwargs):
        submitted["args"] = args

    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry",
        fake_submit_with_retry,
    )

    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/run_template/",
        {
            "name": template.name,
            "version": version.template_version,
            "is_run": "prompt",
            "variable_names": {"name": ["Ada"]},
            "placeholders": {},
            "evaluation_configs": [],
            "prompt_config": [_prompt_config("Say hello to {{name}}")],
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(submitted["args"][2]) == str(organization.id)
    assert submitted["args"][2] != organization


@pytest.mark.django_db
def test_prompt_default_version_is_exclusive_for_set_default_and_commit(
    auth_client, organization, workspace, user
):
    template, version_v1 = _create_prompt_template(
        organization, workspace, user, "Prompt default exclusivity contract"
    )
    version_v1.is_default = True
    version_v1.is_draft = False
    version_v1.save(update_fields=["is_default", "is_draft"])
    version_v2 = _create_prompt_version(
        template,
        "v2",
        text="Default v2 {{name}}",
        is_draft=False,
    )

    set_default_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/set_default/",
        {"version_name": "v2"},
        format="json",
    )

    assert set_default_response.status_code == 200
    version_v1.refresh_from_db()
    version_v2.refresh_from_db()
    assert version_v1.is_default is False
    assert version_v2.is_default is True

    default_lookup = auth_client.get(
        "/model-hub/prompt-templates/get-template-by-name/",
        {"name": template.name},
    )
    assert default_lookup.status_code == 200
    assert default_lookup.json()["version"] == "v2"

    commit_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/commit/",
        {
            "version_name": "v1",
            "message": "restore v1 default",
            "is_draft": False,
            "set_default": True,
        },
        format="json",
    )

    assert commit_response.status_code == 200
    version_v1.refresh_from_db()
    version_v2.refresh_from_db()
    assert version_v1.is_default is True
    assert version_v2.is_default is False
