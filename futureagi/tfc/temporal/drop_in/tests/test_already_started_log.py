"""How a refused workflow start is logged.

Temporal refuses to start a workflow id whose run is still open. Callers that
start work under a fixed id handle that refusal themselves (the knowledge base
ingestion starts the new batch under an id of its own), so the start helpers
pass the refusal on unchanged and log it as one info line without a
traceback, instead of as a failure. Any other start error is still logged as
a failure.

The Temporal client is a mock and the sync-to-async bridge runs the dispatch
on a loop of its own, as in test_start_delay.py, so nothing leaves the
process.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from tfc.temporal.drop_in.runner import start_activity

ACTIVITY = "ingest_kb_files_activity"
TASK_ID = "kb-ingest-kb-1"
REFUSAL_EVENTS = {
    "start_activity_already_started",
    "start_activity_async_already_started",
}
FAILURE_EVENTS = {"start_activity_failed", "start_activity_async_failed"}


def _start_and_capture_logs(error):
    """Start an activity whose workflow start raises ``error``; return the
    runner's logger after checking that ``error`` itself reached the caller."""
    client = MagicMock()
    client.namespace = "test-ns"
    client.start_workflow = AsyncMock(side_effect=error)
    logger = MagicMock()

    with (
        patch("tfc.temporal.common.client.get_client", AsyncMock(return_value=client)),
        patch(
            "tfc.temporal.common.client._run_async_in_sync_context",
            side_effect=lambda dispatch: asyncio.run(dispatch()),
        ),
        patch("tfc.temporal.drop_in.workflow.TaskRunnerWorkflow", MagicMock()),
        patch("tfc.temporal.drop_in.workflow.TaskRunnerInput", MagicMock()),
        patch("tfc.temporal.drop_in.runner.logger", logger),
    ):
        with pytest.raises(type(error)) as raised:
            start_activity(ACTIVITY, args=({}, "kb-1", "org-1"), task_id=TASK_ID)

    assert raised.value is error
    assert client.start_workflow.await_count == 1
    return logger


def _events(log_method):
    return [call.args[0] for call in log_method.call_args_list if call.args]


def test_refused_start_is_logged_at_info_without_traceback():
    refusal = WorkflowAlreadyStartedError(f"task-{TASK_ID}", "TaskRunnerWorkflow")

    logger = _start_and_capture_logs(refusal)

    assert logger.exception.call_args_list == [], (
        "a refused start was logged as a failure with a traceback: "
        f"{logger.exception.call_args_list}"
    )
    assert logger.error.call_args_list == []
    assert not FAILURE_EVENTS & set(_events(logger.warning))

    refusal_calls = [
        call
        for call in logger.info.call_args_list
        if call.args and call.args[0] in REFUSAL_EVENTS
    ]
    assert {call.args[0] for call in refusal_calls} == REFUSAL_EVENTS, (
        f"info events: {_events(logger.info)}"
    )
    for call in refusal_calls:
        assert call.kwargs.get("activity_name") == ACTIVITY
        assert call.kwargs.get("task_id") == TASK_ID
    for call in logger.info.call_args_list:
        assert "exc_info" not in call.kwargs, call


def test_other_start_error_is_still_logged_as_a_failure():
    error = RuntimeError("Temporal is unavailable")

    logger = _start_and_capture_logs(error)

    assert set(_events(logger.exception)) == FAILURE_EVENTS
    assert not REFUSAL_EVENTS & set(_events(logger.info))
