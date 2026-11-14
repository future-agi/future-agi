"""TH-2356: literal TTS speaks user-role script, not control messages.

Lives under model_hub/tests so the backend CI shards collect it:
futureagi/pytest.ini does not recurse into agentic_eval. Does not stub
structlog or other shared modules: a collection-time stub leaked into the
rest of the suite and broke CI (`NoneType` has no attribute `debug`).
Provider I/O and AudioProcessor.format_audio_output (upload, duration and
metadata) are patched per test; the extractor, handlers, adapter and
provider request builders are the real ones.
"""

import base64
import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.audio_preview_handler import (
    AudioPreviewHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.custom_tts_handler import (
    CustomTTSHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
    SpeechAPIHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts_handler import (
    TTSHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    MAX_TTS_TEXT_LENGTH,
    AudioProcessor,
)

PHRASE = "record audio with script"
SCRIPT = "Hello, welcome."
EMPTY_SCRIPT_ERROR = "Enter text to synthesize speech."
CONTROL_TEXTS = [PHRASE, "developer instruction", "assistant aside", "tool payload"]
AUDIO_URL = "https://audio.example/tts.mp3"


def control_and_user(user_content=SCRIPT):
    return [
        {"role": "system", "content": PHRASE},
        {"role": "developer", "content": "developer instruction"},
        {"role": "assistant", "content": "assistant aside"},
        {"role": "tool", "content": "tool payload"},
        {"role": "user", "content": user_content},
    ]


def tts_context(model, messages, provider="openai", run_prompt_config=None):
    return ModelHandlerContext(
        model=model,
        messages=messages,
        organization_id=str(uuid4()),
        output_format="audio",
        api_key="test-key",
        provider=provider,
        run_prompt_config=run_prompt_config or {},
    )


@pytest.fixture
def upload():
    """Stub AudioProcessor.format_audio_output (upload and metadata).

    Records the input_text the handler passes for metadata.
    """
    with patch.object(
        AudioProcessor,
        "format_audio_output",
        return_value=(AUDIO_URL, {"metadata": {"input_characters": 0}}),
    ) as mock_upload:
        yield mock_upload


# ---------------------------------------------------------------------------
# Script contract (R-02, R-03, R-04)
# ---------------------------------------------------------------------------


class TestLiteralSpeechScript:
    def test_excludes_control_roles_and_keeps_user_script(self):
        script = AudioProcessor.extract_literal_speech_script(control_and_user())

        assert script == SCRIPT
        for text in CONTROL_TEXTS:
            assert text not in script

    @pytest.mark.parametrize(
        "user_text",
        [
            PHRASE,
            f"{PHRASE}. Hello, welcome.",
            f"Hello, {PHRASE}, welcome.",
            f"Hello, welcome. {PHRASE}",
            f"{PHRASE} {PHRASE}",
            "Record Audio With Script! RECORD AUDIO WITH SCRIPT?",
            "record audio, with script",
        ],
    )
    def test_intentional_phrase_in_user_script_is_kept_verbatim(self, user_text):
        messages = [
            {"role": "system", "content": "Do not say this."},
            {"role": "user", "content": user_text},
        ]

        assert AudioProcessor.extract_literal_speech_script(messages) == user_text

    @pytest.mark.parametrize(
        "messages",
        [
            control_and_user(""),
            control_and_user("   \n\t "),
            control_and_user([]),
            control_and_user(None),
            control_and_user([{"type": "text", "text": ""}]),
            control_and_user(
                [{"type": "image_url", "image_url": {"url": "https://x/a.png"}}]
            ),
            [{"role": "system", "content": PHRASE}],
            [{"role": "system", "content": ""}, {"role": "user", "content": ""}],
            [{"content": "no role"}, {"role": "narrator", "content": "unknown"}],
        ],
        ids=[
            "empty",
            "whitespace",
            "empty-parts",
            "none",
            "empty-text-part",
            "non-text-only",
            "system-only",
            "all-empty",
            "missing-and-unknown-role",
        ],
    )
    def test_empty_user_script_is_rejected(self, messages):
        with pytest.raises(ValueError, match=EMPTY_SCRIPT_ERROR):
            AudioProcessor.extract_literal_speech_script(messages)

    def test_unknown_missing_role_and_non_dict_messages_are_not_spoken(self):
        messages = [
            {"role": "narrator", "content": "should not be spoken"},
            {"content": "no role"},
            "raw string message",
            {"role": "USER", "content": "case variant is not user"},
            {"role": "user", "content": "spoken"},
        ]

        assert AudioProcessor.extract_literal_speech_script(messages) == "spoken"

    def test_parts_and_messages_keep_order_and_internal_whitespace(self):
        messages = [
            {"role": "system", "content": PHRASE},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Line one,\n  indented."},
                    {"type": "image_url", "image_url": {"url": "https://x/a.png"}},
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "Two  spaces."},
                ],
            },
            {"role": "assistant", "content": "assistant aside"},
            {"role": "user", "content": "Last."},
        ]

        script = AudioProcessor.extract_literal_speech_script(messages)

        assert script == "Line one,\n  indented. Two  spaces. Last."
        assert "image_url" not in script and "https://" not in script

    @pytest.mark.parametrize(
        "user_text",
        [
            "नमस्ते, आपका स्वागत है।",
            "こんにちは、ようこそ。",
            "Café déjà vu, señor Müller.",
            "Hello, नमस्ते, こんにちは, olá.",
        ],
    )
    def test_multilingual_script_is_unchanged(self, user_text):
        messages = [
            {"role": "system", "content": PHRASE},
            {"role": "user", "content": user_text},
        ]

        assert AudioProcessor.extract_literal_speech_script(messages) == user_text

    def test_length_cap_counts_user_script_only(self):
        at_cap = "a" * MAX_TTS_TEXT_LENGTH
        messages = [
            {"role": "system", "content": "s" * 5000},
            {"role": "user", "content": at_cap},
        ]

        assert AudioProcessor.extract_literal_speech_script(messages) == at_cap

    def test_length_cap_still_rejects_long_user_script(self):
        messages = [{"role": "user", "content": "a" * (MAX_TTS_TEXT_LENGTH + 1)}]

        with pytest.raises(ValueError, match="Text too long for TTS"):
            AudioProcessor.extract_literal_speech_script(messages)

    def test_stored_messages_are_not_mutated(self):
        messages = control_and_user(
            [{"type": "text", "text": "Hi."}, {"type": "text", "text": ""}]
        )
        before = copy.deepcopy(messages)

        AudioProcessor.extract_literal_speech_script(messages)

        assert messages == before

    def test_completion_extractor_still_keeps_every_role(self):
        text = AudioProcessor.extract_text_from_messages(control_and_user())

        for expected in CONTROL_TEXTS + [SCRIPT]:
            assert expected in text

    def test_legacy_role_joining_runprompt_path_is_not_selected(self):
        """RunPrompt._speech_response still joins every role, but only the old
        dispatcher reaches it, and the module constant selects the handlers."""
        from agentic_eval.core_evals.run_prompt import litellm_response

        assert litellm_response.USE_NEW_RUNPROMPT_HANDLERS is True


