"""A Vertex model runs with a saved key, on the organization's credits, or
through the one named platform helper.

Two platform features call a model of the platform's own choosing, on the
platform's account: the chat simulator's first message and the voice
simulator's background choice. Each still produces its result, now through
one named platform helper that takes which feature is calling and the
prompt, nothing else: the model, its provider and its location are the
platform's own, and the helper cannot be given any of them.

Everywhere else a call to a Vertex model with no service-account key of the
customer's own (no key, a key alone, an empty configuration or one that
gives only a location) is refused with the plain sentence before any call:
an evaluation's model call, and every call through the shared guard given an
empty configuration. A Vertex model given a service-account key, a model of
another provider, and an evaluation's model on the organization's Future AGI
credits (sent to the managed route) are still called as before.

The tests use values they set themselves; every model call is replaced by a
recorder, so nothing leaves the process.
"""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.utils.model_config import ModelConfigs
from model_hub.services import model_route
from model_hub.services.model_route import ManagedRouteBinding
from model_hub.utils import model_endpoints
from model_hub.utils.model_endpoints import ModelConfigurationRefused
from simulate.services.chat_initial_message import _generate_chat_initial_message

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
CHAT = [{"role": "user", "content": "hi"}]
VERTEX_MODEL = "vertex_ai/gemini-2.5-flash"
ORGANIZATION_ID = "fi-test-organization"
SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key_id": "fi-test-key-id-not-real",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}
# What a call could be given that would choose where or how a model runs.
CALL_SETTINGS = (
    "api_base",
    "api_key",
    "custom_llm_provider",
    "model",
    "vertex_ai_credentials",
    "vertex_credentials",
    "vertex_location",
    "vertex_project",
)
# What a platform feature's call is never given: a key, a customer's
# configuration, a project or an address.
KEYS_AND_ADDRESSES = (
    "api_base",
    "api_key",
    "vertex_ai_credentials",
    "vertex_credentials",
    "vertex_project",
)
# The voice simulator's background catalogue, as the service reads it.
SOUNDS_PATH = "/app/backend/simulate/data/background_sounds.json"
SOUNDS = Path(__file__).resolve().parents[2] / "simulate" / "data"


def _response(answer):
    message = SimpleNamespace(content=answer)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class _Model:
    """Stands in for every model call: records what it is given and answers
    with ``answer``; sends nothing."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        return _response(self.answer)


def _model_answering(monkeypatch, answer):
    """Every model call replaced by one recorder that answers ``answer``."""
    model = _Model(answer)
    for name in ("completion", "acompletion"):
        monkeypatch.setattr(litellm, name, model)
    return model


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
def platform_settings(monkeypatch):
    """What building the platform's own Vertex model needs, made up here:
    no file is read and no client connects."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/test.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fi-test-platform-project")
    monkeypatch.setattr(llm_module.genai, "Client", lambda *args, **kwargs: object())


def _a_sound():
    """The first sound of the catalogue the voice simulator can choose."""
    catalogue = json.loads((SOUNDS / "background_sounds.json").read_text())
    for sound in catalogue:
        url = str(sound.get("url") or "").strip()
        named = all(sound.get(field) for field in ("id", "title", "description"))
        if named and url.startswith(("http://", "https://")):
            return sound
    raise AssertionError("the catalogue has no sound to choose")


def _choose_a_background(monkeypatch, model):
    """Run the voice simulator's background choice, the catalogue read from
    the repository; (what it chose, the sound the model was told to pick)."""
    selector = pytest.importorskip("ee.voice.services.background_sound_selector")
    catalogue = (SOUNDS / "background_sounds.json").read_text()

    def _open(path, *args, **kwargs):
        if path == SOUNDS_PATH:
            return io.StringIO(catalogue)
        return open(path, *args, **kwargs)

    monkeypatch.setattr(selector, "open", _open, raising=False)
    sound = _a_sound()
    model.answer = f"SELECTED: {sound['id']}"
    return selector.select_background_sound("a caller on a busy street"), sound


