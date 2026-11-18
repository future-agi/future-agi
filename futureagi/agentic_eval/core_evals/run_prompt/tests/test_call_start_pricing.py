"""A call is priced at the instant its request starts, as the gateway prices it.

Gemini 3.7 Flash bills 0.75 in / 3.75 out per 1M tokens through 31 Dec 2026 and
1.5 / 7.5 from 1 Jan 2027 (UTC). A call that starts before the change and is
handled after it is billed at the 2026 rate, whichever way the response is read:
whole, streamed, formatted by a handler, or logged afterwards.

The clock is faked, not the pricing: "now" (the handling time) is in 2027, and the
call's own request starts in the last second of 2026.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from litellm.types.utils import Usage

from agentic_eval.core_evals.run_prompt import litellm_response, model_pricing
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter import (
    ResponseFormatter,
)

MODEL = "gemini-3.7-flash"
STARTED = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
HANDLED = datetime(2027, 1, 1, 0, 0, 5, tzinfo=UTC)
COST_2026 = 4.5  # 1M in at 0.75 and 1M out at 3.75
COST_2027 = 9.0
USAGE = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}


def _usage():
    return Usage(**USAGE, total_tokens=2_000_000)


class Clock:
    """``time.time()`` as the request sees it: before the call, then after it."""

    def __init__(self):
        self.now = STARTED

    def time(self):
        return self.now.timestamp()

    def reach_the_response(self):
        self.now = HANDLED


@pytest.fixture
def clock(monkeypatch):
    """The module's clock reads 2026 until the model answers; pricing "now" is 2027."""
    clock = Clock()
    fake_time = SimpleNamespace(time=clock.time, sleep=lambda seconds: None)
    monkeypatch.setattr(litellm_response, "time", fake_time)
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)
    return clock


def _run_prompt():
    run_prompt = RunPrompt.__new__(RunPrompt)
    run_prompt.model = MODEL
    run_prompt.tools = None
    run_prompt.show_reasoning_process = False
    run_prompt.output_format = None
    run_prompt.ws_manager = MagicMock()
    run_prompt.ws_manager.is_streaming_stopped.return_value = False
    return run_prompt


def _message_response():
    return SimpleNamespace(
        model=MODEL,
        usage=_usage(),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi", tool_calls=None),
                finish_reason="stop",
            )
        ],
    )


def _chunk():
    delta = SimpleNamespace(content="hi", reasoning_content=None, tool_calls=None)
    return SimpleNamespace(
        model=MODEL, usage=_usage(), choices=[SimpleNamespace(delta=delta)]
    )


def test_a_whole_response_is_priced_at_the_instant_the_call_started(clock, monkeypatch):
    def completion(**kwargs):
        clock.reach_the_response()
        return _message_response()

    monkeypatch.setattr(litellm_response.litellm, "completion", completion)

    _, value_info = _run_prompt()._regular_response({"model": MODEL}, clock.time())

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2026)


def test_a_streamed_response_is_priced_at_the_instant_the_call_started(
    clock, monkeypatch
):
    def completion(**kwargs):
        clock.reach_the_response()
        return iter([_chunk()])

    monkeypatch.setattr(litellm_response.litellm, "completion", completion)

    _, value_info = _run_prompt()._streaming_response(
        {"model": MODEL}, "template", 1, 0, 1, None
    )

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2026)


def test_an_async_streamed_response_is_priced_at_the_instant_the_call_started(
    clock, monkeypatch
):
    async def chunks():
        yield _chunk()

    async def acompletion(**kwargs):
        clock.reach_the_response()
        return chunks()

    monkeypatch.setattr(litellm_response.litellm, "acompletion", acompletion)
    run_prompt = _run_prompt()
    run_prompt.ws_manager = AsyncMock()
    run_prompt.ws_manager.is_streaming_stopped.return_value = False

    _, value_info = asyncio.run(
        run_prompt._streaming_response_async(
            {"model": MODEL}, "template", 1, 0, 1, None
        )
    )

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2026)


def test_a_call_that_starts_after_the_change_is_priced_at_the_new_rate(
    clock, monkeypatch
):
    clock.now = datetime(2027, 1, 1, 0, 0, 1, tzinfo=UTC)

    def completion(**kwargs):
        clock.reach_the_response()
        return _message_response()

    monkeypatch.setattr(litellm_response.litellm, "completion", completion)

    _, value_info = _run_prompt()._regular_response({"model": MODEL}, clock.time())

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2027)