# ---------------------------------------------------------------------------
# Standard speech: litellm.speech / litellm.aspeech (AC-01, AC-07)
# ---------------------------------------------------------------------------


OPENAI_TTS_MODELS = ["gpt-4o-mini-tts", "openai/tts-1", "openai/tts-1-hd"]
OPENAI_CONFIG = {"voice": "alloy", "voice_id": "alloy", "format": "mp3"}


def speech_response():
    return MagicMock(content=b"ID3 fake mp3 bytes")


class TestStandardSpeechBoundary:
    @pytest.mark.parametrize("model", OPENAI_TTS_MODELS)
    def test_sync_speech_input_is_user_script_with_settings_intact(self, model, upload):
        context = tts_context(
            model, control_and_user(), run_prompt_config=OPENAI_CONFIG
        )

        with patch("litellm.speech", return_value=speech_response()) as speech:
            result = TTSHandler(context).execute_sync()

        assert result.failure is None
        assert result.response == AUDIO_URL
        speech.assert_called_once()
        assert speech.call_args.kwargs == {
            "model": model,
            "input": SCRIPT,
            "api_key": "test-key",
            "voice": "alloy",
            "format": "mp3",
            "drop_params": True,
        }
        assert upload.call_args.kwargs["input_text"] == SCRIPT

    @pytest.mark.parametrize("model", OPENAI_TTS_MODELS)
    async def test_async_speech_input_is_user_script_with_settings_intact(
        self, model, upload
    ):
        context = tts_context(
            model, control_and_user(), run_prompt_config=OPENAI_CONFIG
        )

        with patch(
            "litellm.aspeech", new_callable=AsyncMock, return_value=speech_response()
        ) as aspeech:
            result = await TTSHandler(context).execute_async()

        assert result.failure is None
        assert result.response == AUDIO_URL
        aspeech.assert_awaited_once()
        assert aspeech.call_args.kwargs["input"] == SCRIPT
        assert aspeech.call_args.kwargs["voice"] == "alloy"
        assert aspeech.call_args.kwargs["format"] == "mp3"
        assert aspeech.call_args.kwargs["model"] == model

    def test_vertex_voice_dict_and_audio_config_are_unchanged(self, upload):
        voice = {"languageCode": "en-US", "name": "en-US-Studio-O"}
        audio_config = {"audioEncoding": "LINEAR16", "speakingRate": 1.1}
        context = tts_context(
            "vertex_ai/chirp",
            control_and_user(),
            provider="vertex_ai",
            run_prompt_config={"voice": voice, "audioConfig": audio_config},
        )

        with patch("litellm.speech", return_value=speech_response()) as speech:
            SpeechAPIHandler(context).execute_sync()

        kwargs = speech.call_args.kwargs
        assert kwargs["input"] == SCRIPT
        assert kwargs["voice"] == voice
        assert kwargs["audioConfig"] == audio_config

    def test_empty_user_script_fails_before_speech_call_and_upload(self, upload):
        context = tts_context(
            "openai/tts-1", control_and_user("  "), run_prompt_config=OPENAI_CONFIG
        )

        with patch("litellm.speech") as speech:
            result = TTSHandler(context).execute_sync()

        assert result.failure == EMPTY_SCRIPT_ERROR
        assert result.response is None
        speech.assert_not_called()
        upload.assert_not_called()

    async def test_empty_user_script_fails_before_async_speech_call(self, upload):
        context = tts_context(
            "openai/tts-1", control_and_user(""), run_prompt_config=OPENAI_CONFIG
        )

        with patch("litellm.aspeech", new_callable=AsyncMock) as aspeech:
            result = await TTSHandler(context).execute_async()

        assert result.failure == EMPTY_SCRIPT_ERROR
        aspeech.assert_not_awaited()
        upload.assert_not_called()

    def test_provider_error_stays_a_failure(self, upload):
        context = tts_context(
            "openai/tts-1", control_and_user(), run_prompt_config=OPENAI_CONFIG
        )

        with patch("litellm.speech", side_effect=Exception("401 invalid api key")):
            result = TTSHandler(context).execute_sync()

        assert result.failure and "401" in result.failure
        assert result.response is None
        upload.assert_not_called()


