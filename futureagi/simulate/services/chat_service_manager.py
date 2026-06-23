"""ChatServiceManager — provider-agnostic chat service router.

Thin factory + proxy. All provider-specific logic lives in the engine
(VapiChatService, FutureAGIChatService, etc.). To add a provider:
implement ChatServiceBlueprint, register in ENGINE_REGISTRY.
"""

from __future__ import annotations

import threading
import warnings
from typing import TYPE_CHECKING

import structlog

from simulate.pydantic_schemas.chat import ChatMessage
from simulate.services.chat_constants import CHAT_SIMULATION_PROVIDER
from simulate.services.chat_engine import ChatServiceBlueprint
from simulate.services.types.chat import (
    ChatProviderChoices,
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

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


def _get_default_provider() -> ChatProviderChoices:
    """Get default chat provider from constants.

    See simulate/services/chat_constants.py for configuration.
    """
    try:
        return ChatProviderChoices(CHAT_SIMULATION_PROVIDER.lower())
    except ValueError:
        logger.warning(
            "invalid_chat_provider",
            provider=CHAT_SIMULATION_PROVIDER,
            fallback="futureagi",
        )
        return ChatProviderChoices.FUTUREAGI


class ChatServiceManager:
    """Provider-agnostic chat service router.

    Thin factory + proxy. Delegates all work to ``self.engine``.
    To add a provider: implement ChatServiceBlueprint, register in ENGINE_REGISTRY.
    """

    # Lazy import to avoid circular dependencies
    _ENGINE_REGISTRY: dict[ChatProviderChoices, type[ChatServiceBlueprint]] | None = (
        None
    )
    _ENGINE_REGISTRY_LOCK = threading.Lock()

    @classmethod
    def _get_engine_registry(
        cls,
    ) -> dict[ChatProviderChoices, type[ChatServiceBlueprint]]:
        """Lazy load engine registry to avoid circular imports (thread-safe)."""
        if cls._ENGINE_REGISTRY is None:
            with cls._ENGINE_REGISTRY_LOCK:
                if cls._ENGINE_REGISTRY is None:
                    from simulate.services.futureagi_chat import FutureAGIChatService
                    from simulate.services.vapi_chat import VapiChatService

                    cls._ENGINE_REGISTRY = {
                        ChatProviderChoices.VAPI: VapiChatService,
                        ChatProviderChoices.FUTUREAGI: FutureAGIChatService,
                    }
        return cls._ENGINE_REGISTRY

    def __init__(
        self,
        provider: ChatProviderChoices | None = None,
        api_key: str = "",
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ):
        """Initialize the chat service manager.

        Args:
            provider: Chat provider to use. Defaults to env var CHAT_SIMULATION_PROVIDER.
            api_key: API key for Vapi (not needed for Future AGI Chat).
            organization_id: Organization ID for API key resolution (Future AGI Chat).
            workspace_id: Workspace ID for API key resolution (Future AGI Chat).
        """
        self.provider = provider or _get_default_provider()
        self.organization_id = organization_id
        self.workspace_id = workspace_id

        engine_registry = self._get_engine_registry()
        engine_cls = engine_registry.get(self.provider)
        if not engine_cls:
            raise ValueError(f"Unsupported chat provider: {self.provider}")

        # Initialize engine with appropriate parameters
        if self.provider == ChatProviderChoices.FUTUREAGI:
            self.engine: ChatServiceBlueprint = engine_cls(
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        else:
            # Vapi uses API key
            self.engine = engine_cls(api_key=api_key)

        logger.info(
            "chat_service_manager_initialized",
            provider=self.provider.value,
            organization_id=organization_id,
        )

    # ------------------------------------------------------------------
    # Backward compatibility
    # ------------------------------------------------------------------

    @property
    def chat_provider_instance(self):
        """Deprecated: access the engine directly via ChatServiceManager methods."""
        warnings.warn(
            "chat_provider_instance is deprecated. Use ChatServiceManager methods directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.engine

    # ------------------------------------------------------------------
    # Assistant lifecycle
    # ------------------------------------------------------------------

    def create_assistant(
        self,
        name: str,
        system_prompt: str,
        voice_settings: dict | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CreateAssistantResult:
        """Create a chat assistant (simulator persona).

        Args:
            name: Name for the assistant.
            system_prompt: System prompt defining the simulator behavior.
            voice_settings: Optional voice/initial message settings.
            model: Optional LLM model override.
            temperature: Optional temperature override.
            max_tokens: Optional max tokens override.

        Returns:
            CreateAssistantResult with assistant_id on success.
        """
        return self.engine.create_assistant(
            CreateAssistantInput(
                name=name,
                system_prompt=system_prompt,
                voice_settings=voice_settings,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

    def delete_assistant(self, assistant_id: str) -> DeleteAssistantResult:
        """Delete a chat assistant.

        Args:
            assistant_id: ID of the assistant to delete.

        Returns:
            DeleteAssistantResult indicating success/failure.
        """
        return self.engine.delete_assistant(
            DeleteAssistantInput(assistant_id=assistant_id)
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(
        self,
        assistant_id: str,
        name: str,
        initial_message: ChatMessage | None = None,
    ) -> CreateSessionResult:
        """Create a chat session.

        Args:
            assistant_id: ID of the assistant to use.
            name: Name for the session.
            initial_message: Optional initial message from assistant.

        Returns:
            CreateSessionResult with session_id and initial messages.
        """
        return self.engine.create_session(
            CreateSessionInput(
                assistant_id=assistant_id,
                name=name,
                initial_message=initial_message,
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
            )
        )

    def get_session(self, session_id: str) -> GetSessionResult:
        """Get a chat session.

        Args:
            session_id: ID of the session to retrieve.

        Returns:
            GetSessionResult with session details and message history.
        """
        return self.engine.get_session(GetSessionInput(session_id=session_id))

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_message(
        self,
        session_id: str,
        messages: list[ChatMessage],
    ) -> SendMessageResult:
        """Send message(s) to a chat session.

        Args:
            session_id: ID of the session.
            messages: Messages to send.

        Returns:
            SendMessageResult with input/output messages and has_chat_ended flag.
        """
        return self.engine.send_message(
            SendMessageInput(
                session_id=session_id,
                messages=messages,
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
            )
        )

    async def send_message_async(
        self,
        session_id: str,
        messages: list[ChatMessage],
    ) -> SendMessageResult:
        """Async version of send_message.

        Args:
            session_id: ID of the session.
            messages: Messages to send.

        Returns:
            SendMessageResult with input/output messages and has_chat_ended flag.
        """
        return await self.engine.send_message_async(
            SendMessageInput(
                session_id=session_id,
                messages=messages,
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
            )
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_configuration(self) -> bool:
        """Validate the service configuration.

        Returns:
            True if configuration is valid, False otherwise.
        """
        return self.engine.validate_configuration()
