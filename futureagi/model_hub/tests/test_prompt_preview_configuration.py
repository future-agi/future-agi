"""Preview setup failures reject early; mid-run failures retain completed work.

Exercise the real catalog, credential lookup, prompt execution and API envelope;
only outbound completions are mocked in the configuration regression cases.
"""

from unittest.mock import MagicMock

import litellm
import pytest
from structlog.testing import capture_logs

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.develop_dataset import Dataset, Row

pytestmark = pytest.mark.django_db

URL = "/model-hub/develops/preview_run_prompt_column/"
MODEL = "gpt-4o-mini"
PROVIDER_KEY = "synthetic-preview-key-never-sent"
COMPLETED = "Completed preview output"
MISSING_KEY_MESSAGE = (
    "API key not configured for openai. Please add your API key in settings."
)


@pytest.fixture
def preview_dataset(organization, workspace):
    dataset = Dataset.objects.create(
        name="Preview configuration fixture",
        organization=organization,
        workspace=workspace,
    )
    Row.objects.bulk_create([Row(dataset=dataset, order=i) for i in range(3)])
    return dataset


@pytest.fixture
def provider_key(organization, workspace):
    # A real workspace credential, with no organization fallback.
    assert not ApiKey.objects.filter(
        organization=organization, provider="openai"
    ).exists()
    return ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key=PROVIDER_KEY,
    )


def payload(dataset, *, model=MODEL, rows=1):
    config = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Say hello"}]}
        ],
        "output_format": "string",
    }
    if model is not None:
        config["model"] = model
    return {
        "dataset_id": str(dataset.id),
        "name": "Preview fixture",
        "first_n_rows": rows,
        "config": config,
    }


