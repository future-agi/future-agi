"""A Vertex model runs only with a complete service-account key.

A Vertex model configuration is a complete service-account key when, once
its location is set aside, it is an object that gives a private key and a
client email as text that is not blank, gives no type or a service-account
key's, and is a service-account key that signs in only at Google's own
addresses. Wherever a customer's Vertex model is called (the custom-model
forms' validation call, Run Prompt's chat, image, speech and transcription
calls, an evaluation's model call, and any call through the shared guard),
anything else is refused with the plain sentence before any call: a key
alone, a project alone or with a location, a client email without a private
key, a private key without a client email, an empty configuration. A
complete key runs, with or without a type, and the location it gives
reaches the call; Run Prompt's transcription call carries no such key, so
it refuses a Vertex model whatever it is given. The count of stored Vertex
rows without a key of their own counts exactly the configurations the call
refuses.

The tests use values they set themselves, never real key material; every
model call is replaced by a recorder, so nothing leaves the process.
"""

import asyncio
import copy
import io
import json

import litellm
import pytest

from agentic_eval.core.llm import llm as llm_module
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
from model_hub.utils import model_endpoints
from model_hub.utils.model_endpoints import (
    stored_vertex_row_gives_no_service_account_key,
    vertex_call_has_no_service_account_key,
)
from model_hub.utils.utils import validate_model_working

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
CHAT = [{"role": "user", "content": "hi"}]
ORGANIZATION_ID = "fi-test-organization"
VERTEX_MODEL = "vertex_ai/gemini-2.5-flash"
LOCATION = "us-central1"
SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key_id": "fi-test-key-id-not-real",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# What a Vertex model may be given that is not a complete service-account
# key.
NOT_A_COMPLETE_KEY = {
    "a_key_alone": {"key": "fi-test-vertex-key-not-real"},
    "a_project_alone": {"project_id": "fi-test-project"},
    "a_project_and_a_location": {"project_id": "fi-test-project", "location": LOCATION},
    "a_client_email_without_a_private_key": {
        "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com"
    },
    "a_private_key_without_a_client_email": {"private_key": "-----"},
    "an_empty_configuration": {},
}
# Complete service-account keys: with a type, without one (read as a
# service-account key), and with the location the model runs in.
A_COMPLETE_KEY = {
    "a_service_account_key": SERVICE_ACCOUNT_KEY,
    "a_service_account_key_without_a_type": {
        name: value for name, value in SERVICE_ACCOUNT_KEY.items() if name != "type"
    },
    "a_service_account_key_with_a_location": {
        **SERVICE_ACCOUNT_KEY,
        "location": LOCATION,
    },
}
# Every model call the model library makes.
MODEL_LIBRARY_CALLS = (
    "completion",
    "acompletion",
    "image_generation",
    "transcription",
    "speech",
    "aspeech",
)


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
    for name in MODEL_LIBRARY_CALLS:
        monkeypatch.setattr(litellm, name, recorder)
    return recorder


@pytest.fixture
def no_gateway(monkeypatch):
    """No platform gateway here: a call falls back to the model library."""
    try:
        from ee.usage.services import gateway_llm_client
    except ImportError:
        return
    for name in ("get_gateway_client", "get_async_gateway_client"):
        monkeypatch.setattr(gateway_llm_client, name, lambda *args, **kwargs: None)


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


def _as_a_call_gives_it(configuration):
    """A stored Vertex configuration as a model call is given it: the rest
    of it as JSON text, and the location it gives (None when it gives
    none)."""
    rest = dict(configuration)
    location = rest.pop("location", None)
    return json.dumps(rest), location


def _context(model, configuration):
    return ModelHandlerContext(
        model=model,
        messages=CHAT,
        organization_id=ORGANIZATION_ID,
        provider="vertex_ai",
        api_key=configuration,
    )


# ------------------------------------- every place a Vertex model is called ---


def _validation(configuration):
    """The custom-model forms' validation call."""
    return validate_model_working(
        model_name=VERTEX_MODEL,
        api_key={"config_json": configuration},
        provider="vertex_ai",
    )


def _run_prompt(handler_class, model, *, awaited=False):
    """A Run Prompt call through ``handler_class``, awaited or not."""

    def run(configuration):
        handler = handler_class(_context(model, configuration))
        if awaited:
            return asyncio.run(handler.execute_async())
        return handler.execute_sync()

    return run


def _evaluation(configuration):
    """An evaluation's model call: the model is built as one whose provider
    needs no client of its own, then given Vertex AI; the call takes its
    provider from the call."""
    llm = llm_module.LLM(
        provider="bedrock", model_name=VERTEX_MODEL, api_key=configuration
    )
    llm.provider = "vertex_ai"
    return llm.call_llm(CHAT, provider="vertex_ai")


