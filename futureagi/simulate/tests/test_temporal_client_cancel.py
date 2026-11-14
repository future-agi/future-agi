"""Retry classification for Temporal workflow cancellation (issue #1949).

``_cancel_with_retries`` backs the native test-execution, hosted SDK runner and
rerun cancellation paths. It must decide whether to retry from the Temporal
``RPCError.status``, never from words in an exception message.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from simulate.temporal import client as temporal_client
from simulate.temporal.client import _cancel_with_retries

pytestmark = pytest.mark.unit

RETRYABLE_STATUSES = [
    RPCStatusCode.UNAVAILABLE,
    RPCStatusCode.DEADLINE_EXCEEDED,
    RPCStatusCode.RESOURCE_EXHAUSTED,
    RPCStatusCode.ABORTED,
]

PERMANENT_STATUSES = [
    RPCStatusCode.NOT_FOUND,
    RPCStatusCode.INVALID_ARGUMENT,
    RPCStatusCode.PERMISSION_DENIED,
    RPCStatusCode.UNAUTHENTICATED,
    RPCStatusCode.FAILED_PRECONDITION,
]


def _rpc_error(status, message="cancel failed"):
    return RPCError(message, status, b"")


def _client_whose_cancel_raises(*side_effect):
    handle = Mock()
    handle.cancel = AsyncMock(side_effect=list(side_effect))
    client = Mock()
    client.get_workflow_handle.return_value = handle
    return client, handle


@pytest.fixture
def sleeps(monkeypatch):
    """Record the backoff waits instead of sleeping."""
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(temporal_client.asyncio, "sleep", fake_sleep)
    return waits


@pytest.mark.parametrize("status", RETRYABLE_STATUSES, ids=lambda s: s.name)
async def test_transient_rpc_status_is_retried_until_cancel_succeeds(status, sleeps):
    client, handle = _client_whose_cancel_raises(_rpc_error(status), None)

    assert await _cancel_with_retries(client, "workflow-id") is True
    assert handle.cancel.await_count == 2
    assert sleeps == [2]
    client.get_workflow_handle.assert_called_with("workflow-id")


@pytest.mark.parametrize("status", RETRYABLE_STATUSES, ids=lambda s: s.name)
async def test_transient_rpc_status_gives_up_after_max_retries(status, sleeps):
    client, handle = _client_whose_cancel_raises(
        _rpc_error(status), _rpc_error(status), _rpc_error(status)
    )

    assert await _cancel_with_retries(client, "workflow-id") is False
    assert handle.cancel.await_count == 3
    assert sleeps == [2, 4]


@pytest.mark.parametrize("status", PERMANENT_STATUSES, ids=lambda s: s.name)
async def test_permanent_rpc_status_is_not_retried_even_with_transient_words(
    status, sleeps
):
    error = _rpc_error(status, "connection reset; request timeout; unavailable")
    client, handle = _client_whose_cancel_raises(error)

    assert await _cancel_with_retries(client, "workflow-id") is False
    assert handle.cancel.await_count == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "message",
    [
        "timeout while cancelling",
        "h2 protocol error",
        "connection reset by peer",
        "service unavailable",
    ],
)
async def test_non_rpc_error_is_not_retried_even_with_transient_words(message, sleeps):
    client, handle = _client_whose_cancel_raises(RuntimeError(message))

    assert await _cancel_with_retries(client, "workflow-id") is False
    assert handle.cancel.await_count == 1
    assert sleeps == []


async def test_retry_stops_when_status_turns_permanent(sleeps):
    """A workflow that finishes between attempts is reported, not retried."""
    client, handle = _client_whose_cancel_raises(
        _rpc_error(RPCStatusCode.UNAVAILABLE),
        _rpc_error(RPCStatusCode.NOT_FOUND),
    )

    assert await _cancel_with_retries(client, "workflow-id") is False
    assert handle.cancel.await_count == 2
    assert sleeps == [2]
