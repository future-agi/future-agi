"""A model configuration that holds a reference value is refused.

A provider key or configuration must give the customer's own value; a value
that holds a reference (such as os.environ/... or oidc/...) instead of a
literal value is not a valid key or setting. Such a value is refused, with
one plain sentence that repeats nothing of it, when a provider key or a
custom model is saved or edited, and
again before every call, so a row stored before the save-time check (written
here straight to the table) is refused too. The tests use values they set
themselves and never open a connection outside 127.0.0.1.
"""

import json
import secrets
import uuid
from unittest.mock import patch

import httpx
import pytest
from django.test import override_settings
from rest_framework import status

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils import model_endpoints
from model_hub.utils.utils import validate_model_working
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    LoopbackServer,
    clear_proxy_env,
    model_server,
    treat_as_public,
)

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
API_KEYS_URL = "/model-hub/api-keys/"
CUSTOM_MODEL_CREATE_URL = "/model-hub/custom_models/create/"
CUSTOM_MODEL_EDIT_URL = "/model-hub/custom_models/edit/"
CHAT = [{"role": "user", "content": "hi"}]
BEDROCK_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
AZURE_BASE = "https://fi-test.openai.azure.example.test"
SYNTHETIC_SECRET = "fi-test-synthetic-secret-not-real"

# How a value can be written: the forms the check must catch, trimmed and in
# any case.
REFERENCE_FORMS = {
    "lower": "os.environ/{name}",
    "padded-upper": "  OS.ENVIRON/{name}\t",
    "second-form": "oidc/google/{name}",
    "second-form-mixed-case": " Oidc/aws/{name}",
}


class Canary:
    """A variable each test sets itself: a random name and a random value."""

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def written(self, form="lower"):
        return REFERENCE_FORMS[form].format(name=self.name)

    @property
    def reference(self):
        return self.written("lower")

    def absent_from(self, *texts):
        return all(self.value not in str(text) for text in texts)


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)
    monkeypatch.setattr(llm_module, "LITELLM_NUM_RETRIES", 0)


@pytest.fixture
def canary(monkeypatch):
    name = f"FI_TEST_CANARY_{uuid.uuid4().hex[:12].upper()}"
    value = f"canary-{secrets.token_hex(16)}"
    monkeypatch.setenv(name, value)
    return Canary(name, value)


@pytest.fixture
def hosted(monkeypatch):
    """Hosted policy; the loopback address stands in for a public host."""
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def model_calls(monkeypatch):
    """Every completion and image call made, in order; each still runs."""
    import litellm

    calls = []
    for name in ("completion", "image_generation"):
        original = getattr(litellm, name)

        def make(original, name):
            def recorded(*args, **kwargs):
                calls.append((name, args, kwargs))
                return original(*args, **kwargs)

            return recorded

        monkeypatch.setattr(litellm, name, make(original, name))
    return calls


def _record_outbound(monkeypatch):
    """Answer every HTTP request the process sends with a 401 and record it,
    instead of sending it."""
    seen = []

    def handle_request(self, request):
        seen.append(request)
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "unauthorized",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
            request=request,
        )

    async def handle_async_request(self, request):
        return handle_request(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )
    return seen


def _request_text(request):
    if isinstance(request, httpx.Request):
        try:
            body = request.read()
        except Exception:
            body = b""
        return f"{request.method} {request.url} {dict(request.headers)} {body!r}"
    return f"{request.method} {request.target} {request.headers} {request.body!r}"


def _call(function, *args, **kwargs):
    """The exception ``function`` raised, or None."""
    try:
        function(*args, **kwargs)
    except Exception as exc:
        return exc
    return None