def _write_a_first_message(monkeypatch, model):
    """Run the chat simulator's first message; (what it wrote, the answer)."""
    model.answer = "Hi, my order has not arrived yet."
    written = _generate_chat_initial_message(
        persona={"name": "Test Persona", "tone": "friendly"},
        situation="an order arrived late",
        agent_name=None,
    )
    return written, model.answer


# ------------------------------------------- the platform's own features ---


def test_the_chat_simulators_first_message_is_still_written_by_the_platforms_model(
    monkeypatch, no_gateway, platform_settings
):
    model = _model_answering(monkeypatch, "")
    written, answer = _write_a_first_message(monkeypatch, model)
    assert len(model.calls) == 1, "the model call was not made"
    assert written == answer


def test_the_voice_simulators_background_is_still_chosen_by_the_platforms_model(
    monkeypatch, no_gateway, platform_settings
):
    model = _model_answering(monkeypatch, "")
    chosen, sound = _choose_a_background(monkeypatch, model)
    assert len(model.calls) == 1, "the model call was not made"
    assert chosen["id"] == str(sound["id"]).upper()
    assert chosen["value"] == str(sound["url"]).strip()


# feature -> (how it is run, the platform's model for it, its temperature)
PLATFORM_FEATURES = {
    "chat_simulator_first_message": (
        _write_a_first_message,
        ModelConfigs.VERTEX_GEMINI_3_7_FLASH,
        0.7,
    ),
    "voice_background_choice": (
        _choose_a_background,
        ModelConfigs.VERTEX_GEMINI_3_5_FLASH_LITE,
        0.2,
    ),
}


@pytest.mark.parametrize("feature", sorted(PLATFORM_FEATURES))
def test_each_platform_feature_calls_the_platforms_own_model_in_its_own_location(
    monkeypatch, no_gateway, platform_settings, feature
):
    run, platform_model, temperature = PLATFORM_FEATURES[feature]
    model = _model_answering(monkeypatch, "")
    run(monkeypatch, model)
    assert len(model.calls) == 1, "the model call was not made"
    given = model.calls[0]
    assert given.get("vertex_location") == platform_model.vertex_location, (
        "the call is not made in the platform's own location"
    )
    assert given["model"] == platform_model.model_name
    assert given["custom_llm_provider"] == platform_model.provider
    assert given["temperature"] == temperature
    for name in KEYS_AND_ADDRESSES:
        assert name not in given, name


def test_the_platform_helper_cannot_be_given_a_model_or_a_location(
    monkeypatch, no_gateway, platform_settings
):
    from agentic_eval.core.llm.platform_model import (
        PlatformFeature,
        call_platform_model,
    )

    model = _model_answering(monkeypatch, "never")
    for chosen in ("vertex_ai/gemini-chosen-by-a-caller", "voice_background_choice"):
        with pytest.raises(ModelConfigurationRefused):
            call_platform_model(chosen, CHAT)
    for name in CALL_SETTINGS:
        with pytest.raises(TypeError):
            call_platform_model(
                PlatformFeature.VOICE_BACKGROUND_CHOICE,
                CHAT,
                **{name: "chosen-by-a-caller"},
            )
    assert model.calls == [], "the model call was made"


# ------------------------------------------------- every other caller ---


def _evaluation_llm(provider, model_name, api_key):
    """An evaluation's model for ``provider``: built as one whose provider
    needs no client of its own (a Vertex one needs platform settings), then
    given ``provider``. The model call takes its provider from the call."""
    llm = llm_module.LLM(provider="bedrock", model_name=model_name, api_key=api_key)
    llm.provider = provider
    return llm


def _outcome(run):
    """The text of what was raised; None when nothing was."""
    try:
        run()
    except Exception as exc:  # the refusal, or what the call raised
        return str(exc)
    return None


# (the provider the call names, what the evaluation's model is given)
EVALUATION_WITHOUT_A_KEY_OF_ITS_OWN = {
    "no_key": ("vertex_ai", None),
    "a_key_alone": ("vertex_ai", "fi-test-vertex-key-not-real"),
    "an_empty_configuration": ("vertex_ai", {}),
    "only_a_location": ("vertex_ai", {"location": "us-central1"}),
    "a_vertex_model_named_under_another_provider": (
        "openai",
        "fi-test-vertex-key-not-real",
    ),
}


