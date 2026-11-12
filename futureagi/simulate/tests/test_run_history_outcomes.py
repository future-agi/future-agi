"""Hosted history must agree with evaluator-aware run detail."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    CallExecution,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]
SUMMARY_FIELDS = ("total", "outcomes", "measured", "pass_rate")


@pytest.fixture
def hosted_history(organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="History outcome agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        organization=organization,
        workspace=workspace,
    )
    run_test = RunTest.objects.create(
        name="History outcome run",
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="History outcome scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
    )
    template = EvalTemplate.objects.create(
        name="History pass/fail",
        organization=organization,
        output_type_normalized="pass_fail",
    )
    config = SimulateEvalConfig.objects.create(
        name="History pass/fail",
        run_test=run_test,
        eval_template=template,
        mapping={"response": "transcript"},
    )

    def make_run(cases):
        execution = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=len(cases),
            total_calls=len(cases),
        )
        job = HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_test=run_test,
            test_execution=execution,
            run_id=uuid4(),
            idempotency_key=uuid4().hex,
            request_digest=uuid4().hex,
            schema_version="1.6",
            seed=1,
            artifact_level="standard",
            max_artifact_bytes=1024,
            deadline_at=timezone.now() + timedelta(hours=1),
            scenario_count=len(cases),
            payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
        )
        attempt = HostedHarnessAttempt.objects.create(
            job=job,
            attempt_number=1,
            token_hash="t" * 64,
            fence_hash="f" * 64,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        calls = []
        for index, case in enumerate(cases):
            receipt_status = case.get("receipt_status", "passed")
            outputs = {
                str(config.id): {
                    "status": case.get("eval_status", "completed"),
                    "output_type": "pass_fail",
                    "output": case.get("output", True),
                }
            }
            call = CallExecution.all_objects.create(
                test_execution=execution,
                scenario=scenario,
                status=case.get("call_status", CallExecution.CallStatus.COMPLETED),
                simulation_call_type=CallExecution.SimulationCallType.TEXT,
                call_metadata={"harness_outcome_status": receipt_status},
                eval_outputs=outputs,
                deleted=case.get("deleted", False),
            )
            registration = HostedHarnessScenario.objects.create(
                job=job, scenario_key=f"case-{index}", call_execution=call
            )
            HostedHarnessReceipt.objects.create(
                job=job,
                attempt=attempt,
                scenario=registration,
                attempt_number=1,
                digest="sha256:" + "0" * 64,
                status=receipt_status,
                body={},
            )
            calls.append(call)
        return execution, calls

    return run_test, config, make_run


def _history(client, run_test, **params):
    response = client.get(f"/simulate/run-tests/{run_test.id}/executions/", params)
    assert response.status_code == 200, response.content
    return {row["id"]: row for row in response.json()["results"]}


def _assert_detail_parity(client, execution, summary):
    response = client.get(f"/simulate/v3/test-executions/{execution.id}/calls/")
    assert response.status_code == 200, response.content
    detail = response.json()["execution"]["summary"]
    assert summary == {field: detail[field] for field in SUMMARY_FIELDS}


@pytest.mark.parametrize(
    "output,removed,passed,failed,rate",
    [(False, False, 0, 1, 0.0), (True, False, 1, 0, 100.0), (False, True, 1, 0, 100.0)],
    ids=["failed-live-eval", "passed-live-eval", "removed-eval"],
)
def test_history_pass_rate_matches_detail_after_platform_evaluation(
    auth_client, hosted_history, output, removed, passed, failed, rate
):
    run_test, config, make_run = hosted_history
    execution, _ = make_run([{"output": output}])
    if removed:
        config.deleted = True
        config.save(update_fields=["deleted"])

    row = _history(auth_client, run_test)[str(execution.id)]
    # This receipt remains passed after grading; it is not the overall result.
    assert row["outcome_passed"] == 1
    assert row["outcome_failed"] == 0
    summary = row.get("outcome_summary")
    assert summary is not None, "History has no evaluator-aware summary"
    assert summary["outcomes"]["passed"] == passed
    assert summary["outcomes"]["failed"] == failed
    assert summary["measured"] == 1
    assert summary["pass_rate"] == rate
    _assert_detail_parity(auth_client, execution, summary)


def test_history_uses_measured_calls_and_keeps_runs_separate(auth_client, hosted_history):
    run_test, _, make_run = hosted_history
    mixed, _ = make_run(
        [
            {"output": True},
            {"output": False},
            {"receipt_status": "errored"},
            {"receipt_status": "skipped"},
            {"call_status": "pending"},
            {"eval_status": "error"},
            {"output": False, "deleted": True},
        ]
    )
    sibling, _ = make_run([{"output": True}])
    empty, _ = make_run([])
    rows = _history(auth_client, run_test)
    summary = rows[str(mixed.id)].get("outcome_summary")
    assert summary is not None, "History has no evaluator-aware summary"
    assert summary["total"] == 6
    assert summary["outcomes"] == {
        "passed": 1,
        "failed": 1,
        "error": 1,
        "inconclusive": 2,
        "queued": 1,
        "in_progress": 0,
    }
    assert summary["measured"] == 2
    assert summary["pass_rate"] == 50.0
    assert rows[str(sibling.id)]["outcome_summary"]["pass_rate"] == 100.0
    assert rows[str(empty.id)]["outcome_summary"]["measured"] == 0
    assert rows[str(empty.id)]["outcome_summary"]["pass_rate"] is None
    _assert_detail_parity(auth_client, mixed, summary)


def test_history_refreshes_after_regrade_without_changing_receipts(
    auth_client, hosted_history
):
    run_test, config, make_run = hosted_history
    execution, calls = make_run([{"output": True}])
    before = _history(auth_client, run_test)[str(execution.id)]
    assert before.get("outcome_summary") is not None
    assert before["outcome_summary"]["pass_rate"] == 100.0

    calls[0].eval_outputs[str(config.id)]["output"] = False
    calls[0].save(update_fields=["eval_outputs"])
    after = _history(auth_client, run_test)[str(execution.id)]
    assert after["outcome_passed"] == before["outcome_passed"] == 1
    assert after["outcome_summary"]["outcomes"]["failed"] == 1
    assert after["outcome_summary"]["pass_rate"] == 0.0