def _assert_refused_before_sending(
    error, canary, requests, model_calls, refusal_type=ValueError
):
    sent = [_request_text(request) for request in requests]
    called = [repr(call) for call in model_calls]
    assert canary.absent_from(*sent, *called), "the variable's value was sent"
    assert sent == [], "a request was sent"
    assert called == [], "the model was called"
    assert isinstance(error, refusal_type), repr(error)
    assert str(error) == REFUSAL
    assert canary.absent_from(str(error), repr(error))
    assert canary.name not in str(error)


def _assert_refusal_response(response, canary, *written):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    text = json.dumps(response.data, default=str)
    assert REFUSAL in text
    assert canary.name not in text
    assert canary.value not in text
    for value in written:
        assert value.strip() not in text
    lowered = text.lower()
    assert "environ" not in lowered
    assert "oidc" not in lowered


def _base(server, path=""):
    return f"{server.base}{path}"


def _store_provider_key(organization, user, provider, *, key=None, config_json=None):
    """A provider key row written straight to the table: no save-time check."""
    return ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider=provider,
        key=key,
        config_json=config_json,
        workspace=None,
    )


def _stored_credential(organization, provider=None, model_name=""):
    manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization.id
    )
    return manager.get_api_key(organization_id=organization.id, provider=provider)


def _chat_payload(model, provider, api_key):
    """The arguments the run-prompt chat handler builds from a stored key."""
    payload = {"model": model, "messages": CHAT}
    PayloadBuilder._add_api_key(payload, provider, api_key)
    return payload


def _image_params(model, api_key):
    """The arguments the run-prompt image handler builds from a stored key."""
    params = {"model": model, "prompt": "a cat", "size": "1024x1024", "n": 1}
    if isinstance(api_key, dict):
        for name in ("api_key", "api_base"):
            if name in api_key:
                params[name] = api_key[name]
    elif api_key:
        params["api_key"] = api_key
    return params


def agents_server():
    """A public agents API: thread, message, run, poll, messages."""

    def answer(request):
        path = request.target.split("?", 1)[0]
        headers = {"Content-Type": "application/json"}
        if request.method == "POST" and path.endswith("/threads"):
            return 200, headers, b'{"id": "thread_1"}'
        if request.method == "POST" and path.endswith("/threads/thread_1/runs"):
            return 200, headers, b'{"id": "run_1"}'
        if request.method == "POST":
            return 200, headers, b'{"id": "msg_1"}'
        if path.endswith("/runs/run_1"):
            return 200, headers, b'{"status": "completed"}'
        body = {
            "data": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": {"value": MODEL_ANSWER}}],
                }
            ]
        }
        return 200, headers, json.dumps(body).encode()

    return LoopbackServer(answer)


def responses_server():
    """A public OpenAI-compatible endpoint that answers the Responses API."""
    body = json.dumps(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 1700000000,
            "status": "completed",
            "model": "gpt-5-pro",
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": MODEL_ANSWER, "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    ).encode()
    return LoopbackServer(
        lambda request: (200, {"Content-Type": "application/json"}, body)
    )


# ------------------------------------------------- saving a provider key ---


def _provider_key_payloads(reference):
    azure = {"api_key": reference, "api_base": AZURE_BASE, "api_version": "2024-06-01"}
    return {
        "key-only": {"provider": "openai", "key": reference},
        "azure-config": {"provider": "azure", "config_json": azure},
        "azure-config-as-text": {"provider": "azure", "key": json.dumps(azure)},
        "bedrock-config": {
            "provider": "bedrock",
            "config_json": {
                "aws_access_key_id": reference,
                "aws_secret_access_key": SYNTHETIC_SECRET,
                "aws_region_name": "us-east-1",
            },
        },
        "vertex-config": {
            "provider": "vertex_ai",
            "config_json": {
                "type": "service_account",
                "project_id": "fi-test-project",
                "private_key": reference,
                "client_email": "fi-test@fi-test-project.example.test",
            },
        },
    }


PROVIDER_KEY_CASES = list(_provider_key_payloads("x"))


