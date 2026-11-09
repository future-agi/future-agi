"""Policy refusals must not execute, charge, or masquerade as server failures."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ee.usage.exceptions import UsageLimitExceeded
from ee.usage.schemas.events import CheckResult
from sdk.tests.test_evaluations import eval_template, sdk_client, api_key  # noqa: F401
from tfc.utils.evaluation_denials import EvaluationDenied, EvaluationRateLimited


@pytest.fixture
def metering(monkeypatch):
    check = MagicMock(return_value=CheckResult(allowed=True))
    monkeypatch.setitem(sys.modules, "ee.usage.services.metering", SimpleNamespace(check_usage=check))
    return check


@pytest.fixture
def ledger():
    return SimpleNamespace(status="rate_limited", cost=0, deducted_cost=0, config="{}", save=MagicMock())


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["eval", "new-eval"])
@pytest.mark.parametrize("denial", ["rate_limited", "PAYMENT_REQUIRED", "FREE_TIER_LIMIT", "BUDGET_PAUSED", "RATE_LIMITED"])
def test_sdk_policy_denial_is_not_500(sdk_client, eval_template, metering, ledger, route, denial):
    if denial != "rate_limited":
        metering.return_value = CheckResult(allowed=False, error_code=denial, reason="Usage is blocked", dimension="credits", current_usage=10, limit=10)
    with patch("sdk.utils.evaluations.log_and_deduct_cost_for_api_request", return_value=ledger) as log, patch("evaluations.engine.run_eval") as engine, patch("sdk.views.evaluations.logger") as logger, patch("sdk.utils.evaluations.logger") as helper_logger:
        payload = {"inputs": [{"input": "hello"}], "config": {str(eval_template.eval_id): {}}} if route == "eval" else {"eval_name": eval_template.name, "inputs": {"input": "hello", "output": "world"}}
        response = sdk_client.post(f"/sdk/api/v1/{route}/", payload, format="json")
    assert response.status_code == (429 if denial.lower() == "rate_limited" else 402), response.data
    assert response.data["code"] == ("rate_limited" if denial.lower() == "rate_limited" else denial)
    assert "Retry-After" not in response
    if response.status_code == 402:
        assert response.data["dimension"] == "credits"
        assert response.data["current_usage"] == 10
    engine.assert_not_called()
    logger.exception.assert_not_called()
    helper_logger.exception.assert_not_called()
    if denial != "rate_limited":
        log.assert_not_called()
    assert ledger.status == "rate_limited"
    assert ledger.cost == ledger.deducted_cost == 0


@pytest.mark.django_db
@pytest.mark.parametrize("row_status", [None, "error", "success"])
def test_internal_ledger_failure_remains_500(sdk_client, eval_template, metering, ledger, row_status):
    ledger.status = row_status
    with patch("sdk.utils.evaluations.log_and_deduct_cost_for_api_request", return_value=ledger if row_status else None), patch("evaluations.engine.run_eval") as engine, patch("sdk.views.evaluations.logger") as logger:
        response = sdk_client.post("/sdk/api/v1/new-eval/", {"eval_name": eval_template.name, "inputs": {"input": "hello", "output": "world"}}, format="json")
    assert response.status_code == 500
    logger.exception.assert_called_once()
    engine.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["rate", "budget", "internal"])
def test_background_runner_preserves_denial_ledger(eval_template, organization, metering, ledger, kind):
    from model_hub.views.utils import evals

    instance = MagicMock()
    runner = MagicMock()
    runner._create_eval_instance.return_value = instance
    runner.map_fields.return_value = {"input": "hello"}
    if kind == "budget":
        metering.return_value = CheckResult(allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused")
    if kind == "internal":
        ledger.status = "processing"
        instance.run.side_effect = RuntimeError("engine failed")
    with patch.object(evals, "EvaluationRunner", return_value=runner), patch.object(evals, "log_and_deduct_cost_for_api_request", return_value=ledger), patch.object(evals, "logger") as logger:
        with pytest.raises(RuntimeError if kind == "internal" else EvaluationDenied):
            evals.run_eval_func({}, {"input": "hello"}, eval_template, organization, resolved_version=SimpleNamespace(id="version", version_number=1))
    if kind == "internal":
        assert ledger.status == "error"
        logger.exception.assert_called()
    else:
        assert ledger.status == "rate_limited"
        ledger.save.assert_not_called()
        instance.run.assert_not_called()
        logger.exception.assert_not_called()


def test_batch_denial_exposes_completed_prefix():
    from sdk.utils.evaluations import _run_batched_standalone_eval

    completed = {"eval_id": "synthetic-eval", "value": "Pass"}
    with patch("sdk.utils.evaluations._run_standalone_eval", side_effect=[completed, EvaluationRateLimited()]) as run:
        with pytest.raises(EvaluationDenied) as caught:
            _run_batched_standalone_eval(None, {"input": ["one", "two", "three"]}, None, None, None, {}, False, False, None, None)
    assert run.call_count == 2
    from tfc.utils.general_methods import GeneralMethods
    response = caught.value.response(GeneralMethods())
    assert response.status_code == 429
    assert response.data["completed_results"] == [completed]
    assert response.data["failed_index"] == 1


@pytest.mark.django_db
def test_async_denial_survives_polling(sdk_client, user, organization, workspace, eval_template):
    from model_hub.models.evaluation import Evaluation
    from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

    evaluation = Evaluation.objects.create(user=user, organization=organization, workspace=workspace, eval_template=eval_template, input_data={"input": "hello"})
    with patch("sdk.utils.evaluations._run_eval", side_effect=EvaluationRateLimited()):
        result = _run_single_evaluation_sync(str(evaluation.id))
    assert result["status"] == "failed"
    evaluation.refresh_from_db()
    assert evaluation.value is None
    response = sdk_client.get("/sdk/api/v1/new-eval/", {"eval_id": str(evaluation.id)})
    assert response.status_code == 200
    assert response.data["result"]["eval_status"] == "failed"
    assert response.data["result"]["result"]["denial"]["code"] == "rate_limited"


@pytest.mark.parametrize("denial", [EvaluationRateLimited(), UsageLimitExceeded(CheckResult(allowed=False, error_code="BUDGET_PAUSED", reason="Budget paused"))])
def test_direct_evaluator_never_bypasses_denial(denial):
    from ee.tests.test_direct_evaluator_scoring import _direct_evaluator, _eval_template, _EvalConfig

    evaluator = _direct_evaluator()
    template = _eval_template(output="score", output_type_normalized="score")
    with patch("model_hub.views.utils.evals.run_eval_func", side_effect=denial), patch("model_hub.models.evals_metric.EvalTemplate.objects.get", side_effect=ValueError("not saved")), patch.object(evaluator, "_run_eval_direct") as fallback:
        with pytest.raises(EvaluationDenied):
            evaluator._run_eval_with_config(_EvalConfig(template), {}, "output", "prompt")
    fallback.assert_not_called()
