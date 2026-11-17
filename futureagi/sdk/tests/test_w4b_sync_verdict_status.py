"""Keep SDK execution status independent of a returned pass/fail verdict.

Use real persistence, authentication and GET serialization. Stub only evaluation
execution, analytics and Temporal dispatch; run the actual async worker helper.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from accounts.tests.personal_keys import personal_key_for
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils import evaluations as sdk_evaluations
from sdk.views import evaluations as evaluation_views
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.temporal import evaluations as temporal_evaluations
from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

pytestmark = pytest.mark.django_db

EVAL_URL = "/sdk/api/v1/new-eval/"
EXECUTION_ERROR = "Evaluation execution unavailable"


@pytest.fixture
def verdict_scope(organization, user, workspace, monkeypatch):
    clear_workspace_context()
    request_workspace = Workspace.objects.create(
        name="Verdict status workspace", organization=organization, created_by=user
    )
    assert request_workspace.pk != workspace.pk
    template = EvalTemplate.objects.create(
        name=f"Verdict status {uuid.uuid4().hex}",
        organization=None,
        workspace=None,
        owner="system",
        config={
            "eval_type_id": "TestEval",
            "required_keys": ["input", "expected"],
            "output": "Pass/Fail",
        },
    )
    key = personal_key_for(
        user, organization, workspace=request_workspace, name="Verdict status test"
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(request_workspace.id),
    )
    monkeypatch.setattr(evaluation_views, "track_mixpanel_event", Mock())
    dispatch = Mock()
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", dispatch)
    yield SimpleNamespace(
        user=user,
        organization=organization,
        workspace=request_workspace,
        template=template,
        client=client,
        dispatch=dispatch,
    )
    clear_workspace_context()


@pytest.fixture(params=[True, False], ids=["negative", "passing"])
def verdict_result(request, monkeypatch):
    failure = request.param
    result = {
        "failure": failure,
        "value": "Failed" if failure else "Passed",
        "reason": "mismatch" if failure else "match",
        "data": {"input": "actual", "expected": "different" if failure else "actual"},
        "runtime": 0.01,
        "model": None,
        "metrics": [{"id": "passed", "value": float(not failure)}],
        "metadata": {"evaluator": "equals"},
        "output": "Pass/Fail",
    }
    monkeypatch.setattr(sdk_evaluations, "_run_eval", Mock(return_value=result))
    return result


def _run_sync(scope, inputs):
    return sdk_evaluations._run_standalone_eval(
        eval_template=scope.template,
        inputs=inputs,
        model=None,
        user=scope.user,
        workspace=scope.workspace,
        organization=scope.organization,
        eval_config={},
        error_localizer_enabled=False,
        trace_eval=False,
        custom_eval_name=None,
        span_id=None,
    )


def _post(scope, inputs, *, is_async=None):
    payload = {"eval_name": scope.template.name, "inputs": inputs}
    if is_async is not None:
        payload["is_async"] = is_async
    return scope.client.post(EVAL_URL, payload, format="json")


def _posted_evaluation(response):
    assert response.status_code == 200, response.data
    return response.json()["result"][0]["evaluations"][0]


def _get_result(scope, evaluation_id):
    response = scope.client.get(EVAL_URL, {"eval_id": str(evaluation_id)})
    assert response.status_code == 200, response.data
    return response.json()["result"]


def _assert_preserved(evaluation, scope, result):
    assert evaluation.user_id == scope.user.pk
    assert evaluation.organization_id == scope.organization.pk
    assert evaluation.workspace_id == scope.workspace.pk
    assert evaluation.eval_template_id == scope.template.pk
    assert evaluation.input_data == result["data"]
    assert evaluation.eval_config == {}
    for field in ("value", "reason", "data", "runtime", "model", "metrics", "metadata"):
        assert getattr(evaluation, field) == result[field]
    assert evaluation.output_type == result["output"]
    assert evaluation.output_bool is (not result["failure"])
    assert evaluation.error_message in (None, "")
    assert evaluation.trace_data is None
    assert evaluation.error_localizer_enabled is False


def test_sync_verdict_is_completed_in_storage(verdict_scope, verdict_result):
    result = _run_sync(verdict_scope, verdict_result["data"])
    evaluation = Evaluation.no_workspace_objects.get(id=result["eval_id"])

    _assert_preserved(evaluation, verdict_scope, verdict_result)
    assert result["output"] == verdict_result["value"]
    assert result["reason"] == verdict_result["reason"]
    assert result["output_type"] == verdict_result["output"]
    assert evaluation.status == StatusChoices.COMPLETED


@pytest.mark.parametrize("is_async", [None, False], ids=["default-sync", "explicit-sync"])
def test_sync_post_get_preserves_completed_verdict(
    verdict_scope, verdict_result, is_async
):
    posted = _posted_evaluation(
        _post(verdict_scope, verdict_result["data"], is_async=is_async)
    )
    assert posted["output"] == verdict_result["value"]
    assert posted["reason"] == verdict_result["reason"]
    evaluation = Evaluation.no_workspace_objects.get(id=posted["eval_id"])
    _assert_preserved(evaluation, verdict_scope, verdict_result)

    fetched = _get_result(verdict_scope, posted["eval_id"])
    assert fetched["result"]["eval_id"] == posted["eval_id"]
    assert fetched["result"]["value"] == verdict_result["value"]
    assert fetched["result"]["reason"] == verdict_result["reason"]
    assert fetched["result"]["output_type"] == verdict_result["output"]
    assert fetched["eval_status"] == StatusChoices.COMPLETED
    assert "error_message" not in fetched["result"]
    assert evaluation.status == StatusChoices.COMPLETED
    verdict_scope.dispatch.assert_not_called()


def test_sync_execution_exception_does_not_save_a_completed_result(
    verdict_scope, monkeypatch
):
    monkeypatch.setattr(
        sdk_evaluations,
        "_run_eval",
        Mock(side_effect=sdk_evaluations.StandaloneEvaluationError(EXECUTION_ERROR)),
    )
    inputs = {"input": "actual", "expected": "different"}
    with pytest.raises(sdk_evaluations.StandaloneEvaluationError, match=EXECUTION_ERROR):
        _run_sync(verdict_scope, inputs)
    response = _post(verdict_scope, inputs, is_async=False)

    assert response.status_code == 400, response.data
    assert response.json()["message"] == EXECUTION_ERROR
    assert not Evaluation.no_workspace_objects.filter(
        eval_template=verdict_scope.template
    ).exists()
    verdict_scope.dispatch.assert_not_called()


# The worker deliberately closes DB connections, so use real transactions.
@pytest.mark.django_db(transaction=True)
def test_sync_and_async_verdicts_have_same_completed_lifecycle(
    verdict_scope, verdict_result
):
    sync = _posted_evaluation(_post(verdict_scope, verdict_result["data"], is_async=False))
    asynchronous = _posted_evaluation(
        _post(verdict_scope, verdict_result["data"], is_async=True)
    )
    verdict_scope.dispatch.assert_called_once_with(evaluation_id=asynchronous["eval_id"])
    pending = Evaluation.no_workspace_objects.get(id=asynchronous["eval_id"])
    assert pending.status == StatusChoices.PENDING

    worker_result = _run_single_evaluation_sync(asynchronous["eval_id"])
    assert worker_result["status"] == StatusChoices.COMPLETED, worker_result
    for entry in (sync, asynchronous):
        evaluation = Evaluation.no_workspace_objects.get(id=entry["eval_id"])
        _assert_preserved(evaluation, verdict_scope, verdict_result)
        fetched = _get_result(verdict_scope, entry["eval_id"])
        assert fetched["result"]["value"] == verdict_result["value"]
        assert fetched["result"]["reason"] == verdict_result["reason"]
        assert evaluation.status == StatusChoices.COMPLETED
        assert fetched["eval_status"] == StatusChoices.COMPLETED
        assert "error_message" not in fetched["result"]


@pytest.mark.django_db(transaction=True)
def test_async_execution_exception_stays_failed_on_get(verdict_scope, monkeypatch):
    monkeypatch.setattr(
        sdk_evaluations,
        "_run_eval",
        Mock(side_effect=sdk_evaluations.StandaloneEvaluationError(EXECUTION_ERROR)),
    )
    posted = _posted_evaluation(
        _post(verdict_scope, {"input": "actual", "expected": "different"}, is_async=True)
    )
    verdict_scope.dispatch.assert_called_once_with(evaluation_id=posted["eval_id"])
    worker_result = _run_single_evaluation_sync(posted["eval_id"])
    assert worker_result["error"] == EXECUTION_ERROR
    evaluation = Evaluation.no_workspace_objects.get(id=posted["eval_id"])
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.error_message == EXECUTION_ERROR

    fetched = _get_result(verdict_scope, posted["eval_id"])
    assert fetched["eval_status"] == StatusChoices.FAILED
    assert fetched["result"]["error_message"] == EXECUTION_ERROR
    assert fetched["result"]["value"] is None


# A run that fails before the engine returns has no measured runtime. The
# result still reports runtime 0, so a client that reads runtime as an
# integer can parse it and show the failure message.
@pytest.mark.django_db(transaction=True)
def test_async_failed_result_reports_zero_runtime(verdict_scope, monkeypatch):
    monkeypatch.setattr(
        sdk_evaluations,
        "_run_eval",
        Mock(side_effect=sdk_evaluations.StandaloneEvaluationError(EXECUTION_ERROR)),
    )
    posted = _posted_evaluation(
        _post(verdict_scope, {"input": "actual", "expected": "different"}, is_async=True)
    )
    worker_result = _run_single_evaluation_sync(posted["eval_id"])
    assert worker_result["error"] == EXECUTION_ERROR
    evaluation = Evaluation.no_workspace_objects.get(id=posted["eval_id"])
    assert evaluation.status == StatusChoices.FAILED
    assert evaluation.runtime is None

    fetched = _get_result(verdict_scope, posted["eval_id"])
    assert fetched["eval_status"] == StatusChoices.FAILED
    assert fetched["result"]["runtime"] == 0
    assert fetched["result"]["error_message"] == EXECUTION_ERROR


@pytest.mark.django_db(transaction=True)
def test_async_completed_result_keeps_measured_runtime(verdict_scope, verdict_result):
    posted = _posted_evaluation(
        _post(verdict_scope, verdict_result["data"], is_async=True)
    )
    worker_result = _run_single_evaluation_sync(posted["eval_id"])
    assert worker_result["status"] == StatusChoices.COMPLETED, worker_result

    fetched = _get_result(verdict_scope, posted["eval_id"])
    assert fetched["eval_status"] == StatusChoices.COMPLETED
    assert fetched["result"]["runtime"] == verdict_result["runtime"]
    assert "error_message" not in fetched["result"]