@pytest.mark.django_db
@pytest.mark.parametrize("case", PROVIDER_KEY_CASES, ids=PROVIDER_KEY_CASES)
def test_saving_a_provider_key_that_names_a_platform_value_is_refused(
    auth_client, organization, canary, case
):
    payload = _provider_key_payloads(canary.reference)[case]

    response = auth_client.post(API_KEYS_URL, payload, format="json")

    _assert_refusal_response(response, canary, canary.reference)
    assert not ApiKey._base_manager.filter(organization=organization).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("form", list(REFERENCE_FORMS), ids=list(REFERENCE_FORMS))
def test_every_written_form_of_a_platform_value_is_refused(
    auth_client, organization, canary, form
):
    written = canary.written(form)

    response = auth_client.post(
        API_KEYS_URL, {"provider": "openai", "key": written}, format="json"
    )

    _assert_refusal_response(response, canary, written)
    assert not ApiKey._base_manager.filter(organization=organization).exists()


def _provider_key_edits(reference):
    azure = {
        "api_key": "fi-test-azure-key-not-real",
        "api_base": AZURE_BASE,
        "api_version": "2024-06-01",
    }
    bedrock = {
        "aws_access_key_id": "fi-test-access-key-not-real",
        "aws_secret_access_key": SYNTHETIC_SECRET,
        "aws_region_name": "us-east-1",
    }
    return {
        "key-only": (
            {"provider": "openai", "key": "sk-fi-test-valid-not-real"},
            {"provider": "openai", "key": reference},
        ),
        "azure-config": (
            {"provider": "azure", "config_json": azure},
            {"provider": "azure", "config_json": {**azure, "api_key": reference}},
        ),
        "bedrock-config": (
            {"provider": "bedrock", "config_json": bedrock},
            {
                "provider": "bedrock",
                "config_json": {**bedrock, "aws_secret_access_key": reference},
            },
        ),
    }


PROVIDER_KEY_EDIT_CASES = list(_provider_key_edits("x"))


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["put", "patch"], ids=["put", "patch"])
@pytest.mark.parametrize("case", PROVIDER_KEY_EDIT_CASES, ids=PROVIDER_KEY_EDIT_CASES)
def test_editing_a_provider_key_to_name_a_platform_value_is_refused(
    auth_client, canary, case, method
):
    saved, edit = _provider_key_edits(canary.reference)[case]
    created = auth_client.post(API_KEYS_URL, saved, format="json")
    assert created.status_code == status.HTTP_200_OK, created.data
    pk = created.data["result"]["id"]
    before = ApiKey._base_manager.get(pk=pk)
    stored_before = (before.actual_key, before.actual_json)

    response = getattr(auth_client, method)(f"{API_KEYS_URL}{pk}/", edit, format="json")

    _assert_refusal_response(response, canary, canary.reference)
    after = ApiKey._base_manager.get(pk=pk)
    assert (after.actual_key, after.actual_json) == stored_before


# ------------------------------------------------- saving a custom model ---


def _custom_model_payloads(reference):
    return {
        "openai-key": {"model_provider": "openai", "config_json": {"key": reference}},
        "openai-endpoint-key": {
            "model_provider": "openai",
            "config_json": {
                "key": reference,
                "api_base": "https://fi-test-model.example.test/v1",
            },
        },
        "azure-key": {
            "model_provider": "azure",
            "config_json": {
                "api_base": AZURE_BASE,
                "api_key": reference,
                "api_version": "2024-06-01",
            },
        },
        "bedrock-keys": {
            "model_provider": "bedrock",
            "config_json": {
                "aws_access_key_id": reference,
                "aws_secret_access_key": SYNTHETIC_SECRET,
                "aws_region_name": "us-east-1",
            },
        },
        "bedrock-key-field": {
            "model_provider": "bedrock",
            "config_json": {},
            "key": reference,
        },
        "custom-header": {
            "model_provider": "custom",
            "config_json": {
                "api_base": "https://fi-test-model.example.test/v1/chat/completions",
                "headers": {"Authorization": reference},
            },
        },
    }


