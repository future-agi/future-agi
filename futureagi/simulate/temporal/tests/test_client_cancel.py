import asyncio
import sys
import types
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.service import RPCError, RPCStatusCode

# The cancellation helper is independent of the graph input models, but the
# client module imports those models at module load time. Keep this unit test
# focused on cancellation without bootstrapping the Django model graph.
rerun_types = types.ModuleType("simulate.temporal.types.rerun")
rerun_types.MergeCallsSignal = object
rerun_types.RerunCoordinatorInput = object
test_execution_types = types.ModuleType("simulate.temporal.types.test_execution")
test_execution_types.TestExecutionInput = object
sys.modules.setdefault("simulate.temporal.types", types.ModuleType("simulate.temporal.types"))
sys.modules["simulate.temporal.types.rerun"] = rerun_types
sys.modules["simulate.temporal.types.test_execution"] = test_execution_types

from simulate.temporal.client import _cancel_with_retries  # noqa: E402


def _rpc_error(status):
    return RPCError("cancel failed", status, b"")


@pytest.mark.parametrize(
    "status",
    [RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED],
)
def test_cancel_retries_typed_transient_rpc_errors(status):
    handle = Mock()
    handle.cancel = AsyncMock(side_effect=[_rpc_error(status), None])
    client = Mock()
    client.get_workflow_handle.return_value = handle

    with patch("simulate.temporal.client.asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(_cancel_with_retries(client, "workflow-id"))

    assert result is True
    assert handle.cancel.await_count == 2


def test_cancel_does_not_retry_non_rpc_errors_with_transient_words():
    handle = Mock()
    handle.cancel = AsyncMock(side_effect=RuntimeError("timeout while cancelling"))
    client = Mock()
    client.get_workflow_handle.return_value = handle

    with patch("simulate.temporal.client.asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(_cancel_with_retries(client, "workflow-id"))

    assert result is False
    assert handle.cancel.await_count == 1
