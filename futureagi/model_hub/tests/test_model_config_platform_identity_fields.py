"""A model configuration may not set the reserved identity fields.

Whatever their value, a model configuration may not set these settings. A
provider key or custom model that sets one is refused when it
is saved or edited (the create and edit forms and the Django admin), sent as
an object or as JSON text, with the one plain sentence the other refusals use
and nothing of what was sent. A call never passes one on, whoever set it: a
row stored before this check, at any depth of its configuration, on the
run-prompt path and on an evaluation's model call; a Vertex configuration a
call is given, as an object or as JSON text; or the arguments a call is
handed. A stored configuration gives a call only what the call's own
provider takes, for a provider with a list of what it takes, whatever
provider it was stored under, and never the call's own provider, model,
messages or settings.

A Vertex model configuration must be a service-account key: another type, or
a field only another kind of configuration has, at any depth, is refused
with the same sentence, and nothing of it is echoed, when a provider key or
a custom model is saved or edited (the forms and the admin), before any call
made with a row stored before the check, and before the custom-model form's
validation call. A configuration that names a local source or an address
causes no read of that source and no request. A plain service-account key,
and a configuration that gives no type, still save and reach the call, and a
stored one the admin sends back unchanged is still accepted.

A Google credential other than a service-account key, as given or nested in
another provider's configuration, is refused the same way for any provider:
when it is saved (the forms and the admin) and when a stored row is read for
a call with no provider asked for. A Vertex key must sign in at Google's own
address, with or without a type: one that names another is refused wherever
it is saved, edited, read, validated or given to a call, before any request.
Every call that goes through the shared guard refuses a Vertex configuration
other than such a key under either name it takes one by, and drops the
reserved identity fields from it under either name.

Each test gives a field the name of a value the test sets itself; that value
reaches no call, request, response or error. The tests never open a
connection outside 127.0.0.1.
"""

import asyncio
import builtins
import functools
import importlib.metadata
import inspect
import json
import secrets
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import litellm
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, override_settings
from rest_framework import status

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from model_hub.admin import ApiKeyAdmin, CustomAIModelAdmin
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils import model_endpoints
from model_hub.utils.utils import validate_model_working
from tfc.utils.tests.loopback_endpoints import (
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
TENANT_KEY = "sk-fi-test-tenant-not-real"

# Reserved identity fields: a model configuration may not set them,
# whatever their value.
IDENTITY_FIELDS = (
    "aws_web_identity_token",
    "aws_sts_endpoint",
    "aws_profile_name",
    "aws_role_name",
    "aws_session_name",
    "aws_bedrock_runtime_endpoint",
    "aws_external_id",
)
FIELD_IDS = [f"field-{number}" for number in range(1, len(IDENTITY_FIELDS) + 1)]
TENANT_AWS_KEYS = {
    "aws_access_key_id": "fi-test-access-key-not-real",
    "aws_secret_access_key": "fi-test-synthetic-secret-not-real",
    "aws_region_name": "us-east-1",
}
ANOTHER_PROVIDERS_KEYS = {"type": "service_account", "project_id": "fi-test-project"}

# How a field can be written: compared trimmed, in any case.
FIELD_FORMS = {
    "as-given": lambda field: field,
    "padded-upper": lambda field: f"  {field.upper()}\t",
}


class Canary:
    """A variable each test sets itself: a random name and a random value."""

    def __init__(self, name, value):
        self.name = name
        self.value = value


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


def _echoes(canary, *texts):
    """Whether any of ``texts`` carries the canary's name or value."""
    blob = " ".join(
        text if isinstance(text, str) else json.dumps(text, default=str)
        for text in texts
    ).lower()
    return any(needle.lower() in blob for needle in (canary.name, canary.value))


def _call(function, **kwargs):
    """Run a guarded entry point (awaiting the async one); the exception it
    raised, or None."""
    try:
        if inspect.iscoroutinefunction(function):
            asyncio.run(function(**kwargs))
        else:
            function(**kwargs)
    except Exception as exc:
        return exc
    return None


def _assert_refused_and_nothing_echoed(response, canary, field):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    text = json.dumps(response.data, default=str)
    assert REFUSAL in text
    assert not _echoes(canary, text)
    assert field.strip().lower() not in text.lower()


# ------------------------------------------------- saving a provider key ---


def _provider_key_body(shape, field, value):
    if shape == "bedrock-config":
        return {
            "provider": "bedrock",
            "config_json": {**TENANT_AWS_KEYS, field: value},
        }
    if shape == "vertex-config":
        return {
            "provider": "vertex_ai",
            "config_json": {**ANOTHER_PROVIDERS_KEYS, field: value},
        }
    if shape == "config-as-form-text":
        # A form post, for a provider whose configuration is kept as sent.
        return {
            "provider": "anthropic",
            "config_json": json.dumps({"api_key": TENANT_KEY, field: value}),
        }
    # a configuration sent as JSON text
    return {
        "provider": "bedrock",
        "key": json.dumps({**TENANT_AWS_KEYS, field: value}),
    }


PROVIDER_KEY_SHAPES = [
    "bedrock-config",
    "vertex-config",
    "bedrock-config-as-text",
    "config-as-form-text",
]


@pytest.mark.django_db
@pytest.mark.parametrize("form", list(FIELD_FORMS), ids=list(FIELD_FORMS))
@pytest.mark.parametrize("shape", PROVIDER_KEY_SHAPES, ids=PROVIDER_KEY_SHAPES)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_saving_a_provider_key_that_sets_the_platforms_own_identity_fields_is_refused(
    auth_client, canary, field, shape, form
):
    written = FIELD_FORMS[form](field)
    before = ApiKey._base_manager.count()

    response = auth_client.post(
        API_KEYS_URL,
        _provider_key_body(shape, written, canary.name),
        format="multipart" if shape == "config-as-form-text" else "json",
    )

    _assert_refused_and_nothing_echoed(response, canary, field)
    assert ApiKey._base_manager.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["put", "patch"], ids=["put", "patch"])
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_editing_a_provider_key_to_set_the_platforms_own_identity_fields_is_refused(
    auth_client, canary, field, method
):
    created = auth_client.post(
        API_KEYS_URL,
        {"provider": "bedrock", "config_json": TENANT_AWS_KEYS},
        format="json",
    )
    assert created.status_code == status.HTTP_200_OK, created.data
    pk = created.data["result"]["id"]
    before = ApiKey._base_manager.get(pk=pk)
    stored_before = (before.actual_key, before.actual_json)

    response = getattr(auth_client, method)(
        f"{API_KEYS_URL}{pk}/",
        {
            "provider": "bedrock",
            "config_json": {**TENANT_AWS_KEYS, field: canary.name},
        },
        format="json",
    )

    _assert_refused_and_nothing_echoed(response, canary, field)
    after = ApiKey._base_manager.get(pk=pk)
    assert (after.actual_key, after.actual_json) == stored_before


# ------------------------------------------------- saving a custom model ---


@pytest.mark.django_db
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_creating_a_custom_model_that_sets_the_platforms_own_identity_fields_is_refused(
    auth_client, organization, canary, field
):
    name = f"fi-test-model-{uuid.uuid4().hex[:8]}"
    payload = {
        "model_name": name,
        "input_token_cost": 0.0,
        "output_token_cost": 0.0,
        "model_provider": "bedrock",
        "config_json": {**TENANT_AWS_KEYS, field: canary.name},
    }

    with patch(
        "model_hub.views.custom_model.validate_model_working", return_value=True
    ) as checked:
        response = auth_client.post(CUSTOM_MODEL_CREATE_URL, payload, format="json")

    _assert_refused_and_nothing_echoed(response, canary, field)
    checked.assert_not_called()
    assert not CustomAIModel._base_manager.filter(user_model_id=name).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_editing_a_custom_model_to_set_the_platforms_own_identity_fields_is_refused(
    auth_client, organization, user, workspace, canary, field
):
    model = CustomAIModel.objects.create(
        user_model_id=f"fi-test-model-{uuid.uuid4().hex[:8]}",
        provider="bedrock",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config=TENANT_AWS_KEYS,
    )

    with patch(
        "model_hub.views.custom_model.validate_model_working", return_value=True
    ) as checked:
        response = auth_client.patch(
            CUSTOM_MODEL_EDIT_URL,
            {
                "id": str(model.id),
                "config_json": {**TENANT_AWS_KEYS, field: canary.name},
            },
            format="json",
        )

    _assert_refused_and_nothing_echoed(response, canary, field)
    checked.assert_not_called()
    assert CustomAIModel._base_manager.get(pk=model.pk).actual_json == TENANT_AWS_KEYS


