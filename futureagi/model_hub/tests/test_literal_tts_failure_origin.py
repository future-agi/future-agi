"""Identical provider and storage messages must not classify alike."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.custom_tts_handler import (
    CustomTTSHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
    SpeechAPIHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils import audio_processor


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_literal_speech_distinguishes_upload_from_provider(asynchronous):
    handler = SpeechAPIHandler(
        ModelHandlerContext(
            model="openai/tts-1",
            messages=[{"role": "user", "content": "Hello"}],
            organization_id="test-org",
            output_format="audio",
            provider="openai",
        )
    )
    message = "storage unavailable"
    with (
        (
            patch("litellm.aspeech", new_callable=AsyncMock)
            if asynchronous
            else patch("litellm.speech")
        ) as speech,
        patch.object(audio_processor, "upload_audio_to_s3") as upload,
    ):
        speech.side_effect = ValueError(message)
        provider = (
            await handler.execute_async() if asynchronous else handler.execute_sync()
        )
        upload.assert_not_called()

        speech.side_effect = None
        speech.return_value = SimpleNamespace(content=b"test audio")
        upload.side_effect = ValueError(message)
        storage = (
            await handler.execute_async() if asynchronous else handler.execute_sync()
        )
        upload.assert_called_once()

    assert storage.failure == provider.failure == message
    assert storage.literal_tts_failure
    assert not provider.literal_tts_failure


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_custom_speech_preserves_the_local_script_failure(asynchronous):
    handler = CustomTTSHandler(
        ModelHandlerContext(
            model="custom-voice",
            messages=[{"role": "user", "content": " "}],
            organization_id="test-org",
            provider="custom",
            output_format="audio",
        )
    )

    def speak(adapter, start_time, api_key):
        adapter._get_input_text_from_messages()
        pytest.fail("empty script reached the provider")

    with patch.object(handler, "_get_speech_handler", return_value=speak):
        result = (
            await handler.execute_async() if asynchronous else handler.execute_sync()
        )

    assert result.failure == "Enter text to synthesize speech."
    assert result.literal_tts_failure
