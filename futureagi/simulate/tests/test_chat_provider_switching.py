"""Integration tests for chat provider switching between VAPI and FutureAGI."""

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from simulate.models import CallExecution
from simulate.pydantic_schemas.chat import ChatMessage, ChatRole
from simulate.services.chat_service_manager import ChatServiceManager
from simulate.services.types.chat import ChatProviderChoices

# Import fixtures from test_chat_simulation
pytest_plugins = ["simulate.tests.test_chat_simulation"]


@pytest.mark.unit
class TestProviderSwitching:
    """Test that chat service manager correctly selects and uses different providers."""

    @patch("simulate.services.chat_constants.CHAT_SIMULATION_PROVIDER", "vapi")
    @patch("simulate.services.vapi_chat.service.VapiService")
    def test_vapi_provider_initialization(self, mock_vapi_service):
        """Verify VAPI provider is used when explicitly set to VAPI."""
        # Create manager with explicit provider
        manager = ChatServiceManager(
            provider=ChatProviderChoices.VAPI,
            organization_id=str(uuid.uuid4()),
            api_key="test-key",
        )

        # Verify VAPI was selected
        assert manager.provider == ChatProviderChoices.VAPI
        assert manager.engine.__class__.__name__ == "VapiChatService"

    def test_futureagi_provider_initialization(self):
        """Verify FutureAGI provider is used when explicitly set."""
        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
        )

        assert manager.provider == ChatProviderChoices.FUTUREAGI
        assert manager.engine.__class__.__name__ == "FutureAGIChatService"

    def test_explicit_provider_override(self):
        """Verify explicit provider parameter works correctly."""
        manager = ChatServiceManager(
            provider=ChatProviderChoices.VAPI,
            organization_id=str(uuid.uuid4()),
            api_key="test-key",
        )
        assert manager.provider == ChatProviderChoices.VAPI

        manager2 = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
        )
        assert manager2.provider == ChatProviderChoices.FUTUREAGI

    @pytest.mark.django_db
    @patch("simulate.services.futureagi_chat.service.generate_simulator_response")
    def test_futureagi_end_to_end_flow(
        self, mock_generate, db, organization, workspace
    ):
        """Test complete assistant creation → session → message flow with FutureAGI."""
        from simulate.services.types.chat import LLMUsage

        def mock_response(*args, **kwargs):
            return {
                "content": "Hello! How can I help?",
                "tool_calls": [],
                "has_chat_ended": False,
                "usage": LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                "model": "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
            }

        mock_generate.side_effect = mock_response

        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )

        # Step 1: Create assistant
        assistant_result = manager.create_assistant(
            name="Test Assistant",
            system_prompt="You are helpful",
        )
        assert assistant_result.success
        assert assistant_result.assistant_id is not None

        # Step 2: Create session
        session_result = manager.create_session(
            assistant_id=assistant_result.assistant_id,
            name="Test Session",
        )
        assert session_result.success
        assert session_result.session_id is not None

        # Step 3: Send message
        send_result = asyncio.run(
            manager.send_message_async(
                session_id=session_result.session_id,
                messages=[ChatMessage(role=ChatRole.USER, content="Hello")],
            )
        )
        assert send_result.success
        assert len(send_result.output_messages) > 0
        assert send_result.usage is not None


@pytest.mark.unit
class TestLegacySessionCompatibility:
    """Test backward compatibility with old VAPI session metadata."""

    def test_legacy_vapi_session_id_retrieval(self, db, test_execution, scenario):
        """Verify old vapi_chat_session_id key still works."""
        from simulate.services.chat_sim import _get_session_id_from_metadata

        # Create call execution with legacy metadata
        call_exec = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={
                "vapi_chat_session_id": "legacy-session-123",
                "simulation_assistant_id": "asst-123",
            },
        )

        # Should successfully retrieve from legacy key
        session_id = _get_session_id_from_metadata(call_exec)
        assert session_id == "legacy-session-123"

    def test_new_session_id_takes_precedence(self, db, test_execution, scenario):
        """Verify new chat_session_id key takes precedence over legacy."""
        from simulate.services.chat_sim import _get_session_id_from_metadata

        # Create call execution with both keys
        call_exec = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={
                "chat_session_id": "new-session-456",
                "vapi_chat_session_id": "legacy-session-123",
            },
        )

        # Should use new key
        session_id = _get_session_id_from_metadata(call_exec)
        assert session_id == "new-session-456"

    def test_missing_session_id_raises_clear_error(self, db, test_execution, scenario):
        """Verify clear error when no session ID exists."""
        from simulate.services.chat_sim import _get_session_id_from_metadata

        # Create call execution with no session keys
        call_exec = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={
                "some_other_key": "value",
            },
        )

        # Should raise ValueError with helpful message
        with pytest.raises(ValueError) as exc_info:
            _get_session_id_from_metadata(call_exec)

        error_message = str(exc_info.value)
        assert "Chat session ID not found" in error_message
        assert "some_other_key" in error_message  # Shows available keys
        assert "initiate_chat" in error_message  # Suggests fix

    def test_invalid_session_id_type_raises_error(self, db, test_execution, scenario):
        """Verify error when session ID is not a string."""
        from simulate.services.chat_sim import _get_session_id_from_metadata

        # Create call execution with wrong type
        call_exec = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.TEXT,
            call_metadata={
                "chat_session_id": 12345,  # int instead of string
            },
        )

        # Should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            _get_session_id_from_metadata(call_exec)

        error_message = str(exc_info.value)
        assert "Invalid session ID" in error_message
        assert "int" in error_message  # Shows actual type


