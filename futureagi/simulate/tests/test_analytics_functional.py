"""Populated-fixture functional tests for analytics + eval-summary read endpoints."""

import csv
import io
import json
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.agent_optimiser import AgentOptimiser
from simulate.models.agent_optimiser_run import AgentOptimiserRun
from simulate.models.hosted_harness import (
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Analytics Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1230001111",
        inbound=True,
        description="Agent for analytics tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Analytics Simulator Agent",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Analytics Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Analytics Test Scenario",
        description="Scenario for analytics tests",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Analytics Run Test",
        description="Run for analytics tests",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=4,
        completed_calls=3,
        failed_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def test_execution_2(db, run_test, simulator_agent, agent_definition):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        completed_calls=2,
        failed_calls=0,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def analytics_call_executions(db, test_execution, scenario):
    """Seed 4 calls: 3 completed with known overall_score values, 1 failed."""
    completed = [
        (0.9, "+9100000001"),
        (0.6, "+9100000002"),
        (0.3, "+9100000003"),
    ]
    calls = []
    for score, phone in completed:
        calls.append(
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=phone,
                status="completed",
                overall_score=score,
                duration_seconds=60,
            )
        )
    calls.append(
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9100000004",
            status="failed",
            duration_seconds=0,
        )
    )
    return calls


def _latency_calls(test_execution, scenario, rows):
    """One completed call per (latency_ms, duration_seconds, offset_seconds)."""
    base = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    return [
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number=f"+92{index:08d}",
            status="completed",
            avg_agent_latency_ms=latency_ms,
            duration_seconds=duration_seconds,
            started_at=(
                None
                if offset_seconds is None
                else base + timedelta(seconds=offset_seconds)
            ),
        )
        for index, (latency_ms, duration_seconds, offset_seconds) in enumerate(rows)
    ]


@pytest.fixture
def run_test_second_execution_calls(db, test_execution_2, scenario):
    """Seed calls on a second TestExecution for run-test aggregate tests."""
    return [
        CallExecution.objects.create(
            test_execution=test_execution_2,
            scenario=scenario,
            phone_number="+9200000001",
            status="completed",
            overall_score=0.8,
            duration_seconds=45,
        ),
        CallExecution.objects.create(
            test_execution=test_execution_2,
            scenario=scenario,
            phone_number="+9200000002",
            status="completed",
            overall_score=0.4,
            duration_seconds=30,
        ),
    ]


@pytest.fixture
def pass_fail_template(db, organization):
    return EvalTemplate.objects.create(
        name="Quality Gate",
        config={"output": "Pass/Fail"},
        organization=organization,
        output_type_normalized="pass_fail",
    )


@pytest.fixture
def score_template(db, organization):
    return EvalTemplate.objects.create(
        name="Accuracy Score",
        config={"output": "score"},
        organization=organization,
        output_type_normalized="percentage",
    )


@pytest.fixture
def pass_fail_eval_config(db, run_test, pass_fail_template):
    return SimulateEvalConfig.objects.create(
        name="Quality Gate", eval_template=pass_fail_template, run_test=run_test
    )


@pytest.fixture
def score_eval_config(db, run_test, score_template):
    return SimulateEvalConfig.objects.create(
        name="Accuracy Score", eval_template=score_template, run_test=run_test
    )


@pytest.fixture
def eval_summary_te1_calls(
    db, test_execution, scenario, pass_fail_eval_config, score_eval_config
):
    """Seed test_execution with mix of pass/fail + score evals.

    Pass/Fail: 2 Passed + 1 Failed -> pass_rate 66.67, fail_rate 33.33
    Score: 0.8 + 0.6 + 0.4 (scaled x100) -> avg 60.0
    """
    pf_id = str(pass_fail_eval_config.id)
    score_id = str(score_eval_config.id)

    outputs = [
        ("Passed", 0.8),
        ("Passed", 0.6),
        ("Failed", 0.4),
    ]
    calls = []
    for i, (verdict, score) in enumerate(outputs):
        calls.append(
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+930000000{i}",
                status="completed",
                eval_outputs={
                    pf_id: {
                        "name": "Quality Gate",
                        "output": verdict,
                        "output_type": "Pass/Fail",
                    },
                    score_id: {
                        "name": "Accuracy Score",
                        "output": score,
                        "output_type": "score",
                    },
                },
            )
        )
    return calls


@pytest.fixture
def eval_summary_te2_calls(
    db, test_execution_2, scenario, pass_fail_eval_config, score_eval_config
):
    """Second execution with different mix so comparison shows deltas.

    Pass/Fail: 2 Passed -> pass_rate 100.0
    Score: 1.0 + 0.9 -> avg 95.0
    """
    pf_id = str(pass_fail_eval_config.id)
    score_id = str(score_eval_config.id)

    outputs = [
        ("Passed", 1.0),
        ("Passed", 0.9),
    ]
    calls = []
    for i, (verdict, score) in enumerate(outputs):
        calls.append(
            CallExecution.objects.create(
                test_execution=test_execution_2,
                scenario=scenario,
                phone_number=f"+940000000{i}",
                status="completed",
                eval_outputs={
                    pf_id: {
                        "name": "Quality Gate",
                        "output": verdict,
                        "output_type": "Pass/Fail",
                    },
                    score_id: {
                        "name": "Accuracy Score",
                        "output": score,
                        "output_type": "score",
                    },
                },
            )
        )
    return calls


def _make_other_workspace_test_execution(
    organization, user, agent_definition, simulator_agent
):
    """Create a RunTest + TestExecution in a non-default workspace of the
    same org so cross-workspace scoping is triggered by the default manager
    (which drops non-default workspaces from the current context)."""
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Analytics Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_run_test = RunTest.no_workspace_objects.create(
        name="Hidden Analytics Run Test",
        description="Run test in another workspace",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=other_workspace,
    )
    hidden_test_execution = TestExecution.no_workspace_objects.create(
        run_test=hidden_run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )
    return hidden_run_test, hidden_test_execution