CUSTOM_MODEL_CASES = list(_custom_model_payloads("x"))


@pytest.mark.django_db
@pytest.mark.parametrize("case", CUSTOM_MODEL_CASES, ids=CUSTOM_MODEL_CASES)
def test_creating_a_custom_model_that_names_a_platform_value_is_refused_before_it_is_checked(
    auth_client, organization, canary, case
):
    payload = {
        "model_name": "fi-test-model",
        "input_token_cost": 0.0,
        "output_token_cost": 0.0,
        **_custom_model_payloads(canary.reference)[case],
    }

    with patch(
        "model_hub.views.custom_model.validate_model_working", return_value=True
    ) as checked:
        response = auth_client.post(CUSTOM_MODEL_CREATE_URL, payload, format="json")

    _assert_refusal_response(response, canary, canary.reference)
    checked.assert_not_called()
    assert not CustomAIModel._base_manager.filter(organization=organization).exists()


CUSTOM_MODEL_EDIT_CASES = ["key-field", "config"]


@pytest.mark.django_db
@pytest.mark.parametrize("case", CUSTOM_MODEL_EDIT_CASES, ids=CUSTOM_MODEL_EDIT_CASES)
def test_editing_a_custom_model_to_name_a_platform_value_is_refused_before_it_is_checked(
    auth_client, organization, user, workspace, canary, case
):
    model = CustomAIModel.objects.create(
        user_model_id="fi-test-edit-model",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"key": "sk-fi-test-valid-not-real"},
    )
    edit = {"id": str(model.id)}
    if case == "key-field":
        edit["key"] = canary.reference
    else:
        edit["config_json"] = {"key": canary.reference}

    with patch(
        "model_hub.views.custom_model.validate_model_working", return_value=True
    ) as checked:
        response = auth_client.patch(CUSTOM_MODEL_EDIT_URL, edit, format="json")

    _assert_refusal_response(response, canary, canary.reference)
    checked.assert_not_called()
    assert CustomAIModel._base_manager.get(pk=model.pk).actual_json == {
        "key": "sk-fi-test-valid-not-real"
    }


@pytest.mark.parametrize(
    "provider, api_key",
    [
        ("openai", lambda reference: {"key": reference}),
        (
            "bedrock",
            lambda reference: {
                "aws_access_key_id": reference,
                "aws_secret_access_key": SYNTHETIC_SECRET,
                "aws_region_name": "us-east-1",
            },
        ),
    ],
    ids=["key-only", "bedrock"],
)
def test_checking_a_model_that_names_a_platform_value_never_calls_it(
    monkeypatch, canary, model_calls, provider, api_key
):
    requests = _record_outbound(monkeypatch)
    model = "gpt-4o-mini" if provider == "openai" else BEDROCK_MODEL

    result = validate_model_working(
        model_name=model, api_key=api_key(canary.reference), provider=provider
    )

    # The check answers with the exception it would raise, as it does for
    # every configuration it rejects.
    _assert_refused_before_sending(
        result, canary, requests, model_calls, refusal_type=Exception
    )


# ------------------------------------- a row already stored, at call time ---


@pytest.mark.django_db
def test_a_stored_azure_foundry_key_naming_a_platform_value_is_refused_before_a_completion(
    hosted, canary, model_calls, organization, user
):
    with model_server() as server:
        _store_provider_key(
            organization,
            user,
            "azure",
            config_json={
                "api_base": _base(server),
                "api_key": canary.reference,
                "azure_endpoint_type": "foundry",
            },
        )

        def run():
            api_key = _stored_credential(organization, "azure")
            payload = _chat_payload("fi-test-deployment", "azure", api_key)
            return model_endpoints.guarded_completion(**payload, drop_params=True)

        error = _call(run)
    _assert_refused_before_sending(error, canary, server.requests, model_calls)


