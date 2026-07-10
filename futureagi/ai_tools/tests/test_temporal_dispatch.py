from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from ai_tools.base import ToolContext
from ai_tools.tools.simulation.cancel_test_execution import CancelTestExecutionTool
from simulate.models.test_execution import TestExecution as ExecutionModel


@pytest.mark.unit
def test_cancel_tool_always_dispatches_to_temporal(settings):
    settings.TEMPORAL_TEST_EXECUTION_ENABLED = False
    organization = SimpleNamespace(id=uuid4())
    run_test = SimpleNamespace(id=uuid4(), name="Temporal tool dispatch test")
    test_execution = MagicMock()
    test_execution.id = uuid4()
    test_execution.run_test = run_test
    test_execution.run_test_id = run_test.id
    test_execution.status = ExecutionModel.ExecutionStatus.RUNNING
    context = ToolContext(
        user=SimpleNamespace(id=uuid4()),
        organization=organization,
        workspace=SimpleNamespace(id=uuid4()),
    )
    temporal_result = {
        "success": True,
        "message": "Cancellation signal sent to workflow",
        "test_execution_id": str(test_execution.id),
    }
    tool = CancelTestExecutionTool()

    with (
        patch.object(ExecutionModel.objects, "get", return_value=test_execution),
        patch.object(
            tool, "_cancel_with_temporal", return_value=temporal_result
        ) as temporal_dispatch,
        patch(
            "simulate.services.test_executor.TestExecutor.cancel_test",
            return_value=temporal_result,
        ) as legacy_dispatch,
    ):
        result = tool.run({"test_execution_id": str(test_execution.id)}, context)

    assert not result.is_error, result.content
    temporal_dispatch.assert_called_once_with(test_execution)
    legacy_dispatch.assert_not_called()