def completion(text=COMPLETED):
    return litellm.ModelResponse(
        id="synthetic-preview-completion",
        model=MODEL,
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        usage={"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
    )


@pytest.fixture
def provider(monkeypatch):
    mock = MagicMock(return_value=completion())
    monkeypatch.setattr(litellm, "completion", mock)
    return mock


def assert_no_error_logs(logs):
    assert not [
        event
        for event in logs
        if event.get("log_level") in {"error", "exception", "critical"}
    ]


@pytest.mark.parametrize(
    "model",
    ["not-a-catalog-model", "", "   ", None, "perplexity/pplx-7b-chat"],
    ids=["unknown", "blank", "whitespace", "omitted", "retired"],
)
def test_unavailable_model_is_actionable_client_error(
    auth_client, preview_dataset, provider_key, provider, model
):
    with capture_logs() as logs:
        response = auth_client.post(
            URL, payload(preview_dataset, model=model), format="json"
        )

    provider.assert_not_called()
    assert response.status_code == 400, response.json()
    body = str(response.json())
    message = body.lower()
    assert "model" in message
    assert "choose" in message and "supported" in message
    if model and model.strip():
        # The response names the model that is missing from the catalog.
        assert f"Model '{model}' is not available" in body, body
    else:
        # Nothing to name: no empty quoted model name in the message.
        assert "The selected model is not available" in body, body
    assert_no_error_logs(logs)


@pytest.mark.parametrize("rows", [1, 3])
def test_supported_model_preserves_generated_rows(
    auth_client, preview_dataset, provider_key, provider, rows
):
    with capture_logs() as logs:
        response = auth_client.post(
            URL, payload(preview_dataset, rows=rows), format="json"
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["responses"] == [COMPLETED] * rows
    assert provider.call_count == rows
    for call in provider.call_args_list:
        assert call.kwargs["api_key"] == PROVIDER_KEY
    assert_no_error_logs(logs)


@pytest.mark.parametrize("rows,completed_rows", [(2, 1), (3, 1), (3, 2)])
def test_midrun_key_removal_preserves_completed_preview(
    auth_client, preview_dataset, provider_key, provider, rows, completed_rows
):
    def complete_then_remove_connection(**kwargs):
        # Delete the real connection after a completed provider call, before the
        # next row resolves its credentials. No resolver exception is injected.
        result = completion(f"{COMPLETED} {provider.call_count}")
        if provider.call_count == completed_rows:
            provider_key.delete()
        return result

    provider.side_effect = complete_then_remove_connection
    with capture_logs() as logs:
        response = auth_client.post(
            URL, payload(preview_dataset, rows=rows), format="json"
        )

    assert ApiKey.all_objects.get(pk=provider_key.pk).deleted is True
    assert provider.call_count == completed_rows
    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    assert set(result) == {"responses", "token_usage", "cost"}
    assert result["responses"] == [
        f"{COMPLETED} {i}" for i in range(1, completed_rows + 1)
    ] + [f"Error: {MISSING_KEY_MESSAGE}"] * (rows - completed_rows)
    # Preserve the existing last-completed-row usage contract on early stop.
    assert result["token_usage"]["total_tokens"] == 9
    assert_no_error_logs(logs)
    assert len([event for event in logs if event.get("log_level") == "warning"]) == 1


def test_key_removed_after_preflight_marks_all_rows_without_provider_calls(
    auth_client, preview_dataset, provider_key, provider, monkeypatch
):
    resolve_key = LiteLLMModelManager.get_api_key

    def resolve_then_remove_connection(manager, *args, **kwargs):
        key = resolve_key(manager, *args, **kwargs)
        provider_key.delete()
        return key

    monkeypatch.setattr(
        LiteLLMModelManager, "get_api_key", resolve_then_remove_connection
    )
    with capture_logs() as logs:
        response = auth_client.post(
            URL, payload(preview_dataset, rows=3), format="json"
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"] == {
        "responses": [f"Error: {MISSING_KEY_MESSAGE}"] * 3,
        "token_usage": {},
        "cost": {},
    }
    provider.assert_not_called()
    assert_no_error_logs(logs)


def test_missing_key_preflight_rejects_without_provider_calls(
    auth_client, preview_dataset, provider
):
    with capture_logs() as logs:
        response = auth_client.post(
            URL, payload(preview_dataset, rows=3), format="json"
        )

    assert response.status_code == 400, response.json()
    assert MISSING_KEY_MESSAGE in str(response.json())
    provider.assert_not_called()
    assert_no_error_logs(logs)


def test_midrun_key_removal_uses_existing_organization_fallback(
    auth_client, preview_dataset, provider_key, provider, organization
):
    fallback_key = "synthetic-preview-fallback-key"
    fallback = ApiKey.objects.create(
        organization=organization, provider="openai", key=fallback_key
    )
    # The workspace context signal fills unset workspaces on create. Restore
    # an organization-scoped record without firing that signal again.
    ApiKey.objects.filter(pk=fallback.pk).update(workspace=None)

    def complete_then_remove_workspace_connection(**kwargs):
        if provider.call_count == 1:
            provider_key.delete()
        return completion()

    provider.side_effect = complete_then_remove_workspace_connection
    response = auth_client.post(URL, payload(preview_dataset, rows=3), format="json")

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["responses"] == [COMPLETED] * 3
    assert [call.kwargs["api_key"] for call in provider.call_args_list] == [
        PROVIDER_KEY,
        fallback_key,
        fallback_key,
    ]


def test_custom_model_uses_its_own_credentials(
    auth_client, preview_dataset, provider, organization, workspace
):
    model = "synthetic-custom-preview-model"
    CustomAIModel.objects.create(
        user_model_id=model,
        provider="openai",
        input_token_cost=0.01,
        output_token_cost=0.02,
        organization=organization,
        workspace=workspace,
        key_config={"api_key": PROVIDER_KEY},
    )
    response = auth_client.post(
        URL, payload(preview_dataset, model=model), format="json"
    )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["responses"] == [COMPLETED]
    provider.assert_called_once()
    assert provider.call_args.kwargs["api_key"] == PROVIDER_KEY


def test_unexpected_preflight_value_error_remains_server_error(
    auth_client, preview_dataset, provider, monkeypatch
):
    def unexpected_failure(*args, **kwargs):
        raise ValueError("Unexpected resolver failure")

    monkeypatch.setattr(LiteLLMModelManager, "get_provider", unexpected_failure)
    with capture_logs() as logs:
        response = auth_client.post(URL, payload(preview_dataset), format="json")

    assert response.status_code == 500, response.json()
    assert response.json()["code"] == "server_error"
    provider.assert_not_called()
    assert len([event for event in logs if event.get("exc_info")]) == 1
