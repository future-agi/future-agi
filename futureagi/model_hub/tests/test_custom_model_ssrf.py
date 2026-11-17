"""Custom model endpoints cannot reach internal addresses (SSRF).

The create and edit views validated only ``config_json.api_base`` and then
saved ``config_json`` verbatim, while the run-prompt handler POSTs to
``endpoint_url``. Now the stored config holds one endpoint, the one that was
validated, and every save and every call checks it with the deployment's
address policy (``tfc.utils.ssrf_guard``): hosted refuses everything but
public addresses; a self-hosted install keeps localhost and private-network
models working with no setting, refuses link-local and cloud metadata, and
CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS=true tightens it to public only.

DNS and the transport are faked; any request sent through ``requests``
instead of the guard is recorded and refused (``UnguardedHttp``).
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from django.test import override_settings
from rest_framework import status

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils.utils import validate_model_working
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import (
    COMPLETION_BODY,
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
    UnguardedHttp,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

CREATE_URL = "/model-hub/custom_models/create/"
EDIT_URL = "/model-hub/custom_models/edit/"
PREVIEW_URL = "/model-hub/develops/preview_run_prompt_column/"
PUBLIC_IP = "93.184.216.34"
PUBLIC_URL = "https://api.example.com/v1/chat/completions"
METADATA_TOKEN_URL = (
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
)
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"


def _blocked():
    return ssrf_guard.ENDPOINT_ERROR_MESSAGES["blocked"]


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return resolver


@pytest.fixture
def transport(monkeypatch):
    return FakeTransport().install(monkeypatch)


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    return UnguardedHttp().install(monkeypatch)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture
def litellm_calls(monkeypatch):
    """litellm.completion as the validation calls it, answering a success."""
    calls = []

    def _completion(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr("model_hub.utils.utils.litellm.completion", _completion)
    return calls


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


def _create(client, config_json, provider="custom", name="ssrf-model", **extra):
    body = {
        "model_provider": provider,
        "model_name": name,
        "input_token_cost": 0,
        "output_token_cost": 0,
        "config_json": config_json,
        **extra,
    }
    return client.post(CREATE_URL, body, format="json")


def _custom_config(url, headers=None):
    # The payload the Configure Custom Model form sends.
    return {"headers": headers or {}, "api_base": url, "custom_provider": True}


def _saved(name="ssrf-model"):
    return CustomAIModel.no_workspace_objects.filter(user_model_id=name).first()


def _message(response):
    return response.json().get("message") or response.json().get("result")


def _make_model(user, workspace, key_config, provider="custom", name="ssrf-model"):
    return CustomAIModel.objects.create(
        user_model_id=name,
        provider=provider,
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config=key_config,
        user=user,
        deleted=False,
    )


# --------------------------------------------------------- hosted: create ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    "host, address",
    [
        ("alias-metadata.example.com", "169.254.169.254"),
        ("alias-loopback.example.com", "127.0.0.1"),
        ("alias-lan.example.com", "10.0.0.8"),
        ("alias-mapped.example.com", "::ffff:169.254.169.254"),
        ("alias-nat64.example.com", "64:ff9b::a9fe:a9fe"),
    ],
)
def test_hosted_create_refuses_internal_api_base(
    hosted, auth_client, resolver, transport, unguarded, host, address
):
    resolver.add(host, address)
    response = _create(
        auth_client, _custom_config(f"http://{host}/v1/chat/completions")
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert address not in _message(response)
    assert transport.pools == []
    assert unguarded.calls == []
    assert _saved() is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        METADATA_TOKEN_URL,
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[::ffff:a9fe:a9fe]/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
    ],
)
def test_hosted_create_refuses_metadata_literals_and_names(
    hosted, auth_client, resolver, transport, unguarded, url
):
    response = _create(auth_client, _custom_config(url, {"Metadata-Flavor": "Google"}))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert transport.pools == []
    assert unguarded.calls == []
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_create_closes_the_api_base_endpoint_url_split(
    hosted, auth_client, resolver, transport, unguarded
):
    """A validated public api_base cannot smuggle an internal endpoint_url."""
    config = {
        "api_base": PUBLIC_URL,
        "endpoint_url": METADATA_TOKEN_URL,
        "headers": {"Metadata-Flavor": "Google"},
    }
    response = _create(auth_client, config)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert transport.requests == []
    assert unguarded.calls == []
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_create_with_a_key_still_checks_the_saved_endpoint(
    hosted, auth_client, resolver, transport, unguarded, litellm_calls
):
    """Validating with a key never looked at config_json, which was saved."""
    response = _create(
        auth_client,
        {"endpoint_url": METADATA_TOKEN_URL, "headers": {"Metadata-Flavor": "Google"}},
        key="sk-tenant",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_create_saves_one_validated_endpoint(
    hosted, auth_client, resolver, transport, unguarded
):
    config = {
        "endpoint_url": PUBLIC_URL,
        "headers": {"Authorization": "Bearer tenant", "Metadata-Flavor": "Google"},
        "extra": "dropped",
    }
    response = _create(auth_client, config)
    assert response.status_code == status.HTTP_200_OK, response.json()
    # The validation POST went to the pinned public address of that URL.
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]
    assert transport.requests[0]["url"] == "/v1/chat/completions"
    assert "metadata-flavor" not in transport.sent_headers
    # And exactly that URL is what is stored, under one key.
    assert _saved().actual_json == {
        "api_base": PUBLIC_URL,
        "headers": {"Authorization": "Bearer tenant"},
    }
    assert unguarded.calls == []


@pytest.mark.django_db
def test_hosted_create_does_not_follow_a_redirect_to_internal(
    hosted, auth_client, resolver, transport, unguarded
):
    transport.respond(FakeResponse(302, {"Location": METADATA_TOKEN_URL}))
    response = _create(auth_client, _custom_config(PUBLIC_URL))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == ssrf_guard.ENDPOINT_ERROR_MESSAGES["redirect"]
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]
    assert len(transport.requests) == 1
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_plain_http_public_endpoint_still_works(
    hosted, auth_client, resolver, transport, unguarded
):
    url = "http://api.example.com/v1/chat/completions"
    response = _create(auth_client, _custom_config(url))
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert transport.pools[0]["scheme"] == "http"
    assert transport.pools[0]["host"] == PUBLIC_IP
    assert _saved().actual_json["api_base"] == url


@pytest.mark.django_db
def test_hosted_validation_errors_are_generic(
    hosted, auth_client, resolver, transport, unguarded
):
    transport.respond(FakeResponse(500, JSON_HEADERS, b"internal-body-marker"))
    response = _create(auth_client, _custom_config(PUBLIC_URL))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == "The endpoint answered HTTP 500 Internal Server Error."
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_404_message_keeps_its_hint_without_the_url(
    hosted, auth_client, resolver, transport, unguarded
):
    transport.respond(FakeResponse(404, JSON_HEADERS, b"internal-body-marker"))
    response = _create(auth_client, _custom_config(PUBLIC_URL))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    message = _message(response)
    assert "answered 404 Not Found." in message
    assert "full chat-completions URL" in message
    assert "ssrf-model" in message
    assert "api.example.com" not in message
    assert "internal-body-marker" not in message


@pytest.mark.django_db
def test_hosted_openai_api_base_internal_is_refused(
    hosted, auth_client, resolver, transport, unguarded, litellm_calls
):
    resolver.add("lan.example.com", "10.0.0.9")
    response = _create(
        auth_client,
        {"key": "sk-tenant", "api_base": "http://lan.example.com:8000/v1"},
        provider="openai",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert litellm_calls == []
    assert _saved() is None


@pytest.mark.django_db
def test_hosted_azure_api_base_metadata_is_refused(
    hosted, auth_client, resolver, transport, unguarded, litellm_calls
):
    response = _create(
        auth_client,
        {
            "api_base": "http://169.254.169.254/",
            "api_key": "azure-key",
            "api_version": "2024-02-01",
        },
        provider="azure",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert litellm_calls == []
    assert _saved() is None


# ----------------------------------------------------------- hosted: edit ---


@pytest.mark.django_db
def test_hosted_edit_refuses_internal_endpoint(
    hosted, auth_client, user, workspace, resolver, transport, unguarded
):
    original = _custom_config(PUBLIC_URL)
    model = _make_model(user, workspace, original)
    response = auth_client.patch(
        EDIT_URL,
        {
            "id": str(model.id),
            "config_json": _custom_config(
                "http://metadata.google.internal/computeMetadata/v1/",
                {"Metadata-Flavor": "Google"},
            ),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert transport.pools == []
    assert CustomAIModel.no_workspace_objects.get(id=model.id).actual_json == original


@pytest.mark.django_db
def test_hosted_edit_with_key_and_internal_config_is_refused(
    hosted, auth_client, user, workspace, resolver, transport, unguarded, litellm_calls
):
    original = _custom_config(PUBLIC_URL)
    model = _make_model(user, workspace, original)
    response = auth_client.patch(
        EDIT_URL,
        {
            "id": str(model.id),
            "key": "sk-tenant",
            "config_json": {"endpoint_url": METADATA_TOKEN_URL},
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert CustomAIModel.no_workspace_objects.get(id=model.id).actual_json == original


# --------------------------------------------------------------- self-host ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    "host, address",
    [("localhost", "127.0.0.1"), ("gpu-box.lan", "192.168.1.50")],
)
def test_self_hosted_local_and_private_models_work_without_a_setting(
    self_hosted, auth_client, resolver, transport, unguarded, host, address
):
    resolver.add(host, address)
    url = f"http://{host}:8000/v1/chat/completions"
    response = _create(auth_client, _custom_config(url))
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert transport.pools[0]["host"] == address
    assert _saved().actual_json["api_base"] == url


@pytest.mark.django_db
def test_self_hosted_metadata_is_refused_by_default(
    self_hosted, auth_client, resolver, transport, unguarded
):
    response = _create(
        auth_client, _custom_config(METADATA_TOKEN_URL, {"Metadata-Flavor": "Google"})
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert transport.pools == []
    assert _saved() is None


@pytest.mark.django_db
def test_self_hosted_setting_refuses_localhost(
    self_hosted, monkeypatch, auth_client, resolver, transport, unguarded
):
    monkeypatch.setenv(DENY_ENV, "true")
    resolver.add("localhost", "127.0.0.1")
    response = _create(
        auth_client, _custom_config("http://localhost:8000/v1/chat/completions")
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == _blocked()
    assert transport.pools == []


@pytest.mark.django_db
def test_self_hosted_openai_compatible_localhost_still_saves(
    self_hosted, auth_client, resolver, transport, unguarded, litellm_calls
):
    resolver.add("localhost", "127.0.0.1")
    response = _create(
        auth_client,
        {"key": "sk-local", "api_base": "http://localhost:8000/v1"},
        provider="openai",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert litellm_calls[0]["api_base"] == "http://localhost:8000/v1"


# --------------------------------------------------- validate_model_working ---


def test_validate_custom_refuses_metadata_on_hosted(
    hosted, resolver, transport, unguarded
):
    result = validate_model_working(
        model_name="m",
        api_key={"config_json": {"api_base": METADATA_TOKEN_URL, "headers": {}}},
        provider="custom",
    )
    assert isinstance(result, Exception)
    assert str(result) == _blocked()
    assert transport.pools == []
    assert unguarded.calls == []


def test_validate_custom_posts_through_the_guard(
    hosted, resolver, transport, unguarded
):
    result = validate_model_working(
        model_name="m",
        api_key={
            "config_json": {
                "api_base": PUBLIC_URL,
                "headers": {"X-Api-Key": "k", "X-Forwarded-For": "127.0.0.1"},
            }
        },
        provider="custom",
    )
    assert result == COMPLETION_BODY.decode()
    assert transport.pools[0]["host"] == PUBLIC_IP
    assert transport.sent_headers["x-api-key"] == "k"
    assert "x-forwarded-for" not in transport.sent_headers
    assert unguarded.calls == []


def test_validate_openai_private_api_base_on_hosted(
    hosted, resolver, transport, unguarded, litellm_calls
):
    resolver.add("lan.example.com", "192.168.4.4")
    result = validate_model_working(
        model_name="gpt-x",
        api_key={"api_key": "sk", "api_base": "http://lan.example.com/v1"},
        provider="openai",
    )
    assert isinstance(result, Exception)
    assert str(result) == _blocked()
    assert litellm_calls == []


# ------------------------------------------- call time: runtime key lookup ---


@pytest.mark.django_db
def test_runtime_key_lookup_refuses_a_stored_internal_api_base(
    hosted, user, workspace, resolver
):
    """Rows saved before this fix are checked when they are used."""
    resolver.add("lan.example.com", "10.0.0.9")
    _make_model(
        user,
        workspace,
        {"key": "sk-tenant", "api_base": "http://lan.example.com:8000/v1"},
        provider="openai",
        name="legacy-openai",
    )
    manager = LiteLLMModelManager("legacy-openai", organization_id=user.organization.id)
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        manager.get_api_key(organization_id=user.organization.id)
    assert str(excinfo.value) == _blocked()


@pytest.mark.django_db
def test_runtime_key_lookup_returns_a_public_api_base(
    hosted, user, workspace, resolver
):
    _make_model(
        user,
        workspace,
        {"key": "sk-tenant", "api_base": "https://api.example.com/v1"},
        provider="openai",
        name="public-openai",
    )
    manager = LiteLLMModelManager("public-openai", organization_id=user.organization.id)
    config = manager.get_api_key(organization_id=user.organization.id)
    assert config["api_base"] == "https://api.example.com/v1"


# ---------------------------------------------------- preview (run prompt) ---


class _Rows(list):
    def order_by(self, *fields):
        return self

    def values_list(self, *fields, flat=False):
        return [row.order for row in self]


@pytest.fixture
def preview_dataset(db, monkeypatch, user, workspace):
    """The dataset and row the preview view reads.

    The view, RunPrompt, the handler factory, CustomModelHandler and the
    custom model row are all real; only the dataset lookups are stubbed,
    because the Dataset model cannot be stored in the SQLite test database.
    """
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
def test_preview_runs_a_public_custom_model_end_to_end(
    hosted,
    auth_client,
    user,
    workspace,
    preview_dataset,
    resolver,
    transport,
    unguarded,
):
    _make_model(
        user,
        workspace,
        _custom_config(PUBLIC_URL, {"Authorization": "Bearer tenant"}),
        name="public-custom",
    )
    response = _preview(auth_client, preview_dataset, "public-custom")
    assert response.status_code == status.HTTP_200_OK, response.json()
    # The cell is the assistant message in COMPLETION_BODY, not the body.
    assert response.json()["result"]["responses"] == ["public model answer"]
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]
    sent = json.loads(transport.requests[0]["body"])
    assert sent["messages"][0]["role"] == "user"
    assert transport.sent_headers["authorization"] == "Bearer tenant"
    assert unguarded.calls == []


@pytest.mark.django_db
def test_preview_refuses_a_legacy_internal_endpoint_url(
    hosted,
    auth_client,
    user,
    workspace,
    preview_dataset,
    resolver,
    transport,
    unguarded,
):
    """A row saved before the fix, api_base public and endpoint_url metadata."""
    _make_model(
        user,
        workspace,
        {
            "api_base": PUBLIC_URL,
            "endpoint_url": METADATA_TOKEN_URL,
            "headers": {"Metadata-Flavor": "Google"},
        },
        name="legacy-custom",
    )
    response = _preview(auth_client, preview_dataset, "legacy-custom")
    assert response.status_code == status.HTTP_200_OK
    (answer,) = response.json()["result"]["responses"]
    assert _blocked() in answer
    assert "169.254" not in answer
    assert transport.pools == []
    assert unguarded.calls == []
