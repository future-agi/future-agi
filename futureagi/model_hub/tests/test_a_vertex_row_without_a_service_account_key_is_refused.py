"""A Vertex row without a service-account key is refused before the call.

A row stored for a Vertex model that gives no complete service-account key
of the customer's own (a key held as text, a key alone, an empty
configuration, a location alone, a private key without a client email) is
refused with the one plain sentence the other refusals use where stored
credentials are looked up, before any call is built: by the lookup every
call uses, by the provider-key lookup, and by an evaluation's key lookup. A
row that gives one is handed on as an object without a key field: a
service-account key held as JSON text, or beside a key field, reaches Run
Prompt's chat call and an evaluation's call as the model's Vertex
configuration, and no key is sent alone there.

A provider key saved under a provider name with spaces around it is saved
as under the provider's own name: for a Vertex key, text that is not a
configuration is not saved, and a service-account key sent as text is saved
as the key's configuration.

Another provider's key reaches its call as given, a Vertex row that gives a
service-account key and its location is handed on as it is, and a Vertex
model with no saved key is answered that no key is configured.

The tests use made-up names and keys, replace every model call with a
recorder that sends nothing, and contact nothing.
"""

import json

import litellm
import pytest
from rest_framework import status

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils import model_endpoints

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
API_KEYS_URL = "/model-hub/api-keys/"
CHAT = [{"role": "user", "content": "hi"}]
# Made-up model names; the custom-model forms prefix a Vertex model's name.
VERTEX_MODEL = "vertex_ai/fi-test-gemini-model"
OTHER_MODEL = "fi-test-other-model"
LITERAL_KEY = "fi-test-key-not-real"
LOCATION = "us-central1"
# A service-account key built from made-up fields, which signs in at
# Google's own address.
SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key_id": "fi-test-key-id-not-real",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.example.test",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# Rows stored for a Vertex model that give no complete service-account key:
# (stored as, what the row holds).
NO_SERVICE_ACCOUNT_KEY = {
    "provider-key-held-as-text": ("provider_key_text", LITERAL_KEY),
    "provider-key-a-location-alone": ("provider_key", {"location": LOCATION}),
    "provider-key-a-key-alone": ("provider_key", {"key": LITERAL_KEY}),
    "custom-model-empty": ("custom_model", {}),
    "custom-model-a-key-alone": ("custom_model", {"key": LITERAL_KEY}),
    "custom-model-a-location-alone": ("custom_model", {"location": LOCATION}),
    "custom-model-a-key-and-a-location": (
        "custom_model",
        {"key": LITERAL_KEY, "location": LOCATION},
    ),
    "custom-model-a-private-key-without-a-client-email": (
        "custom_model",
        {"type": "service_account", "private_key": "fi-test-private-key-not-real"},
    ),
}
# Each such row read by the lookup every call uses, provider-key rows also by
# the provider-key lookup, and custom-model rows also by an evaluation's key
# lookup.
REFUSED_LOOKUPS = [
    *((row, "get_api_key") for row in NO_SERVICE_ACCOUNT_KEY),
    *(
        (row, "get_provider_api_key")
        for row, (stored_as, _value) in NO_SERVICE_ACCOUNT_KEY.items()
        if stored_as != "custom_model"
    ),
    ("custom-model-a-key-alone", "evaluation_key_lookup"),
    ("custom-model-a-private-key-without-a-client-email", "evaluation_key_lookup"),
]
REFUSED_IDS = [f"{row}-{lookup}" for row, lookup in REFUSED_LOOKUPS]

# Rows stored for a Vertex model that give a service-account key in another
# form than the key's configuration alone: (stored as, what the row holds).
A_SERVICE_ACCOUNT_KEY = {
    "provider-key-as-json-text": ("provider_key_text", SERVICE_ACCOUNT_KEY),
    "provider-key-beside-a-key": (
        "provider_key",
        {**SERVICE_ACCOUNT_KEY, "key": LITERAL_KEY},
    ),
    "custom-model-beside-a-key": (
        "custom_model",
        {**SERVICE_ACCOUNT_KEY, "key": LITERAL_KEY},
    ),
    "custom-model-beside-a-key-and-a-location": (
        "custom_model",
        {**SERVICE_ACCOUNT_KEY, "key": LITERAL_KEY, "location": LOCATION},
    ),
}
GIVEN_READS = [
    (row, path)
    for row in A_SERVICE_ACCOUNT_KEY
    for path in ("run_prompt", "evaluation")
]
GIVEN_IDS = [f"{row}-{path}" for row, path in GIVEN_READS]


