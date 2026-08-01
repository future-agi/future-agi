import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.views import eval_task as eval_task_views

PRIVATE_ERROR = (
    "Code: 159. DB::Exception: private customer query; "
    "Stack trace: 0. DB::PipelineExecutor::execute"
)


@pytest.mark.parametrize(
    ("method_name", "message"),
    [
        (
            "mark_eval_tasks_deleted",
            "Evaluation tasks could not be deleted. Please try again.",
        ),
        (
            "pause_eval_task",
            "Evaluation task could not be paused. Please try again.",
        ),
        (
            "unpause_eval_task",
            "Evaluation task could not be resumed. Please try again.",
        ),
    ],
)
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (RuntimeError(PRIVATE_ERROR), "query_failed"),
        (TimeoutError(PRIVATE_ERROR), "read_budget_exceeded"),
    ],
)
def test_eval_task_actions_sanitize_internal_errors(
    monkeypatch, method_name, message, exc, expected_code
):
    task_id = uuid.uuid4()
    request = SimpleNamespace(
        data={"eval_task_ids": [str(task_id)]},
        query_params={"eval_task_id": str(task_id)},
    )
    view = eval_task_views.EvalTaskView()
    view.request = request
    view._scope_eval_task_queryset = MagicMock(side_effect=exc)
    log = MagicMock()
    monkeypatch.setattr(eval_task_views, "logger", log)

    method = getattr(eval_task_views.EvalTaskView, method_name)
    response = method.__wrapped__(view, request)

    assert response.status_code == 400
    assert response.data["code"] == expected_code
    assert response.data["message"] == message
    assert response.data["result"] == message
    assert PRIVATE_ERROR not in json.dumps(response.data)
    contract = ApiErrorResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors

    assert log.exception.call_count == 1
    assert log.exception.call_args.kwargs["error"] == PRIVATE_ERROR
    assert log.exception.call_args.kwargs["error_type"] == type(exc).__name__
