"""Populated-fixture functional tests for analytics + eval-summary read endpoints."""

import json
import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.agent_optimiser import AgentOptimiser
from simulate.models.agent_optimiser_run import AgentOptimiserRun
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
        assert result["last_updated"].startswith(
            run.updated_at.isoformat()[:19]
        )

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
        assert templates_by_type["score"]["total_avg"] == pytest.approx(
            95.0, abs=0.01
        )

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
