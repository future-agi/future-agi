"""
Integration tests for KPI SQL query optimization.

Tests cover:
1. get_kpi_metrics_query — single-query aggregation of all counts + metric averages
2. get_kpi_eval_metrics_query — SQL-based eval_outputs aggregation via jsonb_each
3. RunTestKPIsView API — end-to-end API response validation
"""

import uuid

import pytest
from django.db import connection
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.utils.sql_query import get_kpi_eval_metrics_query, get_kpi_metrics_query

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="KPI Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent for KPI tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def chat_agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="KPI Chat Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567891",
        inbound=True,
        description="Chat agent for KPI tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="KPI Simulator Agent",
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
        name="KPI Test Dataset",
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
        name="KPI Test Scenario",
        description="Scenario for KPI tests",
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
        name="KPI Test Run",
        description="Run for KPI tests",
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
        total_calls=5,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def voice_call_executions(db, test_execution, scenario):
    """Create call executions with voice metrics."""
    calls = []
    statuses = ["completed", "completed", "failed", "pending", "registered"]
    for i, call_status in enumerate(statuses):
        ce = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number=f"+100000000{i}",
            status=call_status,
            overall_score=80.0 + i * 5 if call_status == "completed" else None,
            response_time_ms=200 + i * 50 if call_status == "completed" else None,
            duration_seconds=120 + i * 30 if call_status == "completed" else 0,
            avg_agent_latency_ms=150.0 if call_status == "completed" else None,
            user_interruption_count=2 if call_status == "completed" else None,
            user_interruption_rate=0.15 if call_status == "completed" else None,
            user_wpm=130.0 if call_status == "completed" else None,
            bot_wpm=160.0 if call_status == "completed" else None,
            talk_ratio=1.2 if call_status == "completed" else None,
            ai_interruption_count=1 if call_status == "completed" else None,
            ai_interruption_rate=0.05 if call_status == "completed" else None,
            avg_stop_time_after_interruption_ms=(
                300.0 if call_status == "completed" else None
            ),
        )
        calls.append(ce)
    return calls


@pytest.fixture
def chat_call_executions(db, test_execution, scenario):
    """Create call executions with chat metrics in conversation_metrics_data."""
    calls = []
    for i in range(3):
        ce = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number=f"+200000000{i}",
            status="completed",
            overall_score=70.0 + i * 10,
            response_time_ms=100 + i * 25,
            duration_seconds=0,
            conversation_metrics_data={
                "total_tokens": 500 + i * 100,
                "input_tokens": 200 + i * 50,
                "output_tokens": 300 + i * 50,
                "avg_latency_ms": 80 + i * 10,
                "turn_count": 5 + i * 2,  # 5, 7, 9 → avg 7.0 (integer)
                "csat_score": 4.0 + i * 0.5,
            },
        )
        calls.append(ce)
    return calls


@pytest.fixture
def eval_call_executions(db, test_execution, scenario):
    """Create call executions with eval_outputs for eval metrics testing."""
    metric_id_1 = str(uuid.uuid4())
    metric_id_2 = str(uuid.uuid4())

    calls = []
    # Call 1: Pass/Fail = Passed, score = 0.85
    ce1 = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+3000000001",
        status="completed",
        eval_outputs={
            metric_id_1: {
                "name": "Quality Check",
                "output": "Passed",
                "output_type": "Pass/Fail",
            },
            metric_id_2: {
                "name": "Accuracy",
                "output": 0.85,
                "output_type": "score",
            },
        },
    )
    calls.append(ce1)

    # Call 2: Pass/Fail = Failed, score = 0.70
    ce2 = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+3000000002",
        status="completed",
        eval_outputs={
            metric_id_1: {
                "name": "Quality Check",
                "output": "Failed",
                "output_type": "Pass/Fail",
            },
            metric_id_2: {
                "name": "Accuracy",
                "output": 0.70,
                "output_type": "score",
            },
        },
    )
    calls.append(ce2)

    return calls, metric_id_1, metric_id_2


# ============================================================================
# get_kpi_metrics_query Tests
# ============================================================================


