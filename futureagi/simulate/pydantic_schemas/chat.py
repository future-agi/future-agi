from enum import StrEnum

from pydantic import BaseModel, field_validator


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class SimulationCallType(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class ToolCallFunction(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str
    function: ToolCallFunction


class ChatMessage(BaseModel):
    """
    Schema for `Chat Message`.
    """

    role: ChatRole
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    metadata: dict | None = None
    tool_calls: list[ToolCall] | None = None


class SendChatRequest(BaseModel):
    messages: list[ChatMessage] | None = None
    metrics: dict[str, float | int | None] | None = None
    initiate_chat: bool | None = False

    @field_validator("metrics")
    @classmethod
    def validate_metrics_keys(cls, v):
        """Validate that metrics dict only contains allowed keys"""
        if v is None:
            return v

        allowed_keys = {
            "latency",
            "tokens",
            "cost",
            "duration",
            "response_time",
        }

        invalid_keys = set(v.keys()) - allowed_keys
        if invalid_keys:
            raise ValueError(
                f"Invalid metric keys: {', '.join(sorted(invalid_keys))}. "
                f"Allowed keys are: {', '.join(sorted(allowed_keys))}"
            )

        return v


class ChatSendMessageViewResponse(BaseModel):
    input_message: list[ChatMessage] | None = None
    output_message: list[ChatMessage] | None = None
    message_history: list[ChatMessage]
    chat_ended: bool | None = False


class Costs(BaseModel):
    cost: float
    type: str
    model: str | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatSessionResponse(BaseModel):
    id: str
    name: str
    status: str
    assistant_id: str
    messages: list[ChatMessage] | None = None


class ChatSessionSendMessageResponse(BaseModel):
    input: list[ChatMessage]
    output: list[ChatMessage]
    id: str
    has_chat_ended: bool | None = False
    session_id: str | None = None
    costs: list[Costs] | None = None