# ---------------------------------------------------------------------------
# Completion-generated audio keeps every role (R-07, AC-09)
# ---------------------------------------------------------------------------


ALIAS = "openai/gpt-4o-audio-preview"
GEMINI_TTS = "vertex_ai/gemini-2.5-flash-preview-tts"
SYSTEM_ONLY = [
    {"role": "system", "content": "Greet the caller warmly."},
    {"role": "user", "content": ""},
]


def fallback_response():
    return HandlerResponse(
        response=AUDIO_URL, start_time=0.0, end_time=1.0, model=ALIAS
    )


def completion_with_audio():
    """Provider-shaped completion response carrying base64 audio."""
    message = MagicMock()
    message.audio.data = base64.b64encode(b"ID3 fake completion audio").decode()
    return MagicMock(choices=[MagicMock(message=message)])


class TestAudioPreviewUnchanged:
    def test_alias_with_system_only_prompt_still_reaches_completion_fallback(self):
        """Provider-qualified audio-preview names reach SpeechAPIHandler.

        Before this PR the speech call got every role, 404'd, and the
        completion fallback generated audio from the full prompt. The
        literal user-only rule must not intercept that lane.
        """
        context = tts_context(ALIAS, copy.deepcopy(SYSTEM_ONLY))
        seen = {}

        def fake_fallback(handler, streaming=False):
            seen["messages"] = handler.context.messages
            return fallback_response()

        with (
            patch(
                "litellm.speech", side_effect=Exception("404 page not found")
            ) as speech,
            patch.object(
                AudioPreviewHandler,
                "execute_sync",
                autospec=True,
                side_effect=fake_fallback,
            ) as fallback,
        ):
            result = TTSHandler(context).execute_sync()

        speech.assert_called_once()
        assert speech.call_args.kwargs["input"].strip() == "Greet the caller warmly."
        fallback.assert_called_once()
        assert seen["messages"] == SYSTEM_ONLY
        assert result.response == AUDIO_URL
        assert result.failure is None

    async def test_alias_async_fallback_keeps_full_roles(self):
        context = tts_context(ALIAS, control_and_user())
        seen = {}

        async def fake_fallback(handler, streaming=False):
            seen["messages"] = handler.context.messages
            return fallback_response()

        with (
            patch(
                "litellm.aspeech",
                new_callable=AsyncMock,
                side_effect=Exception("Invalid URL"),
            ) as aspeech,
            patch.object(
                AudioPreviewHandler,
                "execute_async",
                autospec=True,
                side_effect=fake_fallback,
            ),
        ):
            result = await TTSHandler(context).execute_async()

        sent = aspeech.call_args.kwargs["input"]
        for text in CONTROL_TEXTS + [SCRIPT]:
            assert text in sent
        assert seen["messages"] == control_and_user()
        assert result.response == AUDIO_URL

    def test_alias_fallback_generates_audio_through_real_completion_handler(
        self, upload
    ):
        context = tts_context(
            ALIAS,
            copy.deepcopy(SYSTEM_ONLY),
            run_prompt_config={"voice": "nova", "format": "wav"},
        )

        with (
            patch(
                "litellm.speech", side_effect=Exception("404 page not found")
            ) as speech,
            patch(
                "litellm.completion", return_value=completion_with_audio()
            ) as completion,
        ):
            result = TTSHandler(context).execute_sync()

        assert result.failure is None, result.failure
        assert result.response == AUDIO_URL
        speech.assert_called_once()
        completion.assert_called_once()
        kwargs = completion.call_args.kwargs
        assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
        assert kwargs["modalities"] == ["text", "audio"]
        assert kwargs["audio"] == {"voice": "nova", "format": "wav"}
        assert (
            upload.call_args.kwargs["input_text"].strip() == "Greet the caller warmly."
        )

    async def test_alias_async_fallback_generates_audio_through_real_completion_handler(
        self, upload
    ):
        context = tts_context(ALIAS, control_and_user())

        with (
            patch(
                "litellm.aspeech",
                new_callable=AsyncMock,
                side_effect=Exception("Invalid URL"),
            ),
            patch(
                "litellm.acompletion",
                new_callable=AsyncMock,
                return_value=completion_with_audio(),
            ) as acompletion,
        ):
            result = await TTSHandler(context).execute_async()

        assert result.failure is None, result.failure
        acompletion.assert_awaited_once()
        sent = acompletion.call_args.kwargs["messages"]
        assert [m["role"] for m in sent] == [
            "system",
            "developer",
            "assistant",
            "tool",
            "user",
        ]
        for text in CONTROL_TEXTS + [SCRIPT]:
            assert text in upload.call_args.kwargs["input_text"]

    def test_exact_audio_preview_model_sends_role_bearing_messages(self):
        context = tts_context("gpt-4o-audio-preview", control_and_user())

        with patch(
            "litellm.completion", side_effect=Exception("stop after payload")
        ) as completion:
            result = TTSHandler(context).execute_sync()

        completion.assert_called_once()
        sent = completion.call_args.kwargs["messages"]
        roles = [m["role"] for m in sent]
        assert roles == ["system", "developer", "assistant", "tool", "user"]
        assert json.dumps(sent).count(PHRASE) == 1
        assert result.failure

    def test_vertex_gemini_tts_keeps_every_role_through_completion(self, upload):
        context = tts_context(
            GEMINI_TTS, copy.deepcopy(SYSTEM_ONLY), provider="vertex_ai"
        )

        with (
            patch("litellm.speech") as speech,
            patch(
                "litellm.completion", return_value=completion_with_audio()
            ) as completion,
        ):
            result = TTSHandler(context).execute_sync()

        assert result.failure is None, result.failure
        assert result.response == AUDIO_URL
        speech.assert_not_called()
        kwargs = completion.call_args.kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "Greet the caller warmly."},
            {"role": "user", "content": ""},
        ]
        assert kwargs["modalities"] == ["audio"]
        assert (
            upload.call_args.kwargs["input_text"].strip() == "Greet the caller warmly."
        )

    async def test_vertex_gemini_tts_async_keeps_every_role_through_completion(
        self, upload
    ):
        context = tts_context(GEMINI_TTS, control_and_user(), provider="vertex_ai")

        with (
            patch("litellm.aspeech", new_callable=AsyncMock) as aspeech,
            patch(
                "litellm.completion", return_value=completion_with_audio()
            ) as completion,
        ):
            result = await TTSHandler(context).execute_async()

        assert result.failure is None, result.failure
        aspeech.assert_not_called()
        completion.assert_called_once()
        sent = completion.call_args.kwargs["messages"]
        assert [m["role"] for m in sent] == [
            "system",
            "developer",
            "assistant",
            "tool",
            "user",
        ]
        for text in CONTROL_TEXTS + [SCRIPT]:
            assert text in upload.call_args.kwargs["input_text"]