@pytest.mark.parametrize("given", sorted(EVALUATION_WITHOUT_A_KEY_OF_ITS_OWN))
def test_an_evaluation_call_to_a_vertex_model_without_a_key_of_its_own_is_refused(
    monkeypatch, no_gateway, given
):
    provider, api_key = EVALUATION_WITHOUT_A_KEY_OF_ITS_OWN[given]
    model = _model_answering(monkeypatch, "never")
    llm = _evaluation_llm(provider, VERTEX_MODEL, api_key)
    outcome = _outcome(lambda: llm.call_llm(CHAT, provider=provider))
    assert model.calls == [], "the model call was made"
    assert outcome is not None and REFUSAL in outcome, (
        "the call was not refused with the plain sentence"
    )


def test_an_evaluation_call_to_a_vertex_model_with_a_service_account_key_is_still_made(
    monkeypatch, no_gateway
):
    model = _model_answering(monkeypatch, "an answer")
    llm = _evaluation_llm("vertex_ai", VERTEX_MODEL, SERVICE_ACCOUNT_KEY)
    assert llm.call_llm(CHAT, provider="vertex_ai") == "an answer"
    assert len(model.calls) == 1, "the model call was not made"
    assert json.loads(model.calls[0]["vertex_credentials"]) == SERVICE_ACCOUNT_KEY


def test_an_evaluation_call_to_another_providers_model_given_a_key_alone_is_still_made(
    monkeypatch, no_gateway
):
    model = _model_answering(monkeypatch, "an answer")
    llm = _evaluation_llm("openai", "gpt-4o-mini", "sk-fi-test-not-real")
    assert llm.call_llm(CHAT, provider="openai") == "an answer"
    assert len(model.calls) == 1, "the model call was not made"
    assert model.calls[0]["api_key"] == "sk-fi-test-not-real"


def test_an_evaluation_call_to_a_vertex_model_on_credits_is_still_sent_to_the_managed_route(
    monkeypatch, no_gateway
):
    """An evaluation's Vertex model with no stored key that runs on the
    organization's Future AGI credits: one call through the managed route,
    never the model library, and not refused."""
    model = _model_answering(monkeypatch, "never")
    sent = []

    def managed_chat_completion(binding, request, *, deadline_monotonic=None):
        sent.append((binding, request))
        return _response("an answer")

    monkeypatch.setattr(model_route, "managed_chat_completion", managed_chat_completion)
    binding = ManagedRouteBinding(
        organization_id=ORGANIZATION_ID,
        workspace_id=None,
        model_name=VERTEX_MODEL,
        provider="vertex_ai",
        operation="evaluation",
        port=None,
    )
    llm = llm_module.LLM(
        provider="vertex_ai", model_name=VERTEX_MODEL, model_route=binding
    )
    assert llm.call_llm(CHAT, provider="vertex_ai") == "an answer"
    assert model.calls == [], "the model library was called"
    assert len(sent) == 1, "the managed route was not called"
    assert sent[0][0] is binding


@pytest.mark.parametrize("given_as", ["an-object", "json-text"])
@pytest.mark.parametrize(
    "name",
    ["vertex_credentials", "vertex_ai_credentials"],
    ids=["its-name", "its-other-name"],
)
def test_a_call_given_an_empty_vertex_configuration_is_refused_before_it(
    monkeypatch, name, given_as
):
    model = _model_answering(monkeypatch, "never")
    outcome = _outcome(
        lambda: model_endpoints.guarded_completion(
            model=VERTEX_MODEL,
            custom_llm_provider="vertex_ai",
            messages=CHAT,
            **{name: {} if given_as == "an-object" else "{}"},
        )
    )
    assert model.calls == [], "the model call was made"
    assert outcome == REFUSAL, "the call was not refused with the plain sentence"