@pytest.mark.integration
class TestGetKpiMetricsQuery:
    """Tests for the get_kpi_metrics_query SQL function."""

    def test_counts_by_status(self, voice_call_executions, test_execution):
        """Test that status counts are correct."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        assert m["total_calls"] == 5
        assert m["pending_calls"] == 1
        assert m["queued_calls"] == 1
        assert m["failed_calls"] == 1
        assert m["completed_calls"] == 2

    def test_voice_metric_averages(self, voice_call_executions, test_execution):
        """Test that voice metric averages are computed correctly."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        # avg_agent_latency: only completed calls have 150.0, so avg = 150
        assert float(m["avg_agent_latency"]) == 150.0
        # avg_user_wpm: only completed calls have 130.0
        assert float(m["avg_user_wpm"]) == 130.0
        # avg_bot_wpm: only completed calls have 160.0
        assert float(m["avg_bot_wpm"]) == 160.0

    def test_connected_voice_calls(self, voice_call_executions, test_execution):
        """Test connected_voice_calls counts calls with duration > 0."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        # 2 completed calls have duration > 0
        assert m["connected_voice_calls"] == 2

    def test_total_duration(self, voice_call_executions, test_execution):
        """Test total duration sums correctly."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        # completed calls: 120 + 150 = 270
        assert m["total_duration"] == 270

    def test_chat_metrics_from_json(self, chat_call_executions, test_execution):
        """Test chat metrics extracted from conversation_metrics_data JSON."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        # total_tokens: (500 + 600 + 700) / 3 = 600
        assert float(m["avg_total_tokens"]) == 600
        # input_tokens: (200 + 250 + 300) / 3 = 250
        assert float(m["avg_input_tokens"]) == 250
        # output_tokens: (300 + 350 + 400) / 3 = 350
        assert float(m["avg_output_tokens"]) == 350
        # turn_count: (5 + 7 + 9) / 3 = 7.0 (rounded to integer)
        assert float(m["avg_turn_count"]) == 7.0

    def test_empty_test_execution(self, test_execution):
        """Test query returns zeros for test execution with no call executions."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        assert m["total_calls"] == 0
        assert m["total_duration"] == 0

    def test_avg_score_with_nulls(self, voice_call_executions, test_execution):
        """Test that AVG ignores NULLs (non-completed calls have NULL score)."""
        query, params = get_kpi_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
        m = dict(zip(columns, row))

        # overall_score: 80.0 and 85.0 for the 2 completed calls → avg = 82.5
        assert float(m["avg_score"]) == 82.5


# ============================================================================
# get_kpi_eval_metrics_query Tests
# ============================================================================


