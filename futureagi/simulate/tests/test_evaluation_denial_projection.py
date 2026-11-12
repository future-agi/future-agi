"""Both legacy simulation projections preserve persisted refusal error markers."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
@pytest.mark.parametrize("projection", ["get_eval_outputs", "get_eval_metrics"])
def test_persisted_denial_is_visible_error(organization, workspace, code, projection):
    from ee.usage.exceptions import UsageLimitExceeded
    from ee.usage.schemas.events import CheckResult
    from model_hub.models.evals_metric import EvalTemplate
    from simulate.models import (
        CallExecution,
        RunTest,
        Scenarios,
        SimulateEvalConfig,
        TestExecution,
    )
    from simulate.serializers.test_execution import CallExecutionDetailSerializer
    from simulate.services.evaluation_denials import record_evaluation_denial
    from tfc.utils.evaluation_denials import EvaluationRateLimited

    template = EvalTemplate.no_workspace_objects.create(
        name="Synthetic denominator",
        organization=organization,
        workspace=workspace,
        config={"output": "float"},
    )
    run = RunTest.no_workspace_objects.create(
        name="Synthetic denial display",
        organization=organization,
        workspace=workspace,
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=run, status="completed"
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="Synthetic display scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    call = CallExecution.no_workspace_objects.create(
        test_execution=execution, scenario=scenario, status="completed"
    )
    config = SimulateEvalConfig.no_workspace_objects.create(
        name="Synthetic denominator",
        eval_template=template,
        run_test=run,
    )
    denial = (
        EvaluationRateLimited()
        if code == "rate_limited"
        else UsageLimitExceeded(
            CheckResult(
                allowed=False,
                error_code=code,
                reason="Synthetic budget paused; resume budget to grade.",
            )
        )
    )
    record_evaluation_denial(call, config, denial)
    call.refresh_from_db()
    config.refresh_from_db()
    stored = call.eval_outputs[str(config.id)]
    assert stored["denial"]["code"] == code
    assert stored["output"] is None
    assert config.status == "Failed"
    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(config.id): config}}
    )
    actual = getattr(serializer, projection)(call)[str(config.id)]
    print("CANDIDATE2", projection, code, "stored=", stored, "projected=", actual)
    assert actual["value"] is None, "Denied grading invented a numeric score"
    assert actual["reason"] == str(denial), "Actionable denial reason was lost"
    assert actual["error"] is True, (
        f"Persisted {code} refusal lost legacy error marker: {actual}"
    )


@pytest.mark.parametrize("projection", ["get_eval_outputs", "get_eval_metrics"])
def test_control_existing_error_sentinel_is_recognized(projection):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    payload = {
        "eval_outputs": {
            "synthetic-id": {
                "status": "Failed",
                "error": "error",
                "reason": "Synthetic error",
                "output": None,
                "output_type": "score",
                "name": "Synthetic control",
            }
        }
    }
    cell = getattr(CallExecutionDetailSerializer(), projection)(payload)["synthetic-id"]
    assert cell["error"] is True
    assert cell["value"] is None
