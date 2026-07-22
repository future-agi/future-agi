"""
Unit tests for PromptStreamConsumer background-task lifecycle.

Regression tests for GH-819: the three streaming entry points used bare
asyncio.create_task() without retaining a reference, so the event loop's
weak reference was the only one and tasks could be garbage-collected
mid-run — freezing the client's stream right after execution_started.

Tests cover:
- Spawned tasks are tracked with a strong reference until completion
- Completed tasks are removed from the tracking set
- Each streaming handler (run_template, improve_prompt, generate_prompt)
  tracks its execution task
- disconnect() cancels still-pending tasks
- Task exceptions escaping the executors do not crash the done-callback
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sockets.prompt_stream_consumer import PromptStreamConsumer


def _make_consumer():
    """Create a consumer instance with mocked I/O and identity."""
    consumer = PromptStreamConsumer()
    consumer.scope = {"type": "websocket", "query_string": b""}
    consumer.session_uuid = "session-123"
    consumer.organization_id = "org-123"
    consumer.user = MagicMock(id="user-123")
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    return consumer


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackgroundTaskTracking:
    """Tests for _spawn() strong-reference tracking."""

    async def test_spawn_keeps_strong_reference_until_done(self):
        """Spawned tasks must stay referenced while running."""
        consumer = _make_consumer()
        release = asyncio.Event()

        async def work():
            await release.wait()

        task = consumer._spawn(work())

        assert task in consumer._background_tasks

        release.set()
        await task

        assert task not in consumer._background_tasks

    async def test_spawn_discards_task_on_exception(self):
        """A task failing with an escaped exception is still discarded."""
        consumer = _make_consumer()

        async def boom():
            raise RuntimeError("escaped the executor")

        task = consumer._spawn(boom())
        await asyncio.gather(task, return_exceptions=True)
        # Let the done-callback run.
        await asyncio.sleep(0)

        assert task not in consumer._background_tasks

    async def test_disconnect_cancels_pending_tasks(self):
        """disconnect() must cancel in-flight executions."""
        consumer = _make_consumer()

        async def never_finishes():
            await asyncio.Event().wait()

        task = consumer._spawn(never_finishes())

        await consumer.disconnect(1000)

        assert task.cancelled()
        assert not consumer._background_tasks

    async def test_disconnect_with_no_tasks_is_noop(self):
        """disconnect() must not fail when nothing is running."""
        consumer = _make_consumer()
        await consumer.disconnect(1000)
        assert not consumer._background_tasks


@pytest.mark.unit
@pytest.mark.asyncio
class TestHandlersTrackTasks:
    """Each streaming handler must track its execution task."""

    @patch.object(
        PromptStreamConsumer, "execute_template_async", new_callable=AsyncMock
    )
    @patch.object(
        PromptStreamConsumer, "validate_template_access", new_callable=AsyncMock
    )
    async def test_run_template_tracks_task(self, mock_validate, mock_execute):
        mock_validate.return_value = True
        consumer = _make_consumer()

        await consumer.handle_run_template(
            {"type": "run_template", "template_id": "tpl-1", "version": "1"}
        )

        assert len(consumer._background_tasks) == 1
        await asyncio.gather(*consumer._background_tasks)
        mock_execute.assert_awaited_once()
        assert not consumer._background_tasks

    @patch.object(
        PromptStreamConsumer, "execute_improve_prompt_async", new_callable=AsyncMock
    )
    @patch(
        "sockets.prompt_stream_consumer.replace_ids_with_column_name_async",
        new_callable=AsyncMock,
    )
    async def test_improve_prompt_tracks_task(self, mock_replace, mock_execute):
        mock_replace.side_effect = lambda prompt: prompt
        consumer = _make_consumer()

        await consumer.handle_improve_prompt(
            {
                "type": "improve_prompt",
                "existing_prompt": "prompt text",
                "improvement_requirements": "make it better",
            }
        )

        assert len(consumer._background_tasks) == 1
        await asyncio.gather(*consumer._background_tasks)
        mock_execute.assert_awaited_once()
        assert not consumer._background_tasks

    @patch.object(
        PromptStreamConsumer, "execute_generate_prompt_async", new_callable=AsyncMock
    )
    async def test_generate_prompt_tracks_task(self, mock_execute):
        consumer = _make_consumer()

        await consumer.handle_generate_prompt(
            {"type": "generate_prompt", "statement": "write a prompt"}
        )

        assert len(consumer._background_tasks) == 1
        await asyncio.gather(*consumer._background_tasks)
        mock_execute.assert_awaited_once()
        assert not consumer._background_tasks