@pytest.mark.integration
class TestGetKpiEvalMetricsQuery:
    """Tests for the get_kpi_eval_metrics_query SQL function."""

    def test_pass_fail_aggregation(self, eval_call_executions, test_execution):
        """Test Pass/Fail metric averages (Passed=100, Failed=0)."""
        calls, metric_id_1, metric_id_2 = eval_call_executions

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        # Find the Pass/Fail row
        pass_fail_rows = [r for r in rows if r[2] == "Pass/Fail"]
        assert len(pass_fail_rows) == 1
        pf = pass_fail_rows[0]
        # metric_name
        assert pf[1] == "Quality Check"
        # avg_value: (100 + 0) / 2 = 50.0
        assert float(pf[3]) == 50.0

    def test_score_aggregation(self, eval_call_executions, test_execution):
        """Test score metric averages (value * 100)."""
        calls, metric_id_1, metric_id_2 = eval_call_executions

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        # Find the score row
        score_rows = [r for r in rows if r[2] == "score"]
        assert len(score_rows) == 1
        sr = score_rows[0]
        # metric_name
        assert sr[1] == "Accuracy"
        # avg_value: (0.85*100 + 0.70*100) / 2 = 77.5
        assert float(sr[3]) == 77.5

    def test_no_eval_outputs(self, test_execution, scenario):
        """Test query returns empty for calls without eval_outputs."""
        # Create a call without eval_outputs
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9000000001",
            status="completed",
        )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        assert len(rows) == 0

    def test_choice_string_aggregation(self, test_execution, scenario):
        """Test choices with string values are counted correctly."""
        metric_id = str(uuid.uuid4())

        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+4000000001",
            status="completed",
            eval_outputs={
                metric_id: {
                    "name": "Sentiment",
                    "output": "positive",
                    "output_type": "choices",
                },
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+4000000002",
            status="completed",
            eval_outputs={
                metric_id: {
                    "name": "Sentiment",
                    "output": "positive",
                    "output_type": "choices",
                },
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+4000000003",
            status="completed",
            eval_outputs={
                metric_id: {
                    "name": "Sentiment",
                    "output": "negative",
                    "output_type": "choices",
                },
            },
        )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        choice_rows = [r for r in rows if r[2] == "choices" and r[4] is not None]
        choice_map = {r[4]: r[5] for r in choice_rows}

        assert choice_map.get("positive") == 2
        assert choice_map.get("negative") == 1

    def test_choice_numeric_aggregation(self, test_execution, scenario):
        """Test choices with numeric values are averaged."""
        metric_id = str(uuid.uuid4())

        for val in [3, 5, 7]:
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+500000000{val}",
                status="completed",
                eval_outputs={
                    metric_id: {
                        "name": "Rating",
                        "output": val,
                        "output_type": "choices",
                    },
                },
            )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        numeric_rows = [
            r for r in rows if r[2] == "choices" and r[3] is not None and r[4] is None
        ]
        assert len(numeric_rows) == 1
        # avg of 3, 5, 7 = 5.0
        assert float(numeric_rows[0][3]) == 5.0

    def test_fully_errored_pass_fail_emits_zero_row(self, test_execution, scenario):
        """Pass/Fail metric where every entry errored still shows up as a
        scalar row with NULL avg, so the handler renders a 0% bar instead
        of dropping the metric."""
        metric_id = str(uuid.uuid4())

        for i in range(2):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+600000000{i}",
                status="completed",
                eval_outputs={
                    metric_id: {
                        "name": "Quality Check",
                        "error": "error",
                        "status": "failed",
                        "output": None,
                        "output_type": "Pass/Fail",
                    },
                },
            )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        pass_fail_rows = [r for r in rows if r[2] == "Pass/Fail"]
        assert len(pass_fail_rows) == 1
        assert pass_fail_rows[0][1] == "Quality Check"
        # AVG over all-NULL CASE results yields NULL
        assert pass_fail_rows[0][3] is None

    def test_fully_errored_score_emits_zero_row(self, test_execution, scenario):
        """Score metric where every entry errored emits a scalar row with
        NULL avg."""
        metric_id = str(uuid.uuid4())

        for i in range(3):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+700000000{i}",
                status="completed",
                eval_outputs={
                    metric_id: {
                        "name": "Accuracy",
                        "error": "error",
                        "status": "failed",
                        "output": None,
                        "output_type": "score",
                    },
                },
            )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        score_rows = [r for r in rows if r[2] == "score"]
        assert len(score_rows) == 1
        assert score_rows[0][1] == "Accuracy"
        assert score_rows[0][3] is None

    def test_fully_errored_choices_emits_metric_row(self, test_execution, scenario):
        """Choices metric where every entry errored emerges from
        choice_errored_agg so the chart card still renders."""
        metric_id = str(uuid.uuid4())

        for i in range(2):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+800000000{i}",
                status="completed",
                eval_outputs={
                    metric_id: {
                        "name": "Sentiment",
                        "error": "error",
                        "status": "failed",
                        "output": None,
                        "output_type": "choices",
                    },
                },
            )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        choices_rows = [r for r in rows if r[2] == "choices"]
        assert len(choices_rows) == 1
        row = choices_rows[0]
        assert row[1] == "Sentiment"
        # avg_value, choice_value both NULL; choice_count = 0
        assert row[3] is None
        assert row[4] is None
        assert row[5] == 0

    def test_partial_errored_choices_aggregates_successes_only(
        self, test_execution, scenario
    ):
        """When some entries succeed and others error, choice_errored_agg
        must NOT emit (bool_and is false); the successful entries are
        aggregated by the existing choice_agg path."""
        metric_id = str(uuid.uuid4())

        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9000000001",
            status="completed",
            eval_outputs={
                metric_id: {
                    "name": "Sentiment",
                    "output": "positive",
                    "output_type": "choices",
                },
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+9000000002",
            status="completed",
            eval_outputs={
                metric_id: {
                    "name": "Sentiment",
                    "error": "error",
                    "status": "failed",
                    "output": None,
                    "output_type": "choices",
                },
            },
        )

        query, params = get_kpi_eval_metrics_query(test_execution.id)
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        choices_rows = [r for r in rows if r[2] == "choices"]
        # Only the choice_agg row for "positive" should appear — no extra
        # zero-row from choice_errored_agg.
        assert len(choices_rows) == 1
        assert choices_rows[0][4] == "positive"
        assert choices_rows[0][5] == 1