@pytest.mark.unit
@pytest.mark.django_db
class TestSimulatorMessageRoleAndToolFiltering:
    """Verify the contract between FutureAGIChatService.send_message and downstream consumers.

    Two consumers, two different needs:
      - The simulator LLM needs an OpenAI-valid `messages` array fed back to it on
        the next turn (its own outputs as `assistant`, tool exchanges either
        properly threaded or skipped).
      - Tool-call evaluations read the *raw* agent input from `ChatMessageModel`
        (populated from `SendMessageResult.input_messages`) and need tool_calls
        + tool messages preserved.

    Regression: if the storage path were filtered, tool-call evals would silently
    return zero tool calls. These tests pin both behaviors.
    """

    @patch("simulate.services.futureagi_chat.service.generate_simulator_response")
    def test_tool_messages_skipped_from_simulator_history(
        self, mock_generate, db, organization, workspace
    ):
        """Tool-role messages from the agent SDK must NOT enter session.messages.

        OpenAI rejects orphaned `tool` messages (their parent `tool_calls` field
        is dropped at storage), so the simulator's history must not carry them.
        """
        from simulate.models.chat_simulator import ChatSimulatorSession
        from simulate.services.types.chat import LLMUsage

        mock_generate.return_value = {
            "content": "Got it, thanks for the update.",
            "tool_calls": [],
            "has_chat_ended": False,
            "ended_reason": None,
            "usage": LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            "model": "gpt-5.1",
        }

        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        assistant_result = manager.create_assistant(
            name="T", system_prompt="You are a customer."
        )
        session_result = manager.create_session(
            assistant_id=assistant_result.assistant_id, name="S"
        )

        # Agent SDK forwards: assistant text reply + a tool-result message.
        agent_messages = [
            ChatMessage(role=ChatRole.USER, content="Your order is on its way."),
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_123",
                content='{"status": "in_transit"}',
            ),
        ]
        send_result = manager.send_message(
            session_id=session_result.session_id, messages=agent_messages
        )
        assert send_result.success

        session = ChatSimulatorSession.objects.get(id=session_result.session_id)
        roles = [m.get("role") for m in session.messages]
        assert "tool" not in roles, (
            f"tool-role messages must be filtered from simulator history; got {roles}"
        )

    @patch("simulate.services.futureagi_chat.service.generate_simulator_response")
    def test_simulator_reply_stored_as_assistant_role(
        self, mock_generate, db, organization, workspace
    ):
        """The simulator LLM's own output must be stored as role='assistant'.

        OpenAI requires the model's previous outputs to carry role='assistant'
        on subsequent turns, and only `assistant` messages are allowed to carry
        `tool_calls`.
        """
        from simulate.models.chat_simulator import ChatSimulatorSession
        from simulate.services.types.chat import LLMUsage

        mock_generate.return_value = {
            "content": "I'd like to end this call.",
            "tool_calls": [
                {
                    "id": "call_end_1",
                    "type": "function",
                    "function": {"name": "endCall", "arguments": "{}"},
                }
            ],
            "has_chat_ended": True,
            "ended_reason": "Customer ended",
            "usage": LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            "model": "gpt-5.1",
        }

        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        assistant_result = manager.create_assistant(
            name="T", system_prompt="You are a customer."
        )
        session_result = manager.create_session(
            assistant_id=assistant_result.assistant_id, name="S"
        )

        send_result = manager.send_message(
            session_id=session_result.session_id,
            messages=[ChatMessage(role=ChatRole.USER, content="Anything else?")],
        )
        assert send_result.success

        session = ChatSimulatorSession.objects.get(id=session_result.session_id)
        last_msg = session.messages[-1]
        assert last_msg["role"] == "assistant", (
            f"simulator output must be stored as 'assistant', got '{last_msg['role']}'"
        )
        assert last_msg["content"] == "I'd like to end this call."
        assert last_msg.get("tool_calls"), (
            "tool_calls must be preserved on the simulator's assistant message "
            "when the chat actually ends (endCall)"
        )

    @patch("simulate.services.futureagi_chat.service.generate_simulator_response")
    def test_input_messages_passthrough_preserves_tool_data_for_evals(
        self, mock_generate, db, organization, workspace
    ):
        """SendMessageResult.input_messages must echo the raw input verbatim.

        Tool-call evaluation (ChatToolCallAdapter) reads tool_calls + tool
        messages out of ChatMessageModel.content, which is built by serializing
        SendMessageResult.input_messages. Filtering here would silently zero out
        tool-call evals.
        """
        from simulate.services.types.chat import LLMUsage

        mock_generate.return_value = {
            "content": "Thanks!",
            "tool_calls": [],
            "has_chat_ended": False,
            "ended_reason": None,
            "usage": LLMUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            "model": "gpt-5.1",
        }

        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        assistant_result = manager.create_assistant(
            name="T", system_prompt="You are a customer."
        )
        session_result = manager.create_session(
            assistant_id=assistant_result.assistant_id, name="S"
        )

        from simulate.pydantic_schemas.chat import ToolCall, ToolCallFunction

        agent_messages = [
            ChatMessage(
                role=ChatRole.USER,
                content="Looking that up for you.",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        type="function",
                        function=ToolCallFunction(
                            name="track_order",
                            arguments='{"order_id": "ORD-1"}',
                        ),
                    )
                ],
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_1",
                content='{"status": "in_transit"}',
            ),
        ]
        send_result = manager.send_message(
            session_id=session_result.session_id, messages=agent_messages
        )
        assert send_result.success

        # Both messages survive in the round-tripped input — this is what
        # ChatMessageModel.content captures and what tool-call evals read.
        roles = [m.role for m in send_result.input_messages]
        assert ChatRole.TOOL in roles, (
            "tool message must survive in input_messages for tool-call evals"
        )
        user_msg = next(m for m in send_result.input_messages if m.role == ChatRole.USER)
        assert user_msg.tool_calls, (
            "agent's tool_calls must survive in input_messages for tool-call evals"
        )
        assert user_msg.tool_calls[0].function.name == "track_order"

    @patch("simulate.services.futureagi_chat.service.generate_simulator_response")
    def test_simulator_tool_calls_dropped_when_chat_does_not_end(
        self, mock_generate, db, organization, workspace
    ):
        """tool_calls on the simulator's assistant message are only persisted
        when has_chat_ended=True (i.e. an actual endCall).

        A non-ending tool_call (hallucinated tool name, malformed endCall, or a
        future simulator tool that doesn't terminate) would otherwise leave the
        next OpenAI turn with dangling tool_calls and no `tool` response,
        re-triggering the BadRequest this fix is for.
        """
        from simulate.models.chat_simulator import ChatSimulatorSession
        from simulate.services.types.chat import LLMUsage

        mock_generate.return_value = {
            "content": "Hmm, let me think.",
            # Hallucinated tool the simulator isn't supposed to have.
            "tool_calls": [
                {
                    "id": "call_hallucinated",
                    "type": "function",
                    "function": {"name": "lookupSomething", "arguments": "{}"},
                }
            ],
            "has_chat_ended": False,
            "ended_reason": None,
            "usage": LLMUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            "model": "gpt-5.1",
        }

        manager = ChatServiceManager(
            provider=ChatProviderChoices.FUTUREAGI,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        assistant_result = manager.create_assistant(
            name="T", system_prompt="You are a customer."
        )
        session_result = manager.create_session(
            assistant_id=assistant_result.assistant_id, name="S"
        )

        send_result = manager.send_message(
            session_id=session_result.session_id,
            messages=[ChatMessage(role=ChatRole.USER, content="Hi")],
        )
        assert send_result.success

        session = ChatSimulatorSession.objects.get(id=session_result.session_id)
        last_msg = session.messages[-1]
        assert last_msg["role"] == "assistant"
        assert last_msg["content"] == "Hmm, let me think."
        assert "tool_calls" not in last_msg, (
            "tool_calls must NOT be persisted on the simulator's assistant "
            "message when the chat does not end — would dangle on the next "
            "OpenAI turn."
        )
