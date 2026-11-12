"""Persisted grading refusals remain visible through the authenticated detail API."""

import json
import os
from pathlib import Path

import pytest


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["rate_limited", "BUDGET_PAUSED"])
def test_call_detail_http_preserves_denial(auth_client, organization, workspace, code):
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
    from simulate.services.evaluation_denials import record_evaluation_denial
    from tfc.utils.evaluation_denials import EvaluationRateLimited

    template = EvalTemplate.no_workspace_objects.create(
        name="Synthetic HTTP grading",
        organization=organization,
        workspace=workspace,
        config={"output": "float"},
    )
    run = RunTest.no_workspace_objects.create(
        name="Synthetic HTTP denial run",
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.no_workspace_objects.create(
        name="Synthetic HTTP scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    run.scenarios.add(scenario)
    execution = TestExecution.no_workspace_objects.create(
        run_test=run, status="completed"
    )
    call = CallExecution.no_workspace_objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
    )
    config = SimulateEvalConfig.no_workspace_objects.create(
        name="Synthetic HTTP grading",
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
    response = auth_client.get(f"/simulate/call-executions/{call.id}/")
    assert response.status_code == 200, response.content
    body = response.json()
    if os.environ.get("SIMULATION_DENIAL_UI_FIXTURES"):
        directory = Path(os.environ["SIMULATION_DENIAL_UI_FIXTURES"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{code}.json").write_text(json.dumps(body, indent=2))
    for field in ["eval_outputs", "eval_metrics"]:
        cell = body[field][str(config.id)]
        print("CANDIDATE2_HTTP", code, field, json.dumps(cell))
        assert cell["value"] is None
        assert cell["reason"] == str(denial)
        assert cell["error"] is True, f"HTTP 200 returned denial as non-error: {cell}"