def test_a_transcription_is_priced_at_the_start_the_caller_took(monkeypatch):
    # gemini-3.7-flash has no per-second price, so check the instant it is given.
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils import (
        audio_processor,
    )

    seen = {}

    def calculate(model_name, token_usage, **kwargs):
        seen["at"] = kwargs.get("at")
        return {"total_cost": 0.0}

    monkeypatch.setattr(audio_processor, "calculate_total_cost", calculate)
    monkeypatch.setattr(audio_processor, "get_audio_duration", lambda audio: 3.0)

    AudioProcessor.format_transcription_output(
        "text", "whisper-1", STARTED.timestamp(), b"audio"
    )

    assert seen["at"] == STARTED


# --- The handlers' formatter


def test_a_formatted_response_is_priced_at_the_start_the_handler_took(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)

    _, value_info = ResponseFormatter.format_llm_response(
        response=_message_response(),
        model=MODEL,
        start_time=STARTED.timestamp(),
    )

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2026)


def test_a_formatted_response_is_priced_at_the_instant_the_call_started(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)

    # The row started earlier; the request itself started in 2027.
    _, value_info = ResponseFormatter.format_llm_response(
        response=_message_response(),
        model=MODEL,
        start_time=STARTED.timestamp(),
        started_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    assert value_info["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2027)


def test_streaming_metadata_is_priced_at_the_start_the_handler_took(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)

    metadata = ResponseFormatter.build_streaming_metadata(_chunk(), STARTED.timestamp())

    assert metadata["cost"]["total_cost"] == pytest.approx(COST_2026)


def test_streaming_metadata_is_priced_at_the_instant_the_call_started(monkeypatch):
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)

    metadata = ResponseFormatter.build_streaming_metadata(
        _chunk(), STARTED.timestamp(), started_at=datetime(2027, 1, 1, tzinfo=UTC)
    )

    assert metadata["cost"]["total_cost"] == pytest.approx(COST_2027)


# --- Records priced after the call, from what they carry


def test_a_logged_inference_is_priced_at_the_start_its_duration_implies(monkeypatch):
    from agentic_eval.core_evals.llm_services import inference_logger

    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)
    seen = {}
    real = inference_logger.calculate_total_cost

    def calculate(*args, **kwargs):
        seen["at"] = kwargs.get("at")
        return real(*args, **kwargs)

    monkeypatch.setattr(inference_logger, "calculate_total_cost", calculate)
    # It is logged at 00:00:05 on 1 Jan 2027, for a call that took 6 s: it
    # started at 23:59:59 on 31 Dec 2026.
    monkeypatch.setattr(
        inference_logger, "datetime", SimpleNamespace(now=lambda tz=None: HANDLED)
    )
    # Run the logging inline instead of on its thread.
    monkeypatch.setattr(
        inference_logger,
        "threading",
        SimpleNamespace(Thread=lambda target: SimpleNamespace(start=target)),
    )

    inference_logger.InferenceLogger.log_inference(
        language_model_id=MODEL,
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        response_time=6000,
    )

    assert seen["at"] == STARTED


def test_a_finished_response_is_dated_by_its_created_time_less_its_duration():
    from agentic_eval.core_evals.llm_services.llm_response import _started_at

    response = SimpleNamespace(created=int(HANDLED.timestamp()))

    assert _started_at(response, SimpleNamespace(response_time=6000)) == datetime(
        2026, 12, 31, 23, 59, 59, tzinfo=UTC
    )
    assert _started_at(response, SimpleNamespace(response_time=None)) == datetime(
        2027, 1, 1, 0, 0, 5, tzinfo=UTC
    )
    assert _started_at(SimpleNamespace(), SimpleNamespace(response_time=6000)) is None


def test_an_openai_call_is_priced_at_the_instant_it_started(monkeypatch):
    from agentic_eval.core_evals.llm_services import openai_api

    clock = Clock()
    monkeypatch.setattr(
        openai_api, "time", SimpleNamespace(time=clock.time, sleep=lambda s: None)
    )
    monkeypatch.setattr(model_pricing, "_utc_now", lambda: HANDLED)

    def create(**kwargs):
        clock.reach_the_response()
        message = SimpleNamespace(content="hi", tool_calls=None)
        return SimpleNamespace(
            usage=_usage(),
            choices=[SimpleNamespace(finish_reason="stop", message=message)],
        )

    service = openai_api.OpenAiService.__new__(openai_api.OpenAiService)
    service.openai = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = service.chat_completion([{"role": "user", "content": "x"}], MODEL)

    assert result["metadata"]["cost"]["total_cost"] == pytest.approx(COST_2026)
