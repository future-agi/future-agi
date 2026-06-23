"""Pydantic input/output models for the ChatServiceBlueprint engine contract.

These types define the structured interface between ChatServiceManager
(thin router) and the engine implementations (VapiChatService, FutureAGIChatService, etc.).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from simulate.pydantic_schemas.chat import ChatMessage

# ---------------------------------------------------------------------------
# Provider Choices
# ---------------------------------------------------------------------------


class ChatProviderChoices(StrEnum):
    """Chat service provider choices."""

    VAPI = "vapi"
    FUTUREAGI = "futureagi"

    # Backward compatibility


# ---------------------------------------------------------------------------
# Create Assistant
# ---------------------------------------------------------------------------


class CreateAssistantInput(BaseModel):
    """Input for creating a chat assistant (simulator persona)."""

    name: str = Field(..., description="Name for the assistant")
    system_prompt: str = Field(..., description="System prompt for the simulator")
    voice_settings: dict[str, Any] | None = Field(
        default=None, description="Voice/initial message settings"
    )
    model: str | None = Field(default=None, description="LLM model to use")
    temperature: float | None = Field(
        default=0.9, description="Sampling temperature"
    )
    max_tokens: int | None = Field(default=800, description="Max output tokens")


class CreateAssistantResult(BaseModel):
    """Result of creating a chat assistant."""

    success: bool
    assistant_id: str | None = None
    provider_data: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Create Session
# ---------------------------------------------------------------------------


class CreateSessionInput(BaseModel):
    """Input for creating a chat session."""

    assistant_id: str = Field(..., description="ID of the assistant to use")
    name: str = Field(..., description="Name for the session")
    initial_message: ChatMessage | None = Field(
        default=None, description="Optional initial message from assistant"
    )
    organization_id: str | None = Field(
        default=None, description="Organization ID for API key resolution"
    )
    workspace_id: str | None = Field(
        default=None, description="Workspace ID for API key resolution"
    )


class CreateSessionResult(BaseModel):
    """Result of creating a chat session."""

    success: bool
    session_id: str | None = None
    messages: list[ChatMessage] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Get Session
# ---------------------------------------------------------------------------


class GetSessionInput(BaseModel):
    """Input for retrieving a chat session."""

    session_id: str = Field(..., description="ID of the session to retrieve")


class GetSessionResult(BaseModel):
    """Result of retrieving a chat session."""

    success: bool
    session_id: str | None = None
    name: str | None = None
    status: str | None = None
    assistant_id: str | None = None
    messages: list[ChatMessage] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Send Message
# ---------------------------------------------------------------------------


class SendMessageInput(BaseModel):
    """Input for sending messages to a chat session."""

    session_id: str = Field(..., description="ID of the session")
    messages: list[ChatMessage] = Field(..., description="Messages to send")
    organization_id: str | None = Field(
        default=None, description="Organization ID for API key resolution"
    )
    workspace_id: str | None = Field(
        default=None, description="Workspace ID for API key resolution"
    )


class SendMessageResult(BaseModel):
    """Result of sending messages to a chat session."""

    success: bool
    input_messages: list[ChatMessage] = Field(default_factory=list)
    output_messages: list[ChatMessage] = Field(default_factory=list)
    message_id: str | None = None
    has_chat_ended: bool = False
    ended_reason: str | None = None  # Reason why chat ended (if has_chat_ended=True)
    costs: list[dict] | None = None
    usage: Any | None = None  # LLMUsage or legacy dict
    error: str | None = None


# ---------------------------------------------------------------------------
# LLM Usage
# ---------------------------------------------------------------------------


class LLMUsage(BaseModel):
    """Structured token usage from an LLM completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# Delete Assistant
# ---------------------------------------------------------------------------


class DeleteAssistantInput(BaseModel):
    """Input for deleting a chat assistant."""

    assistant_id: str = Field(..., description="ID of the assistant to delete")


class DeleteAssistantResult(BaseModel):
    """Result of deleting a chat assistant."""

    success: bool
    error: str | None = None
