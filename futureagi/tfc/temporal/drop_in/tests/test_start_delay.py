"""A task's ``start_delay`` option reaches the workflow start.

A caller that schedules a job to start later relies on every hop between
``apply_async`` and ``client.start_workflow`` handing the delay on: the
decorator's ``apply_async``, ``start_activity`` and its async half. These
tests go in through ``apply_async`` and read what the client was asked to
start, so a hop that drops the option is seen whichever hop it is.

The Temporal client is a mock and the sync-to-async bridge runs the dispatch
on a loop of its own, so nothing leaves the process and no bridge thread is
started.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY, temporal_activity


@pytest.fixture(autouse=True)
def _isolate_registry():
    """The decorator registers each task by name; put the registry back so
    the task these tests declare is not left behind for other tests."""
    snapshot = dict(_ACTIVITY_REGISTRY)
    try:
        yield
    finally:
        _ACTIVITY_REGISTRY.clear()
        _ACTIVITY_REGISTRY.update(snapshot)


def _start_kwargs(**options):
    """Call ``apply_async`` on a task with ``options`` and return the keyword
    arguments ``client.start_workflow`` was awaited with."""

    @temporal_activity(name="start_delay_probe_task", queue="tasks_s")
    def probe_task(run_id):
        return run_id

    client = MagicMock()
    client.namespace = "test-ns"
    client.start_workflow = AsyncMock(return_value=MagicMock())

    with (
        patch("tfc.temporal.common.client.get_client", AsyncMock(return_value=client)),
        patch(
            "tfc.temporal.common.client._run_async_in_sync_context",
            side_effect=lambda dispatch: asyncio.run(dispatch()),
        ),
        patch("tfc.temporal.drop_in.workflow.TaskRunnerWorkflow", MagicMock()),
        patch("tfc.temporal.drop_in.workflow.TaskRunnerInput", MagicMock()),
    ):
        result = probe_task.apply_async(args=("run-1",), **options)

    assert client.start_workflow.await_count == 1
    kwargs = client.start_workflow.await_args.kwargs
    assert result.id == kwargs["id"]
    return kwargs


def test_apply_async_hands_start_delay_to_the_workflow_start():
    """The delay a caller asks for is the delay the workflow is started
    with, unchanged."""
    kwargs = _start_kwargs(start_delay=timedelta(seconds=300))

    assert kwargs["start_delay"] == timedelta(seconds=300)
    assert kwargs["task_queue"] == "tasks_s"


def test_apply_async_without_start_delay_passes_no_such_keyword():
    """A caller that asks for no delay starts the workflow exactly as before
    the option existed: the keyword is absent, not ``None``."""
    kwargs = _start_kwargs()

    assert "start_delay" not in kwargs
