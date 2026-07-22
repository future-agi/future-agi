"""
Unit and integration tests for chat simulation utilities.

Tests cover:
- initiate_chat: Chat session initialization
- send_message_to_chat: Message sending and handling
- store_chat_messages: Message persistence and metrics
- monitor_test_execution_for_chat: Test execution status monitoring
- monitor_chat_test_executions: Batch monitoring of test executions
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from simulate.models import AgentDefinition, AgentVersion, Scenarios
from simulate.models.chat_message import ChatMessageModel
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    EvalExplanationSummaryStatus,
    TestExecution,
)
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
)
from simulate.services.chat_sim import initiate_chat, send_message_to_chat
from simulate.services.types.chat import (
    CreateAssistantResult,
    CreateSessionResult,
    GetSessionResult,
    SendMessageResult,
)
from simulate.tasks.chat_sim import (
    monitor_chat_test_executions,
    monitor_test_execution_for_chat,
    store_chat_messages,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a test agent definition for chat (TEXT type)."""
    return AgentDefinition.objects.create(
        agent_name="Chat Support Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567890",
        inbound=True,
        description="Test chat agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    """Create a simulator agent for chat testing."""
    return SimulatorAgent.objects.create(
        name="Test Chat Simulator",
        prompt="You are a helpful customer service representative.",
        organization=organization,
        workspace=workspace,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4-turbo",
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition, simulator_agent):
    """Create a test scenario for chat simulation."""
    return Scenarios.objects.create(
        name="Chat Support Scenario",
        description="Test chat scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent, scenario):
    """Create a test run for chat simulation."""
    run_test = RunTest.objects.create(
        name="Chat Test Run",
        description="Test run for chat simulation",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, run_test, agent_definition):
    """Create a test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    """Create a call execution for chat simulation."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.PENDING,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={},
    )


@pytest.fixture
def ongoing_call_execution(db, test_execution, scenario):
    """Create an ongoing call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.ONGOING,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={
            "vapi_chat_session_id": "test-session-123",
            "simulation_assistant_id": "asst-123",
        },
        assistant_id="asst-123",
    )


