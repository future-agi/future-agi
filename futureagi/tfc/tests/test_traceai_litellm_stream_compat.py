"""
Streamed LiteLLM chunks with no choices must pass through the traceAI wrapper.

With LiteLLM instrumented, every ``litellm.completion(stream=True)`` result is
wrapped by traceai-litellm's ``StreamingIteratorWrapper``. Older releases of
that wrapper index ``choices[0]`` on each chunk, so a chunk with
``choices == []`` (Azure's prompt-filter chunk, a usage-only chunk) raised
``IndexError: list index out of range`` and failed Run Prompt streaming.
These tests go through ``instrument_litellm()`` and the installed wrapper.
"""

import asyncio

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


@pytest.fixture
def litellm_instrumented():
    assert instrument_litellm() is True
    yield
    traceai_litellm.LiteLLMInstrumentor().uninstrument()


def _content_chunk(text):
    return ModelResponseStream(
        model="gpt-4o",
        choices=[StreamingChoices(index=0, delta=Delta(content=text))],
    )


def _usage_only_chunk():
    chunk = ModelResponseStream(model="gpt-4o", choices=[])
    chunk.usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return chunk


def _span():
    span_cm = trace.get_tracer(__name__).start_as_current_span("litellm-stream")
    return span_cm.__enter__(), span_cm


def _assert_precondition(chunk):
    # The bug only shows when the chunk really serialises with no choices.
    assert chunk.to_dict()["choices"] == []


class _RaisingIterator:
    def __iter__(self):
        return self

    def __next__(self):
        raise IndexError("raised by the provider stream")


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


@pytest.mark.usefixtures("litellm_instrumented")
class TestStreamingWrapperEmptyChoices:
    def test_sync_wrapper_yields_usage_only_chunk(self):
        first, usage_chunk = _content_chunk("Hello"), _usage_only_chunk()
        _assert_precondition(usage_chunk)
        span, span_cm = _span()

        wrapper = traceai_litellm.StreamingIteratorWrapper(
            iter([first, usage_chunk]), span, span_cm
        )
        received = list(wrapper)

        assert received == [first, usage_chunk]
        assert received[1].usage.prompt_tokens == 10
        assert received[1].usage.total_tokens == 15
        assert wrapper.content_string == "Hello"

    def test_async_wrapper_yields_usage_only_chunk(self):
        first, usage_chunk = _content_chunk("Hello"), _usage_only_chunk()
        _assert_precondition(usage_chunk)
        span, span_cm = _span()

        wrapper = traceai_litellm.AsyncStreamingIteratorWrapper(
            _AsyncChunks([first, usage_chunk]), span, span_cm
        )

        async def collect():
            return [chunk async for chunk in wrapper]

        received = asyncio.run(collect())

        assert received == [first, usage_chunk]
        assert received[1].usage.completion_tokens == 5
        assert wrapper.content_string == "Hello"

    def test_empty_choices_chunk_first_in_stream(self):
        # Azure sends a prompt-filter chunk with no choices before any content.
        leading = ModelResponseStream(model="gpt-4o", choices=[])
        _assert_precondition(leading)
        span, span_cm = _span()

        wrapper = traceai_litellm.StreamingIteratorWrapper(
            iter([leading, _content_chunk("Hi")]), span, span_cm
        )
        received = list(wrapper)

        assert len(received) == 2
        assert received[0] is leading
        assert wrapper.content_string == "Hi"

    def test_index_error_from_the_stream_itself_still_raises(self):
        span, span_cm = _span()
        wrapper = traceai_litellm.StreamingIteratorWrapper(
            _RaisingIterator(), span, span_cm
        )

        with pytest.raises(IndexError, match="raised by the provider stream"):
            next(wrapper)

    def test_instrumenting_twice_keeps_behaviour(self):
        assert instrument_litellm() is True
        usage_chunk = _usage_only_chunk()
        span, span_cm = _span()

        wrapper = traceai_litellm.StreamingIteratorWrapper(
            iter([_content_chunk("a"), usage_chunk]), span, span_cm
        )

        assert list(wrapper)[-1] is usage_chunk
