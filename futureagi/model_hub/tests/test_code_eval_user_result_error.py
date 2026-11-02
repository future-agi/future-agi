"""A code eval whose own code returns no usable result says so.

custom_code_eval raises ValueError when the user's evaluate() returns None
or a value that is not a bool, number or {"result"/"score": ...} dict. The
eval playground and "Test Evaluation" caught that in their catch-all, logged
it with logger.exception (the backend error alert, logger
model_hub.views.separate_evals, ValueError in custom_code_eval) and told the
user "could not be completed. Please retry", which no retry can fix.

These messages are the backend's own fixed texts, so they are safe to show,
unlike sandbox errors, which stay generic (see test_code_eval_setup_error).

CodeExecution.execute is replaced with the code-executor's answer.
"""

from unittest import mock

import pytest
from rest_framework import status
from structlog.testing import capture_logs

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate

PY_CODE = "def evaluate(output, expected, **kwargs):\n    pass\n"

VIEW_ERROR_EVENTS = {"eval_playground_execution_failed", "evaluation_template_test_failed"}


@pytest.fixture
def executor_answers():
    """Replace the sandbox run with the code-executor's answer."""
    patcher = mock.patch(
        "agentic_eval.core_evals.fi_evals.function.functions.CodeExecution.execute"
    )
    execute = patcher.start()

    def answer(result: dict):
        execute.return_value = result

    yield answer
    patcher.stop()


def _py_code_eval(user, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="py-user-result-error",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": PY_CODE,
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output", "expected"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _playground(auth_client, template):
    return auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "same", "expected": "same"},
            "config": {"params": {}},
        },
        format="json",
    )


def _test_evaluation(auth_client, template):
    return auth_client.post(
        "/model-hub/test-evaluation/",
        {
            "name": "py-user-result-error-test",
            "template_type": "Function",
            "template_id": str(template.id),
            "eval_type_id": "CustomCodeEval",
            "model": "",
            "output_type": "Pass/Fail",
            "required_keys": ["output", "expected"],
            "input_data_types": {"output": "text", "expected": "text"},
            "config": {
                "mapping": {"output": "same", "expected": "same"},
                "config": {"code": PY_CODE},
            },
        },
        format="json",
    )


def _view_errors(logs):
    return [
        entry
        for entry in logs
        if entry.get("event") in VIEW_ERROR_EVENTS
        and entry.get("log_level") in ("error", "critical")
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
@pytest.mark.parametrize(
    "result, message",
    [
        # evaluate() fell off the end: the sandbox reports a skip.
        (
            {"status": "skip", "data": None},
            "Code eval function returned None (no result produced)",
        ),
        # evaluate() returned a string instead of a bool, number or dict.
        (
            {"status": "success", "data": "yes"},
            "Code eval must return a boolean, number, or dict result",
        ),
        # evaluate() returned a dict without "result" or "score".
        (
            {"status": "success", "data": {"passed": True}},
            "Code eval dict return must include a 'result' or 'score' key",
        ),
    ],
    ids=["returned-none", "returned-string", "dict-without-result"],
)
def test_code_eval_without_a_usable_result_says_why(
    call, result, message, auth_client, user, workspace, executor_answers
):
    executor_answers({**result, "execution_time": 0})

    with capture_logs() as logs:
        response = call(auth_client, _py_code_eval(user, workspace))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert message in str(response.data)
    assert "Please retry" not in str(response.data)
    assert "could not be completed" not in str(response.data)
    assert _view_errors(logs) == []


@pytest.mark.django_db
@pytest.mark.parametrize("call", [_playground, _test_evaluation])
def test_a_valid_code_eval_still_returns_its_result(
    call, auth_client, user, workspace, executor_answers
):
    executor_answers({"status": "success", "data": True, "execution_time": 0})

    response = call(auth_client, _py_code_eval(user, workspace))

    assert response.status_code == status.HTTP_200_OK
