"""Every call to a Vertex model carries its saved service-account key.

A Vertex model's saved service-account key reaches each of these calls as
the model's Vertex configuration, with its location, and no key is sent:
Run Prompt's image generation and speech, and the prompt optimiser's
generator, reflection model check, reflection model and prompt variations.
Each of these calls to a Vertex model that carries no Vertex configuration,
or is given a configuration that holds a key alone, is refused before it is
made, with the one plain sentence the other refusals use. So are Run
Prompt's image generation and speech for a model whose provider is Vertex AI
but whose name the model library sends to another provider.

Another provider's call is given what it is given today, and so is a call
the prompt optimiser makes with the platform's own settings.

The tests use made-up names and keys, replace every model call with a
recorder that sends nothing, and contact nothing.
"""

import json
import time

import litellm
import pytest

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.custom_models import CustomAIModel

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
CHAT = [{"role": "user", "content": "hi"}]
ORGANIZATION_ID = "fi-test-organization"
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
SAVED = {**SERVICE_ACCOUNT_KEY, "location": LOCATION}
# Made-up model names; a customer's Vertex model is named vertex_ai/...
VERTEX_CHAT_MODEL = "vertex_ai/fi-test-gemini-model"
VERTEX_IMAGE_MODEL = "vertex_ai/fi-test-image-model"
VERTEX_SPEECH_MODEL = "vertex_ai/fi-test-speech-model"
OTHER_CHAT_MODEL = "openai/fi-test-other-model"
OTHER_IMAGE_MODEL = "openai/fi-test-image-model"
OTHER_SPEECH_MODEL = "openai/fi-test-speech-model"


class _CallMade(Exception):
    """The model call was reached."""