# ============================================================================
# Tests for TestExecutionChatBatchView
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
def test_chat_batch_creates_text_calls_without_voice_provider_config(
    auth_client,
    monkeypatch,
    agent_definition,
    simulator_agent,
    run_test,
    test_execution,
    scenario,
):
    """Chat batching should not require voice-provider credentials."""
    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    agent_version = agent_definition.create_version(
        description="Active text chat version",
        commit_message="Initial chat version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    run_test.agent_version = agent_version
    run_test.save(update_fields=["agent_version"])
    test_execution.agent_definition = agent_definition
    test_execution.agent_version = agent_version
    test_execution.simulator_agent = simulator_agent
    test_execution.scenario_ids = [str(scenario.id)]
    test_execution.save(
        update_fields=[
            "agent_definition",
            "agent_version",
            "simulator_agent",
            "scenario_ids",
        ]
    )

    response = auth_client.post(
        f"/simulate/test-executions/{test_execution.id}/chat/call-executions/batch/",
        {},
        format="json",
    )

    assert response.status_code == 200
    result = response.data["result"]
    assert len(result["call_execution_ids"]) == 1
    call_execution = CallExecution.objects.get(id=result["call_execution_ids"][0])
    assert call_execution.test_execution == test_execution
    assert call_execution.scenario == scenario
    assert call_execution.simulation_call_type == CallExecution.SimulationCallType.TEXT
    assert call_execution.call_metadata["call_channel"] == "chat"
    assert call_execution.call_metadata["call_direction"] == "inbound"


# ============================================================================
# Tests for initiate_chat
# ============================================================================


@pytest.mark.unit
class TestInitiateChat:
    """Tests for the initiate_chat function."""

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.services.test_executor.TestExecutor._check_call_balance")
    def test_initiate_chat_success(
        self,
        mock_check_balance,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Successfully initiate a chat session with all required setup."""
        # Mock balance check
        mock_check_balance.return_value = (True, 100.0, 10.0, None)

        # Mock ChatServiceManager responses
        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance
        mock_service_instance.provider.value = "langgraph"

        mock_service_instance.create_assistant.return_value = CreateAssistantResult(
            success=True,
            assistant_id="asst-test-123",
            provider_data={"type": "langgraph"},
        )

        mock_service_instance.create_session.return_value = CreateSessionResult(
            success=True,
            session_id="session-test-123",
            messages=[],
        )

        # Execute
        result = initiate_chat(call_execution, organization, workspace)

        # Assert
        assert result is not None
        assert len(result) == 1
        # Simulator (AI customer) always speaks as ChatRole.USER
        assert result[0].role == ChatRole.USER
        assert result[0].content == "Hi!"

        # Verify call_execution was updated
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.ONGOING
        assert call_execution.assistant_id == "asst-test-123"
        assert call_execution.call_metadata["chat_session_id"] == "session-test-123"
        assert (
            call_execution.call_metadata["simulation_assistant_id"] == "asst-test-123"
        )

        # Verify assistant was created
        mock_service_instance.create_assistant.assert_called_once()

        # Verify chat session was created
        mock_service_instance.create_session.assert_called_once()

        # Verify chat message was stored
        # Note: initiate_chat stores initial message as USER role (simulator/customer)
        assert ChatMessageModel.objects.filter(
            call_execution=call_execution,
            role=ChatMessageModel.RoleChoices.USER,
        ).exists()

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.services.test_executor.TestExecutor._check_call_balance")
    def test_initiate_chat_insufficient_balance(
        self,
        mock_check_balance,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Chat initiation should fail gracefully when balance is insufficient."""
        # Mock insufficient balance
        mock_check_balance.return_value = (
            False,
            5.0,
            10.0,
            "Insufficient balance: current=5.0, required=10.0",
        )

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            initiate_chat(call_execution, organization, workspace)

        assert "Insufficient balance" in str(exc_info.value)

        # Verify call_execution was marked as failed
        call_execution.refresh_from_db()
        assert call_execution.status == CallExecution.CallStatus.FAILED
        assert "Insufficient balance" in call_execution.ended_reason

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.services.test_executor.TestExecutor._check_call_balance")
    def test_initiate_chat_with_custom_initial_message(
        self,
        mock_check_balance,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Chat should use custom initial message from call_metadata."""
        # Setup
        custom_message = "Welcome to our support chat! How can I help you today?"
        call_execution.call_metadata["initial_message"] = custom_message
        call_execution.save()

        mock_check_balance.return_value = (True, 100.0, 10.0, None)

        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance
        mock_service_instance.provider.value = "langgraph"

        mock_service_instance.create_assistant.return_value = CreateAssistantResult(
            success=True,
            assistant_id="asst-123",
        )
        mock_service_instance.create_session.return_value = CreateSessionResult(
            success=True,
            session_id="session-123",
            messages=[],
        )

        # Execute
        result = initiate_chat(call_execution, organization, workspace)

        # Assert
        assert result[0].content == custom_message

        # Verify chat session was created with custom initial message
        create_session_call = mock_service_instance.create_session.call_args
        assert create_session_call.kwargs["initial_message"].content == custom_message

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.services.test_executor.TestExecutor._check_call_balance")
    def test_initiate_chat_updates_test_execution_status(
        self,
        mock_check_balance,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Test execution should transition from PENDING to RUNNING on first chat."""
        # Setup
        mock_check_balance.return_value = (True, 100.0, 10.0, None)

        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance
        mock_service_instance.provider.value = "langgraph"

        mock_service_instance.create_assistant.return_value = CreateAssistantResult(
            success=True,
            assistant_id="asst-123",
        )
        mock_service_instance.create_session.return_value = CreateSessionResult(
            success=True,
            session_id="session-123",
            messages=[],
        )

        # Verify initial state
        assert (
            call_execution.test_execution.status
            == TestExecution.ExecutionStatus.PENDING
        )

        # Execute
        initiate_chat(call_execution, organization, workspace)

        # Assert
        call_execution.test_execution.refresh_from_db()
        assert (
            call_execution.test_execution.status
            == TestExecution.ExecutionStatus.RUNNING
        )

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.services.test_executor.TestExecutor._check_call_balance")
    def test_initiate_chat_service_failure(
        self,
        mock_check_balance,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Chat initiation should handle service failures gracefully."""
        # Setup
        mock_check_balance.return_value = (True, 100.0, 10.0, None)

        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance
        mock_service_instance.provider.value = "langgraph"

        mock_service_instance.create_assistant.return_value = CreateAssistantResult(
            success=False,
            error="Service unavailable",
        )

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            initiate_chat(call_execution, organization, workspace)

        assert "Error initiating chat" in str(exc_info.value)


# ============================================================================
# Tests for TestExecutor._check_call_balance
# ============================================================================


@pytest.mark.unit
class TestCheckCallBalance:
    """Regression tests for the postpaid metering gate used by chat sims."""

    @patch("simulate.services.test_executor.check_usage")
    def test_paid_plan_is_allowed(self, mock_check_usage, organization):
        """Regression for TH-4313: paid plans (e.g. PAYG+Scale) must not be
        blocked by the legacy wallet_balance gate. ``check_usage`` returns
        ``allowed=True`` for paid plans, which must propagate as a pass."""
        from simulate.pydantic_schemas.chat import SimulationCallType
        from simulate.services.test_executor import TestExecutor

        try:
            from ee.usage.schemas.events import CheckResult
        except ImportError:
            CheckResult = None

        mock_check_usage.return_value = CheckResult(allowed=True)

        executor = TestExecutor()
        allowed, balance, cost, err = executor._check_call_balance(
            organization, call_type=SimulationCallType.TEXT
        )

        assert allowed is True
        assert balance == 0
        assert cost == 0
        assert err is None
        mock_check_usage.assert_called_once_with(str(organization.id), "text_call")

    @patch("simulate.services.test_executor.check_usage")
    def test_free_tier_limit_blocks(self, mock_check_usage, organization):
        """Free-tier caps enforced: disallowed result propagates the reason."""
        from simulate.pydantic_schemas.chat import SimulationCallType
        from simulate.services.test_executor import TestExecutor

        try:
            from ee.usage.schemas.events import CheckResult
        except ImportError:
            CheckResult = None

        mock_check_usage.return_value = CheckResult(
            allowed=False,
            reason="Free tier text tokens limit reached",
            error_code="FREE_TIER_LIMIT",
        )

        executor = TestExecutor()
        allowed, _, _, err = executor._check_call_balance(
            organization, call_type=SimulationCallType.TEXT
        )

        assert allowed is False
        assert err == "Free tier text tokens limit reached"

    @patch("simulate.services.test_executor.check_usage")
    def test_redis_failure_fails_closed(self, mock_check_usage, organization):
        """If Redis is unavailable, ``check_usage`` raises. The method must
        fail closed (``allowed=False``) rather than silently allowing calls."""
        from simulate.pydantic_schemas.chat import SimulationCallType
        from simulate.services.test_executor import TestExecutor

        mock_check_usage.side_effect = RuntimeError("Redis unreachable")

        executor = TestExecutor()
        allowed, _, _, err = executor._check_call_balance(
            organization, call_type=SimulationCallType.TEXT
        )

        assert allowed is False
        assert "Redis unreachable" in err


# ============================================================================
# Tests for send_message_to_chat
# ============================================================================


@pytest.mark.unit
class TestSendMessageToChat:
    """Tests for the send_message_to_chat function."""

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.tasks.chat_sim.store_chat_messages.apply_async")
    def test_send_message_success(
        self,
        mock_store_task,
        mock_chat_service_manager,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Successfully send a message and receive response."""
        # Setup
        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance

        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Hi there!")]

        mock_service_instance.get_session.return_value = GetSessionResult(
            success=True,
            session_id="test-session-123",
            name="Test Session",
            status="active",
            assistant_id="asst-123",
            messages=[],
        )

        mock_service_instance.send_message.return_value = SendMessageResult(
            success=True,
            input_messages=input_messages,
            output_messages=output_messages,
            message_id="msg-123",
            has_chat_ended=False,
        )

        # Execute
        result = send_message_to_chat(
            ongoing_call_execution,
            organization,
            workspace,
            input_messages,
        )

        # Assert
        assert result is not None
        assert result["input_message"] == input_messages
        assert result["output_message"] == output_messages
        assert result["chat_ended"] is False

        # Verify task was triggered
        mock_store_task.assert_called_once()

        # Verify call execution status unchanged (chat not ended)
        ongoing_call_execution.refresh_from_db()
        assert ongoing_call_execution.status == CallExecution.CallStatus.ONGOING

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.tasks.chat_sim.store_chat_messages.apply_async")
    def test_send_message_chat_ends(
        self,
        mock_store_task,
        mock_chat_service_manager,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Call execution should be marked as analyzing when chat ends (metrics/evals still pending)."""
        # Setup
        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance

        input_messages = [ChatMessage(role=ChatRole.USER, content="Goodbye")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Take care!")]

        mock_service_instance.get_session.return_value = GetSessionResult(
            success=True,
            session_id="test-session-123",
            name="Test Session",
            status="active",
            assistant_id="asst-123",
            messages=[],
        )

        mock_service_instance.send_message.return_value = SendMessageResult(
            success=True,
            input_messages=input_messages,
            output_messages=output_messages,
            message_id="msg-123",
            has_chat_ended=True,  # Chat ended
        )

        # Execute
        result = send_message_to_chat(
            ongoing_call_execution,
            organization,
            workspace,
            input_messages,
        )

        # Assert
        assert result["chat_ended"] is True

        # Verify call execution marked as analyzing (not completed yet - metrics/evals still pending)
        ongoing_call_execution.refresh_from_db()
        assert ongoing_call_execution.status == CallExecution.CallStatus.ANALYZING
        assert ongoing_call_execution.completed_at is not None

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch(
        "simulate.tasks.chat_sim.store_chat_messages.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_send_message_store_dispatch_failure_falls_back_sync(
        self,
        mock_store_task,
        mock_chat_service_manager,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Chat response should survive async store dispatch failure."""
        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance

        input_messages = [ChatMessage(role=ChatRole.USER, content="Goodbye")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Take care!")]

        mock_service_instance.get_session.return_value = GetSessionResult(
            success=True,
            session_id="test-session-123",
            name="Test Session",
            status="active",
            assistant_id="asst-123",
            messages=[],
        )
        mock_service_instance.send_message.return_value = SendMessageResult(
            success=True,
            input_messages=input_messages,
            output_messages=output_messages,
            message_id="msg-123",
            has_chat_ended=False,
        )

        result = send_message_to_chat(
            ongoing_call_execution,
            organization,
            workspace,
            input_messages,
        )

        assert result["chat_ended"] is False
        mock_store_task.assert_called_once()
        assert (
            ChatMessageModel.objects.filter(
                call_execution=ongoing_call_execution
            ).count()
            == 2
        )

        ongoing_call_execution.refresh_from_db()
        assert ongoing_call_execution.status == CallExecution.CallStatus.ONGOING
        assert ongoing_call_execution.completed_at is None

    @patch("simulate.services.chat_sim.ChatServiceManager")
    def test_send_message_to_non_ongoing_call(
        self,
        mock_chat_service_manager,
        call_execution,
        organization,
        workspace,
    ):
        """Sending message to non-ongoing call should raise exception."""
        # Setup - call_execution is in PENDING status
        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            send_message_to_chat(
                call_execution,
                organization,
                workspace,
                input_messages,
            )

        assert "Call execution is not ongoing" in str(exc_info.value)

    @patch("simulate.services.chat_sim.ChatServiceManager")
    def test_send_message_without_session_id(
        self,
        mock_chat_service_manager,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Should raise exception when chat session ID is missing."""
        # Setup - remove session ID
        ongoing_call_execution.call_metadata = {}
        ongoing_call_execution.save()

        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            send_message_to_chat(
                ongoing_call_execution,
                organization,
                workspace,
                input_messages,
            )

        assert "Chat session ID not found" in str(exc_info.value)

    @patch("simulate.services.chat_sim.ChatServiceManager")
    @patch("simulate.tasks.chat_sim.store_chat_messages.apply_async")
    def test_send_message_with_metrics(
        self,
        mock_store_task,
        mock_chat_service_manager,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Metrics should be passed to store_chat_messages task."""
        # Setup
        mock_service_instance = MagicMock()
        mock_chat_service_manager.return_value = mock_service_instance

        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Hi!")]

        mock_service_instance.get_session.return_value = GetSessionResult(
            success=True,
            session_id="test-session-123",
            name="Test",
            status="active",
            assistant_id="asst-123",
            messages=[],
        )

        mock_service_instance.send_message.return_value = SendMessageResult(
            success=True,
            input_messages=input_messages,
            output_messages=output_messages,
            message_id="msg-123",
            has_chat_ended=False,
        )

        metrics = {"latency": 250, "tokens": 15}

        # Execute
        send_message_to_chat(
            ongoing_call_execution,
            organization,
            workspace,
            input_messages,
            metrics=metrics,
        )

        # Assert - verify metrics passed to task
        mock_store_task.assert_called_once()
        call_args = mock_store_task.call_args
        assert call_args.kwargs["args"][-1] == metrics


# ============================================================================
# Tests for store_chat_messages
# ============================================================================


@pytest.mark.integration
class TestStoreChatMessages:
    """Tests for the store_chat_messages function."""

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async")
    def test_store_messages_success(
        self,
        mock_eval_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Successfully store chat messages to database."""
        # Setup
        input_messages = [ChatMessage(role=ChatRole.USER, content="Test message")]
        output_messages = [
            ChatMessage(role=ChatRole.ASSISTANT, content="Response message")
        ]

        # Execute
        result = store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=False,
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert
        assert result is True

        # Verify messages were stored
        # Note: store_chat_messages swaps roles - input (USER) becomes ASSISTANT, output (ASSISTANT) becomes USER
        stored_messages = ChatMessageModel.objects.filter(
            call_execution=ongoing_call_execution
        )
        assert stored_messages.count() == 2  # Input + output

        # Input messages (USER on wire = agent-under-test) are stored as ASSISTANT
        assistant_message = stored_messages.filter(
            role=ChatMessageModel.RoleChoices.ASSISTANT
        ).first()
        assert assistant_message is not None
        assert assistant_message.messages == ["Test message"]

        # Output messages (ASSISTANT on wire = simulator) are stored as USER
        user_message = stored_messages.filter(
            role=ChatMessageModel.RoleChoices.USER
        ).first()
        assert user_message is not None
        assert user_message.messages == ["Response message"]

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    @patch("simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async")
    @patch("simulate.tasks.chat_sim._aggregate_chat_metrics")
    def test_store_messages_triggers_eval_on_chat_end(
        self,
        mock_aggregate,
        mock_eval_task,
        mock_monitor_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Evaluations should be triggered when chat ends."""
        # Setup
        input_messages = [ChatMessage(role=ChatRole.USER, content="Goodbye")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Bye!")]

        # Execute
        store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=True,  # Chat ended
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert
        mock_aggregate.assert_called_once_with(ongoing_call_execution)
        mock_eval_task.assert_called_once()
        mock_monitor_task.assert_called_once()

        # Verify eval_started flag was set
        ongoing_call_execution.refresh_from_db()
        assert ongoing_call_execution.call_metadata.get("eval_started") is True

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch(
        "simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async",
        side_effect=TimeoutError("monitor dispatch timed out"),
    )
    @patch(
        "simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async",
        side_effect=TimeoutError("eval dispatch timed out"),
    )
    @patch("simulate.tasks.chat_sim._aggregate_chat_metrics")
    @patch("simulate.services.test_executor.TestExecutor._deduct_call_cost")
    def test_store_messages_completes_call_when_terminal_dispatch_fails(
        self,
        mock_deduct_cost,
        mock_aggregate,
        mock_eval_task,
        mock_monitor_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Terminal chat persistence should not fail on downstream dispatch timeouts."""
        input_messages = [ChatMessage(role=ChatRole.USER, content="Goodbye")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Bye!")]

        result = store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=True,
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        assert result is True
        mock_aggregate.assert_called_once()
        mock_deduct_cost.assert_called_once()
        mock_eval_task.assert_called_once()
        mock_monitor_task.assert_called_once()
        assert (
            ChatMessageModel.objects.filter(
                call_execution=ongoing_call_execution
            ).count()
            == 2
        )

        ongoing_call_execution.refresh_from_db()
        assert ongoing_call_execution.status == CallExecution.CallStatus.COMPLETED
        assert ongoing_call_execution.call_metadata.get("eval_started") is False
        assert (
            ongoing_call_execution.call_metadata.get("eval_dispatch_failed")
            == "eval dispatch timed out"
        )

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async")
    def test_store_messages_with_metrics(
        self,
        mock_eval_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Metrics should be stored with messages."""
        # Setup
        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Hi!")]
        metrics = {"latency": 250, "tokens": 20}

        # Execute
        store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=False,
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
            metrics=metrics,
        )

        # Assert
        assistant_message = ChatMessageModel.objects.filter(
            call_execution=ongoing_call_execution,
            role=ChatMessageModel.RoleChoices.ASSISTANT,
        ).first()

        assert assistant_message is not None
        assert assistant_message.latency_ms == 250

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.utils.chat_simulation._run_simulate_evaluations_task.apply_async")
    def test_store_messages_handles_dict_input(
        self,
        mock_eval_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Function should handle dict input (from Temporal serialization)."""
        # Setup - messages as dicts (simulating Temporal serialization)
        input_messages = [{"role": "user", "content": "Test"}]
        output_messages = [{"role": "assistant", "content": "Response"}]

        # Execute
        result = store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=False,
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert
        assert result is True

        # Verify messages were stored correctly
        stored_messages = ChatMessageModel.objects.filter(
            call_execution=ongoing_call_execution
        )
        assert stored_messages.count() == 2

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    def test_store_messages_handles_invalid_call_execution(
        self,
        mock_close_connections,
        organization,
        workspace,
    ):
        """Function should return None on invalid call execution ID."""
        # Setup
        invalid_id = str(uuid.uuid4())
        input_messages = [ChatMessage(role=ChatRole.USER, content="Test")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Response")]

        # Execute
        result = store_chat_messages(
            call_execution_id=invalid_id,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=False,
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert
        assert result is None


# ============================================================================
# Tests for monitor_test_execution_for_chat
# ============================================================================


@pytest.mark.integration
class TestMonitorTestExecutionForChat:
    """Tests for the monitor_test_execution_for_chat function."""

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async")
    def test_monitor_marks_completed_when_no_calls(
        self,
        mock_eval_summary,
        mock_close_connections,
        test_execution,
    ):
        """Test execution should be marked completed when no call executions exist."""
        # Execute
        monitor_test_execution_for_chat(str(test_execution.id))

        # Assert
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )
        mock_eval_summary.assert_called_once()

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async")
    def test_monitor_marks_completed_when_all_calls_completed(
        self,
        mock_eval_summary,
        mock_close_connections,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Test execution marked completed when all calls are completed."""
        # Setup - create completed call executions
        for i in range(3):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                phone_number=f"+123456789{i}",
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.TEXT,
                call_metadata={"eval_started": True, "eval_completed": True},
            )

        # Execute
        monitor_test_execution_for_chat(str(test_execution.id))

        # Assert
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        mock_eval_summary.assert_called_once()

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    def test_monitor_marks_evaluating_when_evals_running(
        self,
        mock_close_connections,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Test execution marked EVALUATING when evaluations have started."""
        # Setup - create call with eval started but not completed
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={"eval_started": True, "eval_completed": False},
        )

        # Execute
        monitor_test_execution_for_chat(str(test_execution.id))

        # Assert - get fresh object from DB
        test_execution = TestExecution.objects.get(id=test_execution.id)
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    def test_monitor_waits_when_calls_still_running(
        self,
        mock_close_connections,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Test execution status unchanged when calls still running (not yet completed)."""
        # Setup - create call still in progress
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )

        # Execute
        monitor_test_execution_for_chat(str(test_execution.id))

        # Assert - status stays unchanged (PENDING) since no calls have completed yet.
        # EVALUATING should only be set when completed calls have evals in progress.
        test_execution = TestExecution.objects.get(id=test_execution.id)
        assert test_execution.status == TestExecution.ExecutionStatus.PENDING

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    def test_monitor_handles_mixed_call_statuses(
        self,
        mock_close_connections,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Monitor handles mix of completed, failed, and cancelled calls."""
        # Setup - create calls with different statuses
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567891",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={"eval_started": True, "eval_completed": True},
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567892",
            status=CallExecution.CallStatus.FAILED,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
        )

        # Execute
        with patch(
            "simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"
        ) as mock_eval:
            monitor_test_execution_for_chat(str(test_execution.id))

            # Assert - should be marked completed
            test_execution.refresh_from_db()
            assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
            mock_eval.assert_called_once()

    @pytest.mark.django_db
    def test_monitor_handles_invalid_test_execution_id(self):
        """Monitor should handle invalid test execution ID gracefully."""
        # Setup
        invalid_id = str(uuid.uuid4())

        # Execute - should not raise exception
        result = monitor_test_execution_for_chat(invalid_id)

        # Assert
        assert result is None


# ============================================================================
# Tests for monitor_chat_test_executions
# ============================================================================


@pytest.mark.integration
class TestMonitorChatTestExecutions:
    """Tests for the monitor_chat_test_executions batch monitoring function."""

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    def test_monitor_processes_active_test_executions(
        self,
        mock_monitor_task,
        mock_close_connections,
        organization,
        workspace,
        agent_definition,
        run_test,
    ):
        """Should process active test executions in batches."""
        # Setup - create multiple active test executions
        test_executions = []
        for _ in range(5):
            te = TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.PENDING,
                agent_definition=agent_definition,
                picked_up_by_executor=False,
            )
            test_executions.append(te)

        # Execute
        monitor_chat_test_executions()

        # Assert - all 5 should be processed (batch size is 10)
        assert mock_monitor_task.call_count == 5

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    def test_monitor_respects_batch_size(
        self,
        mock_monitor_task,
        mock_close_connections,
        organization,
        workspace,
        agent_definition,
        run_test,
    ):
        """Should process only batch size (10) test executions at a time."""
        # Setup - create more than batch size
        for _ in range(15):
            TestExecution.objects.create(
                run_test=run_test,
                status=TestExecution.ExecutionStatus.PENDING,
                agent_definition=agent_definition,
                picked_up_by_executor=False,
            )

        # Execute
        monitor_chat_test_executions()

        # Assert - should process only 10 (batch size)
        assert mock_monitor_task.call_count == 10

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    def test_monitor_filters_by_agent_type_text(
        self,
        mock_monitor_task,
        mock_close_connections,
        organization,
        workspace,
        run_test,
    ):
        """Should only process TEXT agent type test executions."""
        # Setup - create TEXT agent test execution
        text_agent = AgentDefinition.objects.create(
            agent_name="Text Agent",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="Test text agent",
            organization=organization,
            workspace=workspace,
        )
        text_test_exec = TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            agent_definition=text_agent,
            picked_up_by_executor=False,
        )

        # Create VOICE agent test execution (should be ignored)
        voice_agent = AgentDefinition.objects.create(
            agent_name="Voice Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            inbound=True,
            description="Test voice agent",
            organization=organization,
            workspace=workspace,
        )
        TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            agent_definition=voice_agent,
            picked_up_by_executor=False,
        )

        # Execute
        monitor_chat_test_executions()

        # Assert - only TEXT agent test execution should be processed
        assert mock_monitor_task.call_count == 1
        mock_monitor_task.assert_called_with(args=(str(text_test_exec.id),))

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    def test_monitor_ignores_picked_up_executions(
        self,
        mock_monitor_task,
        mock_close_connections,
        organization,
        workspace,
        agent_definition,
        run_test,
    ):
        """Should ignore test executions already picked up by executor."""
        # Setup - create test execution already picked up
        TestExecution.objects.create(
            run_test=run_test,
            status=TestExecution.ExecutionStatus.PENDING,
            agent_definition=agent_definition,
            picked_up_by_executor=True,  # Already picked up
        )

        # Execute
        monitor_chat_test_executions()

        # Assert - should not process any
        mock_monitor_task.assert_not_called()

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    def test_monitor_processes_multiple_statuses(
        self,
        mock_monitor_task,
        mock_close_connections,
        organization,
        workspace,
        agent_definition,
        run_test,
    ):
        """Should process PENDING, RUNNING, and EVALUATING statuses."""
        # Setup - create test executions with different active statuses
        statuses = [
            TestExecution.ExecutionStatus.PENDING,
            TestExecution.ExecutionStatus.RUNNING,
            TestExecution.ExecutionStatus.EVALUATING,
        ]

        for status in statuses:
            TestExecution.objects.create(
                run_test=run_test,
                status=status,
                agent_definition=agent_definition,
                picked_up_by_executor=False,
            )

        # Execute
        monitor_chat_test_executions()

        # Assert - all 3 should be processed
        assert mock_monitor_task.call_count == 3


# ============================================================================
# Tests for TestExecutor._deduct_call_cost
# ============================================================================


@pytest.mark.integration
class TestDeductCallCost:
    """Tests for the TestExecutor._deduct_call_cost static method."""

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_for_text_call(
        self,
        mock_deduct_cost,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Should deduct cost for TEXT (chat) calls based on turns."""
        from simulate.services.test_executor import TestExecutor

        # Setup - create some chat messages with required fields
        ChatMessageModel.objects.create(
            call_execution=ongoing_call_execution,
            role=ChatMessageModel.RoleChoices.USER,
            messages=["Hello"],
            latency_ms=100,
            organization=organization,
            workspace=workspace,
            session_id="test-session-123",
        )
        ChatMessageModel.objects.create(
            call_execution=ongoing_call_execution,
            role=ChatMessageModel.RoleChoices.ASSISTANT,
            messages=["Hi there!"],
            latency_ms=150,
            organization=organization,
            workspace=workspace,
            session_id="test-session-123",
        )
        ChatMessageModel.objects.create(
            call_execution=ongoing_call_execution,
            role=ChatMessageModel.RoleChoices.USER,
            messages=["How are you?"],
            latency_ms=120,
            organization=organization,
            workspace=workspace,
            session_id="test-session-123",
        )

        # Execute
        TestExecutor._deduct_call_cost(ongoing_call_execution)

        # Assert
        mock_deduct_cost.assert_called_once()
        call_kwargs = mock_deduct_cost.call_args.kwargs

        assert call_kwargs["api_call_type"] == "text_call"
        assert call_kwargs["source"] == "text_call"
        assert call_kwargs["organization"] == organization

        # Verify config has correct structure
        config = call_kwargs["config"]
        assert "call_execution_id" in config
        assert "no_of_agent_turns" in config
        assert config["no_of_agent_turns"] == 2  # 2 USER messages
        assert config["default_value"] == "0.005"
        assert config["source"] == "text_call"

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_for_voice_call(
        self,
        mock_deduct_cost,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Should deduct cost for VOICE calls based on duration."""
        from simulate.services.test_executor import TestExecutor

        # Setup - create a voice call execution
        voice_call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            duration_seconds=120,  # 2 minutes
            service_provider_call_id="vapi-123",
            recording_url="https://example.com/recording.mp3",
        )

        # Execute
        TestExecutor._deduct_call_cost(voice_call_execution)

        # Assert
        mock_deduct_cost.assert_called_once()
        call_kwargs = mock_deduct_cost.call_args.kwargs

        assert call_kwargs["api_call_type"] == "voice_call"
        assert call_kwargs["source"] == "voice_call"
        assert call_kwargs["organization"] == organization

        # Verify config has correct structure
        config = call_kwargs["config"]
        assert "call_execution_id" in config
        assert config["duration_seconds"] == 120
        assert "duration_minutes" in config
        assert config["service_provider_call_id"] == "vapi-123"
        assert config["source"] == "voice_call"

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_config_is_json_serializable(
        self,
        mock_deduct_cost,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Config dict should be JSON serializable (no Decimal or method objects)."""
        import json

        from simulate.services.test_executor import TestExecutor

        # Setup - create some chat messages with required fields
        ChatMessageModel.objects.create(
            call_execution=ongoing_call_execution,
            role=ChatMessageModel.RoleChoices.USER,
            messages=["Test message"],
            latency_ms=100,
            organization=organization,
            workspace=workspace,
            session_id="test-session-123",
        )

        # Execute
        TestExecutor._deduct_call_cost(ongoing_call_execution)

        # Assert - config should be JSON serializable
        call_kwargs = mock_deduct_cost.call_args.kwargs
        config = call_kwargs["config"]

        # This should not raise TypeError
        try:
            json.dumps(config)
        except TypeError as e:
            pytest.fail(f"Config is not JSON serializable: {e}")

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_voice_call_config_is_json_serializable(
        self,
        mock_deduct_cost,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Voice call config dict should be JSON serializable."""
        import json

        from simulate.services.test_executor import TestExecutor

        # Setup - create a voice call execution
        voice_call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            duration_seconds=300,  # 5 minutes
            service_provider_call_id="vapi-456",
        )

        # Execute
        TestExecutor._deduct_call_cost(voice_call_execution)

        # Assert - config should be JSON serializable
        call_kwargs = mock_deduct_cost.call_args.kwargs
        config = call_kwargs["config"]

        # This should not raise TypeError
        try:
            json.dumps(config)
        except TypeError as e:
            pytest.fail(f"Voice call config is not JSON serializable: {e}")

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_handles_zero_duration(
        self,
        mock_deduct_cost,
        test_execution,
        scenario,
        organization,
        workspace,
    ):
        """Should handle voice calls with zero duration gracefully."""
        from simulate.services.test_executor import TestExecutor

        # Setup - create a voice call with zero duration
        voice_call_execution = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            duration_seconds=0,
            service_provider_call_id="vapi-789",
        )

        # Execute - should not raise
        TestExecutor._deduct_call_cost(voice_call_execution)

        # Assert
        mock_deduct_cost.assert_called_once()
        config = mock_deduct_cost.call_args.kwargs["config"]
        assert config["duration_seconds"] == 0

    def test_deduct_cost_is_static_method(self):
        """_deduct_call_cost should be callable without instance."""
        from simulate.services.test_executor import TestExecutor

        # This should not raise - proves it's a static method
        assert callable(TestExecutor._deduct_call_cost)
        # Verify it doesn't require self
        import inspect

        sig = inspect.signature(TestExecutor._deduct_call_cost)
        params = list(sig.parameters.keys())
        assert "self" not in params
        assert "call_execution" in params

    @patch("simulate.services.test_executor.deduct_cost_for_request")
    def test_deduct_cost_handles_exception_gracefully(
        self,
        mock_deduct_cost,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Should handle exceptions without raising (not break call flow)."""
        from simulate.services.test_executor import TestExecutor

        # Setup - make deduct_cost_for_request raise an exception
        mock_deduct_cost.side_effect = Exception("Database error")

        # Execute - should not raise
        TestExecutor._deduct_call_cost(ongoing_call_execution)

        # Assert - function completed without raising


# ============================================================================
# Tests for store_chat_messages with cost deduction
# ============================================================================


@pytest.mark.integration
class TestStoreChatMessagesWithCostDeduction:
    """Tests for cost deduction integration in store_chat_messages."""

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim.monitor_test_execution_for_chat.apply_async")
    @patch("simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async")
    @patch("simulate.tasks.chat_sim._aggregate_chat_metrics")
    @patch("simulate.services.test_executor.TestExecutor._deduct_call_cost")
    def test_cost_deducted_when_chat_ends(
        self,
        mock_deduct_cost,
        mock_aggregate,
        mock_eval_task,
        mock_monitor_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Cost should be deducted when chat ends."""
        # Setup
        input_messages = [ChatMessage(role=ChatRole.USER, content="Goodbye")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Bye!")]

        # Execute
        store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=True,  # Chat ended
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert - cost deduction was called
        mock_deduct_cost.assert_called_once()
        mock_monitor_task.assert_called_once()
        # Verify it was called with the call_execution
        call_args = mock_deduct_cost.call_args
        assert call_args[0][0].id == ongoing_call_execution.id

    @patch("tfc.temporal.drop_in.decorator.close_old_connections")
    @patch("simulate.tasks.chat_sim._run_simulate_evaluations_task.apply_async")
    @patch("simulate.services.test_executor.TestExecutor._deduct_call_cost")
    def test_cost_not_deducted_when_chat_continues(
        self,
        mock_deduct_cost,
        mock_eval_task,
        mock_close_connections,
        ongoing_call_execution,
        organization,
        workspace,
    ):
        """Cost should NOT be deducted when chat is still ongoing."""
        # Setup
        input_messages = [ChatMessage(role=ChatRole.USER, content="Hello")]
        output_messages = [ChatMessage(role=ChatRole.ASSISTANT, content="Hi!")]

        # Execute
        store_chat_messages(
            call_execution_id=str(ongoing_call_execution.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
            input_messages=input_messages,
            output_messages=output_messages,
            chat_ended=False,  # Chat NOT ended
            chat_session_id="test-session-123",
            create_timestamp=timezone.now(),
        )

        # Assert - cost deduction was NOT called
        mock_deduct_cost.assert_not_called()
