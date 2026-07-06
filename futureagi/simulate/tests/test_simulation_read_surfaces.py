import pytest

from accounts.models.user import OrgApiKey
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    CallTranscript,
    TestExecution as SimulationTestExecution,
)
from simulate.serializers.test_execution import CallExecutionDetailSerializer


@pytest.fixture
def simulation_tree(db, organization, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name="Transcript Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for transcript read API tests.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator_agent = SimulatorAgent.objects.create(
        name="Transcript Simulator",
        prompt="Simulate a customer.",
        organization=organization,
        workspace=workspace,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4o-mini",
    )
    scenario = Scenarios.objects.create(
        name="Transcript Scenario",
        description="Scenario for transcript read API tests.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    run_test = RunTest.objects.create(
        name="Transcript Run",
        description="Run for transcript read API tests.",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )
    call_execution = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+15551234567",
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={},
    )
    CallTranscript.objects.create(
        call_execution=call_execution,
        speaker_role=CallTranscript.SpeakerRole.USER,
        content="I need help scheduling an interview.",
        start_time_ms=0,
        end_time_ms=1000,
    )
    CallTranscript.objects.create(
        call_execution=call_execution,
        speaker_role=CallTranscript.SpeakerRole.ASSISTANT,
        content="I can help with that.",
        start_time_ms=1000,
        end_time_ms=2000,
    )
    return {
        "run_test": run_test,
        "test_execution": test_execution,
        "call_execution": call_execution,
    }


def _result(response):
    return response.data.get("result", response.data)


@pytest.mark.django_db
def test_call_transcript_views_traverse_run_test_organization(
    auth_client, simulation_tree
):
    call_execution = simulation_tree["call_execution"]
    test_execution = simulation_tree["test_execution"]

    response = auth_client.get(
        f"/simulate/call-executions/{call_execution.id}/transcripts/"
    )

    assert response.status_code == 200
    assert response.data["call_execution_id"] == str(call_execution.id)
    assert response.data["total_transcripts"] == 2
    assert [row["speaker_role"] for row in response.data["transcripts"]] == [
        "user",
        "assistant",
    ]

    response = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/transcripts/"
    )

    assert response.status_code == 200
    assert response.data["test_execution_id"] == test_execution.id
    assert response.data["total_calls"] == 1
    assert response.data["total_transcripts"] == 2
    assert response.data["calls"][0]["call_execution_id"] == str(call_execution.id)


@pytest.mark.django_db
def test_chat_sdk_code_uses_placeholders_without_leaking_org_keys(
    auth_client, organization, user, simulation_tree
):
    OrgApiKey.objects.create(
        organization=organization,
        user=user,
        type="user",
        api_key="liveapikey1234567890",
        secret_key="livesecret1234567890",
    )
    run_test = simulation_tree["run_test"]

    response = auth_client.get(f"/simulate/run-tests/{run_test.id}/sdk-code/")

    assert response.status_code == 200
    payload = _result(response)
    sdk_code = payload["sdk_code"]
    assert payload["run_test_id"] == str(run_test.id)
    assert run_test.name in sdk_code
    assert "liveapikey1234567890" not in sdk_code
    assert "livesecret1234567890" not in sdk_code
    assert 'FI_API_KEY="<YOUR_FI_API_KEY>"' in sdk_code
    assert 'FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"' in sdk_code


@pytest.fixture
def eval_configs(db, simulation_tree, organization):
    template = EvalTemplate.objects.create(
        name="Read Surface Eval Template",
        config={},
        organization=organization,
    )
    run_test = simulation_tree["run_test"]
    live = SimulateEvalConfig.objects.create(
        name="Live Eval",
        eval_template=template,
        run_test=run_test,
    )
    deleted = SimulateEvalConfig.objects.create(
        name="Deleted Eval",
        eval_template=template,
        run_test=run_test,
    )
    deleted.delete()
    return {"live": live, "deleted": deleted}


def _eval_outputs_for(live, deleted):
    return {
        str(live.id): {
            "name": "Live Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
        str(deleted.id): {
            "name": "Deleted Eval",
            "output": "Passed",
            "output_type": "Pass/Fail",
            "status": "completed",
        },
    }


@pytest.mark.django_db
def test_get_eval_outputs_skips_missing_config(simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    )
    outputs = serializer.get_eval_outputs(call_execution)

    assert str(live.id) in outputs
    assert str(deleted.id) not in outputs


@pytest.mark.django_db
def test_get_eval_metrics_skips_missing_config(simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer(
        context={"eval_configs": {str(live.id): live}}
    )
    metrics = serializer.get_eval_metrics(call_execution)

    assert str(live.id) in metrics
    assert str(deleted.id) not in metrics


@pytest.mark.django_db
def test_get_eval_outputs_surfaces_all_when_context_absent(
    simulation_tree, eval_configs
):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = _eval_outputs_for(live, deleted)
    call_execution.save(update_fields=["eval_outputs"])

    serializer = CallExecutionDetailSerializer()
    outputs = serializer.get_eval_outputs(call_execution)

    assert str(live.id) in outputs
    assert str(deleted.id) in outputs


@pytest.mark.django_db
def test_column_order_drops_deleted_eval_columns(
    auth_client, simulation_tree, eval_configs
):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    test_execution = simulation_tree["test_execution"]
    test_execution.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "type": "scenario_dataset_column",
                "id": "scenario_col",
                "column_name": "Scenario",
            },
            {"type": "evaluation", "id": str(live.id), "column_name": "Live Eval"},
            {
                "type": "evaluation",
                "id": str(deleted.id),
                "column_name": "Deleted Eval",
            },
        ],
    }
    test_execution.save(update_fields=["execution_metadata"])

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(col.get("id"))
        for col in response.data["column_order"]
        if col.get("type") == "evaluation"
    }
    assert str(live.id) in eval_col_ids
    assert str(deleted.id) not in eval_col_ids


@pytest.mark.django_db
def test_csv_export_excludes_deleted_evals(auth_client, simulation_tree, eval_configs):
    live, deleted = eval_configs["live"], eval_configs["deleted"]
    call_execution = simulation_tree["call_execution"]
    call_execution.eval_outputs = {
        str(live.id): {
            "name": "Live Eval Column",
            "output": "Passed",
            "output_type": "Pass/Fail",
        },
        str(deleted.id): {
            "name": "Deleted Eval Column",
            "output": "Passed",
            "output_type": "Pass/Fail",
        },
    }
    call_execution.save(update_fields=["eval_outputs"])
    test_execution = simulation_tree["test_execution"]

    response = auth_client.get(
        f"/simulate/export/{test_execution.id}/?type=testexecution"
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Live Eval Column" in body
    assert "Deleted Eval Column" not in body
