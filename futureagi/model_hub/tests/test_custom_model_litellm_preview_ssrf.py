"""The run-prompt preview of an openai custom model, through the real stack.

PreviewRunPromptColumnView -> RunPrompt -> LLMHandler -> litellm -> the
OpenAI SDK, against loopback servers. A hosted public ``api_base`` that
redirects to the metadata server must not reach it, and what the tenant sees
in the preview must be generic categorised text, not the provider's error
(which carried the redirect target's body). Only the dataset lookups are
stubbed, as in test_custom_model_ssrf.py (the Dataset model cannot be stored
in the SQLite test database).
"""

import uuid
from types import SimpleNamespace

import pytest
from django.test import override_settings
from rest_framework import status

from model_hub.models.custom_models import CustomAIModel
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    PUBLIC_NAME,
    SECRET_MARKER,
    clear_proxy_env,
    metadata_stand_in,
    model_server,
    redirector,
    treat_as_public,
)

PREVIEW_URL = "/model-hub/develops/preview_run_prompt_column/"
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add(PUBLIC_NAME, "127.0.0.1")
    return resolver


@pytest.fixture
def hosted(monkeypatch):
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


class _Rows(list):
    def order_by(self, *fields):
        return self

    def values_list(self, *fields, flat=False):
        return [row.order for row in self]


@pytest.fixture
def preview_dataset(db, monkeypatch, user, workspace):
    dataset = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=user.organization.id,
        organization=user.organization,
        workspace=workspace,
    )
    rows = [SimpleNamespace(id=uuid.uuid4(), order=0)]

    class _Datasets:
        def filter(self, **kwargs):
            return self

        def first(self):
            return dataset

    monkeypatch.setattr(
        "model_hub.views.run_prompt._request_dataset_queryset",
        lambda request: _Datasets(),
    )
    monkeypatch.setattr(
        "model_hub.views.run_prompt.Row",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **kwargs: _Rows(rows))),
    )
    monkeypatch.setattr(
        "model_hub.views.run_prompt.populate_placeholders",
        lambda messages, **kwargs: messages,
    )
    return dataset


def _make_openai_model(user, workspace, api_base, name="openai-custom"):
    return CustomAIModel.objects.create(
        user_model_id=name,
        provider="openai",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={
            "key": "sk-tenant",
            "api_base": api_base,
            "extra_headers": {"Metadata-Flavor": "Google"},
        },
        user=user,
        deleted=False,
    )


def _preview(client, dataset, model_name):
    return client.post(
        PREVIEW_URL,
        {
            "dataset_id": str(dataset.id),
            "name": "SSRF preview",
            "first_n_rows": 1,
            "config": {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Say hello"}],
                    }
                ],
            },
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("redirect_status", [302, 307])
def test_preview_of_a_redirecting_openai_model_never_reaches_metadata(
    hosted, auth_client, user, workspace, preview_dataset, resolver, redirect_status
):
    with metadata_stand_in() as metadata:
        target = (
            f"http://[::1]:{metadata.port}/latest/meta-data/iam/security-credentials/r"
        )
        with redirector(target, redirect_status) as public:
            _make_openai_model(
                user, workspace, f"http://{PUBLIC_NAME}:{public.port}/v1"
            )
            response = _preview(auth_client, preview_dataset, "openai-custom")
        assert metadata.requests == []
    assert response.status_code == status.HTTP_200_OK, response.json()
    (answer,) = response.json()["result"]["responses"]
    assert SECRET_MARKER not in str(response.json())
    assert ssrf_guard.ENDPOINT_ERROR_MESSAGES["redirect"] in answer
    assert "public-a.test" not in answer
    assert "metadata-flavor" not in public.requests[0].headers


@pytest.mark.django_db
def test_preview_of_a_public_openai_model_works_end_to_end(
    hosted, auth_client, user, workspace, preview_dataset, resolver
):
    with model_server() as server:
        _make_openai_model(user, workspace, f"http://{PUBLIC_NAME}:{server.port}/v1")
        response = _preview(auth_client, preview_dataset, "openai-custom")
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["result"]["responses"] == [MODEL_ANSWER]
    [seen] = server.requests
    assert seen.headers["authorization"] == "Bearer sk-tenant"
    assert "metadata-flavor" not in seen.headers