# ----------------------------------------------------------- Django admin ---


@pytest.fixture
def admin_request(user):
    user.is_staff = user.is_superuser = True
    request = RequestFactory().post("/admin/")
    request.user = user
    return request


def _admin_form(admin_class, model, request, data, instance=None):
    form_class = admin_class(model, AdminSite()).get_form(request, obj=instance)
    return form_class(data=data, instance=instance)


def _admin_cases(organization, configuration, provider="bedrock"):
    return {
        "provider-config": (
            ApiKeyAdmin,
            ApiKey,
            {
                "provider": provider,
                "organization": str(organization.pk),
                "key": "",
                "config_json": json.dumps(configuration),
            },
        ),
        "custom-model": (
            CustomAIModelAdmin,
            CustomAIModel,
            {
                "user_model_id": f"fi-test-admin-model-{uuid.uuid4().hex[:8]}",
                "provider": provider,
                "input_token_cost": "0",
                "output_token_cost": "0",
                "organization": str(organization.pk),
                "key_config": json.dumps(configuration),
            },
        ),
    }


ADMIN_CASES = ["provider-config", "custom-model"]


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_the_admin_refuses_a_configuration_that_sets_the_platforms_own_identity_fields(
    admin_request, organization, canary, field, case
):
    # The same form with the customer's own settings alone is accepted.
    admin_class, model, accepted = _admin_cases(organization, TENANT_AWS_KEYS)[case]
    valid = _admin_form(admin_class, model, admin_request, accepted)
    assert valid.is_valid(), valid.errors

    admin_class, model, data = _admin_cases(
        organization, {**TENANT_AWS_KEYS, field: canary.name}
    )[case]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    errors = json.dumps(refused.errors.get_json_data(), default=str)
    assert not _echoes(canary, errors)
    assert field not in errors.lower()
    assert not model._base_manager.filter(organization=organization).exists()


# ------------------------------------------------------------ at call time ---


class _CallMade(Exception):
    """The model call was reached."""


class _Recorder:
    """Stands in for the model call: records what it is given, sends nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise _CallMade()


@pytest.fixture
def no_model_call(monkeypatch):
    """Every model call replaced by one recorder: what it is given is
    recorded and nothing is sent."""
    recorder = _Recorder()
    for name in ("completion", "acompletion", "image_generation"):
        monkeypatch.setattr(litellm, name, recorder)
    return recorder


def _assert_no_identity_field_reached_the_call(recorder, canary, error):
    assert len(recorder.calls) == 1, "the model call was not made"
    _args, arguments = recorder.calls[0]
    given = {str(name).strip().lower() for name in arguments}
    assert not given & set(IDENTITY_FIELDS), "an identity field reached the call"
    assert not _echoes(canary, repr(arguments)), "the value reached the call"
    assert not _echoes(canary, str(error), repr(error))


STORED_CALLS = {
    "bedrock": ("bedrock", BEDROCK_MODEL),
    "azure-foundry": ("azure_ai", "azure_ai/fi-test-deployment"),
}


@pytest.mark.parametrize("call", list(STORED_CALLS), ids=list(STORED_CALLS))
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_stored_configuration_never_gives_a_call_the_platforms_own_identity_fields(
    canary, no_model_call, field, call
):
    provider, model_name = STORED_CALLS[call]
    stored = {**ANOTHER_PROVIDERS_KEYS, **TENANT_AWS_KEYS, field: canary.name}
    if provider == "azure_ai":
        stored["api_key"] = TENANT_KEY
    llm = llm_module.LLM(
        provider=provider,
        model_name=model_name,
        api_key=stored,
    )

    with pytest.raises(ValueError) as caught:
        llm.call_llm(CHAT, provider=provider)

    _assert_no_identity_field_reached_the_call(no_model_call, canary, caught.value)
    if provider == "azure_ai":
        assert no_model_call.calls[0][1]["api_key"] == TENANT_KEY


def _entry_call(entry, base):
    """(the entry point, the call's arguments other than the field)."""
    if entry == "completion":
        return model_endpoints.guarded_completion, {
            "model": BEDROCK_MODEL,
            "custom_llm_provider": "bedrock",
            "messages": CHAT,
            **TENANT_AWS_KEYS,
        }
    if entry == "async-completion":
        return model_endpoints.guarded_acompletion, {
            "model": BEDROCK_MODEL,
            "custom_llm_provider": "bedrock",
            "messages": CHAT,
            **TENANT_AWS_KEYS,
        }
    if entry == "image-generation":
        return model_endpoints.guarded_image_generation, {
            "model": "azure/dall-e-3",
            "prompt": "a cat",
            "api_key": "fi-test-azure-key-not-real",
            "api_version": "2024-02-01",
        }
    # a call to a tenant's own address
    return model_endpoints.guarded_completion, {
        "model": "openai/fi-test-model",
        "messages": CHAT,
        "api_key": TENANT_KEY,
        "api_base": f"{base}/v1",
    }


ENTRIES = [
    "completion",
    "async-completion",
    "image-generation",
    "completion-to-a-tenant-address",
]


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRIES)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_call_never_passes_on_the_platforms_own_identity_fields(
    hosted, canary, no_model_call, field, entry
):
    with model_server() as server:
        function, arguments = _entry_call(entry, server.base)
        error = _call(function, **arguments, **{field: canary.name})
    assert server.requests == [], "a request was sent"
    _assert_no_identity_field_reached_the_call(no_model_call, canary, error)


SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key_id": "fi-test-key-id-not-real",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}
VERTEX_MODEL = "vertex_ai/gemini-2.5-flash"
GIVEN_AS = ["an-object", "json-text"]


def _with_identity_field(configuration, field, value):
    """``configuration`` setting ``field`` at its top level and again nested
    in a dict and in a list."""
    return {
        **configuration,
        field: value,
        "options": {field: value, "more": [{field: value}]},
    }


def _names_at_any_depth(value):
    """Every dict key in ``value`` at any depth, trimmed and in lower case;
    JSON text is read as what it holds."""
    names, pending = set(), [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except ValueError:
                continue
        if isinstance(current, dict):
            names.update(str(name).strip().lower() for name in current)
            pending.extend(current.values())
        elif isinstance(current, list | tuple):
            pending.extend(current)
    return names


def _assert_no_identity_field_reached_the_call_at_any_depth(recorder, canary, error):
    assert len(recorder.calls) == 1, "the model call was not made"
    _args, arguments = recorder.calls[0]
    reached = _names_at_any_depth(arguments)
    assert not reached & set(IDENTITY_FIELDS), "an identity field reached the call"
    assert not _echoes(canary, repr(arguments)), "the value reached the call"
    assert not _echoes(canary, str(error), repr(error))
    return arguments


def _assert_refused_before_any_call(error, recorder, canary):
    assert recorder.calls == [], "the model call was made"
    assert str(error) == REFUSAL, "the configuration was not refused"
    assert not _echoes(canary, str(error))


@pytest.mark.parametrize("given_as", GIVEN_AS, ids=GIVEN_AS)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_call_never_passes_on_the_platforms_own_identity_fields_inside_a_vertex_configuration(
    canary, no_model_call, field, given_as
):
    configuration = _with_identity_field(SERVICE_ACCOUNT_KEY, field, canary.name)
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        vertex_credentials=(
            configuration if given_as == "an-object" else json.dumps(configuration)
        ),
    )
    arguments = _assert_no_identity_field_reached_the_call_at_any_depth(
        no_model_call, canary, error
    )
    # The rest of the configuration reaches the call as it was given.
    given = arguments["vertex_credentials"]
    assert isinstance(given, dict if given_as == "an-object" else str)
    if isinstance(given, str):
        given = json.loads(given)
    assert {name: given.get(name) for name in SERVICE_ACCOUNT_KEY} == (
        SERVICE_ACCOUNT_KEY
    )


