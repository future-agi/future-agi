from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.harness_run_evals import EVAL_QUEUED_KEY
from simulate.services.scoring_status import (
    REASON_CSAT_CALL_ERRORED,
    REASON_EVAL_CALL_ERRORED,
)

ERRORED = {"harness_outcome_status": "errored"}
CLOSED = {"eval_started": True, "eval_completed": True}
NO_SKIP = SimpleNamespace(processing_skipped=False, processing_skip_reason=None)


@pytest.fixture(autouse=True)
def dispatch():
    with (
        patch(
            "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
        ),
        patch("simulate.tasks.alk_sim.calculate_alk_voice_csat_score.apply_async"),
        patch("simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"),
        patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async"),
        patch("simulate.utils.websocket_notifications.notify_simulation_update"),
        patch("simulate.tasks.chat_sim.notify_simulation_update"),
        patch("simulate.services.alk_simulate_ingestion.notify_simulation_update"),
    ):
        yield


@pytest.fixture
def run_test(db, organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Errored Call Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12813716796",
        inbound=True,
        description="Agent under test for errored calls",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator = SimulatorAgent.objects.create(
        name="Errored Call Simulator",
        prompt="You are a customer.",
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4o",
        initial_message="Hi!",
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Manager transfer",
        description="The caller asks for a manager",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        simulator_agent=simulator,
        status=StatusType.COMPLETED.value,
    )
    rt = RunTest.objects.create(
        name="Errored Call Run Test",
        description="Run for errored call scoring",
        agent_definition=agent,
        simulator_agent=simulator,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="Errored Call Eval",
        config={"required_keys": ["conversation"], "output": "Pass/Fail"},
        organization=organization,
    )


def _config(run_test, eval_template, name, *, required=False):
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=eval_template,
        run_test=run_test,
        mapping={"conversation": "transcript"},
        config={"required": required},
    )


def _run(run_test, status):
    return TestExecution.objects.create(
        run_test=run_test,
        status=status,
        total_scenarios=1,
        agent_definition=run_test.agent_definition,
    )


def _call(execution, *, metadata, eval_outputs=None, **fields):
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.run_test.scenarios.first(),
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata=metadata,
        eval_outputs=eval_outputs or {},
        completed_at=timezone.now(),
        **fields,
    )


@pytest.mark.django_db
def test_csat_is_not_scored_on_a_call_that_connected_and_then_errored(run_test):
    from simulate.tasks import alk_sim

    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={**ERRORED, **CLOSED, "csat_status": "pending"},
        recording_url="https://example.com/rec.wav",
        duration_seconds=35,
    )

    with (
        patch("simulate.tasks.alk_sim.close_old_connections"),
        patch.object(alk_sim, "_run_agent_csat", return_value=2.0) as scorer,
    ):
        alk_sim.calculate_alk_voice_csat_score._original_func(str(call.id))

    scorer.assert_not_called()
    call.refresh_from_db()
    assert call.call_metadata["csat_status"] == "skipped"
    assert call.call_metadata["csat_error"] == REASON_CSAT_CALL_ERRORED
    assert "csat_score" not in (call.conversation_metrics_data or {})
    assert call.overall_score is None
    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.COMPLETED