# ============================================================================
# TestExecutionAnalyticsView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionAnalyticsView:
    """GET /simulate/test-executions/<uuid>/analytics/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/analytics/"

    def test_analytics_populated_response_shape_and_values(
        self, auth_client, test_execution, analytics_call_executions
    ):
        response = auth_client.get(self.URL_TEMPLATE.format(test_execution.id))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()

        assert set(body.keys()) == {
            "fail_rate_over_test_runs",
            "evaluation_categories_over_test_runs",
            "metadata",
        }

        fail_rate_chart = body["fail_rate_over_test_runs"]
        assert fail_rate_chart["title"] == "Fail Rate Over Test Runs"
        assert fail_rate_chart["x_axis_label"] == "Test Runs"
        assert fail_rate_chart["y_axis_label"] == "Fail Rate (%)"
        assert fail_rate_chart["chart_type"] == "scatter"
        assert isinstance(fail_rate_chart["data"], list)
        assert fail_rate_chart["data"], "expected at least one fail-rate point"

        total_failed = sum(p["failed_calls"] for p in fail_rate_chart["data"])
        total_across_batches = sum(p["total_calls"] for p in fail_rate_chart["data"])
        assert total_failed == 1
        assert total_across_batches == 4

        eval_chart = body["evaluation_categories_over_test_runs"]
        assert eval_chart["chart_type"] == "line"
        total_scored = sum(p["scored_calls"] for p in eval_chart["data"])
        assert total_scored == 3

        metadata = body["metadata"]
        assert metadata["total_calls"] == 4
        assert metadata["test_execution_id"] == str(test_execution.id)
        assert metadata["test_execution_name"] == test_execution.run_test.name
        assert metadata["total_test_runs"] == len(fail_rate_chart["data"])

    def test_analytics_empty_execution_returns_empty_series(
        self, auth_client, test_execution
    ):
        """No calls -> zero total_calls and empty data lists."""
        response = auth_client.get(self.URL_TEMPLATE.format(test_execution.id))
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["metadata"]["total_calls"] == 0
        assert body["fail_rate_over_test_runs"]["data"] == []
        assert body["evaluation_categories_over_test_runs"]["data"] == []

    def test_analytics_other_workspace_returns_404(
        self, auth_client, organization, user, agent_definition, simulator_agent
    ):
        _, hidden_te = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        response = auth_client.get(self.URL_TEMPLATE.format(hidden_te.id))
        # TestExecutionAnalyticsView does not apply run_test_workspace_filter,
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_analytics_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.get(self.URL_TEMPLATE.format(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestRunResultsV3Views:
    """The v3 run-results surface remains independent from legacy contracts."""

    @pytest.mark.parametrize(
        (
            "output_type",
            "output",
            "reverse",
            "threshold",
            "choice_scores",
            "status_value",
            "expected",
            "expected_score",
        ),
        [
            ("pass_fail", "Passed", True, 0.5, {}, "completed", "passed", 1),
            ("pass_fail", "Failed", True, 0.5, {}, "completed", "failed", 0),
            ("pass_fail", "Failed", True, 0, {}, "completed", "failed", 0),
            ("pass_fail", {"failure": True}, False, 0, {}, "completed", "failed", 0),
            ("pass_fail", "yes", False, 0.5, {}, "completed", "passed", 1),
            ("pass_fail", "no", False, 0.5, {}, "completed", "failed", 0),
            ("pass_fail", 0.3, False, 0.5, {}, "completed", "passed", 1),
            ("percentage", 0.9, True, 0.5, {}, "completed", "failed", 0.1),
            ("percentage", "0.8", False, 0.7, {}, "completed", "passed", 0.8),
            ("percentage", {"result": 0.8}, False, 0.7, {}, "completed", "passed", 0.8),
            ("percentage", -2, True, 0.5, {}, "completed", "passed", 1),
            ("percentage", 200, False, 0.5, {}, "completed", "passed", 1),
            (
                "deterministic",
                "pass",
                False,
                0.5,
                {"pass": 0},
                "completed",
                "failed",
                0,
            ),
            (
                "deterministic",
                ["good", "bad"],
                False,
                0.6,
                {"good": 1.0, "bad": 0.0},
                "completed",
                "failed",
                0.5,
            ),
            ("pass_fail", "Passed", False, 0.5, {}, "error", "inconclusive", None),
        ],
    )
    def test_configured_eval_verdict_agrees_across_rows_filters_and_analytics(
        self,
        auth_client,
        test_execution,
        scenario,
        pass_fail_template,
        pass_fail_eval_config,
        output_type,
        output,
        reverse,
        threshold,
        choice_scores,
        status_value,
        expected,
        expected_score,
    ):
        from simulate.services.run_results_v3 import call_outcome
        from simulate.services.run_results_v3_queries import run_calls_queryset

        pass_fail_template.output_type_normalized = output_type
        pass_fail_template.choice_scores = choice_scores
        pass_fail_template.save(
            update_fields=["output_type_normalized", "choice_scores"]
        )
        pass_fail_eval_config.config = {
            "run_config": {
                "reverse_output": reverse,
                "pass_threshold": threshold,
            }
        }
        pass_fail_eval_config.save(update_fields=["config"])
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9312345678",
            status="completed",
            call_metadata={"harness_outcome_status": "passed"},
            eval_outputs={
                str(pass_fail_eval_config.id): {
                    "name": "Quality Gate",
                    "output": output,
                    "status": status_value,
                }
            },
        )
        assert (
            call_outcome(call, {str(pass_fail_eval_config.id): pass_fail_eval_config})
            == expected
        )
        assert (
            run_calls_queryset(test_execution).get(pk=call.pk).result_outcome
            == expected
        )

        base = f"/simulate/v3/test-executions/{test_execution.id}"
        rows_response = auth_client.get(f"{base}/calls/")
        assert rows_response.status_code == 200
        row = rows_response.json()["results"][0]
        assert row["outcome"] == expected
        if expected_score is None:
            assert row["evaluations"][0]["score"] is None
        else:
            assert row["evaluations"][0]["score"] == pytest.approx(expected_score)
        assert row["evaluations"][0]["passed"] == (
            True if expected == "passed" else False if expected == "failed" else None
        )

        filtered = auth_client.get(
            f"{base}/calls/", {"filters": json.dumps({"status": [expected]})}
        )
        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1

        analytics = auth_client.get(f"{base}/analytics/")
        assert analytics.status_code == 200
        body = analytics.json()
        assert body["summary"]["outcomes"][expected] == 1
        evaluation = next(
            item
            for item in body["evaluations"]
            if item["id"] == str(pass_fail_eval_config.id)
        )
        assert evaluation["passed"] == int(expected == "passed")
        assert evaluation["failed"] == int(expected == "failed")
        assert evaluation["errored"] == int(status_value == "error")
        if expected_score is None:
            assert evaluation["average_score"] is None
        else:
            assert evaluation["average_score"] == pytest.approx(expected_score)

    @pytest.mark.parametrize(
        "output",
        [
            {"score": 1.0, "choice": "Good"},
            {"score": 1.0, "choices": ["Good"]},
        ],
    )
    def test_formatted_choice_override_agrees_across_run_results(
        self,
        auth_client,
        test_execution,
        scenario,
        pass_fail_template,
        pass_fail_eval_config,
        output,
    ):
        from simulate.services.run_results_v3 import call_outcome
        from simulate.services.run_results_v3_queries import run_calls_queryset

        pass_fail_template.output_type_normalized = "deterministic"
        pass_fail_template.choice_scores = {"Good": 1.0}
        pass_fail_template.save(
            update_fields=["output_type_normalized", "choice_scores"]
        )
        pass_fail_eval_config.config = {
            "run_config": {"choice_scores": {"Good": 0.0}, "pass_threshold": 0.5}
        }
        pass_fail_eval_config.save(update_fields=["config"])
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9312345678",
            status="completed",
            call_metadata={"harness_outcome_status": "passed"},
            eval_outputs={
                str(pass_fail_eval_config.id): {
                    "name": "Quality Gate",
                    "output": output,
                    "status": "completed",
                }
            },
        )
        configs = {str(pass_fail_eval_config.id): pass_fail_eval_config}
        assert call_outcome(call, configs) == "failed"
        assert (
            run_calls_queryset(test_execution).get(pk=call.pk).result_outcome
            == "failed"
        )

        base = f"/simulate/v3/test-executions/{test_execution.id}"
        rows = auth_client.get(f"{base}/calls/")
        assert rows.status_code == 200
        row = rows.json()["results"][0]
        assert row["outcome"] == "failed"
        assert row["evaluations"][0]["score"] == pytest.approx(0.0)
        assert row["evaluations"][0]["passed"] is False

        failed = auth_client.get(
            f"{base}/calls/", {"filters": json.dumps({"status": ["failed"]})}
        )
        assert failed.status_code == 200
        assert failed.json()["count"] == 1
        passed = auth_client.get(
            f"{base}/calls/", {"filters": json.dumps({"status": ["passed"]})}
        )
        assert passed.status_code == 200
        assert passed.json()["count"] == 0

        analytics = auth_client.get(f"{base}/analytics/")
        assert analytics.status_code == 200
        body = analytics.json()
        assert body["summary"]["outcomes"]["failed"] == 1
        evaluation = next(
            item
            for item in body["evaluations"]
            if item["id"] == str(pass_fail_eval_config.id)
        )
        assert evaluation["passed"] == 0
        assert evaluation["failed"] == 1
        assert evaluation["average_score"] == pytest.approx(0.0)

    def test_dataset_scenario_uses_name_without_merging_distinct_rows(
        self, auth_client, test_execution, scenario, dataset_for_scenario
    ):
        first_row = Row.objects.filter(dataset=dataset_for_scenario).first()
        second_row = Row.objects.create(dataset=dataset_for_scenario, order=1)
        for index, row in enumerate((first_row, second_row)):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                row_id=row.id,
                phone_number=f"+931234567{index}",
                status="completed",
                call_metadata={"harness_outcome_status": "passed"},
            )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["scenario_risk"]) == 2
        assert {item["scenario"] for item in body["scenario_risk"]} == {
            scenario.name
        }
        assert len({item["scenario_key"] for item in body["scenario_risk"]}) == 2
        assert len({item["scenario_key"] for item in body["reliability"]["rows"]}) == 2

    @pytest.mark.parametrize(
        "value,eval_status,expected",
        [
            ("Passed", "completed", 1),
            (" Failed ", None, 0),
            (True, None, 1),
            (False, "completed", 0),
            ("SUCCESSFUL", "completed", 1),
            (80, "completed", 0.8),
            (0.6, None, 0.6),
            (0, "completed", 0),
            ("Passed", "pending", None),
            (75, "ERROR", None),
            ("Failed", "skipped", None),
            ("NaN", "completed", None),
            ({"score": 1.0, "choice": "always"}, "completed", 1),
            ({"score": 0.4, "choice": "sometimes"}, None, 0.4),
            ({"score": 80, "choice": "mostly"}, "completed", 0.8),
            ({"score": 1, "choice": "always"}, "completed", 1),
            ({"score": 0, "choice": "never"}, "completed", 0),
            ({"choice": "always"}, "completed", None),
            ({"score": "high", "choice": "x"}, "completed", None),
            ({"score": "0.8", "choice": "x"}, "completed", None),
            ({"score": True, "choice": "x"}, "completed", None),
            ({"score": None, "choice": "x"}, "completed", None),
            ({"score": {"value": 1}, "choice": "x"}, "completed", None),
            ({"score": 0.5, "choices": ["a", "b"]}, "completed", 0.5),
            ({"score": 1.0, "choice": "always"}, "error", None),
        ],
    )
    def test_group_evaluation_scores_match_rows(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        value,
        eval_status,
        expected,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"use_case": "Aggregation regression"}
        call.eval_outputs = {
            "native": {
                "source": "harness",
                "name": "Native evaluation",
                "output": value,
                "output_type": "choices" if isinstance(value, dict) else "Pass/Fail",
            }
        }
        if eval_status is not None:
            call.eval_outputs["native"]["status"] = eval_status
        call.save(update_fields=["call_metadata", "eval_outputs"])
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {
                "group_by": "goal",
                "filters": json.dumps({"goal": ["Aggregation regression"]}),
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["results"][0]["evaluations"][0]["score"] == expected
        aggregate = body["groups"][0]["aggregates"]["evaluations"]["native"]
        assert aggregate == {
            "scored": int(expected is not None),
            "score_sum": expected or 0,
        }

    def test_configured_choices_eval_scores_rows_and_groups(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        score_eval_config,
    ):
        score_id = str(score_eval_config.id)
        call = analytics_call_executions[0]
        call.call_metadata = {"use_case": "Configured choices"}
        call.eval_outputs = {
            score_id: {
                "name": "Accuracy Score",
                "output": {"score": 1.0, "choice": "always"},
                "output_type": "choices",
                "status": "completed",
            }
        }
        call.save(update_fields=["call_metadata", "eval_outputs"])
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {
                "group_by": "goal",
                "filters": json.dumps({"goal": ["Configured choices"]}),
            },
        )
        assert response.status_code == 200
        body = response.json()
        entry = next(
            e for e in body["results"][0]["evaluations"] if e["id"] == score_id
        )
        assert entry["score"] == 1
        assert body["groups"][0]["aggregates"]["evaluations"][score_id] == {
            "scored": 1,
            "score_sum": 1,
        }

    @pytest.mark.parametrize(
        ("stop_latencies", "ai_interruptions", "avg_stop_latency", "avg_interruptions"),
        [
            ((1281, 0), (2, 1), 640.5, 1.5),
            ((1281, None), (2, None), 1281, 2),
            ((None, None), (None, None), None, None),
        ],
    )
    def test_group_aggregates_cover_all_filtered_pages(
        self,
        auth_client,
        test_execution,
        eval_summary_te1_calls,
        pass_fail_eval_config,
        score_eval_config,
        stop_latencies,
        ai_interruptions,
        avg_stop_latency,
        avg_interruptions,
    ):
        for i, call in enumerate(eval_summary_te1_calls):
            call.call_metadata = {
                "use_case": "Refunds" if i < 2 else "Excluded",
                "persona": {"name": f"Persona {i}"},
                "row_data": {"situation": f"Situation {i}", "outcome": "Resolved"},
                "conversation_branch": "same-branch",
            }
            call.overall_score = 6 + i * 2
            call.avg_agent_latency_ms = 100 + i * 100
            call.avg_stop_time_after_interruption_ms = (
                stop_latencies[i] if i < 2 else 9000
            )
            call.ai_interruption_count = ai_interruptions[i] if i < 2 else 100
            call.conversation_metrics_data = {
                "csat_score": 6 + i * 2,
                "turn_count": 2 + i * 2,
                "total_tokens": 100 + i * 100,
            }
            call.save()
        query = {
            "group_by": "goal",
            "page_size": 1,
            "filters": json.dumps({"goal": ["Refunds"]}),
        }
        first = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/", query
        )
        second = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {**query, "page": 2},
        )
        assert first.status_code == second.status_code == 200
        group = first.json()["groups"][0]
        assert len(group["result_ids"]) == 1
        assert group["total"] == 2
        assert group["aggregates"] == second.json()["groups"][0]["aggregates"]
        assert group["aggregates"] == {
            "csat": 7,
            "turns": 3,
            "latency_ms": 150,
            "avg_stop_time_after_interruption": avg_stop_latency,
            "ai_interruptions": avg_interruptions,
            "tokens": 300,
            "evaluations": {
                str(pass_fail_eval_config.id): {"scored": 2, "score_sum": 2},
                str(score_eval_config.id): {
                    "scored": 2,
                    "score_sum": pytest.approx(1.4),
                },
            },
        }

    @pytest.mark.parametrize(
        "outputs,expected",
        [
            pytest.param(
                [
                    {"output": {"score": 0.6, "choice": "x"}, "output_type": "score"},
                    {"output": {"score": 1.0, "choice": "y"}, "output_type": "choices"},
                    {
                        "output": {"score": "high", "choice": "z"},
                        "output_type": "choices",
                    },
                ],
                0.8,
                id="choice-score-objects",
            ),
            pytest.param(
                [
                    {"output": 0.2, "output_type": "score"},
                    {"output": {"score": 0.6, "choice": "x"}, "output_type": "score"},
                ],
                0.4,
                id="plain-and-object-together",
            ),
            pytest.param(
                [
                    {"output": {"score": 0.3, "choice": "x"}, "output_type": "numeric"},
                    {
                        "output": {"score": 0.5, "choice": "y"},
                        "output_type": "Pass/Fail",
                    },
                ],
                0.4,
                id="object-under-any-output-type",
            ),
            pytest.param(
                [{"output": {"score": 80, "choice": "x"}, "output_type": "choices"}],
                0.8,
                id="object-normalized-like-configured-row",
            ),
            pytest.param(
                [{"output": "0.8", "output_type": "score"}],
                0.8,
                id="plain-text-number-kept",
            ),
            pytest.param(
                [
                    {"output": 0.7, "output_type": "choices"},
                    {"output": 0.2, "output_type": "score"},
                ],
                0.45,
                id="configured-numbers-independent-of-stored-type-tag",
            ),
        ],
    )
    def test_analytics_average_score(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        score_eval_config,
        outputs,
        expected,
    ):
        score_id = str(score_eval_config.id)
        for call, output in zip(analytics_call_executions, outputs, strict=False):
            call.eval_outputs = {score_id: {"name": "Accuracy Score", **output}}
            call.save(update_fields=["eval_outputs"])
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        entry = next(
            row for row in response.json()["evaluations"] if row["id"] == score_id
        )
        assert entry["average_score"] == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("stop_latency", "ai_interruptions"),
        [(1281, 2), (0, 0), (None, None)],
    )
    def test_calls_include_interruption_metrics(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        stop_latency,
        ai_interruptions,
    ):
        call = analytics_call_executions[0]
        call.avg_stop_time_after_interruption_ms = stop_latency
        call.ai_interruption_count = ai_interruptions
        call.save(
            update_fields=[
                "avg_stop_time_after_interruption_ms",
                "ai_interruption_count",
            ]
        )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        )

        assert response.status_code == status.HTTP_200_OK
        row = next(
            item for item in response.json()["results"] if item["id"] == str(call.id)
        )
        assert row["avg_stop_time_after_interruption"] == stop_latency
        assert row["ai_interruption_count"] == ai_interruptions

    def test_calls_returns_normalized_rows_groups_and_facets(
        self, auth_client, test_execution, analytics_call_executions
    ):
        test_execution.execution_metadata = {
            "selected_scenario_keys": ["routine-return"],
            "trials": 2,
        }
        test_execution.trials = 2
        test_execution.save(update_fields=["execution_metadata", "trials"])
        passed_call = analytics_call_executions[0]
        passed_call.call_metadata = {
            "harness_outcome_status": "passed",
            "harness_scenario_key": "routine-return",
            "harness_trial_index": 2,
            "use_case": "Returns",
            "row_data": {
                "persona": json.dumps(
                    {
                        "name": "Careful caller",
                        "voice": "US female",
                        "age": 68,
                        "traits": ["polite", "hard of hearing"],
                        "scripted_caller": None,
                    }
                ),
                "situation": "Caller needs help with a return.",
                "outcome": "Return is completed",
            },
            "hosted_harness_receipt": {
                "scenario_key": "routine-return",
                "sub_goals": [{"name": "identity_verified", "held": True}],
            },
        }
        passed_call.conversation_metrics_data = {
            "total_tokens": 120,
            "turn_count": 4,
        }
        passed_call.avg_agent_latency_ms = 240
        passed_call.cost_cents = 12
        passed_call.save()

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"group_by": "goal", "filters": json.dumps({"status": ["passed"]})},
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["goal"] == "Returns"
        assert row["persona"] == "Careful caller"
        assert row["persona_details"] == {
            "name": "Careful caller",
            "voice": "US female",
            "age": "68",
            "traits": ["polite", "hard of hearing"],
        }
        assert row["sub_goals"] == ["identity_verified"]
        assert row["scenario_details"] == "Caller needs help with a return."
        assert row["ideal_outcome"] == "Return is completed"
        assert row["conversation_branch"] == "routine-return"
        assert row["source_scenario_key"] == "routine-return"
        assert row["trial_index"] == 2
        assert row["outcome"] == "passed"
        assert row["latency_ms"] == 240.0
        assert row["turn_count"] == 4
        assert row["tokens"] == 120
        assert row["cost_cents"] is None
        analytics = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert analytics.status_code == 200
        assert analytics.json()["summary"]["cost_cents"]["measured"] == 0
        assert body["groups"][0]["key"] == "Returns"
        assert body["groups"][0]["result_ids"] == [str(passed_call.id)]
        assert {item["value"] for item in body["facets"]["status"]} == {
            "passed",
            "error",
            "inconclusive",
        }
        assert body["facets"]["sub_goal"] == [
            {"value": "identity_verified", "count": 1}
        ]
        assert body["execution"]["id"] == str(test_execution.id)
        assert body["execution"]["selected_scenario_keys"] == ["routine-return"]
        assert body["execution"]["trials"] == 2

    def test_harness_error_is_not_green_when_transport_completed(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[0]
        call.status = CallExecution.CallStatus.COMPLETED
        call.call_metadata = {"harness_outcome_status": "error"}
        call.eval_outputs = {}
        call.save(update_fields=["status", "call_metadata", "eval_outputs"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        )

        assert response.status_code == status.HTTP_200_OK
        row = next(
            item for item in response.json()["results"] if item["id"] == str(call.id)
        )
        assert row["outcome"] == "error"
        assert row["harness_outcome_status"] == "error"
        assert row["execution_status"] == "completed"

    def test_persona_is_not_a_grouping_option(
        self, auth_client, test_execution, analytics_call_executions
    ):
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"group_by": "persona"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_calls_filter_by_goal_sub_goal_and_status(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {
            "harness_outcome_status": "passed",
            "goal": "Returns",
            "hosted_harness_receipt": {"sub_goals": [{"name": "identity_verified"}]},
        }
        call.save(update_fields=["call_metadata"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {
                "filters": json.dumps(
                    {
                        "goal": ["Returns"],
                        "sub_goal": ["identity_verified"],
                        "status": ["passed"],
                    }
                )
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert [row["id"] for row in response.json()["results"]] == [str(call.id)]

    def test_calls_filter_by_call_execution_ids(
        self, auth_client, test_execution, analytics_call_executions
    ):
        wanted = sorted(str(call.id) for call in analytics_call_executions[:2])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"filters": json.dumps({"call_execution_id": wanted})},
        )

        assert response.status_code == status.HTTP_200_OK
        assert sorted(row["id"] for row in response.json()["results"]) == wanted
        assert response.json()["count"] == 2
        # The status chips count the handed-off calls, not the whole run.
        assert sum(item["count"] for item in response.json()["facets"]["status"]) == 2

    def test_calls_filter_rejects_malformed_call_execution_id(
        self, auth_client, test_execution, analytics_call_executions
    ):
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"filters": json.dumps({"call_execution_id": ["not-a-uuid"]})},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_dataset_goal_is_used_consistently_for_rows_groups_facets_and_filters(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        dataset_for_scenario,
    ):
        goal_column = Column.objects.create(
            dataset=dataset_for_scenario,
            name="use_case",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        row = Row.objects.filter(dataset=dataset_for_scenario).first()
        Cell.objects.create(
            dataset=dataset_for_scenario,
            column=goal_column,
            row=row,
            value="Dataset-only goal",
        )
        call = analytics_call_executions[0]
        call.row_id = row.id
        call.call_metadata = {"harness_outcome_status": "passed"}
        call.save(update_fields=["row_id", "call_metadata"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {
                "group_by": "goal",
                "filters": json.dumps({"goal": ["Dataset-only goal"]}),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert [result["id"] for result in body["results"]] == [str(call.id)]
        assert body["results"][0]["goal"] == "Dataset-only goal"
        assert body["groups"][0]["key"] == "Dataset-only goal"
        assert {item["value"] for item in body["facets"]["goal"]} >= {
            "Dataset-only goal"
        }

    @pytest.mark.parametrize("layout", ["child_run", "direct_run"])
    def test_hosted_calls_group_by_their_authored_scenario_axes(
        self,
        layout,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        def job(**fields):
            return HostedHarnessJob.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                run_id=uuid.uuid4(),
                idempotency_key=uuid.uuid4().hex,
                request_digest=uuid.uuid4().hex,
                schema_version="1.6",
                seed=1,
                artifact_level="standard",
                max_artifact_bytes=1024,
                deadline_at=timezone.now() + timedelta(hours=1),
                scenario_count=1,
                payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
                **fields,
            )

        call = analytics_call_executions[0]
        authored = {
            "scenario_key": "pin-reset",
            "use_case": "Verify the caller's guest PIN",
            "sub_goals": ["pin_verified", "exact_greeting"],
            "persona": {"accent": "Indian", "age_group": "40-50"},
            "coverage": {"overlay": "prompt_injection", "task": "authenticate_pin"},
        }
        if layout == "child_run":
            # The suite lives on the environment; the run's call carries its key.
            environment = job()
            job(
                environment=environment,
                run_test=test_execution.run_test,
                test_execution=test_execution,
            )
            HostedHarnessScenario.no_workspace_objects.create(
                job=environment, **authored
            )
            call.call_metadata = {"harness_scenario_key": "pin-reset"}
            call.save(update_fields=["call_metadata"])
        else:
            # begin_scenarios(): the suite lives on the run's own job and each
            # registration is linked to its call, which carries no key.
            direct = job(
                run_test=test_execution.run_test, test_execution=test_execution
            )
            HostedHarnessScenario.no_workspace_objects.create(
                job=direct, call_execution=call, **authored
            )
        url = f"/simulate/v3/test-executions/{test_execution.id}/calls/"

        expected = {
            "goal": [
                ("Verify the caller's guest PIN", "Verify the caller's guest PIN")
            ],
            "sub_goal": [
                ("exact_greeting", "Exact greeting"),
                ("pin_verified", "Pin verified"),
            ],
            "accent": [("Indian", "Indian")],
            "age": [("40-50", "40-50")],
            "attack": [("prompt_injection", "Injected instruction")],
            "task": [("authenticate_pin", "Authenticate pin")],
        }
        for axis, levels in expected.items():
            body = auth_client.get(url, {"group_by": axis}).json()
            groups = [g for g in body["groups"] if str(call.id) in g["result_ids"]]
            assert sorted((g["key"], g["label"]) for g in groups) == levels, axis
            assert all(g["total"] == 1 for g in groups), axis
            # A call with no authored scenario keeps the existing goal fallbacks.
            others = [g for g in body["groups"] if str(call.id) not in g["result_ids"]]
            assert axis == "goal" or all(g["key"] == "Ungrouped" for g in others), axis

            for key, _ in levels:
                scoped = auth_client.get(
                    url, {"group_by": axis, "group_key": key}
                ).json()
                assert [row["id"] for row in scoped["results"]] == [str(call.id)], axis
        ungrouped = auth_client.get(
            url, {"group_by": "sub_goal", "group_key": "Ungrouped"}
        ).json()
        assert str(call.id) not in [row["id"] for row in ungrouped["results"]]
        assert ungrouped["count"] == len(analytics_call_executions) - 1
        row = next(
            r for r in auth_client.get(url).json()["results"] if r["id"] == str(call.id)
        )
        assert row["goal"] == "Verify the caller's guest PIN"

    def test_hosted_call_reads_its_own_run_scenario_before_the_environment_suite(
        self,
        auth_client,
        organization,
        workspace,
        test_execution,
        test_execution_2,
        analytics_call_executions,
    ):
        def job(**fields):
            return HostedHarnessJob.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                run_id=uuid.uuid4(),
                idempotency_key=uuid.uuid4().hex,
                request_digest=uuid.uuid4().hex,
                schema_version="1.6",
                seed=1,
                artifact_level="standard",
                max_artifact_bytes=1024,
                deadline_at=timezone.now() + timedelta(hours=1),
                scenario_count=1,
                payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
                **fields,
            )

        # Two runs of the same environment, each re-authoring the same keys.
        environment = job()
        own_run = job(
            environment=environment,
            run_test=test_execution.run_test,
            test_execution=test_execution,
        )
        sibling_run = job(
            environment=environment,
            run_test=test_execution.run_test,
            test_execution=test_execution_2,
        )
        suites = (
            (environment, "Environment goal"),
            (sibling_run, "Sibling run goal"),
            (own_run, "Own run goal"),
        )
        for owner, goal in suites:
            HostedHarnessScenario.no_workspace_objects.create(
                job=owner, scenario_key="pin-reset", use_case=goal
            )
        for owner, goal in suites[:2]:
            HostedHarnessScenario.no_workspace_objects.create(
                job=owner, scenario_key="refund", use_case=goal
            )
        own_key, environment_key, stray_key = analytics_call_executions[:3]
        for call, key in (
            (own_key, "pin-reset"),
            (environment_key, "refund"),
            (stray_key, "never-authored"),
        ):
            call.call_metadata = {"harness_scenario_key": key}
            call.save(update_fields=["call_metadata"])

        url = f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        rows = {row["id"]: row for row in auth_client.get(url).json()["results"]}
        assert rows[str(own_key.id)]["goal"] == "Own run goal"
        # A key the run never re-authored comes from the environment, not from
        # a sibling run of the same test, however recent that run is.
        assert rows[str(environment_key.id)]["goal"] == "Environment goal"
        # A key no job authored keeps the plain scenario fallback.
        assert rows[str(stray_key.id)]["goal"] == stray_key.scenario.name

        groups = auth_client.get(url, {"group_by": "goal"}).json()["groups"]
        by_key = {group["key"]: group["result_ids"] for group in groups}
        assert by_key["Own run goal"] == [str(own_key.id)]
        assert by_key["Environment goal"] == [str(environment_key.id)]
        assert "Sibling run goal" not in by_key
        assert str(stray_key.id) in by_key[stray_key.scenario.name]

    def test_non_numeric_json_metrics_do_not_break_list_or_analytics(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        score_eval_config,
    ):
        call = analytics_call_executions[0]
        call.conversation_metrics_data = {
            "total_tokens": "unknown",
            "turn_count": "not-a-number",
            "avg_latency_ms": {},
        }
        call.eval_outputs = {
            str(score_eval_config.id): {
                "name": "Accuracy Score",
                "output": "NaN",
                "output_type": "score",
            }
        }
        call.save(update_fields=["conversation_metrics_data", "eval_outputs"])

        list_response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        )
        analytics_response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert list_response.status_code == status.HTTP_200_OK
        assert analytics_response.status_code == status.HTTP_200_OK
        result = next(
            item
            for item in list_response.json()["results"]
            if item["id"] == str(call.id)
        )
        assert result["tokens"] is None
        assert result["turn_count"] is None
        assert result["latency_ms"] is None

    def test_harness_native_evaluations_are_stable_across_list_export_and_analytics(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[-1]
        call.eval_outputs = {
            "policy-check": {
                "source": "harness",
                "name": "Policy check",
                "output": "Passed",
                "output_type": "Pass/Fail",
            }
        }
        call.save(update_fields=["eval_outputs"])

        list_response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"page": 1, "page_size": 1},
        )
        export_response = auth_client.post(
            f"/simulate/v3/test-executions/{test_execution.id}/export/",
            {},
            format="json",
        )
        analytics_response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert list_response.status_code == status.HTTP_200_OK
        assert {
            column["id"] for column in list_response.json()["evaluation_columns"]
        } >= {"policy-check"}
        csv_body = b"".join(export_response.streaming_content).decode()
        assert "evaluation:Policy check" in csv_body.splitlines()[0]
        evaluations = {
            item["id"]: item for item in analytics_response.json()["evaluations"]
        }
        assert evaluations["policy-check"]["passed"] == 1

    def test_calls_groups_only_reference_the_requested_page(
        self, auth_client, test_execution, analytics_call_executions
    ):
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"group_by": "goal", "page": 2, "page_size": 1},
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["count"] == len(analytics_call_executions)
        assert body["page"] == 2
        assert len(body["results"]) == 1
        assert body["groups"][0]["result_ids"] == [body["results"][0]["id"]]

    def test_sub_goal_groups_batch_exact_memberships_across_pages(
        self, test_execution, analytics_call_executions, django_assert_num_queries
    ):
        from django.db.models import Case, JSONField, Value, When

        from simulate.services.run_results_v3_queries import (
            group_run_calls,
            run_calls_queryset,
        )

        first, second = analytics_call_executions[:2]
        queryset = run_calls_queryset(test_execution).annotate(
            result_sub_goal=Case(
                When(
                    pk=first.pk,
                    then=Value(
                        ["alpha", "alpha", " beta ", "Ungrouped"],
                        output_field=JSONField(),
                    ),
                ),
                When(
                    pk=second.pk,
                    then=Value(["alpha", "beta"], output_field=JSONField()),
                ),
                default=Value([], output_field=JSONField()),
                output_field=JSONField(),
            )
        )
        with django_assert_num_queries(2):
            groups = group_run_calls(
                queryset, "sub_goal", [{"id": str(first.pk)}], [], execution=test_execution
            )
        by_key = {group["key"]: group for group in groups}
        assert {key: group["total"] for key, group in by_key.items()} == {
            "alpha": 2,
            "beta": 1,
            "Ungrouped": 2,
        }
        assert all(group["result_ids"] == [str(first.pk)] for group in groups)

    def test_sub_goal_group_keeps_zero_total_for_normalized_unmatched_key(
        self, test_execution, analytics_call_executions, django_assert_num_queries
    ):
        from django.db.models import JSONField, Value

        from simulate.services.run_results_v3_queries import (
            group_run_calls,
            run_calls_queryset,
        )

        queryset = run_calls_queryset(test_execution).annotate(
            result_sub_goal=Value([" padded "], output_field=JSONField())
        )
        with django_assert_num_queries(2):
            groups = group_run_calls(
                queryset,
                "sub_goal",
                [{"id": str(analytics_call_executions[0].pk)}],
                [],
                execution=test_execution,
            )
        assert len(groups) == 1
        assert groups[0]["key"] == "padded"
        assert groups[0]["total"] == 0
        assert groups[0]["aggregates"]["avg_stop_time_after_interruption"] is None
        assert groups[0]["aggregates"]["ai_interruptions"] is None

    def test_empty_group_page_does_not_query(
        self, test_execution, django_assert_num_queries
    ):
        from simulate.services.run_results_v3_queries import (
            group_run_calls,
            run_calls_queryset,
        )

        queryset = run_calls_queryset(test_execution)
        with django_assert_num_queries(0):
            assert (
                group_run_calls(queryset, "sub_goal", [], [], execution=test_execution)
                == []
            )

    def test_visible_groups_preserve_null_keys_and_off_page_totals(
        self, test_execution, analytics_call_executions, django_assert_num_queries
    ):
        from django.db.models import Case, CharField, Value, When

        from simulate.services.run_results_v3_queries import (
            group_run_calls,
            run_calls_queryset,
        )

        first = analytics_call_executions[0]
        queryset = run_calls_queryset(test_execution).annotate(
            result_goal=Case(
                When(pk=first.pk, then=Value(None, output_field=CharField())),
                default=Value("None"),
                output_field=CharField(),
            )
        )
        with django_assert_num_queries(2):
            groups = group_run_calls(
                queryset, "goal", [{"id": str(first.pk)}], [], execution=test_execution
            )
        assert sorted(group["total"] for group in groups) == [1, 3]
        assert all(group["key"] == "None" for group in groups)

    def test_detail_uses_v3_route(self, auth_client, analytics_call_executions):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_outcome_status": "passed", "use_case": "Returns"}
        call.provider_call_data = {
            "livekit": {
                "tool_calls": [
                    {
                        "name": "lookup_order",
                        "arguments": {"order_id": "AB-1"},
                        "result": {"status": "shipped"},
                        "duration_ms": 309,
                    }
                ]
            }
        }
        call.save(update_fields=["call_metadata", "provider_call_data"])

        response = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["outcome"] == "passed"
        assert response.json()["goal"] == "Returns"
        assert response.json()["function_calls"] == [
            {
                "name": "lookup_order",
                "arguments": {"order_id": "AB-1"},
                "result": {"status": "shipped"},
                "duration_ms": 309,
            }
        ]

    def test_analytics_exposes_truthful_coverage(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_outcome_status": "passed"}
        call.avg_agent_latency_ms = 300
        call.save(update_fields=["call_metadata", "avg_agent_latency_ms"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["summary"]["outcomes"] == {
            "passed": 1,
            "failed": 0,
            "error": 1,
            "inconclusive": 2,
        }
        # An infrastructure error says nothing about the agent, so it is not in the rate.
        assert body["summary"]["measured"] == 1
        assert body["summary"]["pass_rate"] == 100.0
        assert body["summary"]["latency"]["measured"] == 1
        assert body["summary"]["latency"]["total"] == 4
        assert "critical_failures" not in body["summary"]

    def test_analytics_scores_trials_per_scenario_without_counting_errors(
        self, auth_client, test_execution, scenario
    ):
        """3 scenarios x 2 trials: one always passes, one flips, one fails then errors."""
        test_execution.trials = 2
        test_execution.save(update_fields=["trials"])
        verdicts = {
            "always-passes": ["passed", "passed"],
            "flips": ["passed", "failed"],
            "fails-then-errors": ["failed", "errored"],
        }
        for key, outcomes in verdicts.items():
            for trial, outcome in enumerate(outcomes, start=1):
                eval_output = {
                    "source": "harness",
                    "name": "Policy check",
                    "output": {"passed": "Passed", "failed": "Failed"}.get(outcome),
                    "status": "Failed" if outcome == "errored" else "completed",
                    "output_type": "Pass/Fail",
                }
                CallExecution.objects.create(
                    test_execution=test_execution,
                    scenario=scenario,
                    phone_number=f"+91{trial}{len(key)}",
                    status="completed",
                    duration_seconds=60,
                    ended_reason="simulator_end_call",
                    simulation_call_type="voice",
                    call_metadata={
                        "harness_outcome_status": outcome,
                        "harness_scenario_key": key,
                        "harness_trial_index": trial,
                    },
                    eval_outputs={"policy-check": eval_output},
                )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["summary"]["outcomes"]["error"] == 1
        assert body["summary"]["measured"] == 5
        assert body["summary"]["pass_rate"] == 60.0
        assert {row["scenario"]: row["total"] for row in body["scenario_risk"]} == {
            "always-passes": 2,
            "flips": 2,
            "fails-then-errors": 2,
        }
        reliability = body["reliability"]
        assert reliability["trials"] == 2
        assert reliability["scenarios"] == 3
        assert reliability["consistent_pass"] == 1
        assert reliability["passed_at_least_once"] == 2
        assert reliability["repeated"] == 2
        assert reliability["flaky"] == 1
        by_key = {row["scenario"]: row for row in reliability["rows"]}
        assert by_key["flips"]["verdict"] == "flaky"
        assert by_key["fails-then-errors"]["error"] == 1
        interval = reliability["pass_rate_interval"]
        # Trials of one scenario are not independent, so the range must not claim more
        # precision than the 3 scenarios behind these 5 verdicts.
        assert interval["low"] < 60.0 < interval["high"]
        assert 3 <= interval["effective_n"] <= 5
        evaluation = next(
            row for row in body["evaluations"] if row["id"] == "policy-check"
        )
        assert (evaluation["passed"], evaluation["failed"], evaluation["errored"]) == (
            3,
            2,
            1,
        )
        dashboard = body["dashboard"]
        endings = {
            segment["label"]: segment["count"]
            for segment in next(
                chart
                for chart in dashboard["breakdowns"]
                if chart["key"] == "disconnection"
            )["segments"]
        }
        assert endings == {"Simulator ended": 6}
        drop_off = next(
            metric for metric in dashboard["metrics"] if metric["key"] == "drop_off"
        )
        assert drop_off["value"] is None
        assert drop_off["measured"] == 0
        assert dashboard["evaluation_summary"]["pass_rate"] == 60.0

    def test_end_reason_categories_do_not_imply_early_caller_drop_off(
        self, auth_client, test_execution, scenario
    ):
        cases = {
            "simulator_end_call": "Simulator ended",
            "target_end_call": "Agent ended",
            "target_disconnected": "Agent disconnected",
            "session_closed": "Session closed",
            "participant_disconnected": "Disconnected",
            "room_disconnected": "Disconnected",
            "provider_disconnected": "Disconnected",
            "closing_loop": "Completed",
            "some-new-provider-reason": "Unrecognised",
        }
        for index, reason in enumerate(cases):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+931234560{index}",
                status="completed",
                ended_reason=reason,
                call_metadata={
                    "harness_outcome_status": "failed",
                    "hosted_harness_receipt": {
                        "call": {
                            "stop_reason": reason,
                            "script_completed": reason == "simulator_end_call",
                        }
                    },
                },
            )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == status.HTTP_200_OK
        dashboard = response.json()["dashboard"]
        chart = next(
            item for item in dashboard["breakdowns"] if item["key"] == "disconnection"
        )
        assert {item["label"]: item["count"] for item in chart["segments"]} == dict(
            Counter(cases.values())
        )
        assert response.json()["summary"]["outcomes"]["failed"] == len(cases)
        drop_off = next(
            item for item in dashboard["metrics"] if item["key"] == "drop_off"
        )
        assert drop_off["value"] == 0
        assert drop_off["measured"] == len(cases)
        assert set(test_execution.calls.values_list("ended_reason", flat=True)) == set(
            cases
        )

    @pytest.mark.parametrize(
        ("stop_reason", "expected_drop_off"),
        [
            ("simulator_end_call", 33.33),
            ("customer-ended-call", 33.33),
        ],
    )
    def test_drop_off_counts_only_early_caller_hangups_with_failed_evaluations(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        stop_reason,
        expected_drop_off,
    ):
        cases = [
            (True, "failed"),
            (False, "failed"),
            (False, "passed"),
            (False, "error"),
        ]
        for call, (script_completed, outcome) in zip(
            analytics_call_executions, cases, strict=True
        ):
            call.status = (
                CallExecution.CallStatus.FAILED
                if outcome == "error"
                else CallExecution.CallStatus.COMPLETED
            )
            call.ended_reason = stop_reason
            call.call_metadata = {
                "harness_outcome_status": outcome,
                "hosted_harness_receipt": {
                    "call": {
                        "stop_reason": stop_reason,
                        "script_completed": script_completed,
                    }
                },
            }
            call.save(update_fields=["status", "ended_reason", "call_metadata"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == status.HTTP_200_OK
        metric = next(
            item
            for item in response.json()["dashboard"]["metrics"]
            if item["key"] == "drop_off"
        )
        assert metric["measured"] == 3
        assert metric["value"] == expected_drop_off

    def test_dashboard_prioritises_decision_metrics_and_shared_scenario_change(
        self,
        auth_client,
        test_execution,
        analytics_call_executions,
        scenario,
    ):
        def evaluation(passed):
            return {
                "policy": {
                    "name": "Policy",
                    "output": {"passed": "Passed" if passed else "Failed"},
                    "source": "harness",
                }
            }

        current = [
            ("shared-pass", True, "customer-ended-call", 10, 9, 500),
            ("shared-fail", False, "customer-ended-call", 20, 8, 2000),
            ("current-only", True, "assistant-ended-call", 30, 4, None),
        ]
        for call, (key, passed, ended, customer_cost, csat, latency) in zip(
            analytics_call_executions[:3], current, strict=True
        ):
            call.call_metadata = {
                "harness_scenario_key": key,
                "harness_outcome_status": "passed" if passed else "failed",
            }
            call.eval_outputs = evaluation(passed)
            call.ended_reason = ended
            call.customer_cost_cents = customer_cost
            call.cost_cents = 999
            call.conversation_metrics_data = {"csat_score": csat}
            call.avg_agent_latency_ms = latency
            call.transcript_available = True
            call.message_count = 2
            call.save()

        previous = TestExecution.objects.create(
            run_test=test_execution.run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=3,
            total_calls=3,
            completed_calls=3,
            failed_calls=0,
            simulator_agent=test_execution.simulator_agent,
            agent_definition=test_execution.agent_definition,
        )
        TestExecution.objects.filter(id=previous.id).update(
            created_at=test_execution.created_at - timedelta(minutes=1)
        )
        for index, (key, passed) in enumerate(
            [
                ("shared-pass", False),
                ("shared-fail", True),
                ("previous-only", True),
            ]
        ):
            CallExecution.objects.create(
                test_execution=previous,
                scenario=scenario,
                phone_number=f"+960000000{index}",
                status="completed",
                call_metadata={
                    "harness_scenario_key": key,
                    "harness_outcome_status": "passed" if passed else "failed",
                },
                eval_outputs=evaluation(passed),
                transcript_available=True,
                message_count=2,
            )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert response.status_code == status.HTTP_200_OK
        dashboard = response.json()["dashboard"]
        metrics = {row["key"]: row for row in dashboard["metrics"]}
        assert metrics["drop_off"]["value"] is None
        assert metrics["drop_off"]["measured"] == 0
        # Spend on the failed call is included, but only the target agent's
        # provider-reported cost is used: (10 + 20 + 30) / 2 passing calls.
        assert metrics["cost_per_pass"]["value"] == 30.0
        assert metrics["cost_per_pass"]["measured"] == 3
        assert dashboard["csat"]["satisfied"] == 2
        assert dashboard["csat"]["satisfied_percent"] == 66.67
        assert dashboard["agent_response_time"]["p95"] == 1925.0
        assert dashboard["comparison"] == {
            "available": True,
            "previous_execution_id": str(previous.id),
            "shared_scenarios": 2,
            "newly_passing": ["shared-pass"],
            "newly_failing": ["shared-fail"],
        }
        assert dashboard["run_health"] == {
            "show_banner": True,
            "attempted": 4,
            "ran_cleanly": 3,
            "connected": 3,
            "errored": 1,
            "not_evaluated": 0,
            "eval_errors": 0,
        }

    def test_export_applies_filters_to_underlying_rows(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_outcome_status": "passed"}
        call.save(update_fields=["call_metadata"])

        response = auth_client.post(
            f"/simulate/v3/test-executions/{test_execution.id}/export/",
            {"filters": {"status": ["passed"]}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        csv_body = b"".join(response.streaming_content).decode()
        assert str(call.id) in csv_body
        assert str(analytics_call_executions[-1].id) not in csv_body

    def test_goal_outcome_chart_drill_down_matches_list_and_export(
        self, auth_client, test_execution, analytics_call_executions
    ):
        transferred, passed = analytics_call_executions[:2]
        for call in (transferred, passed):
            call.call_metadata = {"harness_outcome_status": "passed"}
            call.save(update_fields=["call_metadata"])
        transferred.ended_reason = "warm-transfer-completed"
        transferred.save(update_fields=["ended_reason"])

        base = f"/simulate/v3/test-executions/{test_execution.id}"
        dashboard = auth_client.get(f"{base}/analytics/").json()["dashboard"]
        chart = next(
            item for item in dashboard["breakdowns"] if item["key"] == "goal_outcome"
        )
        counts = {segment["label"]: segment["count"] for segment in chart["segments"]}
        assert counts["passed"] == counts["escalated"] == 1

        for outcome, expected in (("passed", passed), ("escalated", transferred)):
            filters = {"goal_outcome": [outcome]}
            response = auth_client.get(
                f"{base}/calls/", {"filters": json.dumps(filters)}
            )
            assert response.status_code == 200
            assert response.json()["count"] == counts[outcome]
            assert [row["id"] for row in response.json()["results"]] == [
                str(expected.id)
            ]

            export = auth_client.post(
                f"{base}/export/", {"filters": filters}, format="json"
            )
            assert export.status_code == 200
            csv_body = b"".join(export.streaming_content).decode()
            assert str(expected.id) in csv_body
            other = transferred if expected == passed else passed
            assert str(other.id) not in csv_body

    def test_dashboard_distinguishes_missing_metrics_and_tool_verdicts(
        self, auth_client, test_execution, analytics_call_executions
    ):
        from simulate.serializers.run_dashboard_v3 import RunDashboardV3Serializer

        call = analytics_call_executions[0]
        call.call_metadata = {"harness_outcome_status": "failed"}
        call.conversation_metrics_data = {"csat_score": 7, "turn_count": 3}
        call.provider_call_data = {
            "livekit": {
                "tool_calls": [
                    {"name": "lookup", "ok": True},
                    {"name": "lookup", "ok": False},
                    {"name": "unclassified", "result": {"order_id": "synthetic-order"}},
                ]
            }
        }
        call.customer_latency_metrics = {
            "systemMetrics": {"model": 240, "voice": "unavailable"}
        }
        call.simulation_call_type = "voice"
        call.save()
        other = analytics_call_executions[1]
        other.conversation_metrics_data = {"csat_score": "unavailable"}
        other.save(update_fields=["conversation_metrics_data"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        dashboard = response.json()["dashboard"]
        serializer = RunDashboardV3Serializer(data=dashboard)
        assert serializer.is_valid(), serializer.errors
        metrics = {row["key"]: row for row in dashboard["metrics"]}
        assert "turn_count" not in metrics
        assert metrics["total"]["label"] == "Total calls"
        assert {"pass_rate", "ran_cleanly", "wpm", "stop", "talk"} <= metrics.keys()
        assert metrics["talk"]["unit"] == "percent"
        assert metrics["csat"]["value"] == 7
        assert metrics["csat"]["measured"] == 1
        tools = {row["name"]: row for row in dashboard["tools"]["failures"]}
        assert tools["lookup"]["failure_rate"] == 50
        assert tools["lookup"]["measured"] == 2
        assert tools["unclassified"]["failure_rate"] is None
        assert dashboard["series_mode"] == "calls"
        assert len(dashboard["agent_latency_percentiles"]) == 101
        assert all(
            sum(segment["count"] for segment in chart["segments"]) == 4
            for chart in dashboard["breakdowns"]
        )
        assert "failure_attribution" in {
            row["key"] for row in dashboard["unavailable_features"]
        }

    def test_dashboard_labels_a_chat_run_and_hides_voice_only_tiles(
        self, auth_client, test_execution, analytics_call_executions
    ):
        for call in analytics_call_executions:
            call.simulation_call_type = "text"
            call.save(update_fields=["simulation_call_type"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        metrics = {row["key"]: row for row in response.json()["dashboard"]["metrics"]}
        assert not {"wpm", "stop", "talk", "turn_count"} & metrics.keys()
        assert {key: metrics[key]["label"] for key in metrics} == {
            "pass_rate": "Chats passed",
            "total": "Total chats",
            "ran_cleanly": "Chats ran cleanly",
            "connected_rate": "Chats connected (%)",
            "drop_off": "Drop-off",
            "csat": "Avg CSAT (0–10)",
            "agent_latency": "Agent response time",
            "duration": "Avg chat duration",
            "turns": "Avg turns/chat",
            "duration_p90": "Chat duration p90",
            "cost_per_pass": "Cost / pass",
            "total_cost": "Total cost",
        }
        target = response.json()["dashboard"]["agent_response_time"]["target_ms"]
        assert target == 3000

    def _dashboard(self, auth_client, test_execution):
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        return response.json()["dashboard"]

    def test_dashboard_latency_percentiles_use_agent_latency_not_call_length(
        self, auth_client, test_execution, scenario
    ):
        _latency_calls(
            test_execution,
            scenario,
            [(100, 1, 0), (200, 2, 1), (300, 3, 2), (400, 4, 3)],
        )
        dashboard = self._dashboard(auth_client, test_execution)
        curve = dashboard["agent_latency_percentiles"]
        assert [row["percentile"] for row in curve] == list(range(101))
        assert curve[0]["value"] == 100
        assert curve[50]["value"] == 250
        assert curve[90]["value"] == pytest.approx(370)
        assert curve[99]["value"] == pytest.approx(397)
        assert curve[100]["value"] == 400
        call_length = dashboard["latency_percentiles"]
        assert [row["percentile"] for row in call_length] == list(range(101))
        assert call_length[0]["value"] == 1000
        assert call_length[50]["value"] == 2500
        assert call_length[100]["value"] == 4000

    def test_dashboard_latency_excludes_unmeasured_calls(
        self, auth_client, test_execution, scenario
    ):
        _latency_calls(
            test_execution,
            scenario,
            [(None, 10, 0), (-5, 10, 1), (0, 10, 2), (300, 10, 3), (500, 10, None)],
        )
        dashboard = self._dashboard(auth_client, test_execution)
        curve = dashboard["agent_latency_percentiles"]
        assert dashboard["distributions"][0]["measured"] == 3
        assert curve[0]["value"] == 0
        assert curve[100]["value"] == 500

    def test_dashboard_latency_is_null_when_no_call_measured(
        self, auth_client, test_execution, analytics_call_executions
    ):
        dashboard = self._dashboard(auth_client, test_execution)
        curve = dashboard["agent_latency_percentiles"]
        assert len(curve) == 101
        assert all(row["value"] is None for row in curve)
        assert dashboard["distributions"][0] == {
            "key": "latency_ms",
            "measured": 0,
            "average": None,
            "max": None,
            "p50": None,
            "p90": None,
            "p99": None,
        }

    def test_dashboard_distribution_latency_row_matches_curve(
        self, auth_client, test_execution, scenario
    ):
        _latency_calls(
            test_execution,
            scenario,
            [(100, 1, 0), (200, 2, 1), (300, 3, 2), (400, 4, 3)],
        )
        dashboard = self._dashboard(auth_client, test_execution)
        assert [row["key"] for row in dashboard["distributions"]] == [
            "latency_ms",
            "duration_seconds",
            "tokens",
            "cost_cents",
            "turns",
            "end_to_end_ms",
        ]
        call_length = dashboard["distributions"][-1]
        assert call_length["measured"] == 4
        assert call_length["p50"] == 2500
        assert call_length["max"] == 4000
        latency = dashboard["distributions"][0]
        curve = dashboard["agent_latency_percentiles"]
        assert latency["measured"] == 4
        assert latency["average"] == 250
        assert latency["max"] == 400
        for percentile in (50, 90, 99):
            assert latency[f"p{percentile}"] == curve[percentile]["value"]
        duration = dashboard["distributions"][1]
        assert duration["p50"] == 2.5
        assert duration["max"] == 4

    def test_dashboard_series_carries_per_call_latency(
        self, auth_client, test_execution, scenario
    ):
        _latency_calls(
            test_execution,
            scenario,
            [(100, 1, 0), (-5, 2, 1), (None, 3, 2), (0, 4, 3), (300, 5, 4)],
        )
        dashboard = self._dashboard(auth_client, test_execution)
        assert dashboard["series_mode"] == "calls"
        assert [row["latency_ms"] for row in dashboard["series"]] == [
            100,
            None,
            None,
            0,
            300,
        ]
        assert [row["duration_ms"] for row in dashboard["series"]] == [
            1000,
            2000,
            3000,
            4000,
            5000,
        ]

    def test_dashboard_series_buckets_average_measured_latency(
        self, auth_client, test_execution, scenario
    ):
        first_bucket = [(100, 1, 0), (300, 1, 0), (-5, 1, 0)] + [(None, 1, 0)] * 95
        _latency_calls(
            test_execution,
            scenario,
            first_bucket + [(None, 1, 50), (0, 1, 99), (600, 1, 99)],
        )
        dashboard = self._dashboard(auth_client, test_execution)
        assert dashboard["series_mode"] == "time_buckets"
        assert [row["calls"] for row in dashboard["series"]] == [98, 1, 2]
        assert [row["latency_ms"] for row in dashboard["series"]] == [200, None, 300]
        assert [row["duration_ms"] for row in dashboard["series"]] == [
            1000,
            1000,
            1000,
        ]

    def test_dashboard_schema_declares_latency_fields(self):
        from simulate.serializers.run_dashboard_v3 import (
            RunDashboardSeriesSerializer,
            RunDashboardV3Serializer,
        )

        series_fields = RunDashboardSeriesSerializer().fields
        dashboard_fields = RunDashboardV3Serializer().fields
        assert {"latency_ms", "duration_ms"} <= series_fields.keys()
        assert {"agent_latency_percentiles", "latency_percentiles"} <= (
            dashboard_fields.keys()
        )

        swagger_path = (
            Path(__file__).resolve().parents[3]
            / "api_contracts"
            / "openapi"
            / "swagger.json"
        )
        definitions = json.loads(swagger_path.read_text())["definitions"]
        series_properties = definitions["RunDashboardSeries"]["properties"]
        dashboard_properties = definitions["RunDashboardV3"]["properties"]
        assert {"latency_ms", "duration_ms"} <= series_properties.keys()
        assert {"agent_latency_percentiles", "latency_percentiles"} <= (
            dashboard_properties.keys()
        )

    def test_dashboard_shows_provider_verdicts_only_when_reported(
        self, auth_client, test_execution, analytics_call_executions
    ):
        url = f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        charts = {
            row["key"] for row in auth_client.get(url).json()["dashboard"]["breakdowns"]
        }
        assert charts == {"goal_outcome", "disconnection"}

        call = analytics_call_executions[0]
        call.call_metadata = {"harness_outcome_status": "passed"}
        call.analysis_data = {"call_successful": False, "user_sentiment": "negative"}
        call.save()
        response = auth_client.get(url)
        assert response.status_code == 200
        charts = {row["key"]: row for row in response.json()["dashboard"]["breakdowns"]}
        # The provider's verdict is shown beside ours and never replaces it.
        assert charts["goal_outcome"]["headline"]["count"] == 1
        provider = {
            s["label"]: s["count"] for s in charts["provider_success"]["segments"]
        }
        assert provider == {"false": 1, "Not reported": 3}
        sentiment = {s["label"]: s["count"] for s in charts["sentiment"]["segments"]}
        assert sentiment == {"negative": 1, "Not reported": 3}

    def test_dashboard_histograms_use_measured_values_and_exact_threshold(
        self, auth_client, test_execution, analytics_call_executions
    ):
        for call, score, latency in zip(
            analytics_call_executions,
            [0, 4, 10, "missing"],
            [1499, 1500, 1575, None],
            strict=True,
        ):
            call.conversation_metrics_data = {"csat_score": score}
            call.avg_agent_latency_ms = latency
            call.save(
                update_fields=["conversation_metrics_data", "avg_agent_latency_ms"]
            )
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )
        assert response.status_code == 200
        dashboard = response.json()["dashboard"]
        assert dashboard["csat"]["measured"] == 3
        assert len(dashboard["csat"]["bins"]) == 11
        assert sum(row["count"] for row in dashboard["csat"]["bins"]) == 3
        assert dashboard["agent_response_time"]["at_or_above_target"] == 2
        assert dashboard["agent_response_time"]["measured"] == 3
        assert dashboard["agent_response_time"]["at_or_above_target_percent"] == 66.67
        assert dashboard["csat"]["agreement"]["percent"] is None

    def test_export_escapes_spreadsheet_formulas(
        self, auth_client, test_execution, analytics_call_executions
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {
            "harness_outcome_status": "passed",
            "goal": "=SUM(1,1)",
        }
        call.save(update_fields=["call_metadata"])

        response = auth_client.post(
            f"/simulate/v3/test-executions/{test_execution.id}/export/",
            {"filters": {"status": ["passed"]}},
            format="json",
        )

        rows = list(
            csv.DictReader(io.StringIO(b"".join(response.streaming_content).decode()))
        )
        assert rows[0]["goal"] == "'=SUM(1,1)"

    def test_failure_breakdown_counts_each_failed_call_once(
        self, auth_client, test_execution, analytics_call_executions
    ):
        failed = analytics_call_executions[0]
        failed.call_metadata = {"harness_outcome_status": "failed"}
        failed.error_message = "An evaluator failed"
        failed.save(update_fields=["call_metadata", "error_message"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/analytics/"
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        expected = (
            body["summary"]["outcomes"]["failed"] + body["summary"]["outcomes"]["error"]
        )
        assert sum(item["failures"] for item in body["failure_breakdown"]) == expected

    def test_v3_endpoints_reject_other_workspace(
        self, auth_client, organization, user, agent_definition, simulator_agent
    ):
        _, hidden_execution = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        for suffix in ("calls/", "analytics/"):
            response = auth_client.get(
                f"/simulate/v3/test-executions/{hidden_execution.id}/{suffix}"
            )
            assert response.status_code == status.HTTP_404_NOT_FOUND

    @staticmethod
    def _harness_job(organization, workspace):
        return HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_id=uuid.uuid4(),
            idempotency_key=uuid.uuid4().hex,
            request_digest=uuid.uuid4().hex,
            schema_version="1.6",
            seed=1,
            artifact_level="standard",
            max_artifact_bytes=1024,
            deadline_at=timezone.now() + timedelta(hours=1),
            scenario_count=1,
            payload={"metadata": {}, "runtime": {"max_duration_seconds": 600}},
        )

    def _link_call_to_scenario(self, layout, job, call, **scenario_fields):
        if layout == "trial":
            authored = HostedHarnessScenario.no_workspace_objects.create(
                job=job, scenario_key="pin-reset", **scenario_fields
            )
            HostedHarnessExecution.no_workspace_objects.create(
                job=job,
                source_scenario=authored,
                execution_key="pin-reset:1",
                trial_index=1,
                call_execution=call,
            )
            return authored
        return HostedHarnessScenario.no_workspace_objects.create(
            job=job, scenario_key="pin-reset", call_execution=call, **scenario_fields
        )

    def _call_row(self, auth_client, test_execution, call):
        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        )
        assert response.status_code == status.HTTP_200_OK
        return next(
            row for row in response.json()["results"] if row["id"] == str(call.id)
        )

    @pytest.mark.parametrize("layout", ["trial", "registration"])
    def test_calls_without_receipt_sub_goals_show_the_authored_scenarios_sub_goals(
        self,
        layout,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_scenario_key": "pin-reset"}
        call.save(update_fields=["call_metadata"])
        self._link_call_to_scenario(
            layout,
            self._harness_job(organization, workspace),
            call,
            sub_goals=["pin_verified", {"name": "exact_greeting"}],
        )

        row = self._call_row(auth_client, test_execution, call)

        assert row["sub_goals"] == ["pin_verified", "exact_greeting"]

    def test_receipt_sub_goals_take_priority_over_the_authored_scenarios(
        self,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {
            "hosted_harness_receipt": {
                "sub_goals": [{"name": "identity_verified", "held": True}]
            }
        }
        call.save(update_fields=["call_metadata"])
        self._link_call_to_scenario(
            "trial",
            self._harness_job(organization, workspace),
            call,
            sub_goals=["pin_verified"],
        )

        row = self._call_row(auth_client, test_execution, call)

        assert row["sub_goals"] == ["identity_verified"]

    @pytest.mark.parametrize("layout", ["trial", "registration"])
    def test_authored_sub_goals_filter_and_facet_like_the_rows_show_them(
        self,
        layout,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_scenario_key": "pin-reset"}
        call.save(update_fields=["call_metadata"])
        self._link_call_to_scenario(
            layout,
            self._harness_job(organization, workspace),
            call,
            sub_goals=["pin_verified", {"name": "exact_greeting"}],
        )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"filters": json.dumps({"sub_goal": ["pin_verified"]})},
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert [row["id"] for row in body["results"]] == [str(call.id)]
        assert body["count"] == 1
        assert body["facets"]["sub_goal"] == [
            {"value": "exact_greeting", "count": 1},
            {"value": "pin_verified", "count": 1},
        ]

    @pytest.mark.parametrize("layout", ["trial", "registration"])
    def test_a_pruned_scenarios_sub_goals_show_where_they_are_counted(
        self,
        layout,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {"harness_scenario_key": "pin-reset"}
        call.save(update_fields=["call_metadata"])
        authored = self._link_call_to_scenario(
            layout,
            self._harness_job(organization, workspace),
            call,
            sub_goals=["pin_verified"],
        )
        authored.deleted = True
        authored.save(update_fields=["deleted"])

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/"
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        row = next(row for row in body["results"] if row["id"] == str(call.id))
        assert row["sub_goals"] == ["pin_verified"]
        assert body["facets"]["sub_goal"] == [{"value": "pin_verified", "count": 1}]

    def test_receipt_sub_goals_keep_the_authored_ones_out_of_filters_and_facets(
        self,
        auth_client,
        organization,
        workspace,
        test_execution,
        analytics_call_executions,
    ):
        call = analytics_call_executions[0]
        call.call_metadata = {
            "hosted_harness_receipt": {
                "sub_goals": [{"name": "identity_verified", "held": True}]
            }
        }
        call.save(update_fields=["call_metadata"])
        self._link_call_to_scenario(
            "trial",
            self._harness_job(organization, workspace),
            call,
            sub_goals=["pin_verified"],
        )

        response = auth_client.get(
            f"/simulate/v3/test-executions/{test_execution.id}/calls/",
            {"filters": json.dumps({"sub_goal": ["pin_verified"]})},
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["results"] == []
        assert body["facets"]["sub_goal"] == [
            {"value": "identity_verified", "count": 1}
        ]

    @pytest.mark.parametrize("layout", ["trial", "registration"])
    def test_calls_read_situation_and_outcome_from_the_authored_scenarios_row(
        self,
        layout,
        auth_client,
        organization,
        workspace,
        dataset_for_scenario,
        test_execution,
        analytics_call_executions,
    ):
        situation = Column.objects.get(dataset=dataset_for_scenario, name="situation")
        outcome = Column.objects.create(
            dataset=dataset_for_scenario,
            name="outcome",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        scenario_row = Row.objects.create(dataset=dataset_for_scenario, order=1)
        Cell.objects.create(
            dataset=dataset_for_scenario,
            column=situation,
            row=scenario_row,
            value="Caller forgot their guest PIN.",
        )
        Cell.objects.create(
            dataset=dataset_for_scenario,
            column=outcome,
            row=scenario_row,
            value="The agent resets the PIN after verifying identity.",
        )
        call = analytics_call_executions[0]
        self._link_call_to_scenario(
            layout,
            self._harness_job(organization, workspace),
            call,
            dataset_row=scenario_row,
        )

        row = self._call_row(auth_client, test_execution, call)

        assert row["scenario_details"] == "Caller forgot their guest PIN."
        assert (
            row["ideal_outcome"] == "The agent resets the PIN after verifying identity."
        )


# ============================================================================
# RunTestAnalyticsView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestAnalyticsView:
    """GET /simulate/run-tests/<uuid>/analytics/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/analytics/"

    def test_run_test_analytics_aggregates_across_executions(
        self,
        auth_client,
        run_test,
        test_execution,
        test_execution_2,
        analytics_call_executions,
        run_test_second_execution_calls,
    ):
        response = auth_client.get(self.URL_TEMPLATE.format(run_test.id))
        assert response.status_code == status.HTTP_200_OK
        body = response.json()

        assert set(body["run_test_info"].keys()) == {
            "id",
            "name",
            "description",
            "total_test_executions",
            "total_calls",
        }
        info = body["run_test_info"]
        assert info["id"] == str(run_test.id)
        assert info["name"] == run_test.name
        assert info["total_test_executions"] == 2
        # 4 + 2 seeded calls across the two executions
        assert info["total_calls"] == 6

        assert len(body["fail_rate_trends"]) == 2
        assert len(body["evaluation_score_trends"]) == 2
        assert len(body["performance_comparison"]) == 2

        # Find each execution row (order is by created_at ascending)
        rows_by_te = {
            row["test_execution_id"]: row for row in body["performance_comparison"]
        }
        te1_row = rows_by_te[str(test_execution.id)]
        te2_row = rows_by_te[str(test_execution_2.id)]

        # te1: 4 calls total, 1 failed -> fail_rate 25.0
        assert te1_row["total_calls"] == 4
        assert te1_row["failed_calls"] == 1
        assert te1_row["fail_rate"] == 25.0

        # te2: 2 completed, none failed
        assert te2_row["total_calls"] == 2
        assert te2_row["failed_calls"] == 0
        assert te2_row["fail_rate"] == 0.0

        # summary_stats present with computed aggregates
        summary = body["summary_stats"]
        assert summary["total_executions"] == 2
        assert summary["worst_success_rate"] <= summary["best_success_rate"]
        assert summary["avg_fail_rate"] == pytest.approx((25.0 + 0.0) / 2, abs=0.1)

    def test_run_test_analytics_other_workspace_returns_404(
        self, auth_client, organization, user, agent_definition, simulator_agent
    ):
        hidden_run_test, _ = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        response = auth_client.get(self.URL_TEMPLATE.format(hidden_run_test.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_run_test_analytics_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.get(self.URL_TEMPLATE.format(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# TestExecutionOptimiserAnalysisView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionOptimiserAnalysisView:
    """GET /simulate/test-executions/<uuid>/optimiser-analysis/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/optimiser-analysis/"

    def test_optimiser_analysis_returns_stored_run_result(
        self, auth_client, test_execution
    ):
        optimiser = AgentOptimiser.objects.create(
            name="Analytics optimiser",
            description="opt",
            configuration={"type": "simulation_analysis"},
        )
        test_execution.agent_optimiser = optimiser
        test_execution.save(update_fields=["agent_optimiser"])

        run = AgentOptimiserRun.objects.create(
            agent_optimiser=optimiser,
            input_data={"seed": "input"},
            result={"insights": ["latency high"], "score": 42},
            status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        )

        response = auth_client.get(self.URL_TEMPLATE.format(test_execution.id))
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True

        result = body["result"]
        assert result["response"] == {
            "insights": ["latency high"],
            "score": 42,
        }
        assert result["status"] == AgentOptimiserRun.OptimiserStatus.COMPLETED
        assert result["last_updated"] is not None
        # last_updated is the run.updated_at isoformat
        run.refresh_from_db()
        assert result["last_updated"].startswith(run.updated_at.isoformat()[:19])

    def test_optimiser_analysis_other_workspace_returns_404(
        self, auth_client, organization, user, agent_definition, simulator_agent
    ):
        _, hidden_te = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        response = auth_client.get(self.URL_TEMPLATE.format(hidden_te.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json().get("status") is False

    def test_optimiser_analysis_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.get(self.URL_TEMPLATE.format(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json().get("status") is False


# ============================================================================
# RunTestEvalSummaryView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestEvalSummaryView:
    """GET /simulate/run-tests/<uuid>/eval-summary/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/eval-summary/"

    def test_eval_summary_populated_returns_per_template_summary(
        self,
        auth_client,
        run_test,
        eval_summary_te1_calls,
    ):
        response = auth_client.get(self.URL_TEMPLATE.format(run_test.id))
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True

        result = body["result"]
        assert isinstance(result, list)
        # 2 eval templates seeded (pass_fail + score)
        assert len(result) == 2

        templates_by_type = {t["output_type"]: t for t in result}
        assert set(templates_by_type.keys()) == {"Pass/Fail", "score"}

        # 3 completed calls, 2 Passed + 1 Failed -> pass_rate 66.67
        pf = templates_by_type["Pass/Fail"]
        assert pf["name"] == "Quality Gate"
        assert pf["total_pass_rate"] == pytest.approx(66.67, abs=0.01)
        pf_config = pf["result"][0]
        assert pf_config["total_cells"] == 3
        assert pf_config["output"]["pass_count"] == 2
        assert pf_config["output"]["fail_count"] == 1

        # score: 0.8 + 0.6 + 0.4 scaled x100 -> avg 60.0
        score = templates_by_type["score"]
        assert score["name"] == "Accuracy Score"
        assert score["total_avg"] == pytest.approx(60.0, abs=0.01)
        score_config = score["result"][0]
        assert score_config["total_cells"] == 3
        assert score_config["avg_score"] == pytest.approx(60.0, abs=0.01)

    def test_eval_summary_execution_id_scopes_to_single_execution(
        self,
        auth_client,
        run_test,
        test_execution,
        test_execution_2,
        eval_summary_te1_calls,
        eval_summary_te2_calls,
    ):
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.get(url, {"execution_id": str(test_execution_2.id)})
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        templates_by_type = {t["output_type"]: t for t in result}
        # te2 has 2 calls, both Passed -> 100
        assert templates_by_type["Pass/Fail"]["total_pass_rate"] == pytest.approx(
            100.0, abs=0.01
        )
        # te2 scores 1.0 + 0.9 -> avg 95.0
        assert templates_by_type["score"]["total_avg"] == pytest.approx(95.0, abs=0.01)

    def test_eval_summary_no_eval_configs_returns_empty_list(
        self, auth_client, run_test
    ):
        """RunTest with no eval configs short-circuits to []."""
        response = auth_client.get(self.URL_TEMPLATE.format(run_test.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": True, "result": []}

    def test_eval_summary_other_workspace_returns_404(
        self, auth_client, organization, user, agent_definition, simulator_agent
    ):
        hidden_run_test, _ = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        response = auth_client.get(self.URL_TEMPLATE.format(hidden_run_test.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_eval_summary_unknown_uuid_returns_404(self, auth_client):
        response = auth_client.get(self.URL_TEMPLATE.format(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_eval_summary_soft_deleted_run_test_returns_404(
        self, auth_client, run_test, eval_summary_te1_calls
    ):
        RunTest.no_workspace_objects.filter(id=run_test.id).update(deleted=True)
        response = auth_client.get(self.URL_TEMPLATE.format(run_test.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# RunTestEvalSummaryComparisonView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestEvalSummaryComparisonView:
    """GET /simulate/run-tests/<uuid>/eval-summary-comparison/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/eval-summary-comparison/"

    def test_comparison_returns_per_execution_summaries(
        self,
        auth_client,
        run_test,
        test_execution,
        test_execution_2,
        eval_summary_te1_calls,
        eval_summary_te2_calls,
    ):
        url = self.URL_TEMPLATE.format(run_test.id)
        exec_ids = [str(test_execution.id), str(test_execution_2.id)]
        response = auth_client.get(url, {"execution_ids": json.dumps(exec_ids)})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True

        comparison = body["result"]
        assert set(comparison.keys()) == set(exec_ids)

        te1_summary = {t["output_type"]: t for t in comparison[str(test_execution.id)]}
        te2_summary = {
            t["output_type"]: t for t in comparison[str(test_execution_2.id)]
        }

        # te1 pass_rate: 2/3 -> 66.67, te2 pass_rate: 2/2 -> 100
        assert te1_summary["Pass/Fail"]["total_pass_rate"] == pytest.approx(
            66.67, abs=0.01
        )
        assert te2_summary["Pass/Fail"]["total_pass_rate"] == pytest.approx(
            100.0, abs=0.01
        )

        # te1 avg score: (0.8+0.6+0.4)/3*100 = 60, te2: (1.0+0.9)/2*100 = 95
        assert te1_summary["score"]["total_avg"] == pytest.approx(60.0, abs=0.01)
        assert te2_summary["score"]["total_avg"] == pytest.approx(95.0, abs=0.01)

        # Delta must be real (comparison endpoint would be pointless otherwise)
        assert (
            te2_summary["Pass/Fail"]["total_pass_rate"]
            > te1_summary["Pass/Fail"]["total_pass_rate"]
        )
        assert te2_summary["score"]["total_avg"] > te1_summary["score"]["total_avg"]

    def test_comparison_no_eval_configs_returns_empty_dict(
        self, auth_client, run_test, test_execution
    ):
        """RunTest without eval configs returns {} regardless of execution_ids."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.get(
            url, {"execution_ids": json.dumps([str(test_execution.id)])}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": True, "result": {}}

    def test_comparison_other_workspace_returns_404(
        self,
        auth_client,
        organization,
        user,
        agent_definition,
        simulator_agent,
        test_execution,
    ):
        hidden_run_test, _ = _make_other_workspace_test_execution(
            organization, user, agent_definition, simulator_agent
        )
        url = self.URL_TEMPLATE.format(hidden_run_test.id)
        response = auth_client.get(
            url, {"execution_ids": json.dumps([str(test_execution.id)])}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_comparison_unknown_uuid_returns_404(self, auth_client, test_execution):
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.get(
            url, {"execution_ids": json.dumps([str(test_execution.id)])}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
