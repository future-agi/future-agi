"""A customer's Vertex model runs only with the customer's own key.

Where only a customer's model is called (the custom-model forms' validation
call, and Run Prompt's chat, image, speech and transcription calls), a
Vertex model is never called without a service-account key of the
customer's own: given a key alone, no configuration, an empty one, or one
that gives nothing but its location, it is refused with the plain sentence
before any call. A Vertex model given a service-account key is called with
that key, and the location it gives, by Run Prompt's chat, image and speech
calls; Run Prompt's transcription call carries no such key, so a Vertex
model is refused there whatever is stored. A model of another provider is
still called as before, and a Vertex chat model that runs on the
organization's Future AGI credits is still sent to the managed route.

The tests use values they set themselves; every model call is replaced by a
recorder, so nothing leaves the process.
"""

import asyncio
import io
import json
import uuid

import litellm
import pytest
from rest_framework import status

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler import (
    ImageHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
    LLMHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.stt_handler import (
    StandardSTTHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
    SpeechAPIHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)
from model_hub.models.custom_models import CustomAIModel
from model_hub.services import model_route
from model_hub.services.model_route import ManagedRouteBinding
from model_hub.utils.model_endpoints import sets_platform_identity_field
from model_hub.utils.utils import validate_model_working

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
CUSTOM_MODEL_CREATE_URL = "/model-hub/custom_models/create/"
CUSTOM_MODEL_EDIT_URL = "/model-hub/custom_models/edit/"
CHAT = [{"role": "user", "content": "hi"}]
ORGANIZATION_ID = "fi-test-organization"
SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key_id": "fi-test-key-id-not-real",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}
VERTEX_MODEL = "vertex_ai/gemini-2.5-flash"
A_KEY_ALONE = "fi-test-vertex-key-not-real"
LOCATION = "us-central1"

# What a Vertex model may be given that is no service-account key of the
# customer's own.
NO_KEY_OF_ITS_OWN = {
    "a_key_alone": A_KEY_ALONE,
    "an_empty_configuration": {},
    "an_empty_configuration_as_text": "{}",
    "only_a_location": {"location": LOCATION},
}


class _CallMade(Exception):
    """The model call was reached."""


class _Recorder:
    """Stands in for every model call: records what it is given, sends
    nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        raise _CallMade()


@pytest.fixture
def model_calls(monkeypatch):
    """Every model call replaced by one recorder."""
    recorder = _Recorder()
    for name in (
        "completion",
        "acompletion",
        "image_generation",
        "transcription",
        "speech",
        "aspeech",
    ):
        monkeypatch.setattr(litellm, name, recorder)
    return recorder


@pytest.fixture
def audio_input(monkeypatch):
    """Audio a transcription call reads from its messages, read as given."""
    monkeypatch.setattr(
        AudioProcessor,
        "extract_audio_from_messages",
        staticmethod(lambda messages: "fi-test-audio"),
    )
    monkeypatch.setattr(
        AudioProcessor,
        "normalize_audio_for_stt",
        staticmethod(lambda audio: (b"fi-test-audio", "mp3")),
    )
    monkeypatch.setattr(
        AudioProcessor,
        "create_audio_buffer",
        staticmethod(lambda audio_bytes, file_ext: io.BytesIO(audio_bytes)),
    )


def _outcome(run):
    """The text of the refusal, or of what failed; None when nothing did."""
    try:
        result = run()
    except Exception as exc:  # the refusal, or what the call raised
        return str(exc)
    if isinstance(result, HandlerResponse):
        return result.failure
    if isinstance(result, Exception):
        return str(result)
    return None


def _assert_refused_before_any_call(outcome, model_calls):
    assert model_calls.calls == [], "the model call was made"
    assert outcome == REFUSAL, "the call was not refused with the plain sentence"


def _context(model, provider, api_key):
    return ModelHandlerContext(
        model=model,
        messages=CHAT,
        organization_id=ORGANIZATION_ID,
        provider=provider,
        api_key=api_key,
    )


# ------------------------------------------- the forms' validation call ---

# (what the validation is given, the model's provider, the model)
VALIDATION_WITHOUT_A_KEY_OF_ITS_OWN = {
    "a_key_alone": ({"key": A_KEY_ALONE}, "vertex_ai", VERTEX_MODEL),
    "a_key_alone_for_a_vertex_model_under_another_provider": (
        {"key": A_KEY_ALONE},
        "custom",
        VERTEX_MODEL,
    ),
    "the_edit_forms_key_alone": ({"api_key": A_KEY_ALONE}, "vertex_ai", VERTEX_MODEL),
    "an_empty_configuration": ({"config_json": {}}, "vertex_ai", VERTEX_MODEL),
    "only_a_location": (
        {"config_json": {"location": LOCATION}},
        "vertex_ai",
        VERTEX_MODEL,
    ),
}


@pytest.mark.parametrize("given", sorted(VALIDATION_WITHOUT_A_KEY_OF_ITS_OWN))
def test_validating_a_vertex_model_without_a_key_of_its_own_makes_no_call(
    model_calls, given
):
    api_key, provider, model_name = VALIDATION_WITHOUT_A_KEY_OF_ITS_OWN[given]
    outcome = _outcome(
        lambda: validate_model_working(
            model_name=model_name, api_key=api_key, provider=provider
        )
    )
    _assert_refused_before_any_call(outcome, model_calls)


def test_validating_a_model_of_another_provider_given_a_key_alone_still_calls_it(
    model_calls,
):
    validate_model_working(
        model_name="claude-3-5-haiku-20241022",
        api_key={"key": "fi-test-anthropic-key-not-real"},
        provider="anthropic",
    )
    assert len(model_calls.calls) == 1, "the model call was not made"
    assert model_calls.calls[0]["api_key"] == "fi-test-anthropic-key-not-real"


# ------------------------------------------------ creating and editing ---

# What a form may send for a Vertex model that is no service-account key of
# the customer's own.
FORM_WITHOUT_A_KEY_OF_ITS_OWN = {
    "a_key_alone": {"key": A_KEY_ALONE},
    "only_a_location": {"config_json": {"location": LOCATION}},
}


def _refusal(response):
    """A form's refusal, as text; None when the form was not refused."""
    if response.status_code != status.HTTP_400_BAD_REQUEST:
        return None
    return json.dumps(response.data, default=str)


def _assert_form_refused_before_any_call(refusal, model_calls):
    assert model_calls.calls == [], "the model call was made"
    assert refusal is not None and REFUSAL in refusal, (
        "the form was not refused with the plain sentence"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("sent", sorted(FORM_WITHOUT_A_KEY_OF_ITS_OWN))
def test_creating_a_vertex_custom_model_without_a_key_of_its_own_is_refused(
    auth_client, model_calls, sent
):
    name = f"gemini-2.5-flash-{uuid.uuid4().hex[:8]}"
    response = auth_client.post(
        CUSTOM_MODEL_CREATE_URL,
        {
            "model_name": name,
            "input_token_cost": 0.01,
            "output_token_cost": 0.03,
            "model_provider": "vertex_ai",
            **FORM_WITHOUT_A_KEY_OF_ITS_OWN[sent],
        },
        format="json",
    )
    _assert_form_refused_before_any_call(_refusal(response), model_calls)
    created = CustomAIModel._base_manager.filter(user_model_id=f"vertex_ai/{name}")
    assert not created.exists(), "the model was saved"


@pytest.mark.django_db
@pytest.mark.parametrize("sent", sorted(FORM_WITHOUT_A_KEY_OF_ITS_OWN))
def test_editing_a_vertex_custom_model_without_a_key_of_its_own_is_refused_with_the_plain_sentence(
    auth_client, organization, user, workspace, model_calls, sent
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
        {"id": str(model.id), **FORM_WITHOUT_A_KEY_OF_ITS_OWN[sent]},
        format="json",
    )
    _assert_form_refused_before_any_call(_refusal(response), model_calls)
    stored = CustomAIModel._base_manager.get(pk=model.pk)
    assert stored.actual_json == SERVICE_ACCOUNT_KEY


# -------------------------------------------------- Run Prompt's calls ---

# (the model's provider, the model, what the call is given): a Vertex chat
# model with no service-account key of the customer's own.
CHAT_WITHOUT_A_KEY_OF_ITS_OWN = {
    **{
        given: ("vertex_ai", VERTEX_MODEL, value)
        for given, value in NO_KEY_OF_ITS_OWN.items()
    },
    "a_vertex_model_listed_under_another_provider": (
        "openai",
        VERTEX_MODEL,
        A_KEY_ALONE,
    ),
}


@pytest.mark.parametrize("given", sorted(CHAT_WITHOUT_A_KEY_OF_ITS_OWN))
def test_a_vertex_chat_model_without_a_key_of_its_own_is_refused_before_any_call(
    model_calls, given
):
    provider, model, api_key = CHAT_WITHOUT_A_KEY_OF_ITS_OWN[given]
    handler = LLMHandler(_context(model, provider, api_key))
    _assert_refused_before_any_call(_outcome(handler.execute_sync), model_calls)


def test_a_vertex_chat_model_with_a_service_account_key_is_still_called_with_it(
    model_calls,
):
    handler = LLMHandler(_context(VERTEX_MODEL, "vertex_ai", SERVICE_ACCOUNT_KEY))
    _outcome(handler.execute_sync)
    assert len(model_calls.calls) == 1, "the model call was not made"
    given = model_calls.calls[0]["vertex_credentials"]
    assert json.loads(given) == SERVICE_ACCOUNT_KEY


@pytest.fixture
def managed_calls(monkeypatch):
    """The managed route's one call per request, replaced by a recorder:
    what it is given is recorded and nothing is sent."""
    sent = []

    def managed_chat_completion(binding, kwargs):
        sent.append((binding, kwargs))
        raise _CallMade()

    async def managed_chat_completion_async(binding, kwargs):
        sent.append((binding, kwargs))
        raise _CallMade()

    monkeypatch.setattr(model_route, "managed_chat_completion", managed_chat_completion)
    monkeypatch.setattr(
        model_route, "managed_chat_completion_async", managed_chat_completion_async
    )
    return sent


def _runs_on_credits(model):
    """What Run Prompt is given for a model with no stored key that runs on
    the organization's Future AGI credits."""
    return ManagedRouteBinding(
        organization_id=ORGANIZATION_ID,
        workspace_id=None,
        model_name=model,
        provider="vertex_ai",
        operation="run_prompt",
        port=None,
    )


@pytest.mark.parametrize("awaited", [False, True], ids=["called", "awaited"])
def test_a_vertex_chat_model_that_runs_on_credits_is_still_sent_to_the_managed_route(
    model_calls, managed_calls, awaited
):
    binding = _runs_on_credits(VERTEX_MODEL)
    handler = LLMHandler(_context(VERTEX_MODEL, "vertex_ai", binding))
    run = (
        (lambda: asyncio.run(handler.execute_async()))
        if awaited
        else handler.execute_sync
    )
    outcome = _outcome(run)
    assert outcome is None or REFUSAL not in outcome, "the call was refused"
    assert model_calls.calls == [], "the model library was called"
    assert len(managed_calls) == 1, "the managed route was not called"
    given_binding, _arguments = managed_calls[0]
    assert given_binding is binding


# call -> (the handler, a Vertex model for it, a model of another provider)
OTHER_CALLS = {
    "image": (ImageHandler, "vertex_ai/imagen-3.0-generate-001", "dall-e-3"),
    "speech": (SpeechAPIHandler, "vertex_ai/chirp", "tts-1"),
    "transcription": (StandardSTTHandler, "vertex_ai/chirp", "whisper-1"),
}
# What may be stored for a Vertex model that is no service-account key of
# the customer's own, nothing at all included.
NO_KEY_OF_ITS_OWN_STORED = {**NO_KEY_OF_ITS_OWN, "no_configuration": None}
STORED_FOR_A_VERTEX_MODEL = {
    **NO_KEY_OF_ITS_OWN_STORED,
    "a_service_account_key": SERVICE_ACCOUNT_KEY,
}
# (call, what is stored) for every call refused before it is made: each
# call given no service-account key of the customer's own, and the
# transcription call whatever is stored, since it carries no such key.
REFUSED_FOR_A_VERTEX_MODEL = sorted(
    [(call, given) for call in OTHER_CALLS for given in NO_KEY_OF_ITS_OWN_STORED]
    + [("transcription", "a_service_account_key")]
)


@pytest.mark.parametrize(("call", "given"), REFUSED_FOR_A_VERTEX_MODEL)
def test_a_vertex_model_for_images_speech_or_transcription_is_refused_before_any_call(
    model_calls, audio_input, call, given
):
    handler_class, vertex_model, _other_model = OTHER_CALLS[call]
    handler = handler_class(
        _context(vertex_model, "vertex_ai", STORED_FOR_A_VERTEX_MODEL[given])
    )
    _assert_refused_before_any_call(_outcome(handler.execute_sync), model_calls)


def _a_service_account_document():
    """A service-account document made here for this test, with made-up key
    material and a location of its own: never a real key."""
    made_up = uuid.uuid4().hex[:8]
    return {
        "type": "service_account",
        "project_id": f"fi-test-project-{made_up}",
        "private_key_id": f"fi-test-key-id-{made_up}",
        "private_key": f"fi-test-private-key-not-real-{made_up}",
        "client_email": f"fi-test-{made_up}@fi-test-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
        "location": "europe-west4",
    }


# call -> (the handler, a Vertex model for it, whether the call is awaited):
# the calls made with the service-account key stored for a customer's model.
CALLS_WITH_THE_CUSTOMERS_OWN_KEY = {
    "image": (ImageHandler, "vertex_ai/imagen-3.0-generate-001", False),
    "speech": (SpeechAPIHandler, "vertex_ai/chirp", False),
    "speech_awaited": (SpeechAPIHandler, "vertex_ai/chirp", True),
}


@pytest.mark.parametrize("call", sorted(CALLS_WITH_THE_CUSTOMERS_OWN_KEY))
def test_a_vertex_model_for_images_or_speech_is_called_with_the_customers_own_key(
    model_calls, call
):
    handler_class, vertex_model, awaited = CALLS_WITH_THE_CUSTOMERS_OWN_KEY[call]
    stored = _a_service_account_document()
    handler = handler_class(_context(vertex_model, "vertex_ai", stored))
    if awaited:
        _outcome(lambda: asyncio.run(handler.execute_async()))
    else:
        _outcome(handler.execute_sync)
    assert len(model_calls.calls) == 1, "the model call was not made"
    given = model_calls.calls[0]
    key_without_location = dict(stored)
    location = key_without_location.pop("location")
    credentials = json.loads(given["vertex_credentials"])
    assert credentials == key_without_location
    assert given["vertex_location"] == location
    assert "api_key" not in given, "the stored configuration was given as a key"
    assert not sets_platform_identity_field(given)
    assert not sets_platform_identity_field(credentials)


@pytest.mark.parametrize("call", sorted(OTHER_CALLS))
def test_a_model_of_another_provider_for_images_speech_or_transcription_is_still_called(
    model_calls, audio_input, call
):
    handler_class, _vertex_model, other_model = OTHER_CALLS[call]
    handler = handler_class(
        _context(other_model, "openai", "fi-test-openai-key-not-real")
    )
    _outcome(handler.execute_sync)
    assert len(model_calls.calls) == 1, "the model call was not made"
    assert model_calls.calls[0]["api_key"] == "fi-test-openai-key-not-real"


# call -> (the handler, a Vertex model for it): the calls that are awaited.
AWAITED_CALLS = {
    "chat": (LLMHandler, VERTEX_MODEL),
    "speech": (SpeechAPIHandler, "vertex_ai/chirp"),
}


@pytest.mark.parametrize("call", sorted(AWAITED_CALLS))
def test_an_awaited_call_to_a_vertex_model_given_a_key_alone_is_refused_before_it(
    model_calls, call
):
    handler_class, vertex_model = AWAITED_CALLS[call]
    handler = handler_class(_context(vertex_model, "vertex_ai", A_KEY_ALONE))
    outcome = _outcome(lambda: asyncio.run(handler.execute_async()))
    _assert_refused_before_any_call(outcome, model_calls)
