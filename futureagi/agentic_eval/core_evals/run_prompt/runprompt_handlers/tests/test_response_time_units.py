"""metadata.response_time is milliseconds on every Run Prompt handler path,
and new metadata says so with response_time_unit "ms".

The Prompts playground always streams, and a streaming LLM run took its
metadata from ``ResponseFormatter.build_streaming_metadata``, which reported
seconds (on cut 3 the custom model's Playground path matched it). Every other
producer (``format_llm_response``, the custom model outside the Playground,
image and audio handlers, ``HandlerResponse.runtime``) reports milliseconds,
and the dataset and experiment readers label the value "ms". So the Prompts
time chip showed "587.98s" for a 0.59 s custom-model call on staging, and
still does for image, text-to-speech and speech-to-text runs on cut 3.

Prompts runs saved before this change stored seconds for streaming LLM runs
with no unit. The time chip converts only values marked "ms", so those saved
runs still read right. These tests pin the unit and the marker; the custom
model's own paths are pinned in test_custom_model_handler_output.py.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_eval.core_evals.run_prompt.runprompt_handlers import base_handler
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils import (
    audio_processor as ap,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils import (
    response_formatter as rf,
)

ZERO_COST = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
ELAPSED_SECONDS = 0.5


def _final_chunk(with_usage):
    chunk = MagicMock()
    chunk.model = "gpt-4o-mini"
    if with_usage:
        chunk.usage = MagicMock(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    else:
        chunk.usage = None
    return chunk


def _completion_response():
    response = MagicMock()
    response.model = "gpt-4o-mini"
    message = response.choices[0].message
    message.content = "hi"
    message.tool_calls = None
    message.reasoning_content = None
    response.usage.prompt_tokens = 3
    response.usage.completion_tokens = 4
    response.usage.total_tokens = 7
    return response


def _handler_self():
    # _ws_send_completed only reads self.context; BaseModelHandler is abstract.
    return SimpleNamespace(
        context=SimpleNamespace(
            template_id="tpl-1",
            version="v1",
            result_index=0,
            num_results=1,
            output_format=None,
        )
    )


# --- producers --------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("with_usage", [True, False])
def test_streaming_metadata_response_time_is_milliseconds(with_usage):
    # A call that took about 0.5 s must report about 500 (ms), not 0.5 (s),
    # and say the unit.
    start_time = time.time() - ELAPSED_SECONDS
    with patch.object(rf, "calculate_total_cost", return_value=dict(ZERO_COST)):
        meta = rf.ResponseFormatter.build_streaming_metadata(
            _final_chunk(with_usage), start_time
        )

    assert 450 <= meta["response_time"] <= 5000, meta["response_time"]
    assert meta.get("response_time_unit") == "ms", meta


@pytest.mark.unit
def test_streaming_and_regular_metadata_share_one_unit():
    # The same 0.5 s call reads the same whether the run streamed or not.
    start_time = time.time() - ELAPSED_SECONDS
    with patch.object(rf, "calculate_total_cost", return_value=dict(ZERO_COST)):
        _, value_info = rf.ResponseFormatter.format_llm_response(
            response=_completion_response(),
            model="gpt-4o-mini",
            start_time=start_time,
        )
        stream = rf.ResponseFormatter.build_streaming_metadata(
            _final_chunk(True), start_time
        )

    regular = value_info["metadata"]
    assert regular["response_time"] >= 450, regular
    assert stream["response_time"] >= 450, stream
    assert abs(regular["response_time"] - stream["response_time"]) < 1000
    assert regular.get("response_time_unit") == "ms", regular
    assert stream.get("response_time_unit") == "ms", stream


@pytest.mark.unit
def test_text_to_speech_metadata_says_ms():
    start_time = time.time() - ELAPSED_SECONDS
    upload = patch.object(ap, "upload_audio_to_s3", return_value="https://x.test/a")
    duration = patch.object(
        ap.AudioProcessor, "estimate_audio_duration", return_value=None
    )
    cost = patch.object(ap, "calculate_total_cost", return_value=dict(ZERO_COST))
    with upload, duration, cost:
        _, value_info = ap.AudioProcessor.format_audio_output(
            audio_bytes=b"audio",
            model="tts-1",
            start_time=start_time,
            input_text="hi",
        )

    meta = value_info["metadata"]
    assert meta["response_time"] >= 450, meta
    assert meta.get("response_time_unit") == "ms", meta


@pytest.mark.unit
def test_speech_to_text_metadata_says_ms():
    start_time = time.time() - ELAPSED_SECONDS
    duration = patch.object(ap, "get_audio_duration", return_value=1.0)
    cost = patch.object(ap, "calculate_total_cost", return_value=dict(ZERO_COST))
    with duration, cost:
        _, value_info = ap.AudioProcessor.format_transcription_output(
            transcription_text="hi",
            model="whisper-1",
            start_time=start_time,
            audio_bytes=b"audio",
        )

    meta = value_info["metadata"]
    assert meta["response_time"] >= 450, meta
    assert meta.get("response_time_unit") == "ms", meta


# --- choke points every handler goes through ---------------------------------


@pytest.mark.unit
def test_value_info_marks_response_time_as_ms():
    # What Prompts saves (PromptExecution.metadata) and dataset cells store.
    response = base_handler.HandlerResponse(
        response="ok",
        start_time=time.time() - ELAPSED_SECONDS,
        end_time=time.time(),
        model="custom-model",
        metadata={"usage": {"total_tokens": 7}, "response_time": 587.98},
    )

    _, value_info = response.to_value_info()

    assert value_info["metadata"]["response_time"] == 587.98
    assert value_info["metadata"].get("response_time_unit") == "ms"
    assert value_info["metadata"]["usage"] == {"total_tokens": 7}


@pytest.mark.unit
@pytest.mark.parametrize("metadata", [{}, {"usage": {"total_tokens": 7}}])
def test_value_info_without_response_time_gets_no_unit(metadata):
    # A stopped run sends metadata={}; nothing is added to it.
    response = base_handler.HandlerResponse(
        response="",
        start_time=time.time(),
        end_time=time.time(),
        model="gpt-4o-mini",
        metadata=dict(metadata),
    )

    _, value_info = response.to_value_info()

    assert value_info["metadata"] == metadata


@pytest.mark.unit
def test_ws_completed_marks_response_time_as_ms():
    # What the open Prompts page reads when a run finishes.
    ws = MagicMock()
    base_handler.BaseModelHandler._ws_send_completed(
        _handler_self(), ws, {"usage": {}, "response_time": 587.98}
    )

    sent = ws.send_completed_message.call_args.kwargs["metadata"]
    assert sent["response_time"] == 587.98
    assert sent.get("response_time_unit") == "ms", sent


@pytest.mark.unit
def test_ws_completed_async_marks_response_time_as_ms():
    ws = MagicMock()
    ws.send_completed_message = AsyncMock()
    asyncio.run(
        base_handler.BaseModelHandler._ws_send_completed_async(
            _handler_self(), ws, {"usage": {}, "response_time": 587.98}
        )
    )

    sent = ws.send_completed_message.await_args.kwargs["metadata"]
    assert sent["response_time"] == 587.98
    assert sent.get("response_time_unit") == "ms", sent


@pytest.mark.unit
def test_ws_completed_keeps_empty_metadata_empty():
    # The WebSocket manager drops empty metadata; it must stay empty.
    ws = MagicMock()
    base_handler.BaseModelHandler._ws_send_completed(_handler_self(), ws, {})

    assert ws.send_completed_message.call_args.kwargs["metadata"] == {}