class _CallMade(Exception):
    """The model call was reached."""


class _Recorder:
    """Stands in for a model call: records what it is given, sends nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise _CallMade()


@pytest.fixture
def no_model_call(monkeypatch):
    """Every model call replaced by one recorder."""
    recorder = _Recorder()
    for name in ("completion", "acompletion", "image_generation"):
        monkeypatch.setattr(litellm, name, recorder)
    return recorder


def _copy(configuration):
    """A deep copy of ``configuration``."""
    return json.loads(json.dumps(configuration))


def _call(function, *args):
    """The exception ``function`` raised, or None."""
    try:
        function(*args)
    except Exception as exc:  # the refusal, or what the call raised
        return exc
    return None


def _store(stored_as, provider, value, organization, user, workspace):
    """A row of ``provider`` holding ``value``, written straight to its table:
    a custom model, or a provider key holding it as its configuration or as
    text (JSON text when it is not text already). Returns the provider a call
    looks the row up by: None for a custom model, which is found by its
    name."""
    if stored_as == "custom_model":
        CustomAIModel.objects.create(
            user_model_id=VERTEX_MODEL if provider == "vertex_ai" else OTHER_MODEL,
            provider=provider,
            input_token_cost=0.0,
            output_token_cost=0.0,
            organization=organization,
            workspace=workspace,
            user=user,
            key_config=_copy(value),
        )
        return None
    if stored_as == "provider_key_text":
        stored = {"key": value if isinstance(value, str) else json.dumps(value)}
    else:
        stored = {"config_json": _copy(value)}
    ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        workspace=workspace,
        provider=provider,
        **stored,
    )
    return provider


def _manager(model_name, organization):
    return LiteLLMModelManager(model_name=model_name, organization_id=organization.id)


def _look_up(lookup, provider, organization, workspace):
    """Read the stored row of a Vertex model the way ``lookup`` reads it."""
    if lookup == "get_provider_api_key":
        return _manager(VERTEX_MODEL, organization).get_provider_api_key(
            organization.id, "vertex_ai", workspace_id=workspace.id
        )
    if lookup == "evaluation_key_lookup":
        from evaluations.engine import instance as evaluation_engine

        return evaluation_engine._get_api_key(
            VERTEX_MODEL, organization.id, workspace.id
        )
    return _manager(VERTEX_MODEL, organization).get_api_key(
        organization_id=organization.id, workspace_id=workspace.id, provider=provider
    )


def _evaluation_llm(provider, model_name, api_key):
    """An evaluation's model for ``provider``: built as one whose provider
    needs no client of its own (a Vertex one needs platform settings these
    tests do not give), then given ``provider``. The model call takes its
    provider from the call."""
    llm = llm_module.LLM(provider="bedrock", model_name=model_name, api_key=api_key)
    llm.provider = provider
    return llm


def _call_with_the_stored_row(organization, workspace, lookup, provider, path):
    """Read the stored row the way every call reads it and make the call:
    Run Prompt's chat payload, or an evaluation's model call."""
    model_name = VERTEX_MODEL if provider == "vertex_ai" else OTHER_MODEL
    api_key = _manager(model_name, organization).get_api_key(
        organization_id=organization.id, workspace_id=workspace.id, provider=lookup
    )
    if path == "evaluation":
        llm = _evaluation_llm(provider, model_name, api_key)
        return llm.call_llm(CHAT, provider=provider)
    if isinstance(api_key, dict) and "key" in api_key:
        # Run Prompt sends a stored key given alone as text.
        api_key = api_key["key"]
    payload = {"model": model_name, "messages": CHAT}
    PayloadBuilder._add_api_key(payload, provider, api_key)
    return model_endpoints.guarded_completion(**payload)


# ---------------------------------------------------- where it is read ---


@pytest.mark.django_db
@pytest.mark.parametrize(("row", "lookup"), REFUSED_LOOKUPS, ids=REFUSED_IDS)
def test_a_vertex_row_that_gives_no_service_account_key_is_refused_where_it_is_read(
    organization, user, workspace, no_model_call, row, lookup
):
    stored_as, value = NO_SERVICE_ACCOUNT_KEY[row]
    provider = _store(stored_as, "vertex_ai", value, organization, user, workspace)

    error = _call(_look_up, lookup, provider, organization, workspace)

    assert no_model_call.calls == [], "a model call was made"
    assert str(error) == REFUSAL, "the lookup handed the row on"


# ------------------------------------------------------- at call time ---


@pytest.mark.django_db
@pytest.mark.parametrize(("row", "path"), GIVEN_READS, ids=GIVEN_IDS)
def test_a_service_account_key_reaches_the_call_as_the_models_vertex_configuration(
    organization, user, workspace, no_model_call, row, path
):
    stored_as, value = A_SERVICE_ACCOUNT_KEY[row]
    lookup = _store(stored_as, "vertex_ai", value, organization, user, workspace)

    _call(_call_with_the_stored_row, organization, workspace, lookup, "vertex_ai", path)

    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert "api_key" not in arguments, "a key was sent alone"
    assert "vertex_credentials" in arguments, "no Vertex configuration was sent"
    assert json.loads(arguments["vertex_credentials"]) == SERVICE_ACCOUNT_KEY
    assert arguments.get("vertex_location") == (
        LOCATION if isinstance(value, dict) and "location" in value else None
    )


# -------------------------------------------------------------- saved ---


def _saved(auth_client, organization, provider, key):
    """Save a provider key and say what was answered and stored, then remove
    the row: (status, [(provider, whether it holds a key as text, its
    configuration)])."""
    response = auth_client.post(
        API_KEYS_URL, {"provider": provider, "key": key}, format="json"
    )
    rows = ApiKey._base_manager.filter(organization=organization)
    stored = [(row.provider, bool(row.key), row.actual_json) for row in rows]
    rows.delete()
    return response.status_code, stored


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key",
    [LITERAL_KEY, json.dumps(SERVICE_ACCOUNT_KEY)],
    ids=["text-that-is-not-a-configuration", "a-service-account-key-as-text"],
)
def test_a_vertex_key_saved_under_a_spaced_provider_name_is_saved_as_under_its_own_name(
    auth_client, organization, key
):
    under_its_own_name = _saved(auth_client, organization, "vertex_ai", key)
    under_a_spaced_name = _saved(auth_client, organization, " vertex_ai", key)

    assert under_a_spaced_name == under_its_own_name


@pytest.mark.django_db
def test_text_that_is_not_a_configuration_is_not_saved_under_a_spaced_vertex_name(
    auth_client, organization
):
    answered, stored = _saved(auth_client, organization, " vertex_ai", LITERAL_KEY)

    assert answered == status.HTTP_400_BAD_REQUEST
    assert stored == []


# ----------------------------------------------------------- as before ---


@pytest.mark.django_db
def test_another_providers_key_held_as_text_reaches_its_call_as_given(
    organization, user, workspace, no_model_call
):
    lookup = _store(
        "provider_key_text", "openai", LITERAL_KEY, organization, user, workspace
    )

    error = _call(
        _call_with_the_stored_row,
        organization,
        workspace,
        lookup,
        "openai",
        "run_prompt",
    )

    assert isinstance(error, _CallMade), repr(error)
    _args, arguments = no_model_call.calls[0]
    assert arguments == {"model": OTHER_MODEL, "messages": CHAT, "api_key": LITERAL_KEY}


@pytest.mark.django_db
@pytest.mark.parametrize("stored_as", ["custom_model", "provider_key"])
def test_a_vertex_row_that_gives_its_key_and_location_is_handed_on_as_it_is(
    organization, user, workspace, no_model_call, stored_as
):
    value = {**SERVICE_ACCOUNT_KEY, "location": LOCATION}
    provider = _store(stored_as, "vertex_ai", value, organization, user, workspace)

    given = _look_up("get_api_key", provider, organization, workspace)

    assert given == value
    assert no_model_call.calls == [], "a model call was made"


@pytest.mark.django_db
def test_a_vertex_model_with_no_saved_key_is_answered_that_no_key_is_configured(
    organization, workspace, no_model_call
):
    error = _call(
        _call_with_the_stored_row,
        organization,
        workspace,
        "vertex_ai",
        "vertex_ai",
        "run_prompt",
    )

    assert no_model_call.calls == [], "a model call was made"
    assert isinstance(error, ValueError), repr(error)
    assert "API key not configured for vertex_ai" in str(error)
