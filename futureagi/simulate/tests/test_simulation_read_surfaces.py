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


@pytest.fixture
def three_eval_configs(db, simulation_tree, organization):
    """Two evals present at TE creation, one added later on the run_test."""
    template = EvalTemplate.objects.create(
        name="Reconcile Late-Add Template", config={}, organization=organization
    )
    run_test = simulation_tree["run_test"]
    test_execution = simulation_tree["test_execution"]
    orig_a = SimulateEvalConfig.objects.create(
        name="Task", eval_template=template, run_test=run_test
    )
    orig_b = SimulateEvalConfig.objects.create(
        name="Prompt", eval_template=template, run_test=run_test
    )
    test_execution.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "type": "scenario_dataset_column",
                "id": "scenario_col",
                "column_name": "Scenario",
            },
            {"type": "evaluation", "id": str(orig_a.id), "column_name": "Task"},
            {"type": "evaluation", "id": str(orig_b.id), "column_name": "Prompt"},
        ],
    }
    test_execution.save(update_fields=["execution_metadata"])
    late_add = SimulateEvalConfig.objects.create(
        name="Toxicity", eval_template=template, run_test=run_test
    )
    return {"orig_a": orig_a, "orig_b": orig_b, "late_add": late_add}


@pytest.mark.django_db
def test_late_added_eval_column_appears_only_after_it_has_been_evaluated(
    auth_client, simulation_tree, three_eval_configs
):
    """Adding a 3rd eval on the run_test alone must not add a phantom
    column on TE#1; only after it has run against TE#1's calls does the
    column land."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    orig_a = three_eval_configs["orig_a"]
    orig_b = three_eval_configs["orig_b"]
    late_add = three_eval_configs["late_add"]

    response_before = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/"
    )
    assert response_before.status_code == 200
    eval_cols_before = [
        c
        for c in response_before.data["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in eval_cols_before] == [
        str(orig_a.id),
        str(orig_b.id),
    ]

    call_execution.eval_outputs = {
        str(orig_a.id): {"status": "completed", "output": "Passed"},
        str(orig_b.id): {"status": "completed", "output": "Passed"},
        str(late_add.id): {"status": "completed", "output": "Passed"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    response_after = auth_client.get(
        f"/simulate/test-executions/{test_execution.id}/"
    )
    assert response_after.status_code == 200
    eval_cols_after = [
        c
        for c in response_after.data["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in eval_cols_after] == [
        str(orig_a.id),
        str(orig_b.id),
        str(late_add.id),
    ]

    test_execution.refresh_from_db()
    persisted = [
        c
        for c in test_execution.execution_metadata["column_order"]
        if c.get("type") == "evaluation"
    ]
    assert [str(c["id"]) for c in persisted] == [
        str(orig_a.id),
        str(orig_b.id),
        str(late_add.id),
    ]


@pytest.mark.django_db
def test_errored_eval_output_still_surfaces_the_column(
    auth_client, simulation_tree, three_eval_configs
):
    """An eval that ran but errored is still 'attempted' - the column
    must surface so the user can see the failure."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    late_add = three_eval_configs["late_add"]
    call_execution.eval_outputs = {
        str(late_add.id): {"status": "error", "error": "boom"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(c["id"])
        for c in response.data["column_order"]
        if c.get("type") == "evaluation"
    }
    assert str(late_add.id) in eval_col_ids


@pytest.mark.django_db
def test_corrupted_eval_outputs_does_not_crash_the_view(
    auth_client, simulation_tree, three_eval_configs
):
    """Legacy / bad-actor rows with a non-dict `eval_outputs` (string,
    list, etc.) must not 500 the detail GET. The reconciler skips them."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    # Bypass model-level validation to persist a legacy-shape payload.
    CallExecution.objects.filter(id=call_execution.id).update(
        eval_outputs="not-a-dict-corrupted-legacy"
    )

    response = auth_client.get(f"/simulate/test-executions/{test_execution.id}/")

    assert response.status_code == 200
    eval_col_ids = {
        str(c["id"])
        for c in response.data["column_order"]
        if c.get("type") == "evaluation"
    }
    # Only the two originals that were already in column_order survive;
    # the late-add is not appended because the corrupted row contributes
    # nothing to evaluated_eval_ids.
    orig_a = three_eval_configs["orig_a"]
    orig_b = three_eval_configs["orig_b"]
    assert eval_col_ids == {str(orig_a.id), str(orig_b.id)}


@pytest.mark.django_db
def test_second_get_is_a_noop_no_repeated_writes(
    auth_client, simulation_tree, three_eval_configs
):
    """Once column_order matches the live state, subsequent GETs must
    not re-save. Guards against a subtle write-loop regression."""
    test_execution = simulation_tree["test_execution"]
    call_execution = simulation_tree["call_execution"]
    late_add = three_eval_configs["late_add"]
    call_execution.eval_outputs = {
        str(late_add.id): {"status": "completed", "output": "Passed"},
    }
    call_execution.save(update_fields=["eval_outputs"])

    # First GET reconciles; second GET must land the same column_order
    # with no additional persistence.
    auth_client.get(f"/simulate/test-executions/{test_execution.id}/")
    test_execution.refresh_from_db()
    updated_at_after_first = test_execution.updated_at

    auth_client.get(f"/simulate/test-executions/{test_execution.id}/")
    test_execution.refresh_from_db()

    assert test_execution.updated_at == updated_at_after_first


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