# ---------------------------------------------------------------------------
# Custom speech adapters: every registered provider (AC-07)
# ---------------------------------------------------------------------------


AUDIO_B64 = base64.b64encode(b"\x00\x01" * 32).decode()


class FakeProviderResponse:
    status_code = 200
    ok = True
    text = "ok"
    content = b"ID3 fake provider audio"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "audioContent": AUDIO_B64,
            "generations": [{"audio": AUDIO_B64}],
            "audio": AUDIO_B64,
        }

    def iter_lines(self):
        return [("data: " + json.dumps({"data": {"audio": AUDIO_B64}})).encode()]


ELEVEN_VOICE = "21m00Tcm4TlvDq8ikWAMxx"
CUSTOM_PROVIDERS = {
    "elevenlabs": {
        "model": "elevenlabs/eleven_multilingual_v2",
        "config": {"voice_id": ELEVEN_VOICE, "output_format": "mp3_22050_32"},
        "script": lambda req: req["text"],
        "settings": lambda req: (
            req["voice_id"] == ELEVEN_VOICE
            and req["model_id"] == "eleven_multilingual_v2"
            and req["output_format"] == "mp3_22050_32"
        ),
    },
    "deepgram": {
        "model": "aura-2",
        "config": {"voice": "thalia", "encoding": "mp3"},
        "script": lambda req: req["text"],
        "settings": lambda req: (
            "model=aura-2-thalia-en" in req["_url"] and "encoding=mp3" in req["_url"]
        ),
    },
    "inworld": {
        "model": "inworld-tts-1",
        "config": {"voice": "Ashley", "temperature": 0.7},
        "script": lambda req: req["text"],
        "settings": lambda req: (
            req["voiceId"] == "Ashley"
            and req["modelId"] == "inworld-tts-1"
            and req["temperature"] == 0.7
        ),
    },
    "rime": {
        "model": "arcana",
        "config": {"voice": "luna", "lang": "eng"},
        "script": lambda req: req["text"],
        "settings": lambda req: (
            req["speaker"] == "luna"
            and req["modelId"] == "arcana"
            and req["lang"] == "eng"
        ),
    },
    "neuphonic": {
        "model": "neuphonic",
        "config": {"sampling_rate": 22050, "speed": 1.1},
        "script": lambda req: req["text"],
        "settings": lambda req: req["sampling_rate"] == 22050 and req["speed"] == 1.1,
    },
    "hume": {
        "model": "octave",
        "config": {"voice": "KORA", "format": "wav"},
        "script": lambda req: req["utterances"][0]["text"],
        "settings": lambda req: (
            req["utterances"][0]["voice"]["name"] == "KORA"
            and req["format"] == {"type": "wav"}
        ),
    },
    "cartesia": {
        "model": "sonic-3",
        "config": {"voice": "Katie", "language": "en"},
        "script": lambda req: req["transcript"],
        "settings": lambda req: (
            req["model_id"] == "sonic-3"
            and req["voice"]["mode"] == "id"
            and req["language"] == "en"
        ),
    },
    "lmnt": {
        "model": "blizzard",
        "config": {"voice": "leah", "format": "mp3"},
        "script": lambda req: req["text"],
        "settings": lambda req: req["voice"] == "leah" and req["format"] == "mp3",
    },
}