def _evaluation_llm(provider, model_name, api_key):
    """An evaluation's model for ``provider``: built as one whose provider
    needs no client of its own (a Vertex one needs platform settings these
    tests do not give), then given ``provider``. The model call takes its
    provider from the call."""
    llm = llm_module.LLM(provider="bedrock", model_name=model_name, api_key=api_key)
    llm.provider = provider
    return llm


@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_vertex_configuration_never_gives_an_evaluation_call_the_platforms_own_identity_fields(
    canary, no_model_call, field
):
    """A Vertex configuration that also carries one of these fields, at any
    depth, is not a service-account key: the evaluation's call is refused
    before any model call, and the value is never repeated."""
    llm = _evaluation_llm(
        "vertex_ai",
        VERTEX_MODEL,
        _with_identity_field(SERVICE_ACCOUNT_KEY, field, canary.name),
    )
    with pytest.raises(ValueError) as caught:
        llm.call_llm(CHAT, provider="vertex_ai")
    assert no_model_call.calls == [], "a model call was made"
    assert REFUSAL in str(caught.value), "the call was not refused"
    assert not _echoes(canary, str(caught.value))


def _store_row(
    stored_as, provider, model_name, configuration, organization, user, workspace
):
    """A row written straight to the table, as one stored before the save
    check; the lookup ``get_api_key`` takes for it."""
    if stored_as == "custom_model":
        CustomAIModel.objects.create(
            user_model_id=model_name,
            provider=provider,
            input_token_cost=0.0,
            output_token_cost=0.0,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config=configuration,
        )
        return {}
    ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider=provider,
        workspace=None,
        config_json=configuration,
    )
    return {"provider": provider}


def _call_with_a_stored_row(manager, organization, lookup, provider, model_name, path):
    """Read the stored row the way every call does and make the call: the
    run-prompt payload, or an evaluation's model call. The exception raised,
    or None."""
    try:
        key = manager.get_api_key(organization_id=organization.id, **lookup)
        if path == "run_prompt":
            payload = {"model": model_name, "messages": CHAT}
            PayloadBuilder._add_api_key(payload, provider, key)
            model_endpoints.guarded_completion(**payload)
        else:
            _evaluation_llm(provider, model_name, key).call_llm(CHAT, provider=provider)
    except Exception as exc:  # the refusal, or what the call raised
        return exc
    return None


# (stored as, provider, path): rows of a provider whose stored configuration
# no key list cuts down.
STORED_ROW_CALLS = [
    pytest.param(
        "custom_model", "vertex_ai", "run_prompt", id="vertex_custom_model-run_prompt"
    ),
    pytest.param(
        "custom_model", "vertex_ai", "evaluation", id="vertex_custom_model-evaluation"
    ),
    pytest.param(
        "provider_key", "vertex_ai", "run_prompt", id="vertex_provider_key-run_prompt"
    ),
    pytest.param(
        "provider_key", "vertex_ai", "evaluation", id="vertex_provider_key-evaluation"
    ),
    pytest.param(
        "provider_key",
        "anthropic",
        "run_prompt",
        id="provider_key_without_a_key_list-run_prompt",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("stored_as, provider, path", STORED_ROW_CALLS)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_row_stored_before_the_check_never_gives_a_call_the_platforms_own_identity_fields(
    organization,
    user,
    workspace,
    canary,
    no_model_call,
    field,
    stored_as,
    provider,
    path,
):
    vertex = provider == "vertex_ai"
    model_name = VERTEX_MODEL if vertex else "anthropic/claude-x"
    configuration = _with_identity_field(
        SERVICE_ACCOUNT_KEY if vertex else {"api_key": TENANT_KEY},
        field,
        canary.name,
    )
    lookup = _store_row(
        stored_as, provider, model_name, configuration, organization, user, workspace
    )
    manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization.id
    )
    error = _call_with_a_stored_row(
        manager, organization, lookup, provider, model_name, path
    )
    if vertex:
        # A Vertex configuration that carries such a field is not a
        # service-account key: the row is refused before any call.
        _assert_refused_before_any_call(error, no_model_call, canary)
    else:
        _assert_no_identity_field_reached_the_call_at_any_depth(
            no_model_call, canary, error
        )


def _stored_elsewhere(call, base):
    """(provider, model, the stored configuration, what the call may take)."""
    if call == "bedrock-call":
        return (
            "bedrock",
            BEDROCK_MODEL,
            {
                **ANOTHER_PROVIDERS_KEYS,
                **TENANT_AWS_KEYS,
                "aws_session_token": "fi-test-session-token-not-real",
                "extra_headers": {"X-Tenant": "fi-test"},
                "custom_llm_provider": "openai",
            },
            {
                **TENANT_AWS_KEYS,
                "aws_session_token": "fi-test-session-token-not-real",
            },
        )
    if call == "azure-foundry-call":
        return (
            "azure_ai",
            "azure_ai/fi-test-deployment",
            {
                **ANOTHER_PROVIDERS_KEYS,
                "api_base": f"{base}/models",
                "api_key": "fi-test-azure-ai-key-not-real",
                "api_version": "2024-05-01-preview",
                "extra_headers": {"X-Tenant": "fi-test"},
                "timeout": 1,
            },
            {
                "api_base": f"{base}/models",
                "api_key": "fi-test-azure-ai-key-not-real",
                "api_version": "2024-05-01-preview",
            },
        )
    # a call to a tenant's own address
    return (
        "openai",
        "fi-test-model",
        {
            **ANOTHER_PROVIDERS_KEYS,
            "key": TENANT_KEY,
            "api_base": f"{base}/v1",
            "organization": "org-fi-test",
            "extra_headers": {"X-Tenant": "fi-test"},
            "timeout": 1,
        },
        {
            "api_key": TENANT_KEY,
            "api_base": f"{base}/v1",
            "organization": "org-fi-test",
        },
    )


ELSEWHERE_CALLS = ["bedrock-call", "azure-foundry-call", "call-to-a-tenant-address"]


@pytest.mark.parametrize("call", ELSEWHERE_CALLS, ids=ELSEWHERE_CALLS)
def test_a_stored_configuration_gives_a_call_only_what_the_calls_own_provider_takes(
    hosted, monkeypatch, no_model_call, call
):
    # The model client for this provider is built with a key of its own.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fi-test-platform-not-real")
    with model_server() as server:
        provider, model_name, stored, taken = _stored_elsewhere(call, server.base)
        llm = llm_module.LLM(provider=provider, model_name=model_name, api_key=stored)
        with pytest.raises(ValueError):
            llm.call_llm(CHAT, provider=provider)
    assert server.requests == [], "a request was sent"

    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert arguments["custom_llm_provider"] == provider
    assert arguments["model"] == model_name
    assert arguments["messages"] == CHAT
    assert {name: arguments.get(name) for name in taken} == taken
    # The provider is the call's own (checked above); a key is taken renamed.
    left_out = set(stored) - set(taken) - {"custom_llm_provider", "key"}
    assert not left_out & set(arguments), "the call took another provider's keys"


# The call's own arguments, and what a stored configuration tries to set
# each of them to.
STORED_CALL_ARGUMENTS = {
    "custom_llm_provider": "openai",
    "model": "fi-test-other-model",
    "messages": [{"role": "user", "content": "fi-test"}],
    "temperature": 1.9,
    "max_tokens": 7,
    "response_format": {"type": "json_object"},
}


@pytest.mark.parametrize("argument", sorted(STORED_CALL_ARGUMENTS))
@pytest.mark.parametrize("provider", ["azure_ai", "azure_text"])
def test_a_stored_configuration_cannot_set_the_calls_own_arguments(
    no_model_call, provider, argument
):
    model_name = f"{provider}/fi-test-deployment"
    llm = llm_module.LLM(
        provider=provider,
        model_name=model_name,
        temperature=0.2,
        api_key={
            "api_key": "fi-test-azure-key-not-real",
            argument: STORED_CALL_ARGUMENTS[argument],
        },
    )
    with pytest.raises(ValueError):
        llm.call_llm(CHAT, provider=provider)

    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    # The call gives no max_tokens or response_format of its own here.
    own = {
        "custom_llm_provider": provider,
        "model": model_name,
        "messages": CHAT,
        "temperature": 0.2,
        "max_tokens": None,
        "response_format": None,
    }
    assert {name: arguments.get(name) for name in own} == own, (
        "a stored configuration set the call's own arguments"
    )
    assert arguments["api_key"] == "fi-test-azure-key-not-real"


# What a call falls back to for an identity when it is given none: cleared,
# so that only what the test gives is used.
AMBIENT_IDENTITY_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_EXTERNAL_ID",
    *(field.upper() for field in IDENTITY_FIELDS),
)


