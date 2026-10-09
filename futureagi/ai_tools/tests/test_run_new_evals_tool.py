from unittest.mock import MagicMock, patch

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import (
    make_agent_definition,
    make_eval_template,
    make_scenario,
)
from ai_tools.tools.simulation import run_new_evals


def _make_completed_run(tool_context):
    from simulate.models import (
        CallExecution,
        RunTest,
        SimulateEvalConfig,
        TestExecution,
    )

    run_test = RunTest.objects.create(
        name="Test Run",
        agent_definition=make_agent_definition(tool_context),
        organization=tool_context.organization,
        workspace=tool_context.workspace,
    )
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
    )
    call_execution = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=make_scenario(tool_context),
        status=CallExecution.CallStatus.COMPLETED,
        eval_outputs={},
        call_metadata={},
    )
    eval_config = SimulateEvalConfig.objects.create(
        name="Pass/Fail",
        eval_template=make_eval_template(tool_context, config={"output": "Pass/Fail"}),
        run_test=run_test,
        config={},
        mapping={},
    )
    return run_test, test_execution, call_execution, eval_config


class TestRunNewEvalsOnSimulationTool:
    def test_tool_module_imports_and_registers(self):
        tool = run_new_evals.RunNewEvalsOnSimulationTool
        assert tool.name == "run_new_evals_on_simulation"
        assert tool.input_model is run_new_evals.RunNewEvalsOnSimulationInput

    @patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    )
    def test_happy_path_marks_executions_and_dispatches(
        self, mock_apply_async, tool_context
    ):
        from simulate.models import TestExecution

        mock_apply_async.return_value = MagicMock(id="task-1")
        run_test, test_execution, call_execution, eval_config = _make_completed_run(
            tool_context
        )

        result = run_tool(
            "run_new_evals_on_simulation",
            {
                "run_test_id": str(run_test.id),
                "eval_config_ids": [str(eval_config.id)],
                "select_all": True,
            },
            tool_context,
        )

        assert not result.is_error, result.content
        assert result.data["test_execution_count"] == 1
        assert result.data["call_execution_count"] == 1
        assert result.data["eval_names"] == ["Pass/Fail"]
        mock_apply_async.assert_called_once_with(
            args=([str(call_execution.id)], [str(eval_config.id)])
        )

        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert test_execution.picked_up_by_executor is False
        column_ids = [
            col["id"] for col in test_execution.execution_metadata["column_order"]
        ]
        assert column_ids == [str(eval_config.id)]

        call_execution.refresh_from_db()
        assert call_execution.eval_outputs[str(eval_config.id)] == {"status": "pending"}
        assert call_execution.call_metadata["eval_started"] is True
        assert call_execution.call_metadata["eval_completed"] is False
