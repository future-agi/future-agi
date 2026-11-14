import json

import pytest

from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution


@pytest.fixture
def execution_with_chat_metrics(db, organization, workspace):
    scenario = Scenarios.objects.create(
        name="Grouped chat scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
    )
    run_test = RunTest.objects.create(
        name="Grouped chat run",
        organization=organization,
        workspace=workspace,
    )
    execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        execution_metadata={
            "Provider": True,
            "column_order": [{"id": "status", "column_name": "Status"}],
        },
    )
    calls = []
    for tokens, latency, csat in [(20, 100, 3.0), (40, 300, 5.0)]:
        calls.append(
            CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.TEXT,
                overall_score=csat,
                response_time_ms=latency,
                conversation_metrics_data={
                    "total_tokens": tokens,
                    "input_tokens": tokens // 2,
                    "output_tokens": tokens // 2,
                    "avg_latency_ms": latency,
                    "turn_count": tokens // 10,
                    "csat_score": csat,
                },
            )
        )
    # Match timestamps because the existing ordering separates otherwise equal groups.
    CallExecution.objects.filter(id=calls[1].id).update(created_at=calls[0].created_at)
    return execution, calls


def test_grouped_execution_detail_averages_chat_metrics(
    auth_client, execution_with_chat_metrics
):
    execution, _ = execution_with_chat_metrics

    response = auth_client.get(
        f"/simulate/test-executions/{execution.id}/",
        {"row_groups": json.dumps(["status"])},
    )

    assert response.status_code == 200, response.data
    assert response.data["count"] == 1
    assert response.data["results"] == [
        {
            "status": CallExecution.CallStatus.COMPLETED,
            "count": 2,
            "avg_overall_score": 4.0,
            "avg_response_time": 200.0,
            "total_tokens": 30.0,
            "input_tokens": 15.0,
            "output_tokens": 15.0,
            "avg_latency_ms": 200.0,
            "turn_count": 3.0,
            "csat_score": 4.0,
        }
    ]


def test_execution_detail_without_grouping_keeps_call_rows(
    auth_client, execution_with_chat_metrics
):
    execution, calls = execution_with_chat_metrics

    response = auth_client.get(f"/simulate/test-executions/{execution.id}/")

    assert response.status_code == 200, response.data
    assert response.data["count"] == 2
    assert {row["id"] for row in response.data["results"]} == {
        str(call.id) for call in calls
    }
