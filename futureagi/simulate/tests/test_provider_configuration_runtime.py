"""Runtime consumers of version-bound provider configuration.

Use real ORM rows and credential decryption, with synthetic credentials only.
Provider transports, model calls and billing are replaced at their boundaries.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync

from simulate.models import AgentDefinition, AgentVersion, RunTest, Scenarios
from simulate.models.agent_definition import ProviderCredentials
from simulate.models.test_execution import CallExecution, TestExecution

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.fixture
def voice_call_factory(organization, workspace):
    def make_call(provider="vapi", *, provider_mode="absent", direction="inbound"):
        definition = AgentDefinition.objects.create(
            agent_name="Synthetic runtime agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            provider=provider,
            api_key="synthetic-definition-key",
            assistant_id="definition-assistant",
            organization=organization,
            workspace=workspace,
        )
        snapshot = {
            "assistant_id": "snapshot-assistant",
            "contact_number": "+16505550100",
        }
        if provider_mode == "empty":
            snapshot["provider"] = ""
        elif provider_mode == "authored":
            snapshot["provider"] = provider
        version = AgentVersion.objects.create(
            agent_definition=definition,
            version_number=1,
            organization=organization,
            workspace=workspace,
            configuration_snapshot=snapshot,
        )
        credentials = ProviderCredentials.objects.create(
            agent_version=version,
            # Migration 0071 stores Bland credentials in the VAPI family.
            provider_type="retell" if provider == "retell" else "vapi",
            api_key="synthetic-version-key",
            assistant_id="credential-assistant",
        )
        if provider_mode == "authored":
            # Authored historical routing must not drift with the definition.
            definition.provider = "retell" if provider == "vapi" else "vapi"
            definition.save(update_fields=["provider"])
        scenario = Scenarios.objects.create(
            name="Synthetic runtime scenario",
            source="Synthetic scenario",
            scenario_type=Scenarios.ScenarioTypes.SCRIPT,
            agent_definition=definition,
            organization=organization,
            workspace=workspace,
        )
        run_test = RunTest.objects.create(
            name="Synthetic runtime run",
            agent_definition=definition,
            agent_version=version,
            enable_tool_evaluation=True,
            organization=organization,
            workspace=workspace,
        )
        execution = TestExecution.objects.create(
            run_test=run_test,
            agent_definition=definition,
            agent_version=version,
            # Keep completion hooks out of these consumer tests.
            status=TestExecution.ExecutionStatus.COMPLETED,
            scenario_ids=[str(scenario.id)],
        )
        call = CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            agent_version=version,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            status=CallExecution.CallStatus.ANALYZING,
            service_provider_call_id="synthetic-system-call",
            customer_call_id="synthetic-customer-call",
            call_metadata={"call_direction": direction},
        )
        return SimpleNamespace(
            definition=definition,
            version=version,
            credentials=credentials,
            run_test=run_test,
            execution=execution,
            call=call,
        )

    return make_call


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("provider", ["bland", "retell"])
@pytest.mark.parametrize("provider_mode", ["absent", "empty", "authored"])
def test_prepare_call_routes_with_effective_provider(
    voice_call_factory, workspace, monkeypatch, provider, provider_mode
):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.temporal.types.activities import PrepareCallInput

    data = voice_call_factory(
        provider, provider_mode=provider_mode, direction="outbound"
    )
    snapshot = dict(data.version.configuration_snapshot)
    ciphertext = data.credentials.api_key
    monkeypatch.delenv("SYSTEM_VOICE_PROVIDER", raising=False)
    # Only Temporal context is mocked; prepare_call, ORM, and resolver run.
    with patch("temporalio.activity.info"):
        result = async_to_sync(prepare_call)(
            PrepareCallInput(call_id=str(data.call.id), workspace_id=str(workspace.id))
        )

    assert result.error is None
    assert result.is_outbound is True
    assert result.client_provider == provider
    assert result.client_api_key == "synthetic-version-key"
    assert result.client_assistant_id == "snapshot-assistant"
    assert result.client_phone_number == "+16505550100"
    assert result.client_uses_own_provider is True
    assert result.connection_type is None  # Phone/SIP path, not web_vapi.
    data.version.refresh_from_db()
    data.credentials.refresh_from_db()
    assert data.version.configuration_snapshot == snapshot
    assert data.credentials.api_key == ciphertext


@pytest.mark.parametrize("provider", ["bland", "retell", "vapi"])
@pytest.mark.parametrize("provider_mode", ["absent", "empty", "authored"])
def test_standalone_tool_adapter_uses_effective_provider(
    voice_call_factory, provider, provider_mode
):
    from simulate.temporal.activities import xl

    data = voice_call_factory(provider, provider_mode=provider_mode)
    snapshot = dict(data.version.configuration_snapshot)
    with (
        patch("ee.agenthub.tool_eval_agent.tool_eval_agent.ToolEvalAgent") as judge,
        patch("ee.agenthub.tool_eval_agent.adapters.get_tool_call_adapter") as factory,
        patch.object(xl, "logger") as log,
    ):
        factory.return_value.get_tool_call_transcript.return_value = []
        judge.return_value.evaluate_tool_calls.return_value = {
            "tool_calls_data": [],
            "conversation_context": [],
        }
        xl._run_tool_evaluation_standalone(data.call, data.execution)

    log.error.assert_not_called()
    if provider == "vapi":
        factory.assert_called_once_with("vapi")
        factory.return_value.get_tool_call_transcript.assert_called_once_with(
            call_execution=data.call,
            customer_call_id="synthetic-customer-call",
            api_key="synthetic-version-key",
        )
        judge.return_value.evaluate_tool_calls.assert_called_once_with([])
        data.call.refresh_from_db()
        assert data.call.evaluation_data["tool_column_order"] == []
    else:
        # Neither Bland nor Retell has a tool adapter today. The legacy
        # snapshot must reach this explicit skip, not accidentally use Vapi.
        factory.assert_not_called()
        judge.return_value.evaluate_tool_calls.assert_not_called()
        assert any(
            f"Tool evaluation not supported for provider '{provider}'" in args[0]
            for args, _ in log.info.call_args_list
        )
        data.call.refresh_from_db()
        assert not data.call.evaluation_data
    data.version.refresh_from_db()
    assert data.version.configuration_snapshot == snapshot


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
@pytest.mark.parametrize("source", ["version", "definition", "empty-snapshot"])
def test_evaluation_task_reaches_customer_tool_evaluation(
    voice_call_factory, source, direction
):
    from simulate.services import test_executor
    from tfc.constants.api_calls import APICallStatusChoices

    data = voice_call_factory(direction=direction)
    # Exercise the original task's matching path, not a pre-found customer ID.
    data.call.customer_call_id = None
    data.call.save(update_fields=["customer_call_id"])
    if source == "definition":
        data.credentials.api_key = ""
        data.credentials.save(update_fields=["api_key"])
        expected_key, expected_assistant = (
            "synthetic-definition-key",
            "definition-assistant",
        )
    else:
        expected_key, expected_assistant = (
            "synthetic-version-key",
            "credential-assistant",
        )
    if source == "empty-snapshot":
        # Model.save auto-snapshots empty dicts; model an existing empty row.
        AgentVersion.objects.filter(id=data.version.id).update(
            configuration_snapshot={}
        )
        data.version.refresh_from_db()
    snapshot = dict(data.version.configuration_snapshot)
    assert "api_key" not in snapshot
    assert "provider" not in snapshot

    context = [{"speaker_role": "user", "content": "Look up synthetic order"}]
    tool_calls = [{"tool_name": "lookup", "tool_call_id": "synthetic-tool"}]
    with (
        patch.object(test_executor, "VoiceServiceManager") as manager,
        patch.object(test_executor, "ToolEvalAgent") as judge,
        patch.object(
            test_executor,
            "log_and_deduct_cost_for_api_request",
            return_value=SimpleNamespace(status=APICallStatusChoices.PROCESSING.value),
        ) as billing,
    ):
        manager.return_value.get_call.return_value = {"synthetic": "system-call"}
        manager.return_value.find_client_call.return_value = "synthetic-matched-call"
        judge.return_value.llm = SimpleNamespace(model_name="synthetic-model")
        judge.return_value._get_call_data_from_provider.return_value = {
            "conversation_context": context
        }
        judge.return_value._extract_tool_calls.return_value = tool_calls
        judge.return_value._evaluate_single_tool_call.return_value = {
            "result": True,
            "summary": "Synthetic tool result",
        }
        # Directly invokes the decorated task's real synchronous entry point.
        # Neither executor initialization nor its orchestration is mocked.
        result = test_executor._run_simulate_evaluations_task(
            str(data.call.id), eval_config_ids=[], skip_existing=False
        )

    assert result is True
    manager.assert_called_once_with(system_voice_provider="vapi")
    manager.return_value.get_call.assert_called_once_with(
        "synthetic-system-call", call_data_stored=True
    )
    if direction == "inbound":
        manager.return_value.find_client_call.assert_called_once()
        match_input = manager.return_value.find_client_call.call_args.args[0]
        assert match_input.customer_api_key == expected_key
        assert match_input.customer_assistant_id == expected_assistant
        assert match_input.customer_voice_service_provider == "vapi"
        assert match_input.time_window_seconds == 10
        expected_call_id = "synthetic-matched-call"
    else:
        manager.return_value.find_client_call.assert_not_called()
        expected_call_id = "synthetic-system-call"
    judge.return_value._get_call_data_from_provider.assert_called_once_with(
        expected_call_id, api_key=expected_key
    )
    judge.return_value._evaluate_single_tool_call.assert_called_once_with(
        tool_call=tool_calls[0],
        conversation_context=context,
        all_tool_calls=tool_calls,
    )
    billing.assert_called_once()
    data.call.refresh_from_db()
    data.execution.refresh_from_db()
    assert data.call.status == CallExecution.CallStatus.COMPLETED
    assert data.call.call_metadata["eval_completed"] is True
    assert len(data.call.tool_outputs) == 1
    assert list(data.call.tool_outputs.values()) == [
        {
            "value": True,
            "reason": "Synthetic tool result",
            "type": "Pass/Fail",
            "name": "lookup #1",
            "error": False,
            "status": "completed",
        }
    ]
    column = data.call.evaluation_data["tool_column_order"][0]
    assert column["id"] in data.call.tool_outputs
    assert data.execution.execution_metadata["column_order"] == [column]
    data.version.refresh_from_db()
    assert data.version.configuration_snapshot == snapshot


@pytest.mark.parametrize(
    "state", ["disabled", "wrong-provider", "unreadable", "bland", "retell"]
)
def test_evaluation_task_skips_without_initializing_voice_transport(
    voice_call_factory, state
):
    from simulate.services import test_executor

    provider = state if state in {"bland", "retell"} else "vapi"
    data = voice_call_factory(provider)
    if state == "disabled":
        data.run_test.enable_tool_evaluation = False
        data.run_test.save(update_fields=["enable_tool_evaluation"])
    elif state == "wrong-provider":
        data.credentials.provider_type = "retell"
        data.credentials.save(update_fields=["provider_type"])
    elif state == "unreadable":
        ProviderCredentials.objects.filter(id=data.credentials.id).update(
            api_key="enc::synthetic-invalid-ciphertext"
        )

    with (
        patch.object(test_executor, "VoiceServiceManager") as manager,
        patch.object(test_executor, "ToolEvalAgent") as judge,
        patch.object(test_executor, "log_and_deduct_cost_for_api_request") as billing,
    ):
        result = test_executor._run_simulate_evaluations_task(
            str(data.call.id), eval_config_ids=[]
        )

    # Existing task outcome contract: a skipped tool evaluation does not make
    # the enclosing evaluation task fail or leave eval_completed pending.
    assert result is True
    manager.assert_not_called()
    judge.return_value._get_call_data_from_provider.assert_not_called()
    judge.return_value._evaluate_single_tool_call.assert_not_called()
    billing.assert_not_called()
    if state == "disabled":
        judge.assert_not_called()
    data.call.refresh_from_db()
    assert not data.call.tool_outputs
    assert data.call.call_metadata["eval_completed"] is True


def test_evaluation_task_keeps_text_tool_evaluation_provider_neutral(
    voice_call_factory,
):
    from simulate.services import test_executor

    data = voice_call_factory()
    data.definition.agent_type = AgentDefinition.AgentTypeChoices.TEXT
    data.definition.save(update_fields=["agent_type"])
    data.call.simulation_call_type = CallExecution.SimulationCallType.TEXT
    data.call.service_provider_call_id = None
    data.call.save(update_fields=["simulation_call_type", "service_provider_call_id"])
    with (
        patch.object(test_executor, "VoiceServiceManager") as manager,
        patch.object(test_executor, "ToolEvalAgent") as judge,
    ):
        judge.return_value._get_chat_data_from_database.return_value = {
            "conversation_context": [],
            "messages": [],
        }
        judge.return_value._extract_tool_calls.return_value = []
        result = test_executor._run_simulate_evaluations_task(
            str(data.call.id), eval_config_ids=[]
        )

    assert result is True
    manager.assert_not_called()
    judge.return_value._get_chat_data_from_database.assert_called_once()
    assert (
        judge.return_value._get_chat_data_from_database.call_args.args[0].id
        == data.call.id
    )
    data.call.refresh_from_db()
    assert data.call.evaluation_data["tool_column_order"] == []
    assert data.call.call_metadata["eval_completed"] is True


def test_evaluation_task_keeps_temporal_dispatch_contract():
    from simulate.services.test_executor import _run_simulate_evaluations_task

    assert (
        _run_simulate_evaluations_task._activity_name
        == "_run_simulate_evaluations_task"
    )
    metadata = _run_simulate_evaluations_task._metadata
    assert metadata["queue"] == "tasks_xl"
    assert metadata["time_limit"] == 3600
    assert metadata["max_retries"] == 0
    assert metadata["retry_delay"] == 300
