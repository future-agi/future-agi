"""A failed native evaluation keeps the reason its result cell stores on its score."""

import contextlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from model_hub.views import eval_runner

EXECUTOR_DOWN = (
    "Code executor unavailable: the code-executor service could not be reached"
)


@pytest.fixture
def runner():
    instance = object.__new__(eval_runner.NativeEvaluationRunner)
    instance.source_configs = {"experiment_id": str(uuid.uuid4())}
    instance.user_eval_metric_id = uuid.uuid4()
    instance._resolved_version = SimpleNamespace(id=uuid.uuid4())
    instance.dataset = Mock(name="dataset")
    instance.outcomes = []
    instance._native_outcome = instance.outcomes.append
    instance._native_graph_scope = contextlib.nullcontext
    instance._native_result_column = Mock(return_value=Mock(name="result_column"))
    instance._create_reason_column = Mock(return_value=Mock(name="reason_column"))
    return instance


@pytest.fixture
def writes():
    cell_writes = Mock(name="write_failed_row_cells")
    with (
        patch(
            "model_hub.services.experiment_utils.is_experiment_cancelled",
            return_value=False,
        ),
        patch(
            "model_hub.services.experiment_utils.is_user_eval_stopped",
            return_value=False,
        ),
        patch(
            "tfc.temporal.experiments.run_policy.experiment_write_lock",
            lambda _experiment_id: contextlib.nullcontext(),
        ),
        patch(
            "tfc.temporal.experiments.run_policy.write_failed_row_cells",
            cell_writes,
        ),
        patch.object(eval_runner, "Row") as row_model,
    ):
        row_model.no_workspace_objects.get.return_value = Mock(name="row")
        yield cell_writes


def test_error_score_keeps_the_reason_its_result_cell_stores(runner, writes):
    outcome = runner.record_execution_failure(
        "row-1", response={"reason": EXECUTOR_DOWN}
    )

    assert outcome["status"] == "error"
    assert outcome["reason"] == "evaluator_execution_failed"
    assert outcome["explanation"] == EXECUTOR_DOWN
    assert runner.outcomes == [outcome]
    result_write = writes.call_args_list[0].args[3]
    assert json.loads(result_write["value_infos"])["reason"] == EXECUTOR_DOWN


@pytest.mark.parametrize("response", [None, {"reason": "   "}, {"other": "x"}])
def test_error_score_without_a_specific_reason_keeps_the_generic_one(
    runner, writes, response
):
    outcome = runner.record_execution_failure("row-1", response=response)

    assert outcome["status"] == "error"
    assert outcome["explanation"] == "Native evaluator execution failed"


def test_unavailable_output_records_no_error_explanation(runner, writes):
    outcome = runner.record_execution_failure(
        "row-1", unavailable_reason="terminal_prompt_chain_unavailable"
    )

    assert outcome["status"] == "not_assessed"
    assert "explanation" not in outcome
