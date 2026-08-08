"""Retry classification for Temporal workflow cancellation.

`_cancel_with_retries` used to decide whether a failed cancel was worth retrying
by substring-matching `str(exc)` against
``["timeout", "h2 protocol", "connection reset", "unavailable"]``. That is now
keyed off exception type and gRPC status.

The stakes are in `_cancel_with_retries`'s own docstring: a cancel that never
lands leaves the workflow running, and it later writes its terminal status over
the CANCELLED row. Misclassifying a transient failure as permanent is therefore
not a missed retry — it is an orphaned workflow and a UI that stops showing the
cancellation.

No Temporal server is needed; the client handle is stubbed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.service import RPCError, RPCStatusCode

from simulate.temporal.client import (
    _cancel_with_retries,
    _is_transient_cancel_error,
)


def _rpc(status: RPCStatusCode, message="rpc failure") -> RPCError:
    return RPCError(message, status, b"")


def _client_raising(*errors):
    """A stub Temporal client whose cancel() raises `errors` in order.

    A trailing None means "succeed on this attempt".
    """
    handle = MagicMock()
    handle.cancel = AsyncMock(
        side_effect=[e if e is not None else None for e in errors]
    )
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    return client, handle


class TestTransientClassification:
    @pytest.mark.parametrize(
        "status",
        [
            RPCStatusCode.UNAVAILABLE,
            RPCStatusCode.DEADLINE_EXCEEDED,
            RPCStatusCode.RESOURCE_EXHAUSTED,
            RPCStatusCode.ABORTED,
        ],
    )
    def test_retryable_grpc_statuses(self, status):
        assert _is_transient_cancel_error(_rpc(status)) is True

    @pytest.mark.parametrize(
        "status",
        [
            RPCStatusCode.NOT_FOUND,
            RPCStatusCode.PERMISSION_DENIED,
            RPCStatusCode.INVALID_ARGUMENT,
            RPCStatusCode.UNAUTHENTICATED,
            RPCStatusCode.FAILED_PRECONDITION,
            RPCStatusCode.UNIMPLEMENTED,
        ],
    )
    def test_permanent_grpc_statuses(self, status):
        assert _is_transient_cancel_error(_rpc(status)) is False

    @pytest.mark.parametrize(
        "exc",
        [
            TimeoutError("timed out"),
            ConnectionError("connection failed"),
            ConnectionResetError("connection reset by peer"),
        ],
    )
    def test_transport_errors_are_transient(self, exc):
        """Covers the ground the old 'timeout'/'connection reset' strings did."""
        assert _is_transient_cancel_error(exc) is True

    def test_asyncio_timeout_is_covered_by_the_builtin(self):
        """asyncio.TimeoutError is an alias of TimeoutError on 3.11+.

        Pinned so the single `TimeoutError` entry in _RETRYABLE_TRANSPORT_ERRORS
        is understood to cover both spellings rather than looking like an
        oversight.
        """
        assert asyncio.TimeoutError is TimeoutError
        # noqa UP041: spelling it `asyncio.TimeoutError` is the point of the test —
        # collapsing it to the builtin would make this a duplicate of the case above.
        assert _is_transient_cancel_error(asyncio.TimeoutError()) is True  # noqa: UP041

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("bad input"),
            RuntimeError("something broke"),
            KeyError("missing"),
        ],
    )
    def test_unrelated_exceptions_are_permanent(self, exc):
        assert _is_transient_cancel_error(exc) is False


class TestClassificationNoLongerDependsOnMessageText:
    """The two directions the substring approach got wrong."""

    def test_transient_status_with_no_matching_words_is_still_retried(self):
        """The false-negative case from #1949.

        A gRPC UNAVAILABLE whose message contains none of the four substrings —
        which is version- and wrapper-dependent — used to be classified
        permanent, so the cancel was abandoned after one attempt.
        """
        err = _rpc(RPCStatusCode.UNAVAILABLE, message="upstream connect error")
        text = str(err).lower()
        assert not any(
            s in text
            for s in ("timeout", "h2 protocol", "connection reset", "unavailable")
        ), "precondition: message must not contain the old substrings"

        assert _is_transient_cancel_error(err) is True

    def test_permanent_error_merely_mentioning_a_timeout_is_not_retried(self):
        """The false-positive case: 'timeout' matched too broadly."""
        err = _rpc(
            RPCStatusCode.INVALID_ARGUMENT,
            message="workflow execution timeout must be positive",
        )
        assert "timeout" in str(err).lower()

        assert _is_transient_cancel_error(err) is False

    def test_not_found_is_permanent_even_though_it_is_a_connection_style_failure(self):
        """A workflow that is already gone must not be retried three times."""
        assert (
            _is_transient_cancel_error(
                _rpc(RPCStatusCode.NOT_FOUND, "workflow not found")
            )
            is False
        )


class TestCancelWithRetries:
    async def test_returns_true_on_first_success(self):
        client, handle = _client_raising(None)

        assert await _cancel_with_retries(client, "wf-1") is True
        assert handle.cancel.await_count == 1

    async def test_retries_a_transient_failure_then_succeeds(self):
        client, handle = _client_raising(_rpc(RPCStatusCode.UNAVAILABLE), None)

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()):
            assert await _cancel_with_retries(client, "wf-1") is True

        assert handle.cancel.await_count == 2

    async def test_gives_up_after_max_retries(self):
        client, handle = _client_raising(
            *[_rpc(RPCStatusCode.UNAVAILABLE) for _ in range(3)]
        )

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()):
            assert await _cancel_with_retries(client, "wf-1", max_retries=3) is False

        assert handle.cancel.await_count == 3

    async def test_permanent_failure_is_not_retried(self):
        """One attempt only — retrying a NOT_FOUND just delays the caller."""
        client, handle = _client_raising(
            _rpc(RPCStatusCode.NOT_FOUND), _rpc(RPCStatusCode.NOT_FOUND)
        )

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()) as sleep:
            assert await _cancel_with_retries(client, "wf-1") is False

        assert handle.cancel.await_count == 1
        sleep.assert_not_awaited()

    async def test_backoff_grows_between_attempts(self):
        client, _ = _client_raising(
            *[_rpc(RPCStatusCode.UNAVAILABLE) for _ in range(3)]
        )

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()) as sleep:
            await _cancel_with_retries(client, "wf-1", max_retries=3)

        assert [c.args[0] for c in sleep.await_args_list] == [2, 4]

    async def test_transport_error_is_retried(self):
        client, handle = _client_raising(ConnectionResetError("reset by peer"), None)

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()):
            assert await _cancel_with_retries(client, "wf-1") is True

        assert handle.cancel.await_count == 2

    async def test_no_sleep_after_the_final_attempt(self):
        """The last failure returns immediately rather than backing off first."""
        client, _ = _client_raising(
            _rpc(RPCStatusCode.UNAVAILABLE), _rpc(RPCStatusCode.UNAVAILABLE)
        )

        with patch("simulate.temporal.client.asyncio.sleep", new=AsyncMock()) as sleep:
            await _cancel_with_retries(client, "wf-1", max_retries=2)

        assert sleep.await_count == 1
