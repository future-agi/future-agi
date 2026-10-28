from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, field_validator


class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class SimulationCallType(str, Enum):
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
    content: Optional[str] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Optional[dict] = None
    tool_calls: Optional[List[ToolCall]] = None


class SendChatRequest(BaseModel):
    messages: Optional[List[ChatMessage]] = None
    metrics: Optional[dict[str, Optional[float | int]]] = None
    initiate_chat: Optional[bool] = False

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
    input_message: Optional[List[ChatMessage]] = None
    output_message: Optional[List[ChatMessage]] = None
    message_history: List[ChatMessage]
    chat_ended: Optional[bool] = False


class Costs(BaseModel):
    cost: float
    type: str
    model: Optional[str] = None
    total_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


class ChatSessionResponse(BaseModel):
    id: str
    name: str
    status: str
    assistant_id: str
    messages: Optional[List[ChatMessage]] = None


class ChatSessionSendMessageResponse(BaseModel):
    input: List[ChatMessage]
    output: List[ChatMessage]
    id: str
    has_chat_ended: Optional[bool] = False
    session_id: Optional[str] = None
    costs: Optional[List[Costs]] = None
