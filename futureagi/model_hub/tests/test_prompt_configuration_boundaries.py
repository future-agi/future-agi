"""Missing prompt credentials are run-wide setup failures, not row output."""

import asyncio
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from agentic_eval.core_evals.run_prompt import litellm_response as prompt_module
from agentic_eval.core_evals.run_prompt.litellm_models import (
    LiteLLMModelManager,
    MissingProviderKeyError,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.views import run_prompt as views

pytestmark = pytest.mark.django_db


def preview_payload(dataset):
    return {
        "dataset_id": str(dataset.id),
        "name": "configuration-preview",
        "first_n_rows": 50,
        "config": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say hello"}],
        },
    }


@pytest.fixture
def dataset(organization, workspace):
    dataset = Dataset.objects.create(
        name="configuration-preview", organization=organization, workspace=workspace
    )
    Row.objects.bulk_create([Row(dataset=dataset, order=i) for i in range(50)])
    return dataset


@pytest.mark.parametrize("empty_record", [False, True])
def test_preview_preflights_fifty_rows(
    auth_client, organization, workspace, dataset, monkeypatch, empty_record
):
    if empty_record:
        ApiKey.objects.create(
            organization=organization, workspace=workspace, provider="openai"
        )
    invoke = MagicMock(side_effect=MissingProviderKeyError("openai"))
    populate = MagicMock(return_value=[{"role": "user", "content": "Hello"}])
    monkeypatch.setattr(views.RunPrompt, "litellm_response", invoke)
    monkeypatch.setattr(views, "populate_placeholders", populate)
    with capture_logs() as logs:
        response = auth_client.post(
            "/model-hub/develops/preview_run_prompt_column/",
            preview_payload(dataset),
            format="json",
        )
    assert response.status_code == 400
    assert "openai" in str(response.data)
    assert "settings" in str(response.data)
    invoke.assert_not_called()
    populate.assert_not_called()
    assert not [
        e for e in logs if e.get("log_level") in {"error", "exception", "critical"}
    ]
    assert (
        len(
            [
                e
                for e in logs
                if e.get("event") == "prompt_preview_configuration_invalid"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("org_fallback", [False, True])
def test_configured_preview_keeps_results_and_scope(
    auth_client, organization, workspace, dataset, monkeypatch, org_fallback
):
    ApiKey.objects.create(
        organization=organization,
        workspace=None if org_fallback else workspace,
        provider="openai",
        key="synthetic-provider-key",
    )
    constructor = MagicMock()
    constructor.return_value.litellm_response.return_value = (
        "Generated text",
        {"metadata": {"usage": {}, "cost": {}}},
    )
    monkeypatch.setattr(views, "RunPrompt", constructor)
    monkeypatch.setattr(
        views,
        "populate_placeholders",
        lambda *a, **kw: [{"role": "user", "content": "Hello"}],
    )
    response = auth_client.post(
        "/model-hub/develops/preview_run_prompt_column/",
        preview_payload(dataset),
        format="json",
    )
    assert response.status_code == 200
    assert response.data["result"]["responses"] == ["Generated text"] * 50
    assert constructor.call_count == 50
    assert constructor.call_args.kwargs["workspace_id"] == workspace.id
    assert constructor.call_args.kwargs["organization_id"] == organization.id


def test_empty_workspace_entry_still_masks_org_key(organization, workspace):
    ApiKey.objects.create(
        organization=organization, provider="openai", key="synthetic-org-key"
    )
    ApiKey.objects.create(
        organization=organization, workspace=workspace, provider="openai"
    )
    with pytest.raises(MissingProviderKeyError):
        LiteLLMModelManager("gpt-4o-mini").get_api_key(organization.id, workspace.id)


def test_other_workspace_key_is_not_used(organization, workspace, user):
    from accounts.models.workspace import Workspace

    other = Workspace.objects.create(
        name="Other fixture", organization=organization, created_by=user
    )
    ApiKey.objects.create(
        organization=organization,
        workspace=other,
        provider="openai",
        key="synthetic-other-key",
    )
    with pytest.raises(MissingProviderKeyError):
        LiteLLMModelManager("gpt-4o-mini").get_api_key(organization.id, workspace.id)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("provider", ["openai", "turing"])
def test_prompt_wrapper_preserves_missing_key_type(monkeypatch, async_mode, provider):
    manager = MagicMock()
    manager.get_provider.return_value = provider
    error = MissingProviderKeyError(provider)
    manager.get_api_key.side_effect = error
    monkeypatch.setattr(prompt_module, "LiteLLMModelManager", lambda *a, **kw: manager)
    create_handler = MagicMock()
    monkeypatch.setattr(
        prompt_module.ModelHandlerFactory, "create_handler", create_handler
    )
    prompt = prompt_module.RunPrompt(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
        organization_id=None,
        output_format="string",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        tools=[],
    )
    with capture_logs() as logs:
        with pytest.raises(MissingProviderKeyError) as raised:
            if async_mode:
                asyncio.run(prompt._litellm_response_async_new())
            else:
                prompt._litellm_response_new()
    assert raised.value is error
    create_handler.assert_not_called()
    assert not [
        e
        for e in logs
        if e.get("log_level") in {"warning", "error", "exception", "critical"}
    ]


@pytest.mark.parametrize("async_mode", [False, True])
def test_unexpected_prompt_failure_retains_one_error(monkeypatch, async_mode):
    manager = MagicMock()
    manager.get_provider.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(prompt_module, "LiteLLMModelManager", lambda *a, **kw: manager)
    prompt = prompt_module.RunPrompt(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
        organization_id=None,
        output_format="string",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        tools=[],
    )
    with capture_logs() as logs:
        with pytest.raises(Exception, match="provider unavailable"):
            if async_mode:
                asyncio.run(prompt._litellm_response_async_new())
            else:
                prompt._litellm_response_new()
    errors = [
        e for e in logs if e.get("log_level") in {"error", "exception", "critical"}
    ]
    assert len(errors) == 1
    assert "[NEW]" not in errors[0]["event"]