class _Recorder:
    """Stands in for a model call: records what it is given, sends nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise _CallMade()


_MODEL_CALLS = (
    "completion",
    "acompletion",
    "image_generation",
    "aimage_generation",
    "speech",
    "aspeech",
    "transcription",
    "atranscription",
)


@pytest.fixture
def no_model_call(monkeypatch):
    """Every model call replaced by one recorder."""
    recorder = _Recorder()
    for name in _MODEL_CALLS:
        monkeypatch.setattr(litellm, name, recorder, raising=False)
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


def _save(model_name, configuration, organization, user, workspace):
    """A Vertex custom model named ``model_name`` holding ``configuration``."""
    return CustomAIModel.objects.create(
        user_model_id=model_name,
        provider="vertex_ai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config=_copy(configuration),
    )


def _saved_key(model_name, organization, workspace):
    """What a call reads for ``model_name``, read the way every call reads it."""
    manager = LiteLLMModelManager(
        model_name=model_name, organization_id=organization.id
    )
    return manager.get_api_key(
        organization_id=organization.id, workspace_id=workspace.id
    )


# ----------------------------------------------------------- the routes ---


def _handler_context(model, api_key, provider, output_format):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
        ModelHandlerContext,
    )

    return ModelHandlerContext(
        model=model,
        messages=_copy(CHAT),
        organization_id=ORGANIZATION_ID,
        output_format=output_format,
        provider=provider,
        api_key=api_key,
    )


def _image_on_the_image_handler(model, api_key, provider, recorder):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler import (
        ImageHandler,
    )

    context = _handler_context(model, api_key, provider, "image")
    return ImageHandler(context)._image_generation_response(time.time())


def _speech_on_the_speech_handler(model, api_key, provider, recorder):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
        SpeechAPIHandler,
    )

    context = _handler_context(model, api_key, provider, "audio")
    response = SpeechAPIHandler(context).execute_sync()
    if response.failure:
        # Run Prompt raises a failed handler's answer.
        raise Exception(response.failure)
    return response


def _optimiser_generator(model, api_key, provider, recorder):
    from ee.agent_opt.generators.litellm_generator import LiteLLMGenerator

    generator = LiteLLMGenerator(model=model, prompt_template="{text}")
    return generator.generate({"text": "hi"}, api_key=api_key)


def _optimiser_reflection_check(model, api_key, provider, recorder):
    from ee.agent_opt.optimizers.gepa import GEPAOptimizer

    optimizer = GEPAOptimizer(reflection_model=model)
    optimizer.api_key = api_key
    return optimizer._verify_reflection_auth()


def _optimiser_reflection(model, api_key, provider, recorder):
    """The reflection model's own call. The model is built with a complete
    saved key (the check made when it is built is its own route) and then
    given ``api_key``; only the calls made after it is built are read."""
    from ee.agent_opt.optimizers.gepa import GEPAOptimizer

    optimizer = GEPAOptimizer(reflection_model=model)
    optimizer.api_key = _copy(SAVED)
    reflection = optimizer._build_reflection_lm()
    recorder.calls.clear()
    optimizer.api_key = api_key
    return reflection("hi")


def _optimiser_variations(model, api_key, provider, recorder):
    from ee.agent_opt.optimizers.random_search import _generate_variations

    return _generate_variations("fi-test prompt", 2, model, {}, api_key)


def _variations_max_tokens(model):
    from ee.agent_opt.optimizers.random_search import _get_max_output_tokens

    return _get_max_output_tokens(model)


# Each route: (its call, its Vertex model, another provider's model, what
# the route's call is given for another provider's model with a key held as
# text, the key it is given with nothing saved).
ROUTES = {
    "image-on-the-image-handler": (
        _image_on_the_image_handler,
        VERTEX_IMAGE_MODEL,
        OTHER_IMAGE_MODEL,
        lambda: {
            "model": OTHER_IMAGE_MODEL,
            "prompt": "hi",
            "api_key": LITERAL_KEY,
            "size": "1024x1024",
            "n": 1,
            "drop_params": True,
        },
        None,
    ),
    "speech-on-the-speech-handler": (
        _speech_on_the_speech_handler,
        VERTEX_SPEECH_MODEL,
        OTHER_SPEECH_MODEL,
        lambda: {
            "model": OTHER_SPEECH_MODEL,
            "input": "hi",
            "api_key": LITERAL_KEY,
            "drop_params": True,
        },
        None,
    ),
    "optimiser-generator": (
        _optimiser_generator,
        VERTEX_CHAT_MODEL,
        OTHER_CHAT_MODEL,
        lambda: {
            "model": OTHER_CHAT_MODEL,
            "messages": _copy(CHAT),
            "api_key": LITERAL_KEY,
            "drop_params": True,
        },
        # With no key the optimiser uses the platform's own settings.
        LITERAL_KEY,
    ),
    "optimiser-reflection-check": (
        _optimiser_reflection_check,
        VERTEX_CHAT_MODEL,
        OTHER_CHAT_MODEL,
        lambda: {
            "model": OTHER_CHAT_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "api_key": LITERAL_KEY,
            "drop_params": True,
        },
        None,
    ),
    "optimiser-reflection": (
        _optimiser_reflection,
        VERTEX_CHAT_MODEL,
        OTHER_CHAT_MODEL,
        lambda: {
            "model": OTHER_CHAT_MODEL,
            "messages": _copy(CHAT),
            "api_key": LITERAL_KEY,
            "drop_params": True,
        },
        None,
    ),
    "optimiser-variations": (
        _optimiser_variations,
        VERTEX_CHAT_MODEL,
        OTHER_CHAT_MODEL,
        lambda: {
            "model": OTHER_CHAT_MODEL,
            "api_key": LITERAL_KEY,
            "response_format": {"type": "json_object"},
            "max_tokens": _variations_max_tokens(OTHER_CHAT_MODEL),
            "drop_params": True,
        },
        # With no key the optimiser uses the platform's own settings.
        LITERAL_KEY,
    ),
}
ROUTE_IDS = list(ROUTES)
# The prompt variations' question is long; it is compared apart.
_ASKED_APART = {"optimiser-variations"}


def _given_apart(route, arguments):
    """``arguments`` without what ``route`` asks, which is checked here."""
    if route not in _ASKED_APART:
        return arguments
    messages = arguments.get("messages")
    assert isinstance(messages, list) and len(messages) == 1, messages
    assert messages[0]["role"] == "user"
    assert "fi-test prompt" in messages[0]["content"]
    return {name: item for name, item in arguments.items() if name != "messages"}


# ------------------------------------------------------- at call time ---


@pytest.mark.django_db
@pytest.mark.parametrize("route", ROUTE_IDS, ids=ROUTE_IDS)
def test_a_saved_service_account_key_reaches_the_call_as_the_models_vertex_configuration(
    organization, user, workspace, no_model_call, route
):
    call, vertex_model, _other_model, _given, _nothing = ROUTES[route]
    _save(vertex_model, SAVED, organization, user, workspace)
    api_key = _saved_key(vertex_model, organization, workspace)

    _call(call, vertex_model, api_key, "vertex_ai", no_model_call)

    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert "vertex_credentials" in arguments, "no Vertex configuration was sent"
    assert json.loads(arguments["vertex_credentials"]) == SERVICE_ACCOUNT_KEY
    assert "api_key" not in arguments, "a key was sent"
    assert arguments.get("vertex_location") == LOCATION


@pytest.mark.django_db
@pytest.mark.parametrize("route", ROUTE_IDS, ids=ROUTE_IDS)
def test_a_vertex_call_with_no_vertex_configuration_is_refused_before_the_call(
    no_model_call, route
):
    call, vertex_model, _other_model, _given, nothing = ROUTES[route]

    error = _call(call, vertex_model, nothing, "vertex_ai", no_model_call)

    assert no_model_call.calls == [], "the model call was made"
    assert str(error) == REFUSAL, "the call was not refused"


# A configuration that holds a key alone, with or without a location: the
# forms' key-only shape, which gives no service-account key.
KEY_ALONE = {
    "key-alone": {"key": LITERAL_KEY},
    "key-and-location": {"key": LITERAL_KEY, "location": LOCATION},
}


@pytest.mark.django_db
@pytest.mark.parametrize("configuration", list(KEY_ALONE), ids=list(KEY_ALONE))
@pytest.mark.parametrize("route", ROUTE_IDS, ids=ROUTE_IDS)
def test_a_vertex_call_given_a_key_alone_as_its_configuration_is_refused(
    no_model_call, route, configuration
):
    call, vertex_model, _other_model, _given, _nothing = ROUTES[route]

    error = _call(
        call, vertex_model, _copy(KEY_ALONE[configuration]), "vertex_ai", no_model_call
    )

    assert no_model_call.calls == [], "the model call was made"
    assert str(error) == REFUSAL, "the call was not refused"


# The routes that have the model's provider in hand when they call.
PROVIDER_IN_HAND = ["image-on-the-image-handler", "speech-on-the-speech-handler"]


@pytest.mark.django_db
@pytest.mark.parametrize("route", PROVIDER_IN_HAND, ids=PROVIDER_IN_HAND)
def test_a_vertex_model_named_as_another_providers_is_refused_before_the_call(
    no_model_call, route
):
    """The model's provider is Vertex AI; its name is one the model library
    sends to another provider. The call is not made."""
    call, _vertex_model, other_model, _given, _nothing = ROUTES[route]

    error = _call(call, other_model, _copy(SAVED), "vertex_ai", no_model_call)

    assert no_model_call.calls == [], "the model call was made"
    assert str(error) == REFUSAL, "the call was not refused"


@pytest.mark.django_db
@pytest.mark.parametrize("route", ROUTE_IDS, ids=ROUTE_IDS)
def test_another_providers_call_is_given_what_it_is_given_today(no_model_call, route):
    call, _vertex_model, other_model, given, _nothing = ROUTES[route]

    error = _call(call, other_model, LITERAL_KEY, "openai", no_model_call)

    assert len(no_model_call.calls) == 1, repr(error)
    _args, arguments = no_model_call.calls[0]
    assert _given_apart(route, arguments) == given()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route", ["optimiser-generator", "optimiser-variations"], ids=str
)
def test_the_optimisers_call_with_the_platforms_own_settings_is_as_today(
    no_model_call, route
):
    call, vertex_model, _other_model, _given, _nothing = ROUTES[route]

    error = _call(call, vertex_model, None, "vertex_ai", no_model_call)

    assert len(no_model_call.calls) == 1, repr(error)
    _args, arguments = no_model_call.calls[0]
    assert arguments["api_key"] is None
    assert "vertex_credentials" not in arguments
