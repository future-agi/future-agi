"""A commit establishes a saved-version boundary without a provider run."""

import pytest

from model_hub.models.run_prompt import PromptVersion


@pytest.mark.django_db
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
@pytest.mark.parametrize("set_default", [False, True])
def test_commit_finalizes_draft_even_when_client_sends_is_draft_true(
    auth_client, prompt_template, set_default
):
    saved_config = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Before commit"}]}
        ],
        "configuration": {},
    }
    version = PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v1",
        prompt_config_snapshot=saved_config,
        variable_names={},
        is_draft=True,
        is_default=False,
    )
    assert version.output == []  # No provider run is needed to commit.

    response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/commit/",
        {
            "version_name": "v1",
            "message": "Saved before running",
            "is_draft": True,
            "set_default": set_default,
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    version.refresh_from_db()
    assert version.is_draft is False
    assert version.is_default is set_default
    assert version.commit_message == "Saved before running"
    assert version.output == []

    # The workbench refetch must see the committed flag before its next edit.
    latest = auth_client.get(f"/model-hub/prompt-templates/{prompt_template.id}/")
    assert latest.status_code == 200, latest.content
    assert latest.json()["version"] == "v1"
    assert latest.json()["is_draft"] is False

    edited_config = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "After commit"}]}
        ],
        "configuration": {},
    }
    next_draft = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/add-new-draft/",
        {
            "new_prompts": [
                {
                    "prompt_config": [edited_config],
                    "variable_names": {},
                    "evaluation_configs": [],
                }
            ]
        },
        format="json",
    )

    assert next_draft.status_code == 200, next_draft.content
    new_version = next_draft.json()["result"][0]
    assert new_version["template_version"] == "v2"
    assert new_version["is_draft"] is True
    draft = PromptVersion.no_workspace_objects.get(id=new_version["id"])
    assert draft.prompt_config_snapshot == edited_config
    version.refresh_from_db()
    assert version.is_draft is False
    assert version.is_default is set_default
    assert version.prompt_config_snapshot == saved_config
    assert (
        PromptVersion.no_workspace_objects.filter(
            original_template=prompt_template
        ).count()
        == 2
    )
