"""Saved audio configuration at real handlers and the public RunPrompt switch.

No provider, storage, metadata or DNS network calls. The provider recorder is
at LiteLLM's audio entry point: these tests prove mapping, final guard admission
and scope lifetime, not the pinned SDK's HTTP implementation. The public cases
keep the factory and target handlers real; only model lookup/mode are fixtures.
"""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import litellm
import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
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
from model_hub.utils.model_key import ADD_API_KEY_MESSAGE
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver

BASE = "https://audio.example.test/v1"
OWN_KEY = "synthetic-audio-tenant-key"
AUDIO = b"synthetic audio bytes"
MODES = ("stt", "tts")


@pytest.fixture(autouse=True)
def audio_boundary(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("audio.example.test", "93.184.216.34")
    resolver.add("audio.openai.azure.com", "93.184.216.34")
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-platform-key-must-not-be-used")
    # Recorder tests do not invoke the SDK. Keep the real scope/context var,
    # but do not install process-wide SDK hooks or open certificate files.
    monkeypatch.setattr(model_endpoints, "_install_litellm_client_hooks", lambda: None)
    clients = []

    def no_network(request):
        raise AssertionError("Unexpected HTTP dispatch in audio boundary test")

    def client(policy, *, is_async):
        cls = httpx.AsyncClient if is_async else httpx.Client
        result = cls(transport=httpx.MockTransport(no_network), trust_env=False)
        clients.append(result)
        return result

    monkeypatch.setattr(model_endpoints, "_guarded_http_client", client)
    monkeypatch.setattr(
        AudioProcessor, "normalize_audio_for_stt", lambda value: (AUDIO, "wav")
    )
    monkeypatch.setattr(
        AudioProcessor,
        "format_transcription_output",
        lambda **kwargs: (kwargs["transcription_text"], {"metadata": {"cost": 0}}),
    )
    monkeypatch.setattr(
        AudioProcessor,
        "format_audio_output",
        lambda **kwargs: ("https://output.example.test/audio.wav", {"metadata": {"cost": 0}}),
    )
    seen = []

    def record(**kwargs):
        seen.append((kwargs, model_endpoints._scope_policy()))
        if "file" in kwargs:
            return SimpleNamespace(text="transcribed words")
        return SimpleNamespace(content=AUDIO)

    async def arecord(**kwargs):
        return record(**kwargs)

    monkeypatch.setattr(litellm, "transcription", record)
    monkeypatch.setattr(litellm, "speech", record)
    monkeypatch.setattr(litellm, "aspeech", arecord)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield seen
    for item in clients:
        if isinstance(item, httpx.AsyncClient):
            asyncio.run(item.aclose())
        else:
            item.close()


def context(mode, configuration, *, provider="openai", model=None):
    messages = [{"role": "user", "content": "Say these words"}]
    if mode == "stt":
        messages = [{"role": "user", "content": [{
            "type": "input_audio", "input_audio": {"data": "YXVkaW8=", "format": "wav"}
        }]}]
    return ModelHandlerContext(
        model=model or ("openai/whisper-1" if mode == "stt" else "openai/tts-1"),
        messages=messages,
        organization_id="synthetic-audio-org",
        workspace_id="synthetic-audio-workspace",
        output_format="text" if mode == "stt" else "audio",
        provider=provider,
        api_key=configuration,
        run_prompt_config={"voice": "alloy", "format": "wav"},
    )


def handler(mode, configuration, **kwargs):
    cls = StandardSTTHandler if mode == "stt" else SpeechAPIHandler
    return cls(context(mode, configuration, **kwargs))


def execute(target, asynchronous):
    if asynchronous:
        return asyncio.run(target.execute_async())
    return target.execute_sync()


def refusal(target, asynchronous):
    try:
        result = execute(target, asynchronous)
    except Exception as exc:
        return str(exc)
    return result.failure


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("key_field", ["key", "api_key"])
def test_saved_audio_configuration_reaches_provider(audio_boundary, mode, asynchronous, key_field):
    # api_key is the canonical shape produced by public RunPrompt after its
    # stored {key, api_base} lookup; it is not an OpenAI stored-row alias.
    configuration = {key_field: OWN_KEY, "api_base": BASE}
    original = copy.deepcopy(configuration)
    result = execute(handler(mode, configuration), asynchronous)
    assert result.failure is None
    [entry] = audio_boundary
    kwargs, policy = entry
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["api_base"] == BASE
    assert kwargs["client"].api_key == OWN_KEY
    assert str(kwargs["client"].base_url).rstrip("/") == BASE
    assert policy is not None
    assert model_endpoints._scope_policy() is None
    assert kwargs["drop_params"] is True
    assert configuration == original
    if mode == "stt":
        assert kwargs["file"].getvalue() == AUDIO
        assert kwargs["file"].name.endswith(".wav")
        assert result.response == "transcribed words"
    else:
        assert kwargs["input"] == "Say these words"
        assert kwargs["voice"] == "alloy"
        assert kwargs["format"] == "wav"
        assert result.response == "https://output.example.test/audio.wav"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("base", [
    "http://127.0.0.1/v1", "http://10.0.0.8/v1",
    "http://169.254.169.254/v1", "http://[::1]/v1",
])
def test_saved_audio_private_endpoint_is_refused(audio_boundary, mode, asynchronous, base):
    failure = refusal(handler(mode, {"key": OWN_KEY, "api_base": base}), asynchronous)
    assert audio_boundary == []
    assert failure == ssrf_guard.ENDPOINT_ERROR_MESSAGES["blocked"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("selected", ["", "  ", None, {"nested": "not-a-key"}])
def test_saved_audio_selected_empty_key_is_not_rescued(audio_boundary, mode, asynchronous, selected):
    configuration = {"key": selected, "api_key": OWN_KEY, "api_base": BASE}
    failure = refusal(handler(mode, configuration), asynchronous)
    assert audio_boundary == []
    assert failure == ADD_API_KEY_MESSAGE


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("aliases", [False, True], ids=["canonical", "camel"])
def test_saved_audio_azure_configuration_reaches_provider(audio_boundary, mode, asynchronous, aliases):
    base = "https://audio.openai.azure.com"
    configuration = ({"apiKey": OWN_KEY, "apiBase": base, "apiVersion": "2024-06-01"}
                     if aliases else {"api_key": OWN_KEY, "api_base": base, "api_version": "2024-06-01"})
    model = "azure/whisper" if mode == "stt" else "azure/tts-1"
    result = execute(handler(mode, configuration, provider="azure", model=model), asynchronous)
    assert result.failure is None
    [(kwargs, policy)] = audio_boundary
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["api_base"] == base
    assert kwargs["api_version"] == "2024-06-01"
    assert policy is not None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("canonical", [None, "", "  "], ids=["null", "empty", "whitespace"])
def test_audio_azure_aliases_follow_existing_normalizer(audio_boundary, mode, asynchronous, canonical):
    configuration = {"api_key": canonical, "apiKey": OWN_KEY,
                     "api_base": "https://audio.openai.azure.com", "api_version": "2024-06-01"}
    target = handler(mode, configuration, provider="azure", model="azure/audio-test")
    if canonical == "  ":
        assert refusal(target, asynchronous) == ADD_API_KEY_MESSAGE
        assert audio_boundary == []
    else:
        result = execute(target, asynchronous)
        assert result.failure is None
        [(kwargs, policy)] = audio_boundary
        assert kwargs["api_key"] == OWN_KEY
        assert kwargs["api_version"] == "2024-06-01"
        assert policy is not None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_audio_mapping_keeps_google_kind_refusal_before_key_selection(audio_boundary, mode, asynchronous):
    configuration = {"key": "", "api_key": OWN_KEY, "api_base": BASE,
                     "type": "authorized_user", "refresh_token": "synthetic-not-a-token"}
    failure = refusal(handler(mode, configuration), asynchronous)
    assert audio_boundary == []
    assert failure == str(model_endpoints.ModelConfigurationRefused())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_audio_call_base_url_alias_is_guarded(audio_boundary, mode, asynchronous):
    # Call-time compatibility only: the stored OpenAI whitelist does not keep
    # base_url. Never imply that this is a supported stored-row alias.
    result = execute(handler(mode, {"api_key": OWN_KEY, "base_url": BASE}), asynchronous)
    assert result.failure is None
    [(kwargs, policy)] = audio_boundary
    assert kwargs["api_base"] == BASE
    assert kwargs["api_key"] == OWN_KEY
    assert "base_url" not in kwargs
    assert policy is not None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("key", [None, OWN_KEY])
def test_audio_without_tenant_url_keeps_platform_route(audio_boundary, mode, asynchronous, key):
    result = execute(handler(mode, key), asynchronous)
    assert result.failure is None
    [(kwargs, policy)] = audio_boundary
    assert kwargs["api_key"] == key
    assert "api_base" not in kwargs
    assert policy is None


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_speech_vertex_service_account_is_not_plain_key(audio_boundary, asynchronous):
    account = {"type": "service_account", "private_key": "synthetic-not-a-real-private-key",
               "client_email": "synthetic@example.test", "project_id": "synthetic-project",
               "location": "us-central1"}
    target = handler("tts", account, provider="vertex_ai", model="vertex_ai/chirp")
    target.context.run_prompt_config = {"voice": {"languageCode": "en-US"}, "audioConfig": {"audioEncoding": "LINEAR16"}}
    result = execute(target, asynchronous)
    assert result.failure is None
    [(kwargs, policy)] = audio_boundary
    assert "api_key" not in kwargs
    assert kwargs["vertex_location"] == "us-central1"
    assert json.loads(kwargs["vertex_credentials"])["client_email"] == account["client_email"]
    assert "location" not in json.loads(kwargs["vertex_credentials"])
    assert kwargs["voice"] == {"languageCode": "en-US"}
    assert kwargs["audioConfig"] == {"audioEncoding": "LINEAR16"}
    assert policy is None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_vertex_audio_without_service_account_is_refused(audio_boundary, mode, asynchronous):
    failure = refusal(handler(mode, None, provider="vertex_ai", model="vertex_ai/chirp"), asynchronous)
    assert audio_boundary == []
    assert failure == str(model_endpoints.ModelConfigurationRefused())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_audio_unsupported_tenant_provider_is_refused(audio_boundary, mode, asynchronous):
    target = handler(mode, {"key": OWN_KEY, "api_base": BASE}, provider="anthropic", model="anthropic/audio-test")
    failure = refusal(target, asynchronous)
    assert audio_boundary == []
    assert failure == ssrf_guard.ENDPOINT_ERROR_MESSAGES["unsupported"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_audio_managed_binding_never_reaches_provider(audio_boundary, monkeypatch, mode, asynchronous):
    from model_hub.services import model_route

    binding = object()
    monkeypatch.setattr(model_route, "managed_binding_in", lambda kwargs: binding if kwargs.get("api_key") is binding else None)
    failure = refusal(handler(mode, binding), asynchronous)
    assert audio_boundary == []
    assert failure == str(model_route.ManagedRouteUnsupported())


def test_speech_policy_refusal_cannot_trigger_preview_fallback():
    target = handler("tts", {"key": OWN_KEY, "api_base": BASE}, model="openai/gpt-4o-audio-preview")
    error = ssrf_guard.EndpointBlocked("invalid_url")
    error.args = ("404 invalid url",)
    assert target._should_fallback_to_completion(error) is False
    assert target._should_fallback_to_completion(RuntimeError("404")) is True


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("invalid_selected", [False, True], ids=["own-key", "empty-selected"])
def test_public_runprompt_uses_saved_audio_configuration(audio_boundary, monkeypatch, mode, asynchronous, provider, invalid_selected):
    from agentic_eval.core_evals.run_prompt import litellm_response as public
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.factory import ModelHandlerFactory
    from model_hub.utils import utils as model_utils

    expected_base = "https://audio.openai.azure.com" if provider == "azure" else BASE
    if provider == "azure":
        cfg = {"key": OWN_KEY, "apiBase": expected_base, "apiVersion": "2024-06-01"}
    else:
        cfg = {"key": OWN_KEY, "api_base": expected_base}
    if invalid_selected:
        cfg["key"] = ""
        cfg["apiKey" if provider == "azure" else "api_key"] = OWN_KEY
    model = ("azure/whisper" if mode == "stt" else "azure/tts-1") if provider == "azure" else None
    ctx = context(mode, cfg, provider=provider, model=model)
    prompt = public.RunPrompt.__new__(public.RunPrompt)
    prompt.__dict__.update(vars(ctx))
    manager = Mock()
    manager.get_provider.return_value = provider
    manager.get_api_key.side_effect = lambda **kwargs: dict(cfg)
    monkeypatch.setattr(public, "LiteLLMModelManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(public, "USE_NEW_RUNPROMPT_HANDLERS", True)
    monkeypatch.setattr(model_utils, "get_model_mode", lambda model: "stt" if mode == "stt" else "audio_speech")
    monkeypatch.setattr(ModelHandlerFactory, "_is_custom_model", staticmethod(lambda *args, **kwargs: False))
    # Pin the actual factory decision without replacing either target.
    routed = ModelHandlerFactory.create_handler(ctx)
    if mode == "stt":
        assert isinstance(routed._handler, StandardSTTHandler)
    else:
        assert isinstance(routed._get_sub_handler(), SpeechAPIHandler)
    def invoke():
        return asyncio.run(prompt.litellm_response_async()) if asynchronous else prompt.litellm_response()

    if invalid_selected:
        with pytest.raises(Exception):
            invoke()
        assert manager.get_api_key.called
        assert audio_boundary == []
        return
    response, info = invoke()
    assert info["failure"] is None
    assert response == ("transcribed words" if mode == "stt" else "https://output.example.test/audio.wav")
    [(kwargs, policy)] = audio_boundary
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["api_base"] == expected_base
    if provider == "azure":
        assert kwargs["api_version"] == "2024-06-01"
    assert policy is not None