@pytest.mark.django_db
def test_evals_on_an_errored_call_are_marked_not_run(run_test, eval_template):
    from simulate.services.test_executor import TestExecutor

    run_test.enable_tool_evaluation = True
    run_test.save(update_fields=["enable_tool_evaluation"])
    required = _config(run_test, eval_template, "Prompt conformance", required=True)
    optional = _config(run_test, eval_template, "Naturalness")
    selected = [str(required.id), str(optional.id)]
    harness_check = {
        "name": "transfers_manager_request",
        "output": None,
        "status": "completed",
        "source": "harness",
    }
    stamp = timezone.now().isoformat()
    run = _run(run_test, TestExecution.ExecutionStatus.EVALUATING)
    call = _call(
        run,
        metadata={
            **ERRORED,
            "eval_started": True,
            EVAL_QUEUED_KEY: dict.fromkeys(selected, stamp),
            "csat_status": "skipped",
            "csat_error": REASON_CSAT_CALL_ERRORED,
        },
        eval_outputs={"harness-check": harness_check},
    )

    with (
        patch("simulate.services.test_executor.close_old_connections"),
        patch(
            "simulate.services.test_executor.decide_processing_skip",
            lambda **_: NO_SKIP,
        ),
        patch.object(
            TestExecutor,
            "_get_call_transcript_data",
            return_value={"transcript": "Customer: I want a manager."},
        ),
        patch.object(TestExecutor, "_run_single_simulate_evaluation") as grader,
        patch.object(TestExecutor, "_run_tool_evaluation") as tool_judge,
    ):
        TestExecutor(initialize_voice_service=False)._run_simulate_evaluations(
            call, eval_config_ids=selected
        )

    grader.assert_not_called()
    tool_judge.assert_not_called()
    stored = CallExecution.objects.get(id=call.id)
    for config in (required, optional):
        entry = stored.eval_outputs[str(config.id)]
        assert entry["status"] == "skipped"
        assert entry["skipped"] is True
        assert entry["reason"] == REASON_EVAL_CALL_ERRORED
        assert entry["output"] is None
    assert stored.eval_outputs["harness-check"] == harness_check
    assert stored.call_metadata["eval_completed"] is True
    assert stored.call_metadata["csat_status"] == "skipped"
    run.refresh_from_db()
    assert run.status == TestExecution.ExecutionStatus.COMPLETED


@pytest.mark.django_db
def test_an_errored_call_reads_not_scored_and_a_failed_required_eval_still_fails(
    auth_client, run_test, eval_template
):
    required = _config(run_test, eval_template, "Prompt conformance", required=True)
    required_id = str(required.id)
    run = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    errored = _call(
        run,
        metadata={
            **ERRORED,
            **CLOSED,
            "csat_status": "skipped",
            "csat_error": REASON_CSAT_CALL_ERRORED,
        },
        eval_outputs={
            required_id: {
                "output": None,
                "reason": REASON_EVAL_CALL_ERRORED,
                "output_type": None,
                "name": required.name,
                "status": "skipped",
                "skipped": True,
            }
        },
        error_message="voice_call_not_completed: failed: teardown timed out",
    )
    passed = _call(
        run,
        metadata={
            "harness_outcome_status": "passed",
            **CLOSED,
            "csat_status": "completed",
        },
        eval_outputs={
            required_id: {
                "output": "Failed",
                "output_type": "Pass/Fail",
                "name": required.name,
                "status": "completed",
            }
        },
        conversation_metrics_data={"csat_score": 6},
    )

    response = auth_client.get(f"/simulate/v3/test-executions/{run.id}/calls/")

    assert response.status_code == 200, response.content
    rows = {row["id"]: row for row in response.json()["results"]}
    row = rows[str(errored.id)]
    assert row["outcome"] == "error"
    assert row["outcome_reason"] == "Teardown timed out"
    assert row["csat"] is None
    assert (row["csat_status"], row["csat_reason"]) == (
        "skipped",
        REASON_CSAT_CALL_ERRORED,
    )
    assert row["scoring_status"] == "not_applicable"
    assert row["required_evals_failed"] == []
    evaluation = next(e for e in row["evaluations"] if e["id"] == required_id)
    assert (evaluation["status"], evaluation["passed"], evaluation["score"]) == (
        "skipped",
        None,
        None,
    )
    assert evaluation["reason"] == REASON_EVAL_CALL_ERRORED
    other = rows[str(passed.id)]
    assert other["outcome"] == "failed"
    assert other["required_evals_failed"] == [required.name]
    assert other["csat_status"] == "succeeded"