def _through_the_shared_guard(configuration):
    """Any call through the shared guard, given the configuration as the
    calls above give it."""
    credentials, location = _as_a_call_gives_it(configuration)
    arguments = {"vertex_credentials": credentials}
    if location:
        arguments["vertex_location"] = location
    return model_endpoints.guarded_completion(
        model=VERTEX_MODEL,
        custom_llm_provider="vertex_ai",
        messages=CHAT,
        **arguments,
    )


# place -> how a customer's Vertex model is called there: every place that
# runs it with a complete service-account key.
RUNS_WITH_A_COMPLETE_KEY = {
    "validation": _validation,
    "run_prompt_chat": _run_prompt(LLMHandler, VERTEX_MODEL),
    "run_prompt_chat_awaited": _run_prompt(LLMHandler, VERTEX_MODEL, awaited=True),
    "run_prompt_image": _run_prompt(ImageHandler, "vertex_ai/imagen-3.0-generate-001"),
    "run_prompt_speech": _run_prompt(SpeechAPIHandler, "vertex_ai/chirp"),
    "run_prompt_speech_awaited": _run_prompt(
        SpeechAPIHandler, "vertex_ai/chirp", awaited=True
    ),
    "evaluation": _evaluation,
    "a_call_through_the_shared_guard": _through_the_shared_guard,
}
# Every place a customer's Vertex model is called; the transcription call
# carries no service-account key, so it refuses a Vertex model whatever it
# is given.
EVERY_PLACE = {
    **RUNS_WITH_A_COMPLETE_KEY,
    "run_prompt_transcription": _run_prompt(StandardSTTHandler, "vertex_ai/chirp"),
}


@pytest.mark.parametrize(
    ("place", "given"),
    sorted((place, given) for place in EVERY_PLACE for given in NOT_A_COMPLETE_KEY),
)
def test_a_vertex_model_without_a_complete_service_account_key_is_refused_before_any_call(
    request, model_calls, no_gateway, place, given
):
    if place == "run_prompt_transcription":
        request.getfixturevalue("audio_input")
    configuration = copy.deepcopy(NOT_A_COMPLETE_KEY[given])
    outcome = _outcome(lambda: EVERY_PLACE[place](configuration))
    assert model_calls.calls == [], "the model call was made"
    assert outcome is not None and REFUSAL in outcome, (
        "the call was not refused with the plain sentence"
    )


@pytest.mark.parametrize(
    ("place", "given"),
    sorted(
        (place, given) for place in RUNS_WITH_A_COMPLETE_KEY for given in A_COMPLETE_KEY
    ),
)
def test_a_vertex_model_with_a_complete_service_account_key_runs_with_it_and_its_location(
    model_calls, no_gateway, place, given
):
    configuration = A_COMPLETE_KEY[given]
    _outcome(lambda: RUNS_WITH_A_COMPLETE_KEY[place](copy.deepcopy(configuration)))
    assert len(model_calls.calls) == 1, "the model call was not made"
    called = model_calls.calls[0]
    key = dict(configuration)
    location = key.pop("location", None)
    credentials = called["vertex_credentials"]
    if isinstance(credentials, str):
        credentials = json.loads(credentials)
    assert credentials == key
    assert called.get("vertex_location") == location
    assert "api_key" not in called, "the configuration was given as a key"


# ---------------------------------------- the count of stored Vertex rows ---

# configuration -> (the configuration, whether a call given it is refused)
EVERY_CONFIGURATION = {
    **{given: (value, True) for given, value in NOT_A_COMPLETE_KEY.items()},
    **{given: (value, False) for given, value in A_COMPLETE_KEY.items()},
}


@pytest.mark.parametrize("given", sorted(EVERY_CONFIGURATION))
def test_the_count_of_stored_vertex_rows_without_a_key_agrees_with_the_call(given):
    configuration, refused = EVERY_CONFIGURATION[given]
    credentials, location = _as_a_call_gives_it(configuration)
    arguments = {
        "model": VERTEX_MODEL,
        "messages": CHAT,
        "custom_llm_provider": "vertex_ai",
        "vertex_credentials": credentials,
    }
    if location:
        arguments["vertex_location"] = location
    assert vertex_call_has_no_service_account_key("vertex_ai", arguments) == refused, (
        "the call does not refuse exactly the configurations that are not "
        "a complete service-account key"
    )
    for stored in (configuration, json.dumps(configuration)):
        assert stored_vertex_row_gives_no_service_account_key(stored) == refused, (
            "the count does not agree with the call"
        )
