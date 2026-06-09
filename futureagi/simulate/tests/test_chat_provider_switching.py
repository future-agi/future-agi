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

    @pytest.mark.django_db(transaction=True)
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
