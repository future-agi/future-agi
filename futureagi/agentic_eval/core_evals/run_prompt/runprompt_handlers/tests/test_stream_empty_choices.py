"""
Run Prompt streaming must survive a streamed chunk with no choices.

Some chunks reach the handler with an empty ``choices`` list (Azure's
prompt-filter chunk, some providers' usage-only chunks). With LiteLLM
instrumented by traceAI, the stream reaches the handler through traceai-litellm's
iterator wrappers, which used to raise ``IndexError: list index out of range`` on
such a chunk and fail the whole Run Prompt. These tests feed the handler through
the installed wrappers after ``instrument_litellm()``.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

traceai_litellm = pytest.importorskip("traceai_litellm")

from litellm.types.utils import (  # noqa: E402
    Delta,
    ModelResponseStream,
    StreamingChoices,
    Usage,
)
from opentelemetry import trace  # noqa: E402

from tfc.telemetry.instrumentation import instrument_litellm  # noqa: E402

FORMATTER = (
    "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils."
    "response_formatter.calculate_total_cost"
)
FIXED_COST = {"total_cost": 0.001, "prompt_cost": 0.0005, "completion_cost": 0.0005}


@pytest.fixture
def litellm_instrumented():
    assert instrument_litellm() is True
    yield
    traceai_litellm.LiteLLMInstrumentor().uninstrument()


def _chunks():
    usage_chunk = ModelResponseStream(model="gpt-4o", choices=[])
    usage_chunk.usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    assert usage_chunk.to_dict()["choices"] == []
    return [
        ModelResponseStream(
            model="gpt-4o",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hello"))],
        ),
        ModelResponseStream(
            model="gpt-4o",
            choices=[StreamingChoices(index=0, delta=Delta(content=" world"))],
        ),
        usage_chunk,
    ]


def _span():
    span_cm = trace.get_tracer(__name__).start_as_current_span("litellm-stream")
    return span_cm.__enter__(), span_cm


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _handler(ws_manager):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
        ModelHandlerContext,
    )
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
        LLMHandler,
    )

    context = ModelHandlerContext(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Say hello"}],
        organization_id=str(uuid4()),
        api_key="test-key",
        provider="openai",
        ws_manager=ws_manager,
        template_id="test-template",
        version="v1",
        result_index=0,
        num_results=1,
    )
    return LLMHandler(context)


@pytest.mark.integration
@pytest.mark.usefixtures("litellm_instrumented")
class TestRunPromptStreamUsageOnlyChunk:
    def test_sync_stream_completes_and_keeps_usage(self):
        ws_manager = MagicMock()
        ws_manager.is_streaming_stopped.return_value = False
        handler = _handler(ws_manager)
        span, span_cm = _span()
        stream = traceai_litellm.StreamingIteratorWrapper(
            iter(_chunks()), span, span_cm
        )

        with patch("litellm.completion", return_value=stream), patch(
            FORMATTER, return_value=FIXED_COST
        ):
            response = handler.execute_sync(streaming=True)

        assert response.response == "Hello world"
        assert response.metadata["usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        ws_manager.send_completed_message.assert_called_once()
        ws_manager.send_error_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_stream_completes_and_keeps_usage(self):
        ws_manager = MagicMock()
        ws_manager.is_streaming_stopped = AsyncMock(return_value=False)
        for name in (
            "send_started_message",
            "send_running_message",
            "send_completed_message",
            "send_error_message",
            "set_cached_response",
        ):
            setattr(ws_manager, name, AsyncMock())
        handler = _handler(ws_manager)
        span, span_cm = _span()
        stream = traceai_litellm.AsyncStreamingIteratorWrapper(
            _AsyncChunks(_chunks()), span, span_cm
        )

        with patch("litellm.acompletion", AsyncMock(return_value=stream)), patch(
            FORMATTER, return_value=FIXED_COST
        ):
            response = await handler.execute_async(streaming=True)

        assert response.response == "Hello world"
        assert response.metadata["usage"]["total_tokens"] == 15
        ws_manager.send_completed_message.assert_awaited_once()
        ws_manager.send_error_message.assert_not_awaited()