def _refuses_everything(request):
    return 400, {"Content-Type": "application/json"}, b'{"message": "refused"}'


def test_a_stored_configuration_that_sets_identity_fields_sends_no_platform_value(
    canary, tmp_path, monkeypatch
):
    """A real call, every address it may use on 127.0.0.1: the value the
    platform holds is in no request and no error, and the call reaches only
    here."""
    print("version", importlib.metadata.version("litellm"))
    for name in AMBIENT_IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with LoopbackServer(_refuses_everything) as server:
        # Where a call goes when nothing it is given says otherwise.
        monkeypatch.setenv("AWS_BEDROCK_RUNTIME_ENDPOINT", server.base)
        llm = llm_module.LLM(
            provider="bedrock",
            model_name=BEDROCK_MODEL,
            api_key={
                **ANOTHER_PROVIDERS_KEYS,
                **TENANT_AWS_KEYS,
                "aws_web_identity_token": canary.name,
                "aws_role_name": "arn:aws:iam::000000000000:role/fi-test",
                "aws_session_name": "fi-test-session",
                "aws_sts_endpoint": server.base,
            },
        )
        with pytest.raises(ValueError) as caught:
            llm.call_llm(CHAT, provider="bedrock")
    seen = str(
        [
            (request.method, request.target, request.headers, request.body)
            for request in server.requests
        ]
    )
    assert not _echoes(canary, seen), "the canary value reached a request"
    assert server.requests, "the call went somewhere else"
    assert not _echoes(canary, str(caught.value))


# ------------------- a Vertex configuration other than a service-account key ---

TENANT_ADDRESS = "https://tenant.example/v1/exchange"
TENANT_SOURCE_FILE = "/var/run/tenant/subject"
# Text of the configurations below that no response or error may repeat.
NOT_ECHOED = ("tenant.example", "tenant/subject", "fi-test-client", "aws1")


def _not_a_service_account_key(
    shape, address=TENANT_ADDRESS, source_file=TENANT_SOURCE_FILE
):
    """A Vertex configuration that is not a service-account key."""
    another_kind = {
        "type": "external_account",
        "audience": "//iam.googleapis.com/projects/0/locations/global/"
        "workloadIdentityPools/fi-test/providers/fi-test",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "token_url": address,
    }
    shapes = {
        "another_type": {
            "type": "authorized_user",
            "client_id": "fi-test-client-not-real",
            "client_secret": "fi-test-client-secret-not-real",
            "refresh_token": "fi-test-refresh-not-real",
        },
        "another_kind": another_kind,
        "a_local_source": {**another_kind, "credential_source": {"file": source_file}},
        "a_source_address": {
            **another_kind,
            "credential_source": {"url": address, "headers": {"X-Tenant": "fi-test"}},
        },
        "an_aws_source": {
            **another_kind,
            "credential_source": {
                "environment_id": "aws1",
                "region_url": address,
                "url": address,
                "regional_cred_verification_url": address,
            },
        },
        "a_key_with_an_address": {**SERVICE_ACCOUNT_KEY, "token_url": address},
        "a_key_with_another_address": {
            **SERVICE_ACCOUNT_KEY,
            "service_account_impersonation_url": address,
        },
        "a_key_with_an_aws_field": {**SERVICE_ACCOUNT_KEY, "aws_region": "us-east-1"},
        "a_key_with_a_nested_aws_field": {
            **SERVICE_ACCOUNT_KEY,
            "options": {"more": [{"aws_region": "us-east-1"}]},
        },
        "a_key_with_a_padded_upper_case_field": {
            **SERVICE_ACCOUNT_KEY,
            " TOKEN_URL ": address,
        },
    }
    return shapes[shape]


# Shapes a provider key can hold (its configuration is flat).
FLAT_SHAPES = (
    "another_type",
    "another_kind",
    "a_key_with_an_address",
    "a_key_with_another_address",
    "a_key_with_an_aws_field",
    "a_key_with_a_padded_upper_case_field",
)
SHAPES = (
    *FLAT_SHAPES,
    "a_local_source",
    "a_source_address",
    "an_aws_source",
    "a_key_with_a_nested_aws_field",
)
SENT_AS = ["an-object", "json-text"]


def _assert_nothing_of_it_echoed(text):
    folded = text.lower()
    for needle in NOT_ECHOED:
        assert needle not in folded, needle


