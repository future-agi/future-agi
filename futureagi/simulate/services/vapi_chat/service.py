"""VapiChatService — ChatServiceBlueprint implementation wrapping existing VapiService chat methods.

This service wraps the existing VapiService chat methods to conform to the
ChatServiceBlueprint interface, enabling provider-agnostic chat simulation.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from simulate.pydantic_schemas.chat import ChatMessage, ChatRole
from simulate.services.chat_engine import ChatServiceBlueprint
from simulate.services.types.chat import (
    CreateAssistantInput,
    CreateAssistantResult,
    CreateSessionInput,
    CreateSessionResult,
    DeleteAssistantInput,
    DeleteAssistantResult,
    GetSessionInput,
    GetSessionResult,
    SendMessageInput,
    SendMessageResult,
)
from tfc.ee_stub import _ee_stub

try:
    from ee.voice.services.vapi_service import VapiService
except ImportError:
    VapiService = _ee_stub("VapiService")

logger = structlog.get_logger(__name__)


class VapiChatService(ChatServiceBlueprint):
    """Vapi chat engine wrapping existing VapiService methods.

    This service acts as an adapter between the ChatServiceBlueprint interface
    and the existing VapiService implementation.
    """

    def __init__(self, api_key: str = ""):
        """Initialize the Vapi chat service.

        Args:
            api_key: Vapi API key. If empty, uses system default.
        """
        self._vapi = VapiService(api_key=api_key or None)

    def validate_configuration(self) -> bool:
        """Validate Vapi API key."""
        try:
            return self._vapi.validate_api_key()
        except Exception as e:
            logger.warning("vapi_validation_failed", error=str(e))
            return False

    def create_assistant(self, input: CreateAssistantInput) -> CreateAssistantResult:
        """Create a chat assistant via VapiService.

        Args:
            input: Assistant configuration.

        Returns:
            CreateAssistantResult with assistant_id on success.
        """
        try:
            result = self._vapi.create_assistant(
                name=input.name,
                system_prompt=input.system_prompt,
                voice_settings=input.voice_settings,
                assistant_type="chat",
            )

            assistant_id = result.get("id")
            if not assistant_id:
                return CreateAssistantResult(
                    success=False,
                    error="No assistant ID returned from Vapi",
                )

            logger.info(
                "vapi_assistant_created",
                assistant_id=assistant_id,
                name=input.name,
            )

            return CreateAssistantResult(
                success=True,
                assistant_id=assistant_id,
                provider_data=result,
            )

        except Exception as e:
            logger.exception("vapi_create_assistant_failed", error=str(e))
            return CreateAssistantResult(success=False, error=str(e))

    def delete_assistant(self, input: DeleteAssistantInput) -> DeleteAssistantResult:
        """Delete a chat assistant via VapiService.

        Args:
            input: Contains assistant_id to delete.

        Returns:
            DeleteAssistantResult indicating success/failure.
        """
        try:
            # VapiService has delete_assistant method
            success = self._vapi.delete_assistant(input.assistant_id)
            logger.info(
                "vapi_assistant_deleted",
                assistant_id=input.assistant_id,
                success=success,
            )
            return DeleteAssistantResult(success=success)

        except Exception as e:
            logger.exception("vapi_delete_assistant_failed", error=str(e))
            return DeleteAssistantResult(success=False, error=str(e))

    def create_session(self, input: CreateSessionInput) -> CreateSessionResult:
        """Create a chat session via VapiService.

        Args:
            input: Session configuration.

        Returns:
            CreateSessionResult with session_id and initial messages.
        """
        try:
            result = self._vapi.create_chat_session(
                assistant_id=input.assistant_id,
                name=input.name,
                initial_message=input.initial_message,
            )

            if result is None:
                return CreateSessionResult(
                    success=False,
                    error="No session returned from Vapi",
                )

            logger.info(
                "vapi_session_created",
                session_id=result.id,
                assistant_id=input.assistant_id,
            )

            return CreateSessionResult(
                success=True,
                session_id=result.id,
                messages=result.messages,
            )

        except Exception as e:
            logger.exception("vapi_create_session_failed", error=str(e))
            return CreateSessionResult(success=False, error=str(e))

    def get_session(self, input: GetSessionInput) -> GetSessionResult:
        """Get a chat session via VapiService.

        Args:
            input: Contains session_id to retrieve.

        Returns:
            GetSessionResult with session details and message history.
        """
        try:
            result = self._vapi.get_chat_session(input.session_id)

            if result is None:
                return GetSessionResult(
                    success=False,
                    error="Session not found",
                )

            return GetSessionResult(
                success=True,
                session_id=result.id,
                name=result.name,
                status=result.status,
                assistant_id=result.assistant_id,
                messages=result.messages,
            )

        except Exception as e:
            logger.exception("vapi_get_session_failed", error=str(e))
            return GetSessionResult(success=False, error=str(e))

    def send_message(self, input: SendMessageInput) -> SendMessageResult:
        """Send message(s) to a chat session via VapiService.

        Args:
            input: Contains session_id and messages to send.

        Returns:
            SendMessageResult with input/output messages and has_chat_ended flag.
        """
        try:
            result = self._vapi.send_message_to_chat(
                chat_session_id=input.session_id,
                messages=input.messages,
            )

            logger.info(
                "vapi_message_sent",
                session_id=input.session_id,
                has_chat_ended=result.has_chat_ended,
            )

            has_chat_ended = result.has_chat_ended or False
            return SendMessageResult(
                success=True,
                input_messages=result.input,
                output_messages=result.output,
                message_id=result.id,
                has_chat_ended=has_chat_ended,
                ended_reason="Chat ended by simulator" if has_chat_ended else None,
                costs=[c.model_dump() for c in result.costs] if result.costs else None,
            )

        except Exception as e:
            logger.exception("vapi_send_message_failed", error=str(e))
            return SendMessageResult(success=False, error=str(e))

    async def send_message_async(self, input: SendMessageInput) -> SendMessageResult:
        """Async version of send_message.

        Vapi SDK is synchronous, so we run in an executor.

        Args:
            input: Contains session_id and messages to send.

        Returns:
            SendMessageResult with input/output messages and has_chat_ended flag.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.send_message(input))
