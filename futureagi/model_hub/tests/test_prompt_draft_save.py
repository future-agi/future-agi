"""Saving prompt drafts must not dispatch execution or record run usage."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.run_prompt import PromptVersion
from model_hub.views import prompt_template as prompt_views

pytestmark = pytest.mark.django_db


@pytest.fixture
def execution_hooks(monkeypatch):
    hooks = SimpleNamespace(
        submit=Mock(),
        run=Mock(),
        track=Mock(),
        usage=Mock(),
        model=Mock(),
    )
    monkeypatch.setattr(prompt_views, "submit_with_retry", hooks.submit)
    monkeypatch.setattr(prompt_views.PromptTemplateViewSet, "run", hooks.run)
    monkeypatch.setattr(prompt_views, "track_mixpanel_event", hooks.track)
    monkeypatch.setattr(prompt_views, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(
        prompt_views, "log_and_deduct_cost_for_api_request", hooks.usage
    )
    monkeypatch.setattr(prompt_views, "RunPrompt", hooks.model)
    return hooks


@pytest.fixture
def save_request(organization, workspace, user, execution_hooks):
    def post(template, payload):
        request = APIRequestFactory().post(
            f"/model-hub/prompt-templates/{template.id}/run_template/",
            payload,
            format="json",
            HTTP_X_API_KEY="synthetic-api-key",
        )
        request.organization = organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        return prompt_views.PromptTemplateViewSet.as_view({"post": "run_template"})(
            request, pk=str(template.id)
        )

    return post


@pytest.fixture
def draft_version(prompt_version):
    prompt_version.is_draft = True
    prompt_version.save(update_fields=["is_draft"])
    return prompt_version


@pytest.fixture
def draft_payload():
    return {
        "is_run": "draft",
        "is_sdk": True,
        "prompt_config": [
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Hello {{name}}"}],
                    }
                ],
                "configuration": {"model": "gpt-4o-mini"},
            }
        ],
        "variable_names": {"name": ["Ada"]},
        "evaluation_configs": [],
        "placeholders": {
            "context": [{"role": "user", "content": "Saved context"}]
        },
    }


def _assert_saved(version, payload):
    version.refresh_from_db()
    assert version.prompt_config_snapshot == payload["prompt_config"][0]
    assert version.variable_names == payload["variable_names"]
    assert version.placeholders == payload["placeholders"]
    assert version.evaluation_configs == payload["evaluation_configs"]
    assert version.is_draft is True
    template = version.original_template
    template.refresh_from_db()
    assert template.variable_names == payload["variable_names"]
    assert template.placeholders == payload["placeholders"]


def _assert_not_run(hooks):
    hooks.submit.assert_not_called()
    hooks.run.assert_not_called()
    hooks.track.assert_not_called()
    hooks.usage.assert_not_called()
    hooks.model.assert_not_called()


@pytest.mark.parametrize("is_run", ["draft", False], ids=["draft", "false"])
def test_save_version_persists_draft_without_running(
    save_request, draft_version, draft_payload, execution_hooks, is_run
):
    draft_payload.update(version=draft_version.template_version, is_run=is_run)
    original_output = deepcopy(draft_version.output)
    original_metadata = deepcopy(draft_version.metadata)

    response = save_request(draft_version.original_template, draft_payload)

    assert response.status_code == 200, response.data
    _assert_saved(draft_version, draft_payload)
    _assert_not_run(execution_hooks)
    assert draft_version.output == original_output
    assert draft_version.metadata == original_metadata


def test_save_without_version_updates_latest_draft_without_running(
    save_request, draft_version, draft_payload, execution_hooks
):
    template = draft_version.original_template
    original_config = deepcopy(draft_version.prompt_config_snapshot)
    draft_version.is_draft = False
    draft_version.save(update_fields=["is_draft"])
    latest = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v2",
        prompt_config_snapshot=original_config,
        variable_names={},
        is_draft=True,
    )
    original_output = deepcopy(latest.output)
    original_metadata = deepcopy(latest.metadata)

    response = save_request(template, draft_payload)

    assert response.status_code == 200, response.data
    assert response.data["result"]["version"] == latest.template_version
    _assert_saved(latest, draft_payload)
    _assert_not_run(execution_hooks)
    assert latest.output == original_output
    assert latest.metadata == original_metadata
    draft_version.refresh_from_db()
    assert draft_version.prompt_config_snapshot == original_config
    assert draft_version.variable_names == {}
    assert draft_version.is_draft is False
    assert PromptVersion.no_workspace_objects.filter(
        original_template=template
    ).count() == 2


@pytest.mark.parametrize("is_run", ["prompt", True], ids=["prompt", "true"])
def test_run_version_still_submits_once(
    save_request, draft_version, draft_payload, execution_hooks, is_run
):
    draft_payload.update(version=draft_version.template_version, is_run=is_run)

    response = save_request(draft_version.original_template, draft_payload)

    assert response.status_code == 200, response.data
    _assert_saved(draft_version, draft_payload)
    execution_hooks.submit.assert_called_once()
    args = execution_hooks.submit.call_args.args
    assert args[1] is execution_hooks.run
    assert args[2].id == draft_version.original_template_id
    assert args[3].id == draft_version.id
    assert args[5] == draft_version.template_version
    assert args[6] == is_run
    execution_hooks.track.assert_called_once_with(
        prompt_views.MixpanelEvents.SDK_PROMPT_RUN.value, {}
    )
    execution_hooks.run.assert_not_called()
    execution_hooks.usage.assert_not_called()
    execution_hooks.model.assert_not_called()