@pytest.fixture
def provider_io():
    """Capture each provider request; return provider-shaped audio."""
    captured = []

    def fake_post(url, *args, **kwargs):
        body = dict(kwargs.get("json") or {})
        body["_url"] = url
        captured.append(body)
        return FakeProviderResponse()

    def fake_convert(**kwargs):
        captured.append(dict(kwargs, _url="elevenlabs-sdk"))
        return iter([b"ID3 fake ", b"elevenlabs audio"])

    sdk = MagicMock()
    sdk.ElevenLabs.return_value.text_to_speech.convert.side_effect = fake_convert

    with (
        patch("requests.post", side_effect=fake_post),
        patch("tfc.utils.lazy_extras.load_extra", return_value=sdk),
    ):
        yield captured


class TestCustomAdapterBoundary:
    @pytest.mark.parametrize("provider", sorted(CUSTOM_PROVIDERS))
    def test_sync_provider_field_is_user_script_with_settings_intact(
        self, provider, provider_io, upload
    ):
        spec = CUSTOM_PROVIDERS[provider]
        context = tts_context(
            spec["model"],
            control_and_user(),
            provider=provider,
            run_prompt_config=spec["config"],
        )
        handler = TTSHandler(context)

        result = handler.execute_sync()

        assert isinstance(handler._get_sub_handler(), CustomTTSHandler)
        assert result.failure is None, result.failure
        assert len(provider_io) == 1
        request = provider_io[0]
        assert spec["script"](request) == SCRIPT
        assert spec["settings"](request)
        for text in CONTROL_TEXTS:
            assert text not in json.dumps(request, ensure_ascii=False)
        assert upload.call_args.kwargs["input_text"] == SCRIPT

    @pytest.mark.parametrize("provider", sorted(CUSTOM_PROVIDERS))
    async def test_async_provider_field_is_user_script(
        self, provider, provider_io, upload
    ):
        spec = CUSTOM_PROVIDERS[provider]
        context = tts_context(
            spec["model"],
            control_and_user(),
            provider=provider,
            run_prompt_config=spec["config"],
        )

        result = await TTSHandler(context).execute_async()

        assert result.failure is None, result.failure
        assert len(provider_io) == 1
        assert spec["script"](provider_io[0]) == SCRIPT

    @pytest.mark.parametrize("provider", sorted(CUSTOM_PROVIDERS))
    def test_empty_user_script_fails_before_provider_request(
        self, provider, provider_io, upload
    ):
        spec = CUSTOM_PROVIDERS[provider]
        context = tts_context(
            spec["model"],
            control_and_user(" "),
            provider=provider,
            run_prompt_config=spec["config"],
        )

        result = TTSHandler(context).execute_sync()

        assert result.failure and EMPTY_SCRIPT_ERROR in result.failure
        assert result.response is None
        assert provider_io == []
        upload.assert_not_called()

    def test_inworld_keeps_its_2000_character_truncation(self, provider_io, upload):
        spec = CUSTOM_PROVIDERS["inworld"]
        user_text = "b" * 2500
        messages = [
            {"role": "system", "content": "s" * 3000},
            {"role": "user", "content": user_text},
        ]
        context = tts_context(
            spec["model"],
            messages,
            provider="inworld",
            run_prompt_config=spec["config"],
        )

        result = TTSHandler(context).execute_sync()

        assert result.failure is None, result.failure
        assert provider_io[0]["text"] == user_text[:2000]