def _assert_refused_and_nothing_of_it_echoed(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    text = json.dumps(response.data, default=str)
    assert REFUSAL in text
    _assert_nothing_of_it_echoed(text)


@pytest.fixture
def no_validation_call(monkeypatch):
    """The custom-model views' validation call, replaced: records that it
    ran and answers that the model works."""
    calls = []

    def validate(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("model_hub.views.custom_model.validate_model_working", validate)
    return calls


@pytest.mark.django_db
@pytest.mark.parametrize("sent_as", SENT_AS, ids=SENT_AS)
@pytest.mark.parametrize("shape", FLAT_SHAPES)
def test_saving_a_vertex_provider_key_other_than_a_service_account_key_is_refused(
    auth_client, shape, sent_as
):
    configuration = _not_a_service_account_key(shape)
    body = (
        {"provider": "vertex_ai", "config_json": configuration}
        if sent_as == "an-object"
        else {"provider": "vertex_ai", "key": json.dumps(configuration)}
    )
    before = ApiKey._base_manager.count()
    response = auth_client.post(API_KEYS_URL, body, format="json")
    _assert_refused_and_nothing_of_it_echoed(response)
    assert ApiKey._base_manager.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["patch", "put"])
def test_editing_a_vertex_provider_key_to_another_kind_of_configuration_is_refused(
    auth_client, method
):
    created = auth_client.post(
        API_KEYS_URL,
        {"provider": "vertex_ai", "config_json": SERVICE_ACCOUNT_KEY},
        format="json",
    )
    assert created.status_code == status.HTTP_200_OK, created.data
    pk = created.data["result"]["id"]

    response = getattr(auth_client, method)(
        f"{API_KEYS_URL}{pk}/",
        {
            "provider": "vertex_ai",
            "config_json": _not_a_service_account_key("another_kind"),
        },
        format="json",
    )
    _assert_refused_and_nothing_of_it_echoed(response)
    assert ApiKey._base_manager.get(pk=pk).actual_json == SERVICE_ACCOUNT_KEY


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_creating_a_vertex_custom_model_other_than_a_service_account_key_is_refused(
    auth_client, no_validation_call, shape
):
    name = f"gemini-{uuid.uuid4().hex[:8]}"
    response = auth_client.post(
        CUSTOM_MODEL_CREATE_URL,
        {
            "model_name": name,
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "model_provider": "vertex_ai",
            "config_json": _not_a_service_account_key(shape),
        },
        format="json",
    )
    _assert_refused_and_nothing_of_it_echoed(response)
    assert no_validation_call == [], "the configuration was sent to a model"
    assert not CustomAIModel._base_manager.filter(user_model_id__endswith=name).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("shape", SHAPES)
def test_editing_a_vertex_custom_model_to_another_kind_of_configuration_is_refused(
    auth_client, organization, user, workspace, no_validation_call, shape
):
    model = CustomAIModel.objects.create(
        user_model_id=VERTEX_MODEL,
        provider="vertex_ai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config=SERVICE_ACCOUNT_KEY,
    )
    response = auth_client.patch(
        CUSTOM_MODEL_EDIT_URL,
        {"id": str(model.id), "config_json": _not_a_service_account_key(shape)},
        format="json",
    )
    _assert_refused_and_nothing_of_it_echoed(response)
    assert no_validation_call == [], "the configuration was sent to a model"
    assert CustomAIModel._base_manager.get(pk=model.pk).actual_json == (
        SERVICE_ACCOUNT_KEY
    )


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("shape", SHAPES)
def test_the_admin_refuses_a_vertex_configuration_other_than_a_service_account_key(
    admin_request, organization, shape, case
):
    admin_class, model, data = _admin_cases(
        organization, _not_a_service_account_key(shape), provider="vertex_ai"
    )[case]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    _assert_nothing_of_it_echoed(
        json.dumps(refused.errors.get_json_data(), default=str)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("stored_as", ["custom_model", "provider_key"])
@pytest.mark.parametrize("shape", SHAPES)
def test_a_stored_vertex_row_other_than_a_service_account_key_is_refused_before_any_call(
    organization, user, workspace, canary, no_model_call, shape, stored_as
):
    lookup = _store_row(
        stored_as,
        "vertex_ai",
        VERTEX_MODEL,
        _not_a_service_account_key(shape),
        organization,
        user,
        workspace,
    )
    manager = LiteLLMModelManager(
        model_name=VERTEX_MODEL, organization_id=organization.id
    )
    with pytest.raises(ValueError) as caught:
        manager.get_api_key(organization_id=organization.id, **lookup)
    _assert_refused_before_any_call(caught.value, no_model_call, canary)
    _assert_nothing_of_it_echoed(str(caught.value))


@pytest.mark.parametrize("shape", SHAPES)
def test_validating_a_vertex_configuration_other_than_a_service_account_key_makes_no_call(
    canary, no_model_call, shape
):
    result = validate_model_working(
        model_name=VERTEX_MODEL,
        api_key={"config_json": _not_a_service_account_key(shape)},
        provider="vertex_ai",
    )
    _assert_refused_before_any_call(result, no_model_call, canary)
    _assert_nothing_of_it_echoed(str(result))


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["validation", "stored_row"])
@pytest.mark.parametrize("shape", ["a_local_source", "a_source_address"])
def test_a_vertex_configuration_that_names_a_local_source_or_an_address_reads_nothing_and_sends_nothing(
    organization, user, workspace, canary, tmp_path, monkeypatch, shape, route
):
    """No stand-in for the model call: the configuration is refused before
    anything reads the local source it names or sends a request to the
    addresses it gives (every one on 127.0.0.1)."""
    print("version", importlib.metadata.version("litellm"))
    source = tmp_path / "subject"
    source.write_text(canary.value)
    opened, sent = [], []
    real_open = builtins.open

    def recording_open(file, *args, **kwargs):
        if str(file) == str(source):
            opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def no_request(self, method, url, *args, **kwargs):
        sent.append((method, url))
        raise requests.ConnectionError("no request is sent in this test")

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(requests.Session, "request", no_request)
    with LoopbackServer(_refuses_everything) as server:
        configuration = _not_a_service_account_key(
            shape, address=f"{server.base}/v1/exchange", source_file=str(source)
        )
        if route == "validation":
            error = validate_model_working(
                model_name=VERTEX_MODEL,
                api_key={"config_json": configuration},
                provider="vertex_ai",
            )
        else:
            lookup = _store_row(
                "custom_model",
                "vertex_ai",
                VERTEX_MODEL,
                configuration,
                organization,
                user,
                workspace,
            )
            manager = LiteLLMModelManager(
                model_name=VERTEX_MODEL, organization_id=organization.id
            )
            error = _call_with_a_stored_row(
                manager, organization, lookup, "vertex_ai", VERTEX_MODEL, "run_prompt"
            )
    assert opened == [], "the local source was read"
    assert sent == [], "a request was sent"
    assert server.requests == [], "a request reached the address"
    assert str(error) == REFUSAL, "the configuration was not refused"
    assert not _echoes(canary, str(error))


# A plain service-account key, and a configuration that gives no type (which
# is taken as a service-account key), still save and call as before.
STILL_CALLED = {
    "a_service_account_key": SERVICE_ACCOUNT_KEY,
    "a_configuration_without_a_type": {
        name: value for name, value in SERVICE_ACCOUNT_KEY.items() if name != "type"
    },
}


@pytest.mark.django_db
@pytest.mark.parametrize("sent_as", SENT_AS, ids=SENT_AS)
def test_saving_a_vertex_provider_key_that_is_a_service_account_key_still_works(
    auth_client, sent_as
):
    body = (
        {"provider": "vertex_ai", "config_json": SERVICE_ACCOUNT_KEY}
        if sent_as == "an-object"
        else {"provider": "vertex_ai", "key": json.dumps(SERVICE_ACCOUNT_KEY)}
    )
    response = auth_client.post(API_KEYS_URL, body, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    stored = ApiKey._base_manager.get(pk=response.data["result"]["id"])
    assert stored.actual_json == SERVICE_ACCOUNT_KEY


@pytest.mark.django_db
def test_creating_and_editing_a_vertex_custom_model_with_a_service_account_key_still_works(
    auth_client, no_validation_call
):
    response = auth_client.post(
        CUSTOM_MODEL_CREATE_URL,
        {
            "model_name": "gemini-2.5-flash",
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "model_provider": "vertex_ai",
            "config_json": SERVICE_ACCOUNT_KEY,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    assert len(no_validation_call) == 1
    model = CustomAIModel._base_manager.get(user_model_id=VERTEX_MODEL)
    assert model.actual_json == SERVICE_ACCOUNT_KEY

    edited = {**SERVICE_ACCOUNT_KEY, "project_id": "fi-test-project-2"}
    response = auth_client.patch(
        CUSTOM_MODEL_EDIT_URL,
        {"id": str(model.id), "config_json": edited},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    assert len(no_validation_call) == 2
    assert CustomAIModel._base_manager.get(pk=model.pk).actual_json == edited


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["run_prompt", "evaluation"])
@pytest.mark.parametrize("stored_as", ["custom_model", "provider_key"])
@pytest.mark.parametrize("configuration", sorted(STILL_CALLED))
def test_a_stored_vertex_service_account_key_still_reaches_the_call(
    organization, user, workspace, no_model_call, configuration, stored_as, path
):
    given = STILL_CALLED[configuration]
    lookup = _store_row(
        stored_as, "vertex_ai", VERTEX_MODEL, given, organization, user, workspace
    )
    manager = LiteLLMModelManager(
        model_name=VERTEX_MODEL, organization_id=organization.id
    )
    error = _call_with_a_stored_row(
        manager, organization, lookup, "vertex_ai", VERTEX_MODEL, path
    )
    assert isinstance(error, (_CallMade, ValueError)), error
    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert json.loads(arguments["vertex_credentials"]) == given


@pytest.mark.parametrize("configuration", sorted(STILL_CALLED))
def test_validating_a_vertex_service_account_key_still_calls_the_model(
    no_model_call, configuration
):
    given = STILL_CALLED[configuration]
    validate_model_working(
        model_name=VERTEX_MODEL, api_key={"config_json": given}, provider="vertex_ai"
    )
    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert json.loads(arguments["vertex_credentials"]) == given


def _stored_for_the_admin(case, organization, configuration):
    """A Vertex row stored with ``configuration`` and read back as the admin
    reads it, and the form the admin sends back when it is saved unchanged:
    the stored values exactly as the row holds them."""
    if case == "provider-config":
        created = ApiKey.no_workspace_objects.create(
            organization=organization,
            provider="vertex_ai",
            workspace=None,
            config_json=configuration,
        )
        row = ApiKey._base_manager.get(pk=created.pk)
        return (
            ApiKeyAdmin,
            ApiKey,
            row,
            {
                "provider": "vertex_ai",
                "organization": str(organization.pk),
                "key": "",
                "config_json": json.dumps(row.config_json),
            },
        )
    created = CustomAIModel.no_workspace_objects.create(
        user_model_id=f"vertex_ai/fi-test-admin-{uuid.uuid4().hex[:8]}",
        provider="vertex_ai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=None,
        key_config=configuration,
    )
    row = CustomAIModel._base_manager.get(pk=created.pk)
    return (
        CustomAIModelAdmin,
        CustomAIModel,
        row,
        {
            "user_model_id": row.user_model_id,
            "provider": "vertex_ai",
            "input_token_cost": "0",
            "output_token_cost": "0",
            "organization": str(organization.pk),
            "key_config": json.dumps(row.key_config),
        },
    )


@pytest.mark.django_db
@pytest.mark.parametrize("configuration", sorted(STILL_CALLED))
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
def test_the_admin_still_accepts_a_stored_vertex_service_account_key_saved_unchanged(
    admin_request, organization, case, configuration
):
    admin_class, model, row, data = _stored_for_the_admin(
        case, organization, STILL_CALLED[configuration]
    )
    form = _admin_form(admin_class, model, admin_request, data, instance=row)
    assert form.is_valid(), form.errors


# ------------- another kind of Google credential, wherever a model takes it ---

# A model the catalog lists for another provider than Vertex AI.
ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
# The arguments a model call takes Vertex credentials under.
VERTEX_CREDENTIAL_ARGUMENTS = ("vertex_credentials", "vertex_ai_credentials")
ARGUMENT_IDS = ["its-name", "its-other-name"]


@pytest.mark.parametrize("given_as", GIVEN_AS, ids=GIVEN_AS)
@pytest.mark.parametrize("argument", VERTEX_CREDENTIAL_ARGUMENTS, ids=ARGUMENT_IDS)
@pytest.mark.parametrize("shape", SHAPES)
def test_a_model_call_given_a_vertex_configuration_other_than_a_service_account_key_is_refused(
    canary, no_model_call, shape, argument, given_as
):
    configuration = _not_a_service_account_key(shape)
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        **{
            argument: (
                configuration if given_as == "an-object" else json.dumps(configuration)
            )
        },
    )
    _assert_refused_before_any_call(error, no_model_call, canary)
    _assert_nothing_of_it_echoed(str(error))


@pytest.mark.parametrize("given_as", GIVEN_AS, ids=GIVEN_AS)
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_call_never_passes_on_the_platforms_own_identity_fields_inside_a_vertex_configuration_under_its_other_name(
    canary, no_model_call, field, given_as
):
    configuration = _with_identity_field(SERVICE_ACCOUNT_KEY, field, canary.name)
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        vertex_ai_credentials=(
            configuration if given_as == "an-object" else json.dumps(configuration)
        ),
    )
    arguments = _assert_no_identity_field_reached_the_call_at_any_depth(
        no_model_call, canary, error
    )
    # The rest of the configuration reaches the call as it was given.
    given = arguments["vertex_ai_credentials"]
    assert isinstance(given, dict if given_as == "an-object" else str)
    if isinstance(given, str):
        given = json.loads(given)
    assert {name: given.get(name) for name in SERVICE_ACCOUNT_KEY} == (
        SERVICE_ACCOUNT_KEY
    )


# Refused for any provider: another kind of Google credential, as given or
# nested inside another provider's configuration.
ANY_PROVIDER_SHAPES = (
    "another_type",
    "another_kind",
    "a_local_source",
    "a_source_address",
    "an_aws_source",
    "a_key_with_an_address",
    "a_key_with_another_address",
    "a_key_with_a_padded_upper_case_field",
    "nested_in_another_configuration",
    "an_external_authorized_user",
    "an_external_authorized_user_padded_upper_case",
    "a_distributed_cloud_service_account",
    "a_distributed_cloud_service_account_padded_upper_case",
    "an_impersonated_service_account",
    "an_impersonated_service_account_padded_upper_case",
    "an_external_account_by_its_type_alone",
    "an_external_account_by_its_type_alone_padded_upper_case",
    "an_authorized_user_by_its_type_alone",
    "an_authorized_user_by_its_type_alone_padded_upper_case",
)
# Each of the other kinds of Google credential, by its type alone.
OTHER_KINDS_BY_TYPE = {
    "an_external_authorized_user": "external_account_authorized_user",
    "a_distributed_cloud_service_account": "gdch_service_account",
    "an_impersonated_service_account": "impersonated_service_account",
    "an_external_account_by_its_type_alone": "external_account",
    "an_authorized_user_by_its_type_alone": "authorized_user",
}
SAVED_AS = ["provider_key", "provider_key_as_text", "custom_model"]


def _another_kind_of_credential(shape):
    """A Google credential other than a service-account key, as another
    provider's configuration may hold it."""
    if shape == "nested_in_another_configuration":
        return {
            "api_key": TENANT_KEY,
            "options": {"more": [_not_a_service_account_key("another_kind")]},
        }
    kind = shape.removesuffix("_padded_upper_case")
    if kind in OTHER_KINDS_BY_TYPE:
        given = OTHER_KINDS_BY_TYPE[kind]
        if kind != shape:
            given = f"  {given.upper()} \n"
        return {"type": given, "client_id": "fi-test-client-not-real"}
    return _not_a_service_account_key(shape)


@pytest.mark.django_db
@pytest.mark.parametrize("saved_as", SAVED_AS)
@pytest.mark.parametrize("shape", ANY_PROVIDER_SHAPES)
def test_saving_a_google_credential_other_than_a_service_account_key_is_refused_for_any_provider(
    auth_client, no_validation_call, shape, saved_as
):
    configuration = _another_kind_of_credential(shape)
    name = f"fi-test-model-{uuid.uuid4().hex[:8]}"
    before = ApiKey._base_manager.count()
    if saved_as == "custom_model":
        response = auth_client.post(
            CUSTOM_MODEL_CREATE_URL,
            {
                "model_name": name,
                "input_token_cost": 0.01,
                "output_token_cost": 0.03,
                "model_provider": "openai",
                "config_json": {**configuration, "key": TENANT_KEY},
            },
            format="json",
        )
    else:
        body = (
            {"provider": "anthropic", "config_json": configuration}
            if saved_as == "provider_key"
            else {"provider": "anthropic", "key": json.dumps(configuration)}
        )
        response = auth_client.post(API_KEYS_URL, body, format="json")
    _assert_refused_and_nothing_of_it_echoed(response)
    assert no_validation_call == [], "the configuration was sent to a model"
    assert ApiKey._base_manager.count() == before
    assert not CustomAIModel._base_manager.filter(user_model_id=name).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("shape", ANY_PROVIDER_SHAPES)
def test_the_admin_refuses_a_google_credential_other_than_a_service_account_key_for_any_provider(
    admin_request, organization, shape, case
):
    admin_class, model, data = _admin_cases(
        organization, _another_kind_of_credential(shape), provider="openai"
    )[case]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    _assert_nothing_of_it_echoed(
        json.dumps(refused.errors.get_json_data(), default=str)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("stored_as", SAVED_AS)
@pytest.mark.parametrize("shape", ANY_PROVIDER_SHAPES)
def test_a_stored_google_credential_other_than_a_service_account_key_is_refused_for_any_provider_before_any_call(
    organization, user, workspace, canary, no_model_call, shape, stored_as
):
    configuration = _another_kind_of_credential(shape)
    if stored_as == "custom_model":
        model_name = "fi-test-openai-model"
        _store_row(
            "custom_model",
            "openai",
            model_name,
            {**configuration, "key": TENANT_KEY},
            organization,
            user,
            workspace,
        )
    else:
        model_name = ANTHROPIC_MODEL
        stored = (
            {"config_json": configuration}
            if stored_as == "provider_key"
            else {"key": json.dumps(configuration)}
        )
        ApiKey.no_workspace_objects.create(
            organization=organization,
            user=user,
            provider="anthropic",
            workspace=None,
            **stored,
        )
    manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization.id
    )
    # Read as an evaluation reads it: no provider asked for.
    with pytest.raises(ValueError) as caught:
        manager.get_api_key(organization_id=organization.id)
    _assert_refused_before_any_call(caught.value, no_model_call, canary)
    _assert_nothing_of_it_echoed(str(caught.value))


@pytest.fixture
def reads_and_requests(monkeypatch):
    """Every read of a file the test watches, and every request sent (each
    one raises: nothing leaves the process)."""
    seen = SimpleNamespace(watched=set(), opened=[], sent=[])
    real_open = builtins.open

    def recording_open(file, *args, **kwargs):
        if str(file) in seen.watched:
            seen.opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def no_request(self, method, url, *args, **kwargs):
        seen.sent.append((method, url))
        raise requests.ConnectionError("no request is sent in this test")

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(requests.Session, "request", no_request)
    return seen


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["stored_for_another_provider", "given_to_the_call"])
@pytest.mark.parametrize("shape", ["a_local_source", "a_source_address"])
def test_another_kind_of_google_credential_made_into_a_vertex_call_reads_nothing_and_sends_nothing(
    organization, user, workspace, canary, tmp_path, reads_and_requests, shape, route
):
    """No stand-in for the model call. A provider key stored under another
    provider is read as an evaluation reads it, with no provider asked for,
    and the evaluation's call is made for a Vertex model; or the
    configuration is given to that call directly. Nothing reads the local
    source it names or sends a request to the addresses it gives (every one
    on 127.0.0.1), and it is refused with the plain sentence."""
    print("version", importlib.metadata.version("litellm"))
    source = tmp_path / "subject"
    source.write_text(canary.value)
    reads_and_requests.watched.add(str(source))
    error = None
    with LoopbackServer(_refuses_everything) as server:
        configuration = _not_a_service_account_key(
            shape, address=f"{server.base}/v1/exchange", source_file=str(source)
        )
        try:
            if route == "stored_for_another_provider":
                ApiKey.no_workspace_objects.create(
                    organization=organization,
                    user=user,
                    provider="anthropic",
                    workspace=None,
                    config_json=configuration,
                )
                manager = LiteLLMModelManager(
                    model_name=ANTHROPIC_MODEL, organization_id=organization.id
                )
                configuration = manager.get_api_key(organization_id=organization.id)
            _evaluation_llm("vertex_ai", VERTEX_MODEL, configuration).call_llm(
                CHAT, provider="vertex_ai"
            )
        except Exception as exc:  # the refusal, or what the call raised
            error = exc
    assert reads_and_requests.opened == [], "the local source was read"
    assert reads_and_requests.sent == [], "a request was sent"
    assert server.requests == [], "a request reached the address"
    assert REFUSAL in str(error), "the configuration was not refused"
    assert not _echoes(canary, str(error))


@functools.lru_cache(maxsize=1)
def _a_private_key_that_signs():
    """A private key made in memory for these tests only: a service-account
    key that holds it goes as far as signing in."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


SIGN_IN_CONFIGURATIONS = ["a_service_account_key", "a_configuration_without_a_type"]


def _a_key_that_signs_in_at(address, configuration, private_key=None):
    """A Vertex service-account key that signs in at ``address``; without a
    type for ``a_configuration_without_a_type`` (read as a service-account
    key all the same)."""
    key = {**SERVICE_ACCOUNT_KEY, "token_uri": address}
    if private_key is not None:
        key["private_key"] = private_key
    if configuration == "a_configuration_without_a_type":
        del key["type"]
    return key


def _refusal(response):
    """A form's refusal, as text; None when the form was not refused."""
    if response.status_code != status.HTTP_400_BAD_REQUEST:
        return None
    return json.dumps(response.data, default=str)


def _use_for_vertex(route, configuration, client, organization, user, workspace):
    """Save, edit, read, validate or call with ``configuration`` for a Vertex
    model on ``route``: the text of the refusal, or of what failed; None
    when nothing did."""
    if route == "provider_key_saved":
        return _refusal(
            client.post(
                API_KEYS_URL,
                {"provider": "vertex_ai", "config_json": configuration},
                format="json",
            )
        )
    if route == "custom_model_created":
        return _refusal(
            client.post(
                CUSTOM_MODEL_CREATE_URL,
                {
                    "model_name": f"gemini-{uuid.uuid4().hex[:8]}",
                    "input_token_cost": 0.01,
                    "output_token_cost": 0.03,
                    "model_provider": "vertex_ai",
                    "config_json": configuration,
                },
                format="json",
            )
        )
    if route == "custom_model_edited":
        model = CustomAIModel.objects.create(
            user_model_id=VERTEX_MODEL,
            provider="vertex_ai",
            input_token_cost=0.0,
            output_token_cost=0.0,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config=SERVICE_ACCOUNT_KEY,
        )
        return _refusal(
            client.patch(
                CUSTOM_MODEL_EDIT_URL,
                {"id": str(model.id), "config_json": configuration},
                format="json",
            )
        )
    if route == "validation":
        return str(
            validate_model_working(
                model_name=VERTEX_MODEL,
                api_key={"config_json": configuration},
                provider="vertex_ai",
            )
        )
    if route == "call":
        try:
            _evaluation_llm("vertex_ai", VERTEX_MODEL, configuration).call_llm(
                CHAT, provider="vertex_ai"
            )
        except Exception as exc:  # the refusal, or what the call raised
            return str(exc)
        return None
    stored_as = "custom_model" if route == "stored_custom_model" else "provider_key"
    lookup = _store_row(
        stored_as,
        "vertex_ai",
        VERTEX_MODEL,
        configuration,
        organization,
        user,
        workspace,
    )
    manager = LiteLLMModelManager(
        model_name=VERTEX_MODEL, organization_id=organization.id
    )
    error = _call_with_a_stored_row(
        manager, organization, lookup, "vertex_ai", VERTEX_MODEL, "run_prompt"
    )
    return None if error is None else str(error)


VERTEX_ROUTES = (
    "provider_key_saved",
    "custom_model_created",
    "custom_model_edited",
    "stored_custom_model",
    "stored_provider_key",
    "validation",
    "call",
)


@pytest.mark.django_db
@pytest.mark.parametrize("configuration", SIGN_IN_CONFIGURATIONS)
@pytest.mark.parametrize("route", VERTEX_ROUTES)
def test_a_vertex_service_account_key_that_signs_in_elsewhere_is_refused_and_sends_nothing(
    auth_client,
    organization,
    user,
    workspace,
    reads_and_requests,
    route,
    configuration,
):
    """No stand-in for the model call: wherever such a key is saved, edited,
    read for a call, validated or given to a call, it is refused with the
    plain sentence before any request (the address it gives is on
    127.0.0.1)."""
    print("version", importlib.metadata.version("litellm"))
    with LoopbackServer(_refuses_everything) as server:
        key = _a_key_that_signs_in_at(
            f"{server.base}/token", configuration, _a_private_key_that_signs()
        )
        refusal = _use_for_vertex(
            route, key, auth_client, organization, user, workspace
        )
    assert reads_and_requests.sent == [], "a request was sent"
    assert server.requests == [], "a request reached the address"
    assert refusal is not None and REFUSAL in refusal, (
        "the configuration was not refused"
    )
    assert "127.0.0.1" not in refusal


@pytest.mark.django_db
@pytest.mark.parametrize("configuration", SIGN_IN_CONFIGURATIONS)
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
def test_the_admin_refuses_a_vertex_service_account_key_that_signs_in_elsewhere(
    admin_request, organization, case, configuration
):
    key = _a_key_that_signs_in_at(TENANT_ADDRESS, configuration)
    admin_class, model, data = _admin_cases(organization, key, provider="vertex_ai")[
        case
    ]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    _assert_nothing_of_it_echoed(
        json.dumps(refused.errors.get_json_data(), default=str)
    )


# ---------------------- where a Vertex key says the platform signs in ---

# A Vertex key that gives Google's own addresses, in each form a genuine key
# may give them, still saves, validates and calls; so does one that gives no
# domain (every key here gives the sign-in address of the key above).
GOOGLES_OWN_ADDRESSES = {
    "the_older_sign_in_address": {
        "token_uri": "https://accounts.google.com/o/oauth2/token"
    },
    "the_sign_in_page": {"auth_uri": "https://accounts.google.com/o/oauth2/auth"},
    "the_newer_sign_in_page": {
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth"
    },
    "googles_own_domain": {"universe_domain": "googleapis.com"},
    "a_key_that_names_no_domain": {},
    "every_address_a_genuine_key_gives": {
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "universe_domain": "googleapis.com",
    },
}
# A Vertex key that says it signs in somewhere else: on this machine, or on
# another domain.
SIGNS_IN_ELSEWHERE = {
    "a_sign_in_page_on_this_machine": {
        "auth_uri": "https://127.0.0.1:8443/o/oauth2/auth"
    },
    "a_sign_in_page_on_another_domain": {
        "auth_uri": "https://accounts.tenant.example/o/oauth2/auth"
    },
    "a_domain_on_this_machine": {"universe_domain": "127.0.0.1"},
    "another_domain": {"universe_domain": "tenant.example"},
    "a_sign_in_address_on_another_domain": {
        "token_uri": "https://oauth2.tenant.example/token"
    },
}
# The routes that save or edit a configuration; the others read it for a
# call, validate it or give it to a call.
SAVING_ROUTES = ("provider_key_saved", "custom_model_created", "custom_model_edited")


@pytest.mark.django_db
@pytest.mark.parametrize("route", VERTEX_ROUTES)
@pytest.mark.parametrize("elsewhere", sorted(SIGNS_IN_ELSEWHERE))
def test_a_vertex_key_that_says_it_signs_in_elsewhere_is_refused_wherever_it_is_taken(
    auth_client,
    organization,
    user,
    workspace,
    no_validation_call,
    no_model_call,
    reads_and_requests,
    elsewhere,
    route,
):
    """Saved, edited, read for a call, validated or given to a call: the key
    is refused with the plain sentence, nothing of it echoed; no model call
    is made and no request is sent."""
    key = {**SERVICE_ACCOUNT_KEY, **SIGNS_IN_ELSEWHERE[elsewhere]}
    refusal = _use_for_vertex(route, key, auth_client, organization, user, workspace)
    assert no_model_call.calls == [], "the model call was made"
    assert no_validation_call == [], "the configuration was sent to a model"
    assert reads_and_requests.sent == [], "a request was sent"
    assert refusal is not None and REFUSAL in refusal, (
        "the configuration was not refused"
    )
    assert "127.0.0.1" not in refusal and "tenant.example" not in refusal


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("elsewhere", sorted(SIGNS_IN_ELSEWHERE))
def test_the_admin_refuses_a_vertex_key_that_says_it_signs_in_elsewhere(
    admin_request, organization, elsewhere, case
):
    key = {**SERVICE_ACCOUNT_KEY, **SIGNS_IN_ELSEWHERE[elsewhere]}
    admin_class, model, data = _admin_cases(organization, key, provider="vertex_ai")[
        case
    ]
    refused = _admin_form(admin_class, model, admin_request, data)

    assert not refused.is_valid()
    assert list(refused.non_field_errors()) == [REFUSAL]
    text = json.dumps(refused.errors.get_json_data(), default=str)
    assert "127.0.0.1" not in text and "tenant.example" not in text


@pytest.mark.django_db
@pytest.mark.parametrize("route", VERTEX_ROUTES)
@pytest.mark.parametrize("address", sorted(GOOGLES_OWN_ADDRESSES))
def test_a_vertex_key_that_signs_in_at_googles_own_addresses_still_saves_validates_and_calls(
    auth_client,
    organization,
    user,
    workspace,
    no_validation_call,
    no_model_call,
    address,
    route,
):
    key = {**SERVICE_ACCOUNT_KEY, **GOOGLES_OWN_ADDRESSES[address]}
    outcome = _use_for_vertex(route, key, auth_client, organization, user, workspace)
    assert outcome is None or REFUSAL not in outcome, "the key was refused"
    if route in SAVING_ROUTES:
        assert outcome is None, outcome
        if route != "provider_key_saved":
            assert len(no_validation_call) == 1, "the key was not validated"
        return
    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert json.loads(arguments["vertex_credentials"]) == key


@pytest.mark.django_db
@pytest.mark.parametrize("case", ADMIN_CASES, ids=ADMIN_CASES)
@pytest.mark.parametrize("address", sorted(GOOGLES_OWN_ADDRESSES))
def test_the_admin_still_accepts_a_vertex_key_that_signs_in_at_googles_own_addresses(
    admin_request, organization, address, case
):
    key = {**SERVICE_ACCOUNT_KEY, **GOOGLES_OWN_ADDRESSES[address]}
    admin_class, model, data = _admin_cases(organization, key, provider="vertex_ai")[
        case
    ]
    form = _admin_form(admin_class, model, admin_request, data)
    assert form.is_valid(), form.errors


def _almost_googles_own_addresses():
    """For each address a key may give, values a few characters away from
    Google's own: each one is refused."""
    for kind, field, own in (
        ("sign_in_address", "token_uri", "https://oauth2.googleapis.com/token"),
        ("sign_in_page", "auth_uri", "https://accounts.google.com/o/oauth2/auth"),
    ):
        host, _, path = own.removeprefix("https://").partition("/")
        almost = {
            "http_instead_of_https": f"http://{host}/{path}",
            "an_extra_path_segment": f"{own}/more",
            "a_port": f"https://{host}:8443/{path}",
            "userinfo": f"https://tenant@{host}/{path}",
            "a_lookalike_host": f"https://{host}.tenant.example/{path}",
            "a_trailing_slash": f"{own}/",
            "a_query": f"{own}?tenant=1",
            "a_fragment": f"{own}#tenant",
            "another_case_in_the_path": f"https://{host}/{path.upper()}",
            "not_text": [own],
        }
        for name, value in almost.items():
            yield pytest.param(field, value, id=f"{kind}-{name}")
    for name, value in {
        "a_lookalike_domain": "googleapis.com.tenant.example",
        "a_port": "googleapis.com:443",
        "userinfo": "tenant@googleapis.com",
        "a_scheme": "https://googleapis.com",
        "a_trailing_dot": "googleapis.com.",
        "another_case": "GOOGLEAPIS.COM",
        "not_text": ["googleapis.com"],
    }.items():
        yield pytest.param("universe_domain", value, id=f"domain-{name}")


@pytest.mark.parametrize("field, value", list(_almost_googles_own_addresses()))
def test_an_address_almost_like_googles_own_is_refused_before_any_call(
    canary, no_model_call, reads_and_requests, field, value
):
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        vertex_credentials={**SERVICE_ACCOUNT_KEY, field: value},
    )
    _assert_refused_before_any_call(error, no_model_call, canary)
    assert reads_and_requests.sent == [], "a request was sent"
    assert "tenant" not in str(error)