@pytest.mark.unit
class TestDeriveKpiOutputType:
    """Maps EvalTemplate.output_type_normalized to the runtime KPI
    output_type the SQL aggregation pipeline keys off."""

    def test_known_mappings(self):
        from types import SimpleNamespace

        from simulate.utils.eval_summary import derive_kpi_output_type

        cases = {
            "pass_fail": "Pass/Fail",
            "percentage": "score",
            "deterministic": "choices",
        }
        for normalized, expected in cases.items():
            tpl = SimpleNamespace(output_type_normalized=normalized)
            assert derive_kpi_output_type(tpl) == expected

    def test_unknown_or_missing_falls_back_to_score(self):
        from types import SimpleNamespace

        from simulate.utils.eval_summary import derive_kpi_output_type

        assert derive_kpi_output_type(None) == "score"
        assert (
            derive_kpi_output_type(SimpleNamespace(output_type_normalized=None))
            == "score"
        )
        assert (
            derive_kpi_output_type(SimpleNamespace(output_type_normalized="custom"))
            == "score"
        )
        assert derive_kpi_output_type(SimpleNamespace()) == "score"


# ============================================================================
# RunTestKPIsView API Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestKPIsViewAPI:
    """End-to-end API tests for the optimized KPIs endpoint."""

    def test_kpi_response_structure(
        self, auth_client, voice_call_executions, test_execution
    ):
        """Test that KPI API returns all expected fields."""
        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/kpis/"
        )
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        # Core fields
        assert "total_calls" in data
        assert "avg_score" in data
        assert "avg_response" in data
        assert "calls_attempted" in data
        assert "connected_calls" in data
        assert "calls_connected_percentage" in data
        assert "agent_type" in data
        assert "failed_calls" in data
        assert "total_duration" in data

        # Voice metrics
        assert "avg_agent_latency" in data
        assert "avg_user_interruption_count" in data
        assert "agent_talk_percentage" in data
        assert "customer_talk_percentage" in data

        # Chat metrics
        assert "avg_total_tokens" in data
        assert "avg_input_tokens" in data
        assert "avg_output_tokens" in data
        assert "avg_turn_count" in data

    def test_kpi_values_correct(
        self, auth_client, voice_call_executions, test_execution
    ):
        """Test that KPI values match expected calculations."""
        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/kpis/"
        )
        data = response.json()

        assert data["total_calls"] == 5
        assert data["failed_calls"] == 1
        assert data["total_duration"] == 270

    def test_kpi_chat_agent(
        self,
        auth_client,
        chat_call_executions,
        test_execution,
        chat_agent_definition,
    ):
        """Test KPIs for chat agent type use completed_calls for connected count."""
        # Update the test execution to use the chat agent definition
        test_execution.agent_definition = chat_agent_definition
        test_execution.save()

        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/kpis/"
        )
        data = response.json()

        assert data["agent_type"] == AgentDefinition.AgentTypeChoices.TEXT
        # All 3 calls are completed
        assert data["connected_calls"] == 3

    def test_kpi_with_eval_outputs(
        self, auth_client, eval_call_executions, test_execution
    ):
        """Test that eval averages are included in KPI response."""
        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/kpis/"
        )
        data = response.json()

        # Should have eval average fields
        assert "avg_quality_check" in data  # Pass/Fail → avg_quality_check
        assert "avg_accuracy" in data  # score → avg_accuracy

    def test_kpi_empty_execution(self, auth_client, test_execution):
        """Test KPIs for execution with no call executions returns zeros."""
        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/kpis/"
        )
        data = response.json()

        assert data["total_calls"] == 0
        assert data["avg_score"] == 0
        assert data["total_duration"] == 0
