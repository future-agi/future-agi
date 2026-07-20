"""
Unit tests for PromptStreamConsumer background-task handling.

Tests cover the fire-and-forget task fix (PR #821):
- _spawn() keeps a strong reference to the task while it runs
- the reference is dropped again once the task finishes (no leak)
- disconnect() cancels any background task still in flight
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from sockets.prompt_stream_consumer import PromptStreamConsumer


def _make_consumer():
    """Create a consumer instance with the WebSocket I/O methods mocked."""
    consumer = PromptStreamConsumer()
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    return consumer


@pytest.mark.unit
@pytest.mark.asyncio
class TestPromptStreamConsumerBackgroundTasks:
    """Tests for fire-and-forget task tracking on PromptStreamConsumer."""

    async def test_spawn_retains_strong_reference(self):
        """_spawn() should keep the task in _background_tasks while it runs."""
        consumer = _make_consumer()
        started = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(3600)

        task = consumer._spawn(_work())
        await started.wait()

        assert task in consumer._background_tasks

        # cleanup: cancel the long-running task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_completed_task_is_discarded(self):
        """The done-callback should drop the reference once the task finishes."""
        consumer = _make_consumer()

        async def _work():
            return "done"

        task = consumer._spawn(_work())
        await task
        # let the done-callback (scheduled via call_soon) run
        await asyncio.sleep(0)

        assert task not in consumer._background_tasks
        assert consumer._background_tasks == set()

    async def test_disconnect_cancels_running_tasks(self):
        """disconnect() should cancel any background task still in flight."""
        consumer = _make_consumer()
        started = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(3600)

        task = consumer._spawn(_work())
        await started.wait()

        await consumer.disconnect(close_code=1000)

        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