# The one rule for which sign-in addresses a Google key may give decides only
# for a key given as an object: anything else is refused.
NOT_A_KEY_OBJECT = {
    "text": "fi-test-key-not-real",
    "text_that_names_an_address_field": "token_uri",
    "a_list": ["token_uri"],
    "an_empty_list": [],
    "nothing": None,
    "a_number": 7,
}


@pytest.mark.parametrize("given", sorted(NOT_A_KEY_OBJECT))
def test_a_google_key_that_is_not_an_object_is_never_taken_as_signing_in_at_googles_own_addresses(
    given,
):
    rule = model_endpoints.signs_in_only_at_googles_own_addresses
    assert rule(NOT_A_KEY_OBJECT[given]) is False, "the key was not refused"


# ------------------------ what a Vertex call may take as its configuration ---

# A Vertex call takes its configuration as an object, or as JSON text of one;
# given anything else, under either name, it is refused before any call.
NOT_A_CONFIGURATION_OBJECT = {
    "empty_text": "",
    "text_that_is_not_a_configuration": "/var/run/tenant/key.json",
    "a_list_given_as_text": json.dumps([SERVICE_ACCOUNT_KEY]),
    "text_that_holds_text": json.dumps(json.dumps(SERVICE_ACCOUNT_KEY)),
    "a_list": [SERVICE_ACCOUNT_KEY],
}


