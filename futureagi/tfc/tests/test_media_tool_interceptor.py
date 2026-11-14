"""A missing ffmpeg fails a Temporal activity once instead of on every retry."""

import pytest
from temporalio.exceptions import ApplicationError

from tfc.management.commands.start_temporal_worker import _worker_interceptors
from tfc.temporal.common.activity_thread import OwnThreadActivityInterceptor
from tfc.temporal.common.media_tool_interceptor import (
    MediaToolActivityInbound,
    MediaToolActivityInterceptor,
)
from tfc.utils.media_tools import MediaToolUnavailable


class _Next:
    def __init__(self, outcome):
        self.outcome = outcome

    async def execute_activity(self, _input):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


async def _run(outcome):
    return await MediaToolActivityInbound(_Next(outcome)).execute_activity(None)


@pytest.mark.unit
async def test_missing_ffmpeg_fails_without_retry():
    with pytest.raises(ApplicationError) as caught:
        await _run(MediaToolUnavailable("call recording channel split"))
    assert caught.value.non_retryable is True
    assert caught.value.type == "MediaToolUnavailable"
    assert "ffmpeg is not installed in this backend image" in caught.value.message
    assert isinstance(caught.value.__cause__, MediaToolUnavailable)


@pytest.mark.unit
async def test_wrapped_missing_ffmpeg_fails_without_retry():
    """Provider handlers re-raise a new Exception; the chain is followed."""
    try:
        raise MediaToolUnavailable("Deepgram speech-to-text audio conversion")
    except MediaToolUnavailable as missing:
        wrapped = RuntimeError("Error in Deepgram handler")
        wrapped.__cause__ = missing
    with pytest.raises(ApplicationError) as caught:
        await _run(wrapped)
    assert caught.value.non_retryable is True


@pytest.mark.unit
async def test_other_errors_keep_their_retry_policy():
    error = ConnectionError("storage unreachable")
    with pytest.raises(ConnectionError) as caught:
        await _run(error)
    assert caught.value is error


@pytest.mark.unit
async def test_results_pass_through():
    assert await _run({"ok": True}) == {"ok": True}


@pytest.mark.unit
def test_every_queue_gets_the_interceptor_first():
    assert [type(i) for i in _worker_interceptors("tasks_s")] == [
        MediaToolActivityInterceptor
    ]
    assert [type(i) for i in _worker_interceptors("simulation_runner")] == [
        MediaToolActivityInterceptor,
        OwnThreadActivityInterceptor,
    ]