@pytest.mark.django_db
def test_a_stored_azure_key_naming_a_platform_value_is_refused_before_image_generation(
    hosted, canary, model_calls, organization, user
):
    with model_server() as server:
        _store_provider_key(
            organization,
            user,
            "azure",
            config_json={
                "api_base": _base(server),
                "api_key": canary.reference,
                "api_version": "2024-02-01",
                "azure_endpoint_type": "legacy",
            },
        )

        def run():
            api_key = _stored_credential(organization, "azure")
            params = _image_params("azure/dall-e-3", api_key)
            return model_endpoints.guarded_image_generation(**params, drop_params=True)

        error = _call(run)
    _assert_refused_before_sending(error, canary, server.requests, model_calls)


@pytest.mark.django_db
def test_a_stored_azure_foundry_key_naming_a_platform_value_is_refused_before_image_generation(
    hosted, canary, model_calls, organization, user
):
    with model_server() as server:
        _store_provider_key(
            organization,
            user,
            "azure",
            config_json={
                "api_base": _base(server),
                "api_key": canary.reference,
                "azure_endpoint_type": "foundry",
            },
        )

        def run():
            api_key = _stored_credential(organization, "azure")
            params = _image_params("azure_ai/flux-1.1-pro", api_key)
            return model_endpoints.guarded_image_generation(**params, drop_params=True)

        error = _call(run)
    _assert_refused_before_sending(error, canary, server.requests, model_calls)


@pytest.mark.django_db
def test_a_stored_azure_foundry_key_naming_a_platform_value_is_refused_before_an_agent_run(
    hosted, canary, model_calls, organization, user
):
    with agents_server() as server:
        _store_provider_key(
            organization,
            user,
            "azure",
            config_json={
                "api_base": _base(server, "/api/projects/p1"),
                "api_key": canary.reference,
                "azure_endpoint_type": "foundry",
            },
        )

        def run():
            api_key = _stored_credential(organization, "azure")
            payload = _chat_payload("agents/asst_1", "azure", api_key)
            return model_endpoints.guarded_completion(**payload, drop_params=True)

        error = _call(run)
    _assert_refused_before_sending(error, canary, server.requests, model_calls)


@pytest.mark.django_db
def test_a_stored_openai_model_key_naming_a_platform_value_is_refused_before_a_responses_call(
    hosted, canary, model_calls, organization, user, workspace
):
    with responses_server() as server:
        CustomAIModel.objects.create(
            user_model_id="gpt-5-pro",
            provider="openai",
            input_token_cost=0.0,
            output_token_cost=0.0,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config={"key": canary.reference, "api_base": _base(server, "/v1")},
        )

        def run():
            api_key = _stored_credential(organization, model_name="gpt-5-pro")
            llm = llm_module.LLM(
                provider="openai", model_name="openai/gpt-5-pro", api_key=api_key
            )
            return llm.call_llm(CHAT, provider="openai")

        error = _call(run)
    _assert_refused_before_sending(error, canary, server.requests, model_calls)


@pytest.mark.django_db
def test_stored_bedrock_keys_naming_a_platform_value_are_refused_before_the_call(
    monkeypatch, canary, model_calls, organization, user
):
    requests = _record_outbound(monkeypatch)
    _store_provider_key(
        organization,
        user,
        "bedrock",
        config_json={
            "aws_access_key_id": canary.reference,
            "aws_secret_access_key": SYNTHETIC_SECRET,
            "aws_region_name": "us-east-1",
        },
    )

    def run():
        api_key = _stored_credential(organization, "bedrock")
        payload = _chat_payload(BEDROCK_MODEL, "bedrock", api_key)
        return model_endpoints.guarded_completion(**payload, drop_params=True)

    error = _call(run)
    _assert_refused_before_sending(error, canary, requests, model_calls)