@pytest.mark.parametrize("argument", VERTEX_CREDENTIAL_ARGUMENTS, ids=ARGUMENT_IDS)
@pytest.mark.parametrize("given", sorted(NOT_A_CONFIGURATION_OBJECT))
def test_a_vertex_call_given_anything_but_a_configuration_object_is_refused_before_any_call(
    canary, no_model_call, given, argument
):
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        **{argument: NOT_A_CONFIGURATION_OBJECT[given]},
    )
    _assert_refused_before_any_call(error, no_model_call, canary)


@pytest.mark.parametrize("argument", VERTEX_CREDENTIAL_ARGUMENTS, ids=ARGUMENT_IDS)
def test_a_vertex_call_given_a_path_as_its_configuration_reads_nothing(
    canary, tmp_path, reads_and_requests, argument
):
    """No stand-in for the model call: given, under either name, the path
    of a file this test writes, the call is refused with the plain sentence;
    nothing reads the file and no request is sent."""
    print("version", importlib.metadata.version("litellm"))
    key_file = tmp_path / "key.json"
    key_file.write_text(
        json.dumps({**SERVICE_ACCOUNT_KEY, "private_key_id": canary.value})
    )
    reads_and_requests.watched.add(str(key_file))
    error = _call(
        model_endpoints.guarded_completion,
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        **{argument: str(key_file)},
    )
    assert reads_and_requests.opened == [], "the file was read"
    assert reads_and_requests.sent == [], "a request was sent"
    assert str(error) == REFUSAL, "the configuration was not refused"
    assert not _echoes(canary, str(error))