@pytest.mark.django_db
def test_a_stored_key_only_value_naming_a_platform_value_is_refused_before_the_call(
    monkeypatch, canary, model_calls, organization, user
):
    requests = _record_outbound(monkeypatch)
    _store_provider_key(organization, user, "openai", key=canary.reference)

    def run():
        api_key = _stored_credential(organization, "openai")
        payload = _chat_payload("gpt-4o-mini", "openai", api_key)
        return model_endpoints.guarded_completion(**payload, drop_params=True)

    error = _call(run)
    _assert_refused_before_sending(error, canary, requests, model_calls)


# ---------------------------------- a value handed straight to the call ---


GUARD_CASES = [
    pytest.param(
        model_server,
        lambda base, reference: model_endpoints.guarded_completion(
            model="azure_ai/fi-test-deployment",
            messages=CHAT,
            api_key=reference,
            api_base=base,
        ),
        id="azure-foundry-completion",
    ),
    pytest.param(
        model_server,
        lambda base, reference: model_endpoints.guarded_image_generation(
            model="azure/dall-e-3",
            prompt="a cat",
            api_key=reference,
            api_base=base,
            api_version="2024-02-01",
        ),
        id="azure-image",
    ),
    pytest.param(
        model_server,
        lambda base, reference: model_endpoints.guarded_image_generation(
            model="azure_ai/flux-1.1-pro",
            prompt="a cat",
            api_key=reference,
            api_base=base,
        ),
        id="azure-foundry-image",
    ),
    pytest.param(
        agents_server,
        lambda base, reference: model_endpoints.guarded_completion(
            model="azure_ai/agents/asst_1",
            messages=CHAT,
            api_key=reference,
            api_base=f"{base}/api/projects/p1",
        ),
        id="azure-foundry-agents",
    ),
    pytest.param(
        responses_server,
        lambda base, reference: model_endpoints.guarded_completion(
            model="openai/gpt-5-pro",
            messages=CHAT,
            api_key=reference,
            api_base=f"{base}/v1",
        ),
        id="openai-responses",
    ),
    pytest.param(
        None,
        lambda base, reference: model_endpoints.guarded_completion(
            model=BEDROCK_MODEL,
            custom_llm_provider="bedrock",
            messages=CHAT,
            aws_access_key_id=reference,
            aws_secret_access_key=SYNTHETIC_SECRET,
            aws_region_name="us-east-1",
        ),
        id="bedrock",
    ),
    pytest.param(
        None,
        lambda base, reference: model_endpoints.guarded_completion(
            model="gpt-4o-mini", messages=CHAT, api_key=reference
        ),
        id="key-only",
    ),
    pytest.param(
        None,
        lambda base, reference: llm_module.LLM(
            provider="openai", model_name="gpt-4o-mini", api_key=reference
        ).call_llm(CHAT, provider="openai"),
        id="model-client-key-only",
    ),
]


@pytest.mark.parametrize("server_factory, call", GUARD_CASES)
def test_a_call_whose_credentials_name_a_platform_value_is_refused_before_it_is_sent(
    hosted, monkeypatch, canary, model_calls, server_factory, call
):
    if server_factory is None:
        requests = _record_outbound(monkeypatch)
        error = _call(call, None, canary.reference)
    else:
        with server_factory() as server:
            error = _call(call, server.base, canary.reference)
        requests = server.requests
    _assert_refused_before_sending(error, canary, requests, model_calls)


def test_a_prompt_that_quotes_a_platform_value_is_still_sent(hosted, canary):
    """Only credentials and settings are checked: what the user sends to the
    model may quote any text."""
    with model_server() as server:
        response = model_endpoints.guarded_completion(
            model="openai/fi-test-model",
            messages=[{"role": "user", "content": canary.reference}],
            api_key="sk-fi-test-valid-not-real",
            api_base=f"{server.base}/v1",
        )
    assert response.choices[0].message.content == MODEL_ANSWER
    assert len(server.requests) == 1
    assert canary.absent_from(_request_text(server.requests[0]))
